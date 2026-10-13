"""Resumable, text-first global JEE bank seeding through OpenRouter.

This command is intentionally separate from ``seed_jee``: it never stores a
whole question crop as a diagram.  It extracts Markdown/LaTex question records
from small, overlapping page windows, then uses the worksheet's answer-key and
detailed-solutions sections to verify/publish only complete records.

Example (run after Alembic migrations):
  PYTHONPATH=apps/api python -m app.seed_jee_openrouter --owner-email admin@example.com \
    --replace --dry-run /imports/*_clean.pdf
  PYTHONPATH=apps/api python -m app.seed_jee_openrouter --owner-email admin@example.com \
    --replace /imports/*_clean.pdf

``--replace`` is irreversible: it refuses to run if a legacy bank question is
referenced by any saved test version, then removes only the old JEE seed rows
and their two explicit S3 prefixes.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import fitz
import httpx
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .map_jee_solutions import canonical_chapter, map_pdf
from .models import ImportJob, ImportStatus, Question, QuestionBank, QuestionKind, QuestionScope, QuestionTaxonomy, SourceDocument, TaxonomyNode, TestVersion, User
from .security import uid
from .seed_jee import BANK_SLUG, find_or_create_node, subject_from_filename
from .storage import delete_prefix, put
from .tasks import normalize_answer

PUBLISH_THRESHOLD = 0.90
MAX_WINDOW_PAGES = 3
SYSTEM = """You are extracting a permanent JEE Advanced question bank. Return only JSON matching the schema. Read the page images and native text together. Extract only complete questions whose printed number begins on one of the requested core pages; do not extract answer keys, worked-solution pages, headers, instructions, or continuations that began earlier. Preserve question prose and mathematics in Markdown with valid LaTex delimiters. Keep every option semantic and complete. `chapter` must be the printed/current chapter. Identify the actual question kind.

Classify a question as `single_choice`, `multiple_choice`, or `assertion_reason` only when the source visibly supplies at least two selectable answer choices. Put those choices, and only those choices, in `options`. A long written question with lettered parts such as `(a)`, `(b)`, `(c)` is `subjective`: those parts belong in `stem_markdown`, not in `options`, even if its answer-key entry is labelled A, B, C, or D. Never infer a choice type from the answer key or from a preceding section heading. A numerical/integer question may have no options. A diagram is only a self-contained graph, figure, circuit, table, or geometry drawing: give its exact PDF-point bounding box. Never use a whole-question screenshot, text block, options, or solution as a diagram. If uncertain, omit the diagram and lower confidence."""
ITEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["number", "chapter", "kind", "stem_markdown", "options", "answer", "confidence", "source_spans", "diagrams"],
    "properties": {
        "number": {"type": "string", "minLength": 1},
        "chapter": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": [kind.value for kind in QuestionKind]},
        "stem_markdown": {"type": "string", "minLength": 1},
        "options": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["value", "markdown"], "properties": {"value": {"type": "string"}, "markdown": {"type": "string"}}}},
        "answer": {"type": ["object", "null"], "additionalProperties": True},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source_spans": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["page", "bbox"], "properties": {"page": {"type": "integer", "minimum": 1}, "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}}}},
        "diagrams": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["page", "bbox", "alt"], "properties": {"page": {"type": "integer", "minimum": 1}, "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}, "alt": {"type": "string"}}}},
    },
}
SCHEMA = {"name": "jee_seed_page_window", "strict": True, "schema": {"type": "object", "additionalProperties": False, "required": ["questions"], "properties": {"questions": {"type": "array", "items": ITEM_SCHEMA}}}}


def clamp_bbox(value: object, page: fitz.Page) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    rect = fitz.Rect(x0, y0, x1, y1) & page.rect
    if rect.is_empty or rect.width < 3 or rect.height < 3:
        return None
    return [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]


def area(box: list[float]) -> float:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def valid_diagram(diagram: dict, spans: list[dict], document: fitz.Document) -> dict | None:
    """Accept only a bounded figure, never a crop that is effectively a question."""
    try:
        page_no = int(diagram.get("page"))
        page = document[page_no - 1]
    except (TypeError, ValueError, IndexError):
        return None
    bbox = clamp_bbox(diagram.get("bbox"), page)
    if not bbox or area(bbox) > area([0, 0, page.rect.width, page.rect.height]) * 0.55:
        return None
    source_boxes = [span["bbox"] for span in spans if span.get("page") == page_no and span.get("bbox")]
    if source_boxes and any(area(bbox) >= area(source) * 0.70 for source in source_boxes):
        return None
    return {"page": page_no, "bbox": bbox, "alt": str(diagram.get("alt") or "Question diagram")[:240]}


def clean_spans(item: dict, document: fitz.Document) -> list[dict]:
    clean = []
    for span in item.get("source_spans") or []:
        try:
            page_no = int(span.get("page")); page = document[page_no - 1]
        except (TypeError, ValueError, IndexError):
            continue
        if bbox := clamp_bbox(span.get("bbox"), page):
            clean.append({"page": page_no, "bbox": bbox})
    return sorted(clean, key=lambda span: (span["page"], span["bbox"][1], span["bbox"][0]))


def normalized_options(options: object) -> list[dict]:
    """Keep options in the API's canonical, displayable representation."""
    result=[]
    for index, option in enumerate(options if isinstance(options, list) else []):
        if isinstance(option, str):
            result.append({"value": chr(97 + index), "markdown": option})
        elif isinstance(option, dict):
            result.append({"value": str(option.get("value", chr(97 + index))), "markdown": str(option.get("markdown") or option.get("text") or "")})
    return result


CHOICE_KINDS = {QuestionKind.SINGLE, QuestionKind.MULTIPLE, QuestionKind.ASSERTION}


def normalize_choice_kind(kind: QuestionKind, options: list[dict]) -> QuestionKind:
    """Never let an optionless record reach the UI as a choice question.

    Some worksheet answer keys use A--D labels for written questions.  When a
    model follows a previous section's "single correct" heading, that label can
    otherwise turn a multipart written question into a fake MCQ.  Keeping the
    record as subjective is preferable to silently dropping its stem.
    """
    return QuestionKind.SUBJECTIVE if kind in CHOICE_KINDS and len(options) < 2 else kind


def taxonomy_nodes(db: Session, bank: QuestionBank, subject: str, chapter: str) -> tuple[TaxonomyNode, TaxonomyNode]:
    subject_node = find_or_create_node(db, bank.id, None, "subject", subject)
    chapter = canonical_chapter(subject, chapter) or "Unmapped chapter"
    return subject_node, find_or_create_node(db, bank.id, subject_node.id, "chapter", chapter[:200])


def is_snapshot_reference(content: object, question_ids: set[str]) -> bool:
    if isinstance(content, dict):
        return any(is_snapshot_reference(value, question_ids) for value in content.values())
    if isinstance(content, list):
        return any(is_snapshot_reference(value, question_ids) for value in content)
    return isinstance(content, str) and content in question_ids


def preflight_replace(db: Session, bank: QuestionBank) -> tuple[list[str], list[str]]:
    ids = [row.id for row in db.query(Question.id).filter_by(bank_id=bank.id).all()]
    if ids:
        referenced = [version.id for version in db.query(TestVersion).all() if is_snapshot_reference(version.content, set(ids))]
        if referenced:
            raise ValueError(f"Refusing destructive replacement: {len(referenced)} test version(s) reference the existing JEE bank")
    source_ids = {str((row.interaction or {}).get("source_document_id")) for row in db.query(Question).filter_by(bank_id=bank.id) if (row.interaction or {}).get("source_document_id")}
    return ids, list(source_ids)


def replace_bank(db: Session, bank: QuestionBank) -> None:
    ids, source_ids = preflight_replace(db, bank)
    # Delete blobs first while their exact target prefixes are still known.  The
    # database transaction removes only rows owned by this one bank afterwards.
    delete_prefix(f"seed-diagrams/{bank.id}/")
    delete_prefix(f"seed-sources/{bank.id}/")
    if ids:
        db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id.in_(ids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.id.in_(ids)).delete(synchronize_session=False)
    db.query(TaxonomyNode).filter_by(bank_id=bank.id).delete(synchronize_session=False)
    for source_id in source_ids:
        source = db.get(SourceDocument, source_id)
        if source and source.object_key.startswith(f"seed-sources/{bank.id}/"):
            db.delete(source)
    db.delete(bank)
    db.commit()


def source_document(db: Session, owner: User, bank: QuestionBank, path: Path) -> SourceDocument:
    raw = path.read_bytes(); checksum = hashlib.sha256(raw).hexdigest()
    source = db.query(SourceDocument).filter(SourceDocument.sha256==checksum, SourceDocument.object_key.like(f"seed-sources/{bank.id}/%")).first()
    if source:
        return source
    source = SourceDocument(id=uid(), owner_id=owner.id, filename=path.name, object_key=f"seed-sources/{bank.id}/{uid()}-{path.name}", sha256=checksum, media_type="application/pdf", size_bytes=len(raw))
    put(source.object_key, raw, source.media_type); db.add(source); db.commit()
    return source


def seed_checkpoint(db: Session, owner: User, bank: QuestionBank, source: SourceDocument) -> ImportJob:
    """Persist the last fully committed core page so an interrupted seed resumes safely."""
    job = db.query(ImportJob).filter_by(question_document_id=source.id).order_by(ImportJob.created_at.desc()).first()
    if job:
        return job
    completed = 0
    for question in db.query(Question).filter_by(bank_id=bank.id):
        interaction = question.interaction or {}
        if interaction.get("source_document_id") == source.id:
            completed = max(completed, int(interaction.get("source_page") or 0))
    job = ImportJob(
        id=uid(), owner_id=owner.id, question_document_id=source.id,
        status=ImportStatus.PROCESSING,
        extraction_meta={"seed_checkpoint": {"extracted_through_page": completed, "answers_mapped": False}},
    )
    db.add(job); db.commit()
    return job


def checkpoint_meta(job: ImportJob) -> dict:
    return dict((job.extraction_meta or {}).get("seed_checkpoint") or {})


def save_checkpoint(db: Session, job: ImportJob, **changes: object) -> None:
    meta = checkpoint_meta(job); meta.update(changes)
    job.extraction_meta = {**(job.extraction_meta or {}), "seed_checkpoint": meta}
    db.commit()


def page_windows(limit: int, pages_per_request: int) -> Iterable[tuple[list[int], list[int]]]:
    for start in range(1, limit + 1, pages_per_request):
        core = list(range(start, min(limit + 1, start + pages_per_request)))
        # A lookahead page lets a question that begins at a page boundary retain
        # its final options/figure.  The core-page rule prevents duplicate rows.
        visible = list(range(max(1, start - 1), min(limit + 1, start + pages_per_request + 1)))
        yield core, visible


def extract_window(document: fitz.Document, subject: str, core: list[int], visible: list[int]) -> tuple[list[dict], dict]:
    if not settings().openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    context = []
    for page_no in visible:
        text = document[page_no - 1].get_text("text", sort=True)
        context.append(f"--- PAGE {page_no} ---\n{text[:18000]}")
    content = [{"type": "text", "text": f"Subject: {subject}. Extract questions that START on core pages {core}. Visible pages are {visible}. Native text context:\n" + "\n".join(context)}]
    for page_no in visible:
        image = document[page_no - 1].get_pixmap(matrix=fitz.Matrix(1.45, 1.45), alpha=False).tobytes("png")
        content.extend([{"type": "text", "text": f"PDF page {page_no}"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}}])
    payload = {"model": settings().openrouter_model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}], "response_format": {"type": "json_schema", "json_schema": SCHEMA}, "provider": {"require_parameters": True}, "stream": False}
    last_error=None
    for attempt in range(4):
        try:
            response = httpx.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {settings().openrouter_api_key}", "HTTP-Referer": "https://testnow.app", "X-Title": "TestNow JEE Seeder"}, json=payload, timeout=settings().openrouter_timeout_seconds)
            response.raise_for_status(); body = response.json()
            parsed = json.loads(body["choices"][0]["message"]["content"])
            if not isinstance(parsed.get("questions"), list): raise ValueError("Provider returned no questions array")
            return parsed["questions"], body.get("usage") or {}
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error=exc
            if attempt == 3: break
            delay=2 ** attempt
            print(f"Retrying core pages {core} after {type(exc).__name__}: {exc} (in {delay}s)", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"OpenRouter extraction failed for core pages {core}") from last_error


def store_item(db: Session, bank: QuestionBank, owner: User, source: SourceDocument, subject: str, document: fitz.Document, item: dict, core: list[int], threshold: float) -> bool:
    spans = clean_spans(item, document)
    if not spans or spans[0]["page"] not in core:
        return False
    number = str(item.get("number") or "").strip()
    if not number or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", number):
        return False
    try:
        kind = QuestionKind(str(item.get("kind")))
        confidence = min(1.0, max(0.0, float(item.get("confidence", 0))))
    except (ValueError, TypeError):
        return False
    stem = str(item.get("stem_markdown") or "").strip()
    options = normalized_options(item.get("options") or [])
    kind = normalize_choice_kind(kind, options)
    if len(stem) < 8 or "$$$" in stem:
        return False
    page_no = spans[0]["page"]
    source_key = f"{source.sha256}:{page_no}:{number}"
    if db.query(Question.id).filter_by(bank_id=bank.id, source_key=source_key).first():
        return False
    chapter = re.sub(r"\s+", " ", str(item.get("chapter") or "")).strip() or "Unmapped chapter"
    subject_node, chapter = taxonomy_nodes(db, bank, subject, chapter)
    diagrams=[]
    for index, raw in enumerate(item.get("diagrams") or []):
        if not isinstance(raw, dict) or not (diagram := valid_diagram(raw, spans, document)):
            continue
        page = document[diagram["page"] - 1]; x0, y0, x1, y1 = diagram["bbox"]
        png = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(x0, y0, x1, y1), alpha=False).tobytes("png")
        key = f"seed-diagrams/{bank.id}/{subject.lower()}/{source.id}-{page_no}-{number}-{index}.png"
        put(key, png, "image/png"); diagrams.append({**diagram, "object_key": key})
    # An A--D answer-key label is not a meaningful answer for a written
    # question, so do not expose it as one after normalizing its type.
    answer = None if kind == QuestionKind.SUBJECTIVE and len(options) < 2 else normalize_answer(item.get("answer"), options)
    interaction = {"seed_mode": "openrouter_text_first", "seed_status": "awaiting_native_answer_solution_verification", "source_document_id": source.id, "source_page": page_no, "model": settings().openrouter_model, "diagram_validation": "bounded_figure_only"}
    q = Question(id=uid(), owner_id=owner.id, bank_id=bank.id, scope=QuestionScope.GLOBAL, source_number=number, source_key=source_key, kind=kind, stem_markdown=stem, options=options, answer=answer, solution_markdown=None, diagrams=diagrams, source_spans=spans, interaction=interaction, confidence=confidence, approved_at=None)
    db.add(q); db.flush(); db.add(QuestionTaxonomy(question_id=q.id, taxonomy_node_id=subject_node.id)); db.add(QuestionTaxonomy(question_id=q.id, taxonomy_node_id=chapter.id)); return True


def publish_verified(db: Session, bank: QuestionBank, threshold: float, allow_missing_solution: bool = False) -> dict[str, int]:
    now=datetime.now(timezone.utc); result={"published":0,"review":0}
    for q in db.query(Question).filter_by(bank_id=bank.id, scope=QuestionScope.GLOBAL):
        ready=bool(q.answer and (q.solution_markdown or allow_missing_solution) and q.confidence is not None and q.confidence >= threshold and q.source_spans)
        q.approved_at=now if ready else None
        interaction=dict(q.interaction or {}); interaction["seed_status"]=("published_confidence_gated" if q.solution_markdown else "published_answer_key_gated") if ready else "review_required"
        q.interaction=interaction; result["published" if ready else "review"]+=1
    db.commit(); return result


def main() -> None:
    parser=argparse.ArgumentParser(description="Seed a text-first, globally filtered JEE bank through OpenRouter")
    parser.add_argument("pdfs", nargs="+", type=Path); parser.add_argument("--owner-email", required=True)
    parser.add_argument("--replace", action="store_true", help="Permanently replace the current JEE global bank after reference preflight")
    parser.add_argument("--dry-run", action="store_true", help="Report model windows and estimate only; no model, database, or object-store writes")
    parser.add_argument("--pages-per-request", type=int, default=2, choices=range(1, MAX_WINDOW_PAGES + 1))
    parser.add_argument("--publish-threshold", type=float, default=PUBLISH_THRESHOLD)
    parser.add_argument("--bank-slug", default=BANK_SLUG, help="Target global bank slug")
    parser.add_argument("--bank-name", default="JEE Advanced Question Bank", help="Target global bank display name")
    parser.add_argument("--exam-code", default="JEE_ADVANCED", help="Target global bank exam classification")
    parser.add_argument("--subject", choices=("Physics", "Chemistry", "Mathematics"), help="Subject for every supplied source; otherwise infer it from the filename")
    parser.add_argument("--publish-without-solution", action="store_true", help="Publish answer-keyed questions even when no worked solution was extracted")
    args=parser.parse_args()
    if not 0 <= args.publish_threshold <= 1: raise SystemExit("--publish-threshold must be between 0 and 1")
    for path in args.pdfs:
        if not path.is_file() or path.suffix.lower() != ".pdf": raise SystemExit(f"Not a readable PDF: {path}")
    # Keep only one PDF open at a time.  These documents are large and a seed
    # can run for hours; holding all 23 in memory invites a container restart.
    limits: list[int] = []
    for path in args.pdfs:
        document = fitz.open(path)
        try:
            limits.append(next(index for index, page in enumerate(document) if "ANSWER KEY" in page.get_text("text", sort=True).upper()))
        finally:
            document.close()
    windows=sum(sum(1 for _ in page_windows(limit, args.pages_per_request)) for limit in limits)
    if args.dry_run:
        visible=sum(len(visible) for limit in limits for _, visible in page_windows(limit, args.pages_per_request))
        print(json.dumps({"mode":"dry-run","question_pages":sum(limits),"requests":windows,"visible_page_images":visible,"estimated_input_tokens":visible*2500,"estimated_output_tokens":sum(limits)*900,"model":settings().openrouter_model,"replace_requested":args.replace},indent=2)); return
    db=SessionLocal()
    try:
            owner=db.query(User).filter_by(email=args.owner_email.lower()).first()
            if not owner: raise SystemExit("Seed owner does not exist")
            bank=db.query(QuestionBank).filter_by(slug=args.bank_slug).first(); previous_bank=None
            if bank and args.replace:
                # Extract into an invisible staging bank first.  A failed model
                # call therefore never wipes a teacher's currently usable bank.
                preflight_replace(db, bank); previous_bank=bank
                bank=QuestionBank(id=uid(),slug=f"{args.bank_slug}-staging-{uid()[:8]}",name=f"{args.bank_name} (staging)",exam_code=args.exam_code,is_global=False); db.add(bank); db.commit()
            if not bank:
                bank=QuestionBank(id=uid(),slug=args.bank_slug,name=args.bank_name,exam_code=args.exam_code,is_global=True); db.add(bank); db.commit()
            total=0; usage={"prompt_tokens":0,"completion_tokens":0}
            for path, limit in zip(args.pdfs, limits):
                document = fitz.open(path)
                try:
                    subject=args.subject or subject_from_filename(path.name); source=source_document(db,owner,bank,path)
                    job = seed_checkpoint(db, owner, bank, source); progress = checkpoint_meta(job)
                    completed_through = int(progress.get("extracted_through_page") or 0)
                    for core,visible in page_windows(limit,args.pages_per_request):
                        if core[-1] <= completed_through:
                            continue
                        print(f"Extracting {path.name}: core pages {core[0]}-{core[-1]}", flush=True)
                        items,window_usage=extract_window(document,subject,core,visible)
                        for key in usage: usage[key]+=int(window_usage.get(key,0) or 0)
                        for item in items:
                            if isinstance(item,dict) and store_item(db,bank,owner,source,subject,document,item,core,args.publish_threshold): total+=1
                        db.commit()
                        save_checkpoint(db, job, extracted_through_page=core[-1])
                    # The verified answer key/worked solution remains authoritative;
                    # the VLM's text is retained, but it cannot publish by itself.
                    if not checkpoint_meta(job).get("answers_mapped"):
                        print(f"Mapping answer key and solutions for {path.name}: {map_pdf(db, path, False, bank, subject=args.subject)}")
                        save_checkpoint(db, job, answers_mapped=True)
                finally:
                    document.close()
            result=publish_verified(db,bank,args.publish_threshold,args.publish_without_solution)
            if previous_bank:
                if not db.query(Question).filter_by(bank_id=bank.id).first():
                    raise RuntimeError("Staging extraction created no questions; keeping the current bank untouched")
                replace_bank(db,previous_bank)
                bank.slug=args.bank_slug; bank.name=args.bank_name; bank.exam_code=args.exam_code; bank.is_global=True; db.commit()
            print(json.dumps({"created_or_resumed":total,"usage":usage,"publication":result,"bank_id":bank.id},indent=2))
    finally: db.close()


if __name__ == "__main__": main()
