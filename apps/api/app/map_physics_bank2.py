"""Reconcile the already-extracted Physics Bank 2 questions with native PDFs.

The source books use a global printed question number in their worked-solution
headers (for example ``218 (a,c,d)``), while their compact answer-key tables
restart numbering by section.  The global number plus the source document is
therefore the only safe primary key.  This command deliberately never rewrites
question text, choices, taxonomy, or question diagrams produced by extraction.

It is safe to re-run: source/solution provenance and solution media use stable
identities, and a question is changed only when a native solution header maps
unambiguously to its own source document.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from sqlalchemy.orm import Session

from .db import SessionLocal
from .map_jee_solutions import answer_for, kind_from_source_answer
from .models import Question, QuestionBank, QuestionKind, QuestionScope, SourceDocument, User
from .storage import get, put


BANK_SLUG = "physics-bank-2"
ANSWER_TOKEN = re.compile(r"(?:=\s*)?\(\s*([a-e](?:\s*,\s*[a-e]){0,4}|[-+]?\d+(?:\.\d+)?)\s*\)", re.I)


def number(value: str | None) -> str | None:
    match = re.search(r"\d+", value or "")
    return match.group(0) if match else None


def answer_key_pages(document: fitz.Document) -> list[int]:
    return [index + 1 for index, page in enumerate(document) if "ANSWER KEY" in page.get_text("text", sort=True).upper()]


def solution_start(document: fitz.Document, key_pages: list[int]) -> int | None:
    start = max(key_pages, default=0)
    for index in range(start, len(document)):
        text = document[index].get_text("text", sort=True).upper()
        if "HINTS AND SOLUTIONS" in text or "SOLUTIONS" in text[:400]:
            return index
    return None


def solution_headers(document: fitz.Document) -> tuple[dict[str, list[dict]], list[int]]:
    keys = answer_key_pages(document)
    start = solution_start(document, keys)
    by_number: dict[str, list[dict]] = defaultdict(list)
    if start is None:
        return by_number, keys
    for page_index in range(start, len(document)):
        page = document[page_index]
        words = page.get_text("words", sort=True)
        # Worked solutions are typeset in two fixed columns.  A real solution
        # header begins at the column edge (about x=36 or x=300), whereas
        # equation fragments such as ``1(2)`` occur throughout the body.
        column_starts = (page.rect.width * 0.18, page.rect.width * 0.48, page.rect.width * 0.62)
        for index, word in enumerate(words):
            printed = word[4].strip().rstrip(".)")
            if not re.fullmatch(r"\d{1,4}", printed):
                continue
            x0, y0, x1, y1 = word[:4]
            at_left_edge = x0 <= column_starts[0]
            at_right_edge = column_starts[1] <= x0 <= column_starts[2]
            if not (at_left_edge or at_right_edge):
                continue
            following = []
            for other in words[index + 1:index + 6]:
                if abs(other[1] - y0) > 3 or other[0] - x1 > 105:
                    break
                following.append(other)
            answer_match = ANSWER_TOKEN.search("".join(item[4] for item in following))
            if not answer_match:
                continue
            raw = re.sub(r"\s+", "", answer_match.group(1)).upper()
            answer_word = following[-1] if following else word
            rect = fitz.Rect(x0, y0, max(x1, answer_word[2]), max(y1, answer_word[3]))
            by_number[printed].append({
                "printed_number": printed,
                "raw_answer": raw,
                "page": page_index + 1,
                "header": " ".join(item[4] for item in [word, *following]),
                "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
            })
    return by_number, keys


def choose_header(candidates: list[dict]) -> dict | None:
    answers = {candidate["raw_answer"] for candidate in candidates}
    if len(answers) != 1:
        return None
    # A located visual header is more useful because it lets us retain a bounded
    # worked-solution span and any figure inside it.
    return next((candidate for candidate in candidates if candidate["rect"]), candidates[0])


def solution_region(document: fitz.Document, header: dict, all_headers: dict[str, list[dict]]) -> tuple[str, fitz.Rect | None]:
    """Return text in the same visual column up to the next solution header."""
    if not header.get("rect"):
        return "", None
    page = document[header["page"] - 1]
    rect = fitz.Rect(header["rect"])
    middle = page.rect.width / 2
    left_column = rect.x0 < middle
    following = []
    for entries in all_headers.values():
        for item in entries:
            if item["page"] != header["page"] or not item.get("rect"):
                continue
            other = fitz.Rect(item["rect"])
            if (other.x0 < middle) == left_column and other.y0 > rect.y0 + 2:
                following.append(other)
    bottom = min((other.y0 - 3 for other in following), default=page.rect.y1 - 12)
    clip = fitz.Rect(0 if left_column else middle, max(0, rect.y0 - 2), middle if left_column else page.rect.x1, max(rect.y1 + 2, bottom))
    text = page.get_text("text", clip=clip, sort=True).strip()
    return text, clip if text else None


def solution_diagram(page: fitz.Page, clip: fitz.Rect | None) -> fitz.Rect | None:
    """Find a meaningful vector figure inside a mapped solution block.

    This intentionally ignores glyph-sized drawings and never returns the full
    solution block.  Question diagrams are already stored by the extractor;
    this covers distinct worked-solution sketches only.
    """
    if clip is None:
        return None
    rectangles = []
    for drawing in page.get_drawings():
        rect = drawing["rect"] & clip
        if rect.is_empty or rect.width < 18 or rect.height < 18 or rect.get_area() < 450:
            continue
        if rect.get_area() > clip.get_area() * 0.72:
            continue
        rectangles.append(rect)
    if not rectangles:
        return None
    union = fitz.Rect(rectangles[0])
    for rect in rectangles[1:]:
        gap_x = max(0, rect.x0 - union.x1, union.x0 - rect.x1)
        gap_y = max(0, rect.y0 - union.y1, union.y0 - rect.y1)
        if union.intersects(rect) or max(gap_x, gap_y) < 28:
            union |= rect
    padded = (union + (-5, -5, 5, 5)) & page.rect
    return padded if padded.width >= 24 and padded.height >= 24 else None


def corrected_kind(question: Question, raw: str) -> QuestionKind:
    kind = kind_from_source_answer(question.kind, raw)
    # The extraction model sometimes labelled a selectable paragraph question as
    # subjective.  Its native A--E answer and retained options prove otherwise.
    if kind == QuestionKind.SUBJECTIVE and len(question.options or []) >= 2 and re.fullmatch(r"[A-E]", raw):
        return QuestionKind.SINGLE
    return kind


def map_source(db: Session, bank: QuestionBank, source: SourceDocument, dry_run: bool, publish: bool) -> dict[str, int]:
    raw_pdf = get(source.object_key)
    document = fitz.open(stream=raw_pdf, filetype="pdf")
    try:
        headers, key_pages = solution_headers(document)
        questions = [
            question for question in db.query(Question).filter_by(bank_id=bank.id, scope=QuestionScope.GLOBAL)
            if (question.interaction or {}).get("source_document_id") == source.id
        ]
        stats = {"questions": len(questions), "answer_solution_mapped": 0, "published": 0, "ambiguous_solution_headers": 0, "unmapped": 0, "solution_diagrams": 0}
        now = datetime.now(timezone.utc)
        for question in questions:
            printed = number(question.source_number)
            selected = choose_header(headers.get(printed or "", [])) if printed else None
            if not selected:
                if headers.get(printed or ""):
                    stats["ambiguous_solution_headers"] += 1
                else:
                    stats["unmapped"] += 1
                continue
            kind = corrected_kind(question, selected["raw_answer"])
            answer = answer_for(kind, selected["raw_answer"])
            solution, clip = solution_region(document, selected, headers)
            if not solution:
                stats["unmapped"] += 1
                continue
            stats["answer_solution_mapped"] += 1
            if dry_run:
                continue
            interaction = dict(question.interaction or {})
            question.kind = kind
            question.answer = answer
            question.solution_markdown = solution
            interaction["native_reconciliation"] = {
                "version": "physics-bank2-v1",
                "identity": {"source_document_id": source.id, "printed_question_number": printed},
                "answer_key_pages": key_pages,
                "solution_header": {"page": selected["page"], "printed_answer": selected["raw_answer"]},
            }
            if answer or kind == QuestionKind.SUBJECTIVE:
                question.approved_at = now if publish else question.approved_at
                if publish:
                    stats["published"] += 1
            if clip:
                diagram_rect = solution_diagram(document[selected["page"] - 1], clip)
                if diagram_rect:
                    key = f"seed-diagrams/{bank.id}/physics/solutions/{source.id}-{question.id}-{selected['page']}.png"
                    image = document[selected["page"] - 1].get_pixmap(clip=diagram_rect, matrix=fitz.Matrix(1.8, 1.8), alpha=False).tobytes("png")
                    put(key, image, "image/png")
                    diagrams = [item for item in (question.diagrams or []) if item.get("object_key") != key]
                    diagrams.append({"object_key": key, "page": selected["page"], "bbox": [round(value, 2) for value in diagram_rect], "alt": "Worked-solution diagram", "role": "solution"})
                    question.diagrams = diagrams
                    stats["solution_diagrams"] += 1
            interaction["seed_status"] = "published_native_answer_and_solution" if publish and (answer or kind == QuestionKind.SUBJECTIVE) else "native_answer_and_solution_mapped"
            question.interaction = interaction
        if not dry_run:
            db.commit()
        return stats
    finally:
        document.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Map Physics Bank 2 native answers and worked solutions")
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--bank-slug", default=BANK_SLUG)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--source", action="append", help="Optional exact source filename; may be repeated")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(email=args.owner_email.lower()).first():
            raise SystemExit("Mapping owner does not exist")
        bank = db.query(QuestionBank).filter_by(slug=args.bank_slug, is_global=True).one_or_none()
        if not bank:
            raise SystemExit(f"Global bank {args.bank_slug!r} does not exist")
        sources = db.query(SourceDocument).filter(SourceDocument.object_key.like(f"seed-sources/{bank.id}/%")).order_by(SourceDocument.filename).all()
        if args.source:
            wanted = set(args.source)
            sources = [source for source in sources if source.filename in wanted]
        totals: dict[str, int] = defaultdict(int)
        for source in sources:
            result = map_source(db, bank, source, args.dry_run, args.publish)
            print(f"{source.filename}: {result}", flush=True)
            for key, value in result.items():
                totals[key] += value
        print(f"TOTAL: {dict(totals)}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
