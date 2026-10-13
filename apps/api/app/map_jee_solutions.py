"""Map native JEE seed questions to the answer key and worked solutions in the same PDF.

The worksheet has three useful identifiers that survive text extraction: subject,
chapter heading, and the printed question number.  This mapper deliberately uses
all three; it never applies a bare ``question 12`` answer across chapters.

It is intentionally conservative.  A question is published only when a concrete
answer and a source solution span were found in its own worksheet.  Matrix-match
matrix answers are stored as a row-to-column map in the persistent question
record. The answer-key shape is also used to correct worksheet type labels
when a prior page's section heading was not carried across a PDF page break.

Run after ``seed_jee``:
  python -m app.map_jee_solutions --owner-email utils@bodhaai.tech --publish /imports/*.pdf
"""
from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import fitz
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Question, QuestionBank, QuestionKind, QuestionTaxonomy, SourceDocument, TaxonomyNode, User
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
    "matrices and determinants": "Matrices & Determinants",
    "thermodynamics thermochemistry": "Thermochemistry & Thermodynamics",
    "oxygen containing organic compounds i": "Oxygen Containing Organic Compound-I",
    "oxygen containing organic compounds ii": "Oxygen Containing Organic Compound-II",
    "oxygen containing organic compounds iii": "Oxygen Containing Organic Compounds-III",
    "halogen containing organic compounds and organic concepts": "Organic Halides & Organic Concepts",
    "magnetic effects of current": "Magnetic Effect of Current",
    "halogen containing organic compounds organic concepts": "Organic Halides & Organic Concepts",
    "stoichiometry i ii": "Stoichiometry-I & II",
    "colligative properties solutions": "Colligative Properties of Solutions",
    "colligative properties of solution": "Colligative Properties of Solutions",
    "oxygen containing organic compound iii": "Oxygen Containing Organic Compounds-III",
    "nitrogen containing organic compound": "Nitrogen Containing Organic Compounds",
    "hydrogen s block elements": "Hydrogen & s-Block Elements",
    "surface chemistry biomolecules practical organic chemistry polymers": "Surf., Bio., Practical Org. Chem. & Polymers",
    "p block elements i": "p-Block Elements-I",
    "p block elements ii": "p-Block Elements - II",
    "surface chem biomolecules practical organic chem polymers": "Surf., Bio., Practical Org. Chem. & Polymers",
    "surface chem biomolecules practical organic chemistry polymers": "Surf., Bio., Practical Org. Chem. & Polymers",
}

SOLUTION_HEADER = re.compile(
    r"(?<![\w.])(\d{1,3})\.\s*(?:\(\s*([A-D]{1,4}|[-+]?\d+(?:\.\d+)?)\s*\)|"
    r"(\[\s*[A-D]\s*[-–→\uf0ae][^\]]{1,180}\](?:\s*\[\s*[A-D]\s*[-–→\uf0ae][^\]]{1,180}\]){0,3})|"
    r"([A-D]\s*[-–→\uf0ae]\s*[a-z][^\n]{0,180}))",
    re.I,
)


def compact(value: str) -> str:
    value = value.replace("&", " and ").replace("-", " ")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def canonical_chapter(subject: str, value: str) -> str:
    """Return the single display name used for an extracted chapter heading.

    LLM/native extraction occasionally introduces a space inside the first word
    (for example ``M atrices``).  Taxonomy paths are exact-string unique, so
    normalise before creating nodes rather than allowing that to become a second
    dropdown option.
    """
    cleaned = re.sub(r"\s+", " ", value or "").strip(" :|-")
    cleaned = re.sub(r"\b([A-Za-z])\s+(?=[A-Za-z]{2,}\b)", r"\1", cleaned)
    if subject != "Mathematics" or not cleaned:
        return cleaned
    key = compact(cleaned)
    if key in CHAPTER_ALIASES:
        return CHAPTER_ALIASES[key]
    # Accept canonical names too, including their punctuation variants.
    return next((name for name in dict.fromkeys(CHAPTER_ALIASES.values()) if compact(name) == key), cleaned)


def title_for_page(text: str, names: list[str]) -> str | None:
    """Resolve a standalone chapter-heading line/page without matching body text."""
    lines = text.splitlines()[:16]
    candidates = [chapter for line in lines if (chapter := chapter_heading(line, names))]
    return next((chapter for chapter in candidates if compact(chapter) not in {"physics", "chemistry", "mathematics"}), None)


def chapter_heading(line: str, names: list[str]) -> str | None:
    line = " ".join(line.split()).strip(" :|-")
    if not line:
        return None
    letters = [char for char in line if char.isalpha()]
    key = compact(line)
    padded_key = f" {key} "
    uppercase_ratio = sum(char.isupper() for char in letters) / len(letters) if letters else 0
    # Solution headers often share their visual line with ``1.(A)``. Exact
    # chapter-name inclusion is unambiguous even when a question number follows.
    for name in sorted(names, key=len, reverse=True):
        candidate = compact(name)
        padded_candidate = f" {candidate} "
        prefix_header = key.startswith("solutions advanced problem package ") and candidate in key
        if (
            len(candidate) >= 6
            and (padded_candidate in padded_key or prefix_header or (candidate in key and uppercase_ratio >= 0.78))
            and len(key.split()) <= len(candidate.split()) + 5
        ):
            return name
    for alias, canonical in sorted(CHAPTER_ALIASES.items(), key=lambda item: len(compact(item[0])), reverse=True):
        alias_key = compact(alias)
        padded_alias = f" {alias_key} "
        prefix_header = key.startswith("solutions advanced problem package ") and alias_key in key
        if (
            key == alias_key or padded_alias in padded_key or prefix_header
            or (len(alias_key) > 12 and alias_key in key and uppercase_ratio >= 0.78 and len(key.split()) <= len(alias_key.split()) + 5)
        ):
            exact = next((name for name in names if compact(name) == compact(canonical)), None)
            if exact:
                return exact
    if not letters or re.search(r"\d", line) or sum(char.isupper() for char in letters) / len(letters) < 0.72:
        return None
    winner, score = None, 0.0
    for name in names:
        candidate = compact(name)
        ratio = SequenceMatcher(None, key, candidate).ratio()
        if candidate in key:
            ratio = max(ratio, len(candidate) / max(len(key), 1))
        if ratio > score:
            winner, score = name, ratio
    return winner if score >= 0.84 else None


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
    key = compact(raw)
    if key in CHAPTER_ALIASES:
        target = CHAPTER_ALIASES[key]
        if target in by_name:
            return target
    if raw in by_name:
        return raw
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
    if kind in {QuestionKind.SINGLE, QuestionKind.ASSERTION} and re.fullmatch(r"[A-D]", raw):
        return {"option": ord(raw) - ord("A")}
    if kind == QuestionKind.MULTIPLE and re.fullmatch(r"[A-D]{1,4}", raw):
        return {"options": sorted({ord(letter) - ord("A") for letter in raw})}
    if kind == QuestionKind.MATRIX:
        pairs = re.findall(r"([A-Z])\s*[-–→]\s*([A-Z](?:\s*,\s*[A-Z])*)", raw, re.I)
        if pairs:
            return {"matches": {left.upper(): sorted({item.strip().upper() for item in right.split(",")}) for left, right in pairs}}
    if kind in {QuestionKind.NUMERICAL, QuestionKind.INTEGER}:
        try:
            value = float(raw)
        except ValueError:
            return None
        # Keep numeric JSON values for the scorer while preserving the printed
        # representation in the provenance payload below.
        return {"value": int(value) if value.is_integer() else value}
    return None


def kind_from_source_answer(current: QuestionKind, raw: str) -> QuestionKind:
    """Use the printed answer-key shape to correct a lost section type label."""
    compact_raw = re.sub(r"\s+", "", raw).upper()
    if re.search(r"\[[^]]*[A-D]\s*[-–→]", raw, re.I):
        return QuestionKind.MATRIX
    if re.fullmatch(r"[A-D]{2,4}", compact_raw):
        return QuestionKind.MULTIPLE
    if re.fullmatch(r"[A-D]", compact_raw):
        return current if current == QuestionKind.ASSERTION else QuestionKind.SINGLE
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", compact_raw):
        return current if current in {QuestionKind.INTEGER, QuestionKind.NUMERICAL} else QuestionKind.NUMERICAL
    return current


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


def line_records(page: fitz.Page) -> list[dict]:
    raw_rows = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        for line in block.get("lines", []):
            spans = [span for span in line["spans"] if span["text"].strip()]
            if not spans:
                continue
            raw_rows.append({
                "y0": min(span["bbox"][1] for span in spans),
                "y1": max(span["bbox"][3] for span in spans),
                "text": " ".join(span["text"].strip() for span in spans).strip(),
                "spans": spans,
            })
    rows: list[dict] = []
    for item in sorted(raw_rows, key=lambda row: (round(row["y0"], 1), row["spans"][0]["bbox"][0])):
        same_line = next((row for row in reversed(rows) if abs(row["y0"] - item["y0"]) <= 1.5), None)
        if same_line is None:
            rows.append(item)
        else:
            same_line["y0"] = min(same_line["y0"], item["y0"])
            same_line["y1"] = max(same_line["y1"], item["y1"])
            same_line["spans"].extend(item["spans"])
            same_line["spans"].sort(key=lambda span: span["bbox"][0])
            same_line["text"] = " ".join(span["text"].strip() for span in same_line["spans"]).strip()
    return sorted(rows, key=lambda row: (round(row["y0"], 1), row["spans"][0]["bbox"][0]))


def parse_answer_cell(words: list[tuple], kind: QuestionKind | None) -> str | None:
    value = " ".join(word[4] for word in words).strip()
    if not value:
        return None
    compact_value = re.sub(r"\s+", "", value).upper()
    if re.fullmatch(r"[A-D]{1,4}", compact_value):
        return compact_value
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", compact_value):
        return compact_value
    # Matrix-key cells have a layout such as [A-p, q, s] [B-r]...; retain the
    # printed mapping intact for the matrix answer converter below.
    if re.search(r"[A-D]\s*[-–→]\s*[A-Z]", value, re.I):
        return value
    return None


def extract_answer_key(document: fitz.Document, chapter_names: list[str]) -> dict[tuple[str, str], dict]:
    """Read key tables by aligning question-number columns to the cells below."""
    key_page = next(i for i, page in enumerate(document) if "ANSWER KEY" in page.get_text("text", sort=True).upper())
    end_page = solution_start(document)
    result: dict[tuple[str, str], dict] = {}
    active: str | None = None
    expected_number = 1
    for page_index in range(key_page, end_page):
        page = document[page_index]
        lines = line_records(page)
        number_rows: list[dict] = []
        for index, row in enumerate(lines):
            heading = chapter_heading(row["text"], chapter_names)
            if heading:
                active = heading
                expected_number = 1
                continue
            token_spans = []
            for span in row["spans"]:
                for token in re.findall(r"\d+", span["text"]):
                    x0, y0, x1, y1 = span["bbox"]
                    token_spans.append({"number": token, "x": (x0 + x1) / 2, "x0": x0, "x1": x1})
            nums = [int(token["number"]) for token in token_spans]
            if (
                active and len(nums) >= 2 and nums[0] == expected_number
                and all(1 <= n <= 150 for n in nums)
                and all(b == a + 1 for a, b in zip(nums, nums[1:]))
            ):
                number_rows.append({"index": index, "chapter": active, "cells": token_spans, "y0": row["y0"], "y1": row["y1"]})
                expected_number = nums[-1] + 1
        for row_pos, number_row in enumerate(number_rows):
            next_row = number_rows[row_pos + 1] if row_pos + 1 < len(number_rows) else None
            if next_row and (next_row["chapter"] != number_row["chapter"] or next_row["y0"] <= number_row["y0"]):
                next_row = None
            bottom = next_row["y0"] - 1 if next_row else number_row["y1"] + 28
            page_words = page.get_text("words", sort=True)
            for cell_index, cell in enumerate(number_row["cells"]):
                left = (number_row["cells"][cell_index - 1]["x"] + cell["x"]) / 2 if cell_index else -1
                right = (cell["x"] + number_row["cells"][cell_index + 1]["x"]) / 2 if cell_index + 1 < len(number_row["cells"]) else page.rect.width + 1
                words = [w for w in page_words if left <= (w[0] + w[2]) / 2 < right and w[1] >= number_row["y1"] + 1 and w[3] < bottom]
                raw = parse_answer_cell(words, None)
                if raw:
                    result.setdefault((number_row["chapter"], cell["number"]), {
                        "raw_answer": raw,
                        "page": page_index + 1,
                        "kind": "matrix" if re.search(r"\[[^]]*[-–→]", raw) else "value",
                    })
    return result


def extract_entries(document: fitz.Document, chapter_names: list[str]) -> list[dict]:
    """Extract answer-bearing solution blocks with their chapter and page span."""
    start = solution_start(document)
    pages = [document[index].get_text("text", sort=True) for index in range(start, len(document))]
    # Chemistry begins its detailed solutions immediately after the final
    # answer-key chapter without repeating that chapter title.  Carry the last
    # answer-key heading into the first solution page in that one case.
    initial: str | None = None
    answer_key_page = next(i for i, page in enumerate(document) if "ANSWER KEY" in page.get_text("text", sort=True).upper())
    for index in range(answer_key_page, start):
        for row in line_records(document[index]):
            initial = chapter_heading(row["text"], chapter_names)
            if initial:
                break
        if initial:
            break
    key_chapters = list(dict.fromkeys(chapter for chapter, _ in extract_answer_key(document, chapter_names)))
    order_index = {chapter: index for index, chapter in enumerate(key_chapters)}
    page_chapters: list[str | None] = []
    current = initial or (key_chapters[0] if key_chapters else None)
    for text in pages:
        # Ignore the contents page even if a title happens to resemble a chapter.
        if "CONTENT" not in text[:250].upper():
            found = title_for_page(text, chapter_names)
            if found and found in order_index and (
                current not in order_index or order_index[found] >= order_index[current]
            ):
                current = found
        page_chapters.append(current)

    headers: list[dict] = []
    for local_page, text in enumerate(pages):
        chapter = page_chapters[local_page]
        if not chapter:
            continue
        for match in SOLUTION_HEADER.finditer(text):
            headers.append({
                "chapter": chapter, "number": match.group(1),
                "raw_answer": next(group for group in match.groups()[1:] if group),
                "local_page": local_page, "start": match.start(), "end": match.end(), "text": text,
            })
    entries: list[dict] = []
    for index, header in enumerate(headers):
        following = headers[index + 1] if index + 1 < len(headers) else None
        pieces: list[str] = []
        if following and following["chapter"] == header["chapter"]:
            if following["local_page"] == header["local_page"]:
                pieces.append(header["text"][header["start"]:following["start"]])
            else:
                pieces.append(header["text"][header["start"]:])
                for page_index in range(header["local_page"] + 1, following["local_page"]):
                    if page_chapters[page_index] == header["chapter"]:
                        pieces.append(pages[page_index])
                pieces.append(pages[following["local_page"]][:following["start"]])
        else:
            pieces.append(header["text"][header["start"]:])
            for page_index in range(header["local_page"] + 1, len(pages)):
                if page_chapters[page_index] != header["chapter"]:
                    break
                pieces.append(pages[page_index])
        entries.append({
            "chapter": header["chapter"], "number": header["number"], "raw_answer": header["raw_answer"],
            "solution": re.sub(r"[ \t]+", " ", "\n".join(pieces)).strip(),
            "page": start + header["local_page"] + 1,
        })
    return entries


def map_pdf(
    db: Session,
    path: Path,
    publish: bool,
    bank: QuestionBank | None = None,
    publish_without_solution: bool = False,
) -> dict[str, int]:
    subject = subject_from_filename(path.name)
    # A full OpenRouter seed is built in an invisible staging bank before it is
    # promoted.  Resolve the caller-provided bank in that flow; the CLI keeps
    # the historic global-bank default for standalone mapping runs.
    if bank is None:
        bank = db.query(QuestionBank).filter_by(slug=BANK_SLUG).one()
    by_name, normalized = subject_chapters(db, bank.id, subject)
    chapter_names = [name for name in by_name if compact(name) not in {"physics", "chemistry", "mathematics"}]
    document = fitz.open(path)
    answer_key = extract_answer_key(document, chapter_names)
    entries = extract_entries(document, chapter_names)
    keyed: dict[tuple[str, str], dict] = {}
    solution_candidates: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        chapter = resolve_chapter(entry["chapter"], by_name, normalized)
        if chapter:
            solution_candidates.setdefault((chapter, entry["number"]), []).append(entry)
    for key, candidates in solution_candidates.items():
        key_entry = answer_key.get(key)
        agreeing = [candidate for candidate in candidates if key_entry and raw_answers_equal(key_entry["raw_answer"], candidate["raw_answer"])]
        if agreeing:
            keyed[key] = max(agreeing, key=lambda item: len(item["solution"]))
        elif not key_entry and candidates:
            keyed[key] = max(candidates, key=lambda item: len(item["solution"]))

    source_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    # Checksums can legitimately recur when an earlier seed was interrupted or
    # replaced.  Restrict the lookup to this bank's source prefix so an old
    # document record cannot make every current-bank question look unrelated.
    source = (
        db.query(SourceDocument)
        .filter_by(sha256=source_checksum)
        .filter(SourceDocument.object_key.like(f"seed-sources/{bank.id}/%"))
        .first()
    )
    source_id = source.id if source else None
    answer_key_page = next(i + 1 for i, page in enumerate(document) if "ANSWER KEY" in page.get_text("text", sort=True).upper())

    chapter_by_question = {
        question_id: chapter_name
        for question_id, chapter_name in db.query(QuestionTaxonomy.question_id, TaxonomyNode.name)
        .join(TaxonomyNode, TaxonomyNode.id == QuestionTaxonomy.taxonomy_node_id)
        .filter(TaxonomyNode.bank_id == bank.id, TaxonomyNode.kind == "chapter")
    }
    questions = db.query(Question).filter_by(bank_id=bank.id).filter(Question.scope == "global").all()
    stats = {"source_questions": 0, "mapped_answer": 0, "mapped_solution": 0, "published": 0, "answer_missing": 0, "solution_missing": 0, "options_missing": 0, "non_question_source_pages": 0}
    now = datetime.now(timezone.utc)
    for question in questions:
        interaction = dict(question.interaction or {})
        if interaction.get("source_document_id") != source_id:
            continue
        question_page = int(interaction.get("source_page", 0))
        if question_page >= answer_key_page:
            # The initial seed pass also detected numbered solution blocks as
            # questions. They stay out of the publishable source-question set.
            interaction["seed_status"] = "excluded_solution_page_seed"
            question.interaction = interaction
            question.answer = None
            question.solution_markdown = None
            question.approved_at = None
            stats["non_question_source_pages"] += 1
            continue
        stats["source_questions"] += 1
        question.answer = None
        question.solution_markdown = None
        question.approved_at = None
        chapter = chapter_by_question.get(question.id)
        answer_entry = answer_key.get((chapter or "", question.source_number or ""))
        entry = keyed.get((chapter or "", question.source_number or ""))
        candidates = solution_candidates.get((chapter or "", question.source_number or ""), [])
        solution_answer = keyed.get((chapter or "", question.source_number or ""))
        raw_answer = answer_entry["raw_answer"] if answer_entry else (solution_answer["raw_answer"] if solution_answer else None)
        source_conflict = bool(
            answer_entry and candidates
            and not any(raw_answers_equal(answer_entry["raw_answer"], candidate["raw_answer"]) for candidate in candidates)
        )
        source_kind = kind_from_source_answer(question.kind, raw_answer) if raw_answer and not source_conflict else question.kind
        if source_kind != question.kind:
            question.kind = source_kind
        answer = answer_for(question.kind, raw_answer) if raw_answer and not source_conflict else None
        if answer:
            question.answer = answer
            stats["mapped_answer"] += 1
            interaction["answer_source"] = {
                "page": answer_entry["page"] if answer_entry else solution_answer["page"],
                "printed_answer": raw_answer,
                "from": "answer_key_and_solution" if answer_entry and solution_answer else "answer_key" if answer_entry else "solution_header",
            }
        else:
            stats["answer_missing"] += 1
        if entry:
            question.solution_markdown = entry["solution"]
            interaction["solution_source_spans"] = [{"page": entry["page"]}]
            stats["mapped_solution"] += 1
        else:
            stats["solution_missing"] += 1
        if source_conflict:
            interaction["seed_status"] = "source_answer_conflict"
            question.interaction = interaction
            continue
        if answer and (entry or publish_without_solution):
            needs_options = question.kind in {QuestionKind.SINGLE, QuestionKind.MULTIPLE, QuestionKind.ASSERTION} and len(question.options or []) < 4
            if needs_options:
                interaction["seed_status"] = "answer_mapped_options_missing"
                stats["options_missing"] += 1
                question.interaction = interaction
                continue
            verified_solution = bool(entry)
            interaction["seed_status"] = (
                "published_from_native_answer_and_solution" if publish and verified_solution else
                "published_from_native_answer_key" if publish else
                "answer_and_solution_mapped" if verified_solution else "answer_mapped"
            )
            if question.kind == QuestionKind.MATRIX:
                matrix_items = list(answer["matches"])
                # Offer the full labeled choice range, not just the correct
                # targets, so students can actually make a selection.
                target_labels = {choice for choices in answer["matches"].values() for choice in choices}
                if target_labels and all(len(label) == 1 and label.isalpha() for label in target_labels):
                    lowest, highest = min(map(ord, target_labels)), max(map(ord, target_labels))
                    if set("PQRST") & target_labels:
                        matrix_choices = list("PQRST")
                    else:
                        matrix_choices = [chr(code) for code in range(lowest, highest + 1)]
                else:
                    matrix_choices = sorted(target_labels)
                interaction.update({"response_format": "row_to_many_columns", "matrix_items": matrix_items, "matrix_choices": matrix_choices})
            if publish:
                question.approved_at = now
                stats["published"] += 1
        else:
            interaction["seed_status"] = "answer_mapped_solution_missing" if answer else "answer_and_solution_unmapped"
        question.interaction = interaction
    db.commit()
    return stats


def raw_answers_equal(left: str, right: str) -> bool:
    left_compact = re.sub(r"\s+", "", left).upper()
    right_compact = re.sub(r"\s+", "", right).upper()
    if re.fullmatch(r"[A-D]{1,4}", left_compact) and re.fullmatch(r"[A-D]{1,4}", right_compact):
        return sorted(left_compact) == sorted(right_compact)
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", left_compact) and re.fullmatch(r"[-+]?\d+(?:\.\d+)?", right_compact):
        return float(left_compact) == float(right_compact)

    def matrix_signature(value: str) -> dict[str, tuple[str, ...]]:
        pairs = re.findall(r"([A-D])\s*[-–→\uf0ae]\s*([A-Z](?:\s*,\s*[A-Z])*)", value, re.I)
        return {row.upper(): tuple(sorted(item.strip().upper() for item in choices.split(","))) for row, choices in pairs}

    left_matrix, right_matrix = matrix_signature(left), matrix_signature(right)
    if left_matrix and right_matrix:
        return left_matrix == right_matrix
    def clean(value: str) -> str:
        return re.sub(r"[^A-Z0-9.+-]", "", value.upper())
    return clean(left) == clean(right)


def main() -> None:
    parser = argparse.ArgumentParser(description="Map JEE native seed records to their worksheet solutions")
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--owner-email", required=True, help="Checked to prevent accidental use before seed setup")
    parser.add_argument("--publish", action="store_true", help="Publish only questions with a parsed concrete answer and solution")
    parser.add_argument("--publish-without-solution", action="store_true", help="Allow --publish when an answer key is present but a worked solution is absent")
    parser.add_argument("--bank-slug", default=BANK_SLUG, help="Target global bank slug")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(email=args.owner_email.lower()).first():
            raise SystemExit("Seed owner does not exist")
        bank = db.query(QuestionBank).filter_by(slug=args.bank_slug).one_or_none()
        if not bank:
            raise SystemExit(f"Question bank {args.bank_slug!r} does not exist")
        totals = {"source_questions": 0, "mapped_answer": 0, "mapped_solution": 0, "published": 0, "answer_missing": 0, "solution_missing": 0, "options_missing": 0, "non_question_source_pages": 0}
        for path in args.pdfs:
            result = map_pdf(db, path, args.publish, bank, args.publish_without_solution)
            print(f"{path.name}: {result}")
            for key, value in result.items():
                totals[key] += value
        print(f"TOTAL: {totals}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
