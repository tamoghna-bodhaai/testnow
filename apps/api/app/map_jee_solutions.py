"""Map native JEE seed questions to the answer key and worked solutions in the same PDF.

The worksheet has three useful identifiers that survive text extraction: subject,
chapter heading, and the printed question number.  This mapper deliberately uses
all three; it never applies a bare ``question 12`` answer across chapters.

It is intentionally conservative.  A question is published only when a concrete
answer and a source solution span were found in its own worksheet.  Matrix-match
answers are retained as source references until the matrix response editor is
available; this avoids publishing an answer shape that the test client cannot
submit yet.

Run after ``seed_jee``:
  python -m app.map_jee_solutions --owner-email utils@bodhaai.tech --publish /imports/*.pdf
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import fitz
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Question, QuestionBank, QuestionKind, QuestionTaxonomy, TaxonomyNode, User
from .seed_jee import BANK_SLUG, subject_from_filename


# A compact, deliberate alias list deals with wording differences between the
# worksheet header and the table-of-contents / solution header.
CHAPTER_ALIASES = {
    "kinematics motion in 1 2 dimensions": "Kinematics",
    "kinematics of a particle": "Kinematics",
    "rotation gravitation": "Rotational Motion and Gravitation",
    "energy and momentum": "Energy & Momentum",
    "dc circuit and capacitors": "DC Circuits and Capacitors",
    "emi ac circuit": "EMI and AC Circuits",
    "magnetic effect of current": "Magnetic Effects of Current",
    "ray optics wave optics": "Ray Optics and Wave Optics",
    "dc circuits capacitors": "DC Circuits and Capacitors",
    "dc circuit capacitor": "DC Circuits and Capacitors",
    "dc circuit and capacitor": "DC Circuits and Capacitors",
    "emi ac circuits": "EMI and AC Circuits",
    "emi and ac circuit": "EMI and AC Circuits",
    "rotational motion gravitation": "Rotational Motion and Gravitation",
    "rotation and gravitation": "Rotational Motion and Gravitation",
    "magnetic effect of current": "Magnetic Effect of Current",
    "permutation and combination": "Permutations & Combinations",
    "permutations combinations": "Permutations & Combinations",
    "quadratic equation": "Quadratic Equations",
    "sequence series": "Sequence and Series",
    "complex number": "Complex Numbers",
    "conic sections": "Conic Section",
    "straight lines": "Straight Line",
    "differential equation": "Differential Equations",
    "integral calculus 1": "Integral Calculus-1",
    "integral calculus 2": "Integral Calculus-2",
    "differential calculus 1": "Differential Calculus-1",
    "differential calculus 2": "Differential Calculus-2",
    "vectors": "Vector",
    "matrices and determinants": "M atrices & Determinants",
    "thermodynamics thermochemistry": "Thermochemistry & Thermodynamics",
    "oxygen containing organic compounds i": "Oxygen Containing Organic Compound-I",
    "oxygen containing organic compounds ii": "Oxygen Containing Organic Compound-II",
    "halogen containing organic compounds organic concepts": "Organic Halides & Organic Concepts",
    "stoichiometry i ii": "Stoichiometry-I & II",
    "colligative properties solutions": "Colligative Properties of Solutions",
    "oxygen containing organic compound iii": "Oxygen Containing Organic Compounds-III",
    "nitrogen containing organic compound": "Nitrogen Containing Organic Compounds",
    "hydrogen s block elements": "Hydrogen & s-Block Elements",
    "surface chemistry biomolecules practical organic chemistry polymers": "Surf., Bio., Practical Org. Chem. & Polymers",
    "p block elements i": "p-Block Elements-I",
    "p block elements ii": "p-Block Elements - II",
}

ANSWER_HEADER = re.compile(
    r"(?<![\w.])(\d{1,3})\.\s*\(\s*([A-D]{1,4}|[-+]?\d+(?:\.\d+)?)\s*\)",
    re.I,
)


def compact(value: str) -> str:
    value = value.replace("&", " and ").replace("-", " ")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def title_for_page(text: str, names: list[str]) -> str | None:
    """Return the chapter whose heading occurs in the page's title region."""
    top = " ".join(text.splitlines()[:12])
    normalized = compact(top)
    for alias, canonical in CHAPTER_ALIASES.items():
        if alias in normalized:
            return canonical
    best_name: str | None = None
    best_score = 0.0
    for name in names:
        candidate = compact(name)
        if candidate and candidate in normalized:
            return name
        score = SequenceMatcher(None, candidate, normalized[: max(200, len(candidate) * 2)]).ratio()
        if score > best_score:
            best_name, best_score = name, score
    return best_name if best_score >= 0.52 else None


def subject_chapters(db: Session, bank_id: str, subject: str) -> tuple[dict[str, str], dict[str, str]]:
    subject_node = db.query(TaxonomyNode).filter_by(bank_id=bank_id, parent_id=None, kind="subject", name=subject).one()
    nodes = db.query(TaxonomyNode).filter_by(bank_id=bank_id, parent_id=subject_node.id, kind="chapter").all()
    by_name = {node.name: node.id for node in nodes}
    # Imported Physics page one predates its printed chapter header.  Rename the
    # placeholder once, rather than leave a permanent meaningless taxonomy node.
    unknown = next((node for node in nodes if node.name == "Unmapped chapter"), None)
    if subject == "Physics" and unknown:
        unknown.name = "Kinematics"
        db.flush()
        by_name.pop("Unmapped chapter", None)
        by_name[unknown.name] = unknown.id
    return by_name, {compact(name): name for name in by_name}


def resolve_chapter(raw: str | None, by_name: dict[str, str], normalized: dict[str, str]) -> str | None:
    if not raw:
        return None
    if raw in by_name:
        return raw
    key = compact(raw)
    if key in CHAPTER_ALIASES:
        target = CHAPTER_ALIASES[key]
        if target in by_name:
            return target
    if key in normalized:
        return normalized[key]
    winner, score = None, 0.0
    for name in by_name:
        candidate = compact(name)
        value = SequenceMatcher(None, key, candidate).ratio()
        if value > score:
            winner, score = name, value
    return winner if score >= 0.62 else None


def answer_for(kind: QuestionKind, raw: str) -> dict | None:
    raw = raw.upper().replace(" ", "")
    if kind == QuestionKind.SINGLE and re.fullmatch(r"[A-D]", raw):
        return {"option": raw.lower()}
    if kind == QuestionKind.MULTIPLE and re.fullmatch(r"[A-D]{2,4}", raw):
        return {"options": [letter.lower() for letter in raw]}
    if kind in {QuestionKind.NUMERICAL, QuestionKind.INTEGER}:
        try:
            value = float(raw)
        except ValueError:
            return None
        # Keep numeric JSON values for the scorer while preserving the printed
        # representation in the provenance payload below.
        return {"value": int(value) if value.is_integer() else value}
    return None


def solution_start(document: fitz.Document) -> int:
    """Locate the page after the answer-key tables where detailed solutions begin."""
    answer_key_page = next(
        (index for index, page in enumerate(document) if "ANSWER KEY" in page.get_text("text", sort=True).upper()),
        None,
    )
    if answer_key_page is None:
        raise ValueError("Could not locate the answer-key section")
    for index in range(answer_key_page + 1, len(document)):
        text = document[index].get_text("text", sort=True).upper()
        top = text[:300]
        if "ANSWER KEY" in top or not re.search(r"\bSOLUTIONS?\b", top):
            continue
        if "SOLUTIONS" in text and "CONTENT" in text:
            return min(index + 1, len(document) - 1)
        return index
    raise ValueError("Could not locate the detailed-solutions section")


def extract_entries(document: fitz.Document, chapter_names: list[str]) -> list[dict]:
    """Extract answer-bearing solution blocks with their chapter and page span."""
    start = solution_start(document)
    pages = [document[index].get_text("text", sort=True) for index in range(start, len(document))]
    # Chemistry begins its detailed solutions immediately after the final
    # answer-key chapter without repeating that chapter title.  Carry the last
    # answer-key heading into the first solution page in that one case.
    initial: str | None = None
    for index in range(start - 1, -1, -1):
        found = title_for_page(document[index].get_text("text", sort=True), chapter_names)
        if found:
            initial = found
            break
    page_chapters: list[str | None] = []
    current = initial
    for text in pages:
        # Ignore the contents page even if a title happens to resemble a chapter.
        if "CONTENT" not in text[:250].upper():
            found = title_for_page(text, chapter_names)
            if found:
                current = found
        page_chapters.append(current)

    entries: list[dict] = []
    for local_page, text in enumerate(pages):
        chapter = page_chapters[local_page]
        if not chapter:
            continue
        matches = list(ANSWER_HEADER.finditer(text))
        for position, match in enumerate(matches):
            end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
            # Joining continuation pages is intentionally avoided here: a page
            # reference plus the native PDF is a lossless solution source, while
            # cross-page text often contains unrelated multi-column fragments.
            body = re.sub(r"[ \t]+", " ", text[match.start():end]).strip()
            entries.append({
                "chapter": chapter,
                "number": match.group(1),
                "raw_answer": match.group(2),
                "solution": body,
                "page": start + local_page + 1,
            })
    return entries


def map_pdf(db: Session, path: Path, publish: bool) -> dict[str, int]:
    subject = subject_from_filename(path.name)
    bank = db.query(QuestionBank).filter_by(slug=BANK_SLUG).one()
    by_name, normalized = subject_chapters(db, bank.id, subject)
    document = fitz.open(path)
    entries = extract_entries(document, list(by_name))
    keyed: dict[tuple[str, str], dict] = {}
    for entry in entries:
        chapter = resolve_chapter(entry["chapter"], by_name, normalized)
        if chapter:
            # Keep first occurrence: headers on continuation pages occasionally
            # repeat a question number in a derivation.
            keyed.setdefault((chapter, entry["number"]), entry)

    chapter_by_question = {
        question_id: chapter_name
        for question_id, chapter_name in db.query(QuestionTaxonomy.question_id, TaxonomyNode.name)
        .join(TaxonomyNode, TaxonomyNode.id == QuestionTaxonomy.taxonomy_node_id)
        .filter(TaxonomyNode.bank_id == bank.id, TaxonomyNode.kind == "chapter")
    }
    questions = db.query(Question).filter_by(bank_id=bank.id).filter(Question.scope == "global").all()
    stats = {"questions": 0, "mapped": 0, "published": 0, "matrix_source_only": 0, "unmapped": 0}
    now = datetime.now(timezone.utc)
    for question in questions:
        if subject_from_filename(str((question.interaction or {}).get("source_filename", path.name))) != subject:
            # Older seed records omit a filename.  Their source document id is
            # still unique per input PDF, so fall through to its taxonomy only
            # when this mapper has a matching subject chapter.
            if chapter_by_question.get(question.id) not in by_name:
                continue
        chapter = chapter_by_question.get(question.id)
        entry = keyed.get((chapter or "", question.source_number or ""))
        if not entry:
            continue
        stats["questions"] += 1
        interaction = dict(question.interaction or {})
        interaction.update({
            "seed_status": "solution_mapped",
            "answer_source": {"page": entry["page"], "printed_answer": entry["raw_answer"]},
            "solution_source_spans": [{"page": entry["page"]}],
        })
        question.interaction = interaction
        question.solution_markdown = entry["solution"]
        answer = answer_for(question.kind, entry["raw_answer"])
        if answer:
            question.answer = answer
            stats["mapped"] += 1
            if publish:
                question.approved_at = now
                interaction["seed_status"] = "published_from_native_solution"
                stats["published"] += 1
        elif question.kind == QuestionKind.MATRIX:
            # The source answer and detailed explanation remain connected, but
            # do not make a matrix question selectable until the response client
            # can submit the same mapping shape.
            interaction["seed_status"] = "matrix_solution_linked"
            stats["matrix_source_only"] += 1
        else:
            stats["unmapped"] += 1
    db.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Map JEE native seed records to their worksheet solutions")
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--owner-email", required=True, help="Checked to prevent accidental use before seed setup")
    parser.add_argument("--publish", action="store_true", help="Publish only questions with a parsed concrete answer")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(email=args.owner_email.lower()).first():
            raise SystemExit("Seed owner does not exist")
        totals = {"questions": 0, "mapped": 0, "published": 0, "matrix_source_only": 0, "unmapped": 0}
        for path in args.pdfs:
            result = map_pdf(db, path, args.publish)
            print(f"{path.name}: {result}")
            for key, value in result.items():
                totals[key] += value
        print(f"TOTAL: {totals}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
