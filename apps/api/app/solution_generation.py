"""Generate missing answer keys and worked solutions for bank questions.

The module keeps the public seam deliberately small: callers can inspect a
question's missing fields, queue a durable run, execute that run in a worker,
and approve a completed document run. Provider and validation details stay
local to this module.
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import fitz
import httpx
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import ImportJob, ImportStatus, Question, QuestionKind, QuestionTaxonomy, SourceDocument, TaxonomyNode
from .security import uid
from .services import audit
from .storage import get
from .import_cleanup import delete_import_records
from .tasks import normalize_answer
from .readiness import has_usable_answer, record_readiness


ANSWER_KINDS = {
    QuestionKind.SINGLE,
    QuestionKind.MULTIPLE,
    QuestionKind.NUMERICAL,
    QuestionKind.INTEGER,
    QuestionKind.MATRIX,
    QuestionKind.ASSERTION,
}


def missing_generation_fields(question: Question) -> list[str]:
    """Return only fields that generation is allowed to fill."""
    missing: list[str] = []
    if question.kind in ANSWER_KINDS and not has_usable_answer(question.kind, question.answer, question.options or []):
        missing.append("answer")
    interaction = question.interaction or {}
    keyed_answer_is_sufficient = bool(
        interaction.get("answer_key_authoritative")
        and has_usable_answer(question.kind, question.answer, question.options or [])
    )
    if not str(question.solution_markdown or "").strip() and not keyed_answer_is_sufficient:
        missing.append("solution")
    return missing


ACTIVE_RUN_STATUSES = {"queued", "running"}
SOLUTION_SCHEMA = {
    "name": "generated_question_solution",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["can_solve", "answer", "solution_markdown", "matrix_items", "matrix_choices", "confidence", "notes"],
        "properties": {
            "can_solve": {"type": "boolean"},
            "answer": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "properties": {
                    "option": {"type": "integer"},
                    "options": {"type": "array", "items": {"type": "integer"}},
                    "value": {"type": ["string", "number"]},
                    "matches": {
                        "type": "object",
                        "additionalProperties": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "solution_markdown": {"type": ["string", "null"]},
            "matrix_items": {"type": "array", "items": {"type": "string"}},
            "matrix_choices": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "notes": {"type": "string"},
        },
    },
}
SOLUTION_SYSTEM = """You are a careful educational assessment solver. Solve exactly one supplied question.
Return JSON matching the schema. Never invent missing question text or unreadable visual facts. If the question cannot be solved unambiguously, set can_solve=false, answer=null, solution_markdown=null, and explain why in notes.

For a solvable question, derive the answer independently and write a clear pedagogical Markdown solution. Use numbered steps when the reasoning genuinely has multiple stages; a concise one-line explanation is correct for a direct fact or calculation. Preserve mathematical expressions as valid LaTeX. For single-choice and assertion-reason questions return {\"option\": zero_based_index}; for multiple-choice return {\"options\": [zero_based_indices]}; for numerical/integer return {\"value\": value}; for matrix match return {\"matches\": {row: [columns]}} and copy every row label into matrix_items and every selectable column label into matrix_choices. Return empty matrix arrays for other question types. Subjective questions must return answer=null and a model solution.

An existing answer or solution is authoritative context. Do not propose changing it. When an existing answer is supplied, your derived answer must agree with it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(job: ImportJob) -> dict:
    return dict((job.extraction_meta or {}).get("solution_generation") or {})


def queue_generation(db: Session, job: ImportJob, actor_id: str, question: Question | None = None) -> dict:
    """Create one durable document or per-question run and return its status."""
    # Serialize starts at the source-document seam. Without this lock, two
    # concurrent admin requests can both observe an idle JSON state.
    job = db.query(ImportJob).filter_by(id=job.id).populate_existing().with_for_update().one()
    if job.published_at:
        raise ValueError("This source document is live. Create a bank revision before generating answers or solutions")
    current = _run(job)
    if current.get("status") in ACTIVE_RUN_STATUSES:
        raise ValueError("A solution-generation run is already active for this source document")
    rows = [question] if question is not None else db.query(Question).filter_by(import_job_id=job.id).order_by(Question.created_at, Question.id).all()
    rows = [row for row in rows if row is not None and missing_generation_fields(row)]
    if not rows:
        raise LookupError("No questions in this source document have missing answers or solutions")
    run_id = uid()
    state = {
        "run_id": run_id,
        "mode": "question" if question is not None else "document",
        "question_id": question.id if question is not None else None,
        "status": "queued",
        "phase": "Queued for answer and solution generation",
        "total": len(rows),
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "errors": [],
        "requested_by": actor_id,
        "queued_at": _now(),
    }
    meta=dict(job.extraction_meta or {})
    history=list(meta.get("solution_generation_history") or [])
    if current.get("run_id") and not any(entry.get("run_id")==current["run_id"] for entry in history):
        history=(history+[current])[-20:]
    job.extraction_meta = {**meta, "solution_generation_history": history, "solution_generation": state}
    for row in rows:
        fields = missing_generation_fields(row)
        row.interaction = {**(row.interaction or {}), "solution_generation": {
            "run_id": run_id,
            "status": "queued",
            "requested_fields": fields,
            "requested_at": state["queued_at"],
        }}
    audit(db, actor_id, "question_solution_generation.queued", "import", job.id, {
        "run_id": run_id, "mode": state["mode"], "questions": len(rows),
    })
    return state


def _answer_valid(kind: QuestionKind, raw: object, answer: dict | None, options: list, interaction: dict) -> bool:
    if kind == QuestionKind.SUBJECTIVE:
        return raw is None and answer is None
    if not isinstance(raw, dict) or not isinstance(answer, dict):
        return False
    if kind in {QuestionKind.SINGLE, QuestionKind.ASSERTION}:
        value=raw.get("option")
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(options) and answer == {"option": value}
    if kind == QuestionKind.MULTIPLE:
        values=raw.get("options")
        return isinstance(values,list) and bool(values) and all(isinstance(value,int) and not isinstance(value,bool) and 0<=value<len(options) for value in values) and len(values)==len(set(values)) and answer=={"options":sorted(values)}
    if kind in {QuestionKind.NUMERICAL, QuestionKind.INTEGER}:
        value=raw.get("value")
        return has_usable_answer(kind, answer, options) and answer=={"value":value}
    if kind == QuestionKind.MATRIX:
        matches=raw.get("matches")
        if not isinstance(matches,dict) or not matches:return False
        if not all(str(row).strip() and isinstance(choices,list) and bool(choices) and len(choices)==len(set(map(str,choices))) and all(str(choice).strip() for choice in choices) for row,choices in matches.items()):return False
        allowed_rows={str(value) for value in interaction.get("matrix_items") or []}
        allowed_choices={str(value) for value in interaction.get("matrix_choices") or []}
        if not allowed_rows or not allowed_choices:return False
        if set(map(str,matches)) != allowed_rows:return False
        if allowed_choices and any(not set(map(str,choices)).issubset(allowed_choices) for choices in matches.values()):return False
        return answer==normalize_answer(raw,options)
    return False


def _answers_equivalent(kind:QuestionKind,generated:dict|None,existing:dict|None)->bool:
    if kind in {QuestionKind.NUMERICAL,QuestionKind.INTEGER} and isinstance(generated,dict) and isinstance(existing,dict):
        try:return Decimal(str(generated.get("value")))==Decimal(str(existing.get("value")))
        except (InvalidOperation,TypeError,ValueError):return False
    return generated==existing


def _valid_solution(value: object) -> bool:
    text = str(value or "").strip()
    dollars=len(re.findall(r"(?<!\\)\$",text))
    balanced=(dollars%2==0 and text.count(r"\(")==text.count(r"\)") and text.count(r"\[")==text.count(r"\]") and _balanced_braces(text))
    # Direct questions often need only a short sentence or equation. Structure
    # is a presentation choice, not evidence that the solution is valid.
    placeholder=text.lower().strip(" .:;!?-") in {"n/a","na","none","unknown","answer","solution"}
    words=re.findall(r"[A-Za-z]{2,}",text)
    substantive_equation=bool(re.search(r"(?:\d|[A-Za-z])\s*(?:=|\\approx|\\times|\\div|[+\-*/])\s*(?:\d|[A-Za-z])",text))
    return len(text) >= 8 and not placeholder and (len(words)>=3 or substantive_equation) and "$$$" not in text and balanced


def _balanced_braces(text:str)->bool:
    depth=0
    for match in re.finditer(r"(?<!\\)[{}]",text):
        depth += 1 if match.group()=="{" else -1
        if depth<0:return False
    return depth==0


def _image_part(raw: bytes, media_type: str = "image/png") -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{base64.b64encode(raw).decode()}"}}


def _visual_context(db: Session, question: Question, job: ImportJob) -> list[dict]:
    parts: list[dict] = []
    for diagram in (question.diagrams or [])[:8]:
        key = diagram.get("object_key") if isinstance(diagram, dict) else None
        if not key:
            continue
        try:
            parts.append(_image_part(get(key)))
        except Exception:
            continue
    # A source crop gives the solver the original notation even when ingestion
    # did not classify any standalone diagram.
    span = next((span for span in (question.source_spans or []) if isinstance(span, dict)), None)
    source = db.get(SourceDocument, job.question_document_id)
    if span and source:
        key = (job.extraction_meta or {}).get("canonical_pdf_key") or source.object_key
        try:
            raw = get(key)
            document = fitz.open(stream=raw, filetype="pdf")
            try:
                page_number = int(span.get("page") or 1)
                page = document[page_number - 1]
                box = fitz.Rect(span.get("bbox") or page.rect) & page.rect
                if not box.is_empty:
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), clip=box, alpha=False)
                    parts.append(_image_part(pixmap.tobytes("png")))
            finally:
                document.close()
        except Exception:
            pass
    return parts


def _provider_request(payload: dict) -> tuple[dict,str]:
    if not settings().openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    models = [settings().openrouter_model]
    fallback = settings().openrouter_fallback_model
    if fallback and fallback not in models:
        models.append(fallback)
    last_error: Exception | None = None
    for model in models:
        request = {**payload, "model": model}
        for attempt in range(3):
            try:
                response = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings().openrouter_api_key}",
                        "HTTP-Referer": "https://testnow.app",
                        "X-Title": "TestNow Solution Generator",
                    },
                    json=request,
                    timeout=settings().openrouter_timeout_seconds,
                )
                response.raise_for_status()
                return json.loads(response.json()["choices"][0]["message"]["content"]),model
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
    raise RuntimeError(f"Solution provider failed after retries: {last_error}")


def generate_question(db: Session, question: Question, job: ImportJob, provider=None) -> dict:
    """Generate and validate one result without overwriting populated fields."""
    db.flush()
    original_content = json.dumps([question.kind.value, question.stem_markdown, question.options, question.answer,
                                   question.solution_markdown, question.interaction, question.diagrams], sort_keys=True)
    interaction = dict(question.interaction or {})
    generation = dict(interaction.get("solution_generation") or {})
    requested = list(generation.get("requested_fields") or missing_generation_fields(question))
    # Recheck at execution time: a human edit made after queueing always wins.
    current_missing = set(missing_generation_fields(question))
    requested = [field for field in requested if field in current_missing]
    if not requested:
        return {"status": "skipped", "generated_fields": []}
    if interaction.get("answer_key_authoritative") and question.answer is not None and not has_usable_answer(question.kind, question.answer, question.options or []):
        raise ValueError("The trusted source answer key is malformed. Repair it explicitly before generating content")
    taxonomy=[{"kind":node.kind,"name":node.name} for node in db.query(TaxonomyNode).join(QuestionTaxonomy,QuestionTaxonomy.taxonomy_node_id==TaxonomyNode.id).filter(QuestionTaxonomy.question_id==question.id).order_by(TaxonomyNode.kind,TaxonomyNode.name)]
    content = [{"type": "text", "text": json.dumps({
        "kind": question.kind.value,
        "stem_markdown": question.stem_markdown,
        "options": question.options or [],
        "taxonomy": taxonomy,
        "existing_answer": question.answer,
        "existing_solution_markdown": question.solution_markdown,
        "matrix_items": interaction.get("matrix_items") or [],
        "matrix_choices": interaction.get("matrix_choices") or [],
        "fields_to_generate": requested,
    }, ensure_ascii=False)}]
    content.extend(_visual_context(db, question, job))
    payload = {
        "model": settings().openrouter_model,
        "messages": [{"role": "system", "content": SOLUTION_SYSTEM}, {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": SOLUTION_SCHEMA},
        "provider": {"require_parameters": True},
        "max_tokens": min(settings().openrouter_max_output_tokens, 8192),
        "stream": False,
    }
    provider_result = (provider or _provider_request)(payload)
    if isinstance(provider_result,tuple):result,provider_model=provider_result
    else:result,provider_model=provider_result,settings().openrouter_model
    if not isinstance(result, dict) or result.get("can_solve") is not True:
        raise ValueError(str((result or {}).get("notes") if isinstance(result, dict) else "The model did not return a valid result")[:300])
    raw_answer=result.get("answer")
    generated_answer = normalize_answer(raw_answer, question.options or [])
    solution = str(result.get("solution_markdown") or "").strip()
    if not _valid_solution(solution):
        raise ValueError("The model did not return a valid solution")
    if question.kind == QuestionKind.SUBJECTIVE:
        generated_answer = None
    validation_interaction = interaction
    matrix_contract: tuple[list[str], list[str]] | None = None
    if question.kind == QuestionKind.MATRIX:
        existing_matches = (question.answer or {}).get("matches") if isinstance(question.answer, dict) else None
        existing_choice_contract = list(dict.fromkeys(
            str(choice).strip()
            for choices in existing_matches.values()
            for choice in choices
            if str(choice).strip()
        )) if isinstance(existing_matches, dict) else []
        raw_items = interaction.get("matrix_items") or result.get("matrix_items") or (
            list(existing_matches) if isinstance(existing_matches, dict) else []
        )
        raw_choices = interaction.get("matrix_choices") or result.get("matrix_choices") or existing_choice_contract
        matrix_items = [str(value).strip() for value in raw_items if str(value).strip()]
        matrix_choices = [str(value).strip() for value in raw_choices if str(value).strip()]
        if len(matrix_items) != len(set(matrix_items)) or len(matrix_choices) != len(set(matrix_choices)):
            raise ValueError("The model did not return a valid matrix contract")
        matrix_contract = (matrix_items, matrix_choices)
        validation_interaction = {**interaction, "matrix_items": matrix_items, "matrix_choices": matrix_choices}
    if question.kind != QuestionKind.SUBJECTIVE and not _answer_valid(question.kind,raw_answer,generated_answer,question.options or [],validation_interaction):
        raise ValueError("The model did not return a valid answer for this question type")
    if has_usable_answer(question.kind, question.answer, question.options or []) and not _answers_equivalent(question.kind,generated_answer,question.answer):
        raise ValueError("The generated solution conflicts with the existing answer key")
    locked_job=db.query(ImportJob).filter_by(id=job.id).with_for_update().one()
    if locked_job.published_at:
        raise ValueError("This source document became live during generation; create a bank revision before changing it")
    question = db.query(Question).filter_by(id=question.id).populate_existing().with_for_update().one()
    current_content = json.dumps([question.kind.value, question.stem_markdown, question.options, question.answer,
                                  question.solution_markdown, question.interaction, question.diagrams], sort_keys=True)
    if current_content != original_content:
        raise ValueError("The question changed during generation; the saved content was preserved. Retry generation after review")
    # Both missing fields are committed only after every validation succeeds.
    if "answer" in requested:
        question.answer = generated_answer
    if "solution" in requested:
        question.solution_markdown = solution
    if matrix_contract:
        interaction = {**interaction, "matrix_items": matrix_contract[0], "matrix_choices": matrix_contract[1]}
    issues = [issue for issue in interaction.get("ingestion_issues", []) if issue not in {"missing_answer", "missing_solution", "solution_generation_failed"}]
    if "llm_generated_unreviewed" not in issues:
        issues.append("llm_generated_unreviewed")
    completed = {
        **generation,
        "status": "completed",
        "generated_fields": requested,
        "model": provider_model,
        "confidence": float(result.get("confidence") or 0),
        "notes": str(result.get("notes") or "")[:1000],
        "completed_at": _now(),
    }
    question.interaction = {**interaction, "ingestion_issues": issues, "solution_generation": completed}
    question.approved_at = None
    record_readiness(question)
    return completed


def _checkpoint(db: Session, job_id: str, run_id: str, updates: dict) -> tuple[ImportJob, dict]:
    db.expire_all()
    job = db.query(ImportJob).filter_by(id=job_id).populate_existing().with_for_update().one_or_none()
    if not job:
        raise ValueError("Bank import job not found")
    state = _run(job)
    if state.get("run_id") != run_id:
        raise ValueError("Solution-generation run was superseded")
    state = {**state, **updates}
    job.extraction_meta = {**(job.extraction_meta or {}), "solution_generation": state}
    return job, state


def run_generation(job_id: str, run_id: str, actor_id: str, question_id: str | None = None) -> None:
    db = SessionLocal()
    try:
        job, state = _checkpoint(db, job_id, run_id, {"status": "running", "phase": "Generating answers and solutions", "started_at": _now()})
        db.commit()
        query = db.query(Question).filter_by(import_job_id=job_id)
        if question_id:
            query = query.filter_by(id=question_id)
        rows = query.order_by(Question.created_at, Question.id).all()
        rows = [row for row in rows if (row.interaction or {}).get("solution_generation", {}).get("run_id") == run_id]
        succeeded = failed = skipped = processed = 0
        errors: list[dict] = []
        for row in rows:
            db.expire_all()
            current_job=db.get(ImportJob,job_id)
            if not current_job:return
            if (current_job.extraction_meta or {}).get("delete_requested"):
                delete_import_records(db,current_job,(current_job.extraction_meta or {}).get("delete_requested_by"));db.commit();return
            try:
                result = generate_question(db, row, job)
                if result.get("status") == "skipped":
                    skipped += 1
                else:
                    succeeded += 1
                    audit(db, actor_id, "question_solution_generation.completed", "question", row.id, {"run_id": run_id, "generated_fields": result.get("generated_fields", []),"model":result.get("model")})
            except Exception as exc:
                db.rollback()
                row = db.get(Question, row.id)
                message = str(exc).strip() or exc.__class__.__name__
                interaction = dict(row.interaction or {})
                generation = dict(interaction.get("solution_generation") or {})
                issues = list(interaction.get("ingestion_issues") or [])
                if "solution_generation_failed" not in issues:
                    issues.append("solution_generation_failed")
                row.interaction = {**interaction, "ingestion_issues": issues, "solution_generation": {**generation, "status": "failed", "error": message[:500], "failed_at": _now()}}
                record_readiness(row)
                errors.append({"question_id": row.id, "source_number": row.source_number, "message": message[:300]})
                failed += 1
                audit(db, actor_id, "question_solution_generation.failed", "question", row.id, {"run_id": run_id, "error": message[:300]})
            processed += 1
            db.flush();db.expire_all()
            current_job=db.get(ImportJob,job_id)
            if not current_job:return
            if (current_job.extraction_meta or {}).get("delete_requested"):
                delete_import_records(db,current_job,(current_job.extraction_meta or {}).get("delete_requested_by"));db.commit();return
            # Persist the question result before _checkpoint expires the
            # identity map to merge the latest job metadata.
            job, state = _checkpoint(db, job_id, run_id, {
                "phase": f"Generated {processed} of {len(rows)} questions",
                "processed": processed,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "errors": errors[-50:],
                "error_log": errors,
            })
            db.commit()
        final_status = "completed_with_errors" if failed else "completed"
        job, state = _checkpoint(db, job_id, run_id, {"status": final_status, "phase": "Generation complete" if not failed else "Generation complete with errors", "finished_at": _now()})
        audit(db, actor_id, "question_solution_generation.finished", "import", job.id, {"run_id": run_id, "succeeded": succeeded, "failed": failed, "skipped": skipped})
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(ImportJob, job_id)
        if job and _run(job).get("run_id") == run_id:
            state = {**_run(job), "status": "failed", "phase": "Generation failed", "error": (str(exc).strip() or exc.__class__.__name__)[:500], "finished_at": _now()}
            job.extraction_meta = {**(job.extraction_meta or {}), "solution_generation": state}
            audit(db, actor_id, "question_solution_generation.failed", "import", job.id, {"run_id": run_id, "error": state["error"]})
            db.commit()
    finally:
        db.close()


def approve_generation(db: Session, job: ImportJob, run_id: str, actor_id: str) -> int:
    job = db.query(ImportJob).filter_by(id=job.id).populate_existing().with_for_update().one()
    current=_run(job);history=list((job.extraction_meta or {}).get("solution_generation_history") or [])
    if current.get("status") in ACTIVE_RUN_STATUSES and current.get("run_id") != run_id:
        raise ValueError("Wait for the active solution-generation run before approving an earlier run")
    history_index=next((index for index,entry in enumerate(history) if entry.get("run_id")==run_id),None)
    state=current if current.get("run_id")==run_id else (history[history_index] if history_index is not None else {})
    if state.get("status") not in {"completed", "completed_with_errors", "failed"}:
        raise ValueError("This solution-generation run is not ready for approval")
    now = datetime.now(timezone.utc)
    approved = 0
    overrides=[]
    for question in db.query(Question).filter_by(import_job_id=job.id).all():
        interaction = dict(question.interaction or {})
        generation = dict(interaction.get("solution_generation") or {})
        if generation.get("run_id") != run_id or generation.get("status") != "completed" or missing_generation_fields(question):
            continue
        warnings = list(interaction.get("ingestion_issues") or [])
        remaining_warnings = [warning for warning in warnings if warning not in {"llm_generated_unreviewed", "solution_generation_failed", "missing_answer", "missing_solution"}]
        question.interaction = {
            **interaction,
            "ingestion_issues": remaining_warnings,
            "solution_generation": {**generation, "approved_at": now.isoformat(), "approved_by": actor_id},
        }
        # Confirming generated content must not silently waive a separate
        # extraction or visual defect. Advisory taxonomy findings are retained.
        readiness = record_readiness(question)
        if readiness.content_valid:
            question.interaction = {**question.interaction, "reviewed_by": actor_id}
            question.approved_at = now
            record_readiness(question)
            approved += 1
        overrides.append({"question_id":question.id,"resolved_generation_warnings":[warning for warning in warnings if warning not in remaining_warnings],"remaining_warnings":remaining_warnings})
    state = {**state, "approved": approved, "approved_at": now.isoformat(), "approved_by": actor_id}
    if current.get("run_id")==run_id:current=state
    elif history_index is not None:history[history_index]=state
    job.extraction_meta = {**(job.extraction_meta or {}), "solution_generation": current, "solution_generation_history": history}
    db.flush()
    remaining = db.query(Question).filter_by(import_job_id=job.id, approved_at=None).first()
    failed_pages = bool((job.extraction_meta or {}).get("failed_pages"))
    if not remaining and not failed_pages:
        job.status = ImportStatus.APPROVED
    job.extraction_meta = {**job.extraction_meta, "flagged_count": sum(1 for row in db.query(Question).filter_by(import_job_id=job.id) if (row.interaction or {}).get("ingestion_issues"))}
    audit(db, actor_id, "question_solution_generation.approved", "import", job.id, {"run_id": run_id, "questions": approved, "question_results": overrides})
    return approved

