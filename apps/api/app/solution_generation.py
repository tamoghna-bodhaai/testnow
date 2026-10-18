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

import fitz
import httpx
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import ImportJob, ImportStatus, Question, QuestionKind, SourceDocument
from .security import uid
from .services import audit
from .storage import get
from .tasks import normalize_answer


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
    if question.kind in ANSWER_KINDS and not question.answer:
        missing.append("answer")
    if not str(question.solution_markdown or "").strip():
        missing.append("solution")
    return missing


ACTIVE_RUN_STATUSES = {"queued", "running"}
SOLUTION_SCHEMA = {
    "name": "generated_question_solution",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["can_solve", "answer", "solution_markdown", "confidence", "notes"],
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
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "notes": {"type": "string"},
        },
    },
}
SOLUTION_SYSTEM = """You are a careful educational assessment solver. Solve exactly one supplied question.
Return JSON matching the schema. Never invent missing question text or unreadable visual facts. If the question cannot be solved unambiguously, set can_solve=false, answer=null, solution_markdown=null, and explain why in notes.

For a solvable question, derive the answer independently and write a pedagogical Markdown solution with at least two explicitly numbered steps. Preserve mathematical expressions as valid LaTeX. For single-choice and assertion-reason questions return {\"option\": zero_based_index}; for multiple-choice return {\"options\": [zero_based_indices]}; for numerical/integer return {\"value\": value}; for matrix match return {\"matches\": {row: [columns]}}. Subjective questions must return answer=null and a model solution.

An existing answer or solution is authoritative context. Do not propose changing it. When an existing answer is supplied, your derived answer must agree with it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(job: ImportJob) -> dict:
    return dict((job.extraction_meta or {}).get("solution_generation") or {})


def queue_generation(db: Session, job: ImportJob, actor_id: str, question: Question | None = None) -> dict:
    """Create one durable document or per-question run and return its status."""
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
    job.extraction_meta = {**(job.extraction_meta or {}), "solution_generation": state}
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


def _answer_valid(kind: QuestionKind, answer: dict | None) -> bool:
    if kind == QuestionKind.SUBJECTIVE:
        return answer is None
    if not isinstance(answer, dict):
        return False
    if kind in {QuestionKind.SINGLE, QuestionKind.ASSERTION}:
        return isinstance(answer.get("option"), int) and not isinstance(answer.get("option"), bool)
    if kind == QuestionKind.MULTIPLE:
        return bool(answer.get("options")) and all(isinstance(value, int) and not isinstance(value, bool) for value in answer["options"])
    if kind in {QuestionKind.NUMERICAL, QuestionKind.INTEGER}:
        return answer.get("value") is not None
    if kind == QuestionKind.MATRIX:
        return isinstance(answer.get("matches"), dict) and bool(answer["matches"])
    return False


def _step_by_step(value: object) -> bool:
    text = str(value or "").strip()
    markers = re.findall(r"(?m)^\s*\d+[.)]\s+", text)
    return len(text) >= 40 and len(markers) >= 2 and "$$$" not in text


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


def _provider_request(payload: dict) -> dict:
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
                return json.loads(response.json()["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
    raise RuntimeError(f"Solution provider failed after retries: {last_error}")


def generate_question(db: Session, question: Question, job: ImportJob, provider=None) -> dict:
    """Generate and validate one result without overwriting populated fields."""
    interaction = dict(question.interaction or {})
    generation = dict(interaction.get("solution_generation") or {})
    requested = list(generation.get("requested_fields") or missing_generation_fields(question))
    # Recheck at execution time: a human edit made after queueing always wins.
    current_missing = set(missing_generation_fields(question))
    requested = [field for field in requested if field in current_missing]
    if not requested:
        return {"status": "skipped", "generated_fields": []}
    content = [{"type": "text", "text": json.dumps({
        "kind": question.kind.value,
        "stem_markdown": question.stem_markdown,
        "options": question.options or [],
        "existing_answer": question.answer,
        "existing_solution_markdown": question.solution_markdown,
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
    result = (provider or _provider_request)(payload)
    if not isinstance(result, dict) or result.get("can_solve") is not True:
        raise ValueError(str((result or {}).get("notes") if isinstance(result, dict) else "The model did not return a valid result")[:300])
    generated_answer = normalize_answer(result.get("answer"), question.options or [])
    solution = str(result.get("solution_markdown") or "").strip()
    if not _step_by_step(solution):
        raise ValueError("The model did not return a valid multi-step solution")
    if question.kind == QuestionKind.SUBJECTIVE:
        generated_answer = None
    elif not _answer_valid(question.kind, generated_answer):
        raise ValueError("The model did not return a valid answer for this question type")
    if question.kind != QuestionKind.SUBJECTIVE and question.answer is not None and generated_answer != question.answer:
        raise ValueError("The generated solution conflicts with the existing answer key")
    # Both missing fields are committed only after every validation succeeds.
    if "answer" in requested:
        question.answer = generated_answer
    if "solution" in requested:
        question.solution_markdown = solution
    issues = [issue for issue in interaction.get("ingestion_issues", []) if issue not in {"missing_answer", "missing_solution", "solution_generation_failed"}]
    if "llm_generated_unreviewed" not in issues:
        issues.append("llm_generated_unreviewed")
    completed = {
        **generation,
        "status": "completed",
        "generated_fields": requested,
        "model": settings().openrouter_model,
        "confidence": float(result.get("confidence") or 0),
        "notes": str(result.get("notes") or "")[:1000],
        "completed_at": _now(),
    }
    question.interaction = {**interaction, "ingestion_issues": issues, "solution_generation": completed}
    question.approved_at = None
    return completed


def _checkpoint(db: Session, job_id: str, run_id: str, updates: dict) -> tuple[ImportJob, dict]:
    db.expire_all()
    job = db.get(ImportJob, job_id)
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
            try:
                result = generate_question(db, row, job)
                if result.get("status") == "skipped":
                    skipped += 1
                else:
                    succeeded += 1
                    audit(db, actor_id, "question_solution_generation.completed", "question", row.id, {"run_id": run_id, "generated_fields": result.get("generated_fields", [])})
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
                errors.append({"question_id": row.id, "source_number": row.source_number, "message": message[:300]})
                failed += 1
                audit(db, actor_id, "question_solution_generation.failed", "question", row.id, {"run_id": run_id, "error": message[:300]})
            processed += 1
            # Persist the question result before _checkpoint expires the
            # identity map to merge the latest job metadata.
            db.flush()
            job, state = _checkpoint(db, job_id, run_id, {
                "phase": f"Generated {processed} of {len(rows)} questions",
                "processed": processed,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "errors": errors[-50:],
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
    state = _run(job)
    if state.get("run_id") != run_id or state.get("status") not in {"completed", "completed_with_errors"}:
        raise ValueError("This solution-generation run is not ready for approval")
    now = datetime.now(timezone.utc)
    approved = 0
    for question in db.query(Question).filter_by(import_job_id=job.id).all():
        interaction = dict(question.interaction or {})
        generation = dict(interaction.get("solution_generation") or {})
        if generation.get("run_id") != run_id or generation.get("status") != "completed" or missing_generation_fields(question):
            continue
        warnings = list(interaction.get("ingestion_issues") or [])
        question.interaction = {
            **interaction,
            "ingestion_issues": [],
            "ignored_ingestion_issues": warnings,
            "warnings_ignored_by": actor_id,
            "warnings_ignored_at": now.isoformat(),
            "reviewed_by": actor_id,
            "solution_generation": {**generation, "approved_at": now.isoformat(), "approved_by": actor_id},
        }
        question.approved_at = now
        approved += 1
    state = {**state, "approved": approved, "approved_at": now.isoformat(), "approved_by": actor_id}
    job.extraction_meta = {**(job.extraction_meta or {}), "solution_generation": state}
    remaining = db.query(Question).filter_by(import_job_id=job.id, approved_at=None).first()
    failed_pages = bool((job.extraction_meta or {}).get("failed_pages"))
    if not remaining and not failed_pages:
        job.status = ImportStatus.APPROVED
    job.extraction_meta = {**job.extraction_meta, "flagged_count": sum(1 for row in db.query(Question).filter_by(import_job_id=job.id) if (row.interaction or {}).get("ingestion_issues"))}
    audit(db, actor_id, "question_solution_generation.approved", "import", job.id, {"run_id": run_id, "questions": approved, "warnings_overridden": True})
    return approved

