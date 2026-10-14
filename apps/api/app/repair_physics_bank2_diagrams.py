"""Rebuild Physics Bank 2 question-figure crops from the native PDFs.

The initial import sent 1.45x rendered pages to the extraction model but asked
for PDF-point coordinates.  Some returned coordinates consequently described a
different area of the PDF.  This command never uses those model coordinates:
it finds the printed question on its recorded source page, bounds that question
by the next printed question, and crops connected vector figures from that
native region.  Worked-solution figures (``role == 'solution'``) are left
untouched.
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict

import fitz

from .db import SessionLocal
from .models import Question, QuestionBank, QuestionScope, SourceDocument, User
from .storage import get, put


BANK_SLUG = "physics-bank-2"
QUESTION_NUMBER = re.compile(r"(\d{1,4})[.)]$")
FIGURE_HINT = re.compile(r"\b(?:figure|fig\.?|diagram|graph|circuit|shown|as shown|following)\b", re.I)


def question_number(value: str | None) -> str | None:
    match = re.search(r"\d+", value or "")
    return match.group(0) if match else None


def question_anchors(page: fitz.Page) -> list[tuple[str, float]]:
    """Find printed question headings, not option labels or formula numbers."""
    anchors: list[tuple[str, float]] = []
    for x0, y0, _x1, _y1, text, *_rest in page.get_text("words", sort=True):
        match = QUESTION_NUMBER.fullmatch(text.strip())
        # The supplied books use a 36pt left question margin.  Restricting to
        # the outer margin prevents an option such as ``1)`` from becoming a
        # false question boundary.
        if match and x0 <= page.rect.width * 0.14:
            anchors.append((match.group(1), y0))
    return anchors


def question_region(page: fitz.Page, number: str) -> fitz.Rect | None:
    anchors = question_anchors(page)
    positions = [y for printed, y in anchors if printed == number]
    if len(positions) != 1:
        return None
    top = positions[0] - 5
    following = [y for _printed, y in anchors if y > positions[0] + 2]
    bottom = min(following, default=page.rect.y1 - 18) - 4
    if bottom - top < 18:
        return None
    return fitz.Rect(24, max(0, top), page.rect.x1 - 24, min(page.rect.y1, bottom))


def rect_gap(first: fitz.Rect, second: fitz.Rect) -> tuple[float, float]:
    return (
        max(0.0, first.x0 - second.x1, second.x0 - first.x1),
        max(0.0, first.y0 - second.y1, second.y0 - first.y1),
    )


def figure_rects(page: fitz.Page, region: fitz.Rect) -> list[fitz.Rect]:
    """Return visual clusters large enough to be real figures or tables.

    Ordinary PDF text is not returned by ``get_drawings``.  Maths can add very
    small rule marks, so cluster only connected vector objects and require a
    meaningful final visual footprint.  The page frame is excluded before
    clustering.
    """
    raw: list[fitz.Rect] = []
    for drawing in page.get_drawings():
        rect = drawing["rect"] & region
        if rect.is_empty or (rect.width >= page.rect.width * 0.75) or (rect.height >= page.rect.height * 0.75):
            continue
        if rect.width < 1 and rect.height < 1:
            continue
        raw.append(rect)
    # Include embedded raster figures if a source uses them.
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            rect &= region
            if not rect.is_empty:
                raw.append(rect)

    clusters: list[list[fitz.Rect]] = []
    for rect in raw:
        for cluster in clusters:
            union = fitz.Rect(cluster[0])
            for member in cluster[1:]:
                union |= member
            gap_x, gap_y = rect_gap(union, rect)
            if gap_x <= 14 and gap_y <= 14:
                cluster.append(rect)
                break
        else:
            clusters.append([rect])

    result: list[fitz.Rect] = []
    for cluster in clusters:
        union = fitz.Rect(cluster[0])
        for rect in cluster[1:]:
            union |= rect
        # A diagram can be a thin ray sketch, but not a single fraction bar.
        if union.width < 20 or union.height < 18 or union.get_area() < 420:
            continue
        # The drawing bounds already include vector labels and axes.  Only a
        # very small bleed is desirable here: larger padding pulls the final
        # sentence of the stem or the first answer choice into the image.
        padded = (union + (-3, -3, 3, 3)) & region & page.rect
        if padded.width >= 30 and padded.height >= 28:
            result.append(padded)
    return sorted(result, key=lambda rect: (rect.y0, rect.x0))


def has_question_figure(question: Question) -> bool:
    return any(item.get("role") != "solution" for item in (question.diagrams or []))


def repair_source(db, bank: QuestionBank, source: SourceDocument, dry_run: bool) -> dict[str, int]:
    document = fitz.open(stream=get(source.object_key), filetype="pdf")
    try:
        questions = [
            question for question in db.query(Question).filter_by(bank_id=bank.id, scope=QuestionScope.GLOBAL)
            if (question.interaction or {}).get("source_document_id") == source.id
        ]
        stats = defaultdict(int)
        for question in questions:
            stats["questions"] += 1
            page_no = int((question.interaction or {}).get("source_page") or 0)
            printed = question_number(question.source_number)
            if not printed or not 1 <= page_no <= len(document):
                continue
            page = document[page_no - 1]
            region = question_region(page, printed)
            existing = has_question_figure(question)
            hinted = bool(FIGURE_HINT.search(question.stem_markdown or ""))
            if not region:
                stats["unlocated_questions"] += 1
                continue
            crops = figure_rects(page, region) if (existing or hinted) else []
            # Keep solution figures and replace every old question-figure entry.
            diagrams = [item for item in (question.diagrams or []) if item.get("role") == "solution"]
            for index, rect in enumerate(crops):
                key = f"seed-diagrams/{bank.id}/physics/figures/{source.id}-{question.id}-{page_no}-{index}.png"
                if not dry_run:
                    png = page.get_pixmap(clip=rect, matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
                    put(key, png, "image/png")
                diagrams.append({
                    "object_key": key,
                    "page": page_no,
                    "bbox": [round(value, 2) for value in rect],
                    "alt": "Question figure or graph",
                    "role": "question",
                    "extraction": "native_vector_region_v2",
                })
            if existing or crops:
                stats["question_diagrams_rebuilt"] += len(crops)
                if existing and not crops:
                    stats["bad_crops_removed"] += 1
                if not dry_run:
                    question.diagrams = diagrams
        if not dry_run:
            db.commit()
        return dict(stats)
    finally:
        document.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair Physics Bank 2 question diagrams from native PDF vector regions")
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--bank-slug", default=BANK_SLUG)
    parser.add_argument("--source", action="append", help="Optional exact source filename; may be repeated")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(email=args.owner_email.lower()).first():
            raise SystemExit("Repair owner does not exist")
        bank = db.query(QuestionBank).filter_by(slug=args.bank_slug, is_global=True).one_or_none()
        if not bank:
            raise SystemExit(f"Global bank {args.bank_slug!r} does not exist")
        sources = db.query(SourceDocument).filter(SourceDocument.object_key.like(f"seed-sources/{bank.id}/%")).order_by(SourceDocument.filename).all()
        if args.source:
            selected = set(args.source)
            sources = [source for source in sources if source.filename in selected]
        total: dict[str, int] = defaultdict(int)
        for source in sources:
            result = repair_source(db, bank, source, args.dry_run)
            print(f"{source.filename}: {result}", flush=True)
            for key, value in result.items():
                total[key] += value
        print(f"TOTAL: {dict(total)}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
