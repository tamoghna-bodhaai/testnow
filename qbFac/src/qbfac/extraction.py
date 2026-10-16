"""Deterministic first-pass extraction for editorially structured JEE PDFs.

This is deliberately a candidate extractor, not an answer-generating model.
It retains page-level evidence and only derives fields which the source labels
itself (chapter, topic, question family and printed number).
"""

from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import fitz


_TOPIC = re.compile(r"(?im)^\s*topic\s+(\d+)\s+(.+?)\s*$")
_QUESTION = re.compile(r"(?m)(?<!\w)^\s*(\d{1,4})(?:\s*[.)])?\s+(?=[A-Z(])")
_CHAPTER_PAGE = re.compile(r"(?m)^\s*(\d+)\.\s+(.+?)\s+(\d+)\s*[-–]\s*(\d+)\s*$")


def normalize_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", value)).strip()


def classify_question_kind(heading: str) -> str | None:
    value = re.sub(r"\s+", " ", heading.lower()).strip()
    if not (value.startswith("objective question") or value.startswith("true") or value.startswith("passage") or value.startswith("assertion") or value.startswith("matrix") or value.startswith("integer") or value.startswith("numerical") or value.startswith("fill in") or value.startswith("analytical")):
        return None
    if re.search(r"objective questions? ii\b", value) or "one or more" in value or "multiple correct" in value:
        return "multiple_select"
    if re.search(r"objective questions? i\b", value) or "only one correct" in value or "single correct" in value:
        return "single_choice"
    if "true/false" in value or "true false" in value:
        return "true_false"
    if "passage" in value:
        return "passage"
    if "assertion" in value and "reason" in value:
        return "assertion_reason"
    if "matrix" in value and "match" in value:
        return "matching_list"
    if "integer" in value:
        return "integer"
    if "numerical" in value or "fill in the blanks" in value:
        return "numerical"
    if "analytical" in value or "descriptive" in value:
        return "subjective"
    return None


def _editorial_headings(text: str) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    for line in re.finditer(r"(?m)^(.+)$", text):
        kind = classify_question_kind(line.group(1))
        if kind:
            matches.append((line.start(), kind))
    return matches


def parse_page_text(
    text: str, *, page_number: int, chapter: str | None, state: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract numbered candidates from one page while carrying editorial state."""
    text = normalize_text(text)
    # Publishers commonly place the key and full worked solutions immediately
    # after the question set.  Their printed numbers must never become new
    # question candidates.  A fresh editorial question-family heading begins
    # the next question phase (normally the next chapter).
    if re.search(r"(?im)^\s*answers?\s*$", text):
        state["phase"] = "answers"
    elif re.search(r"(?im)^\s*(solution|solutions)\s*$", text):
        state["phase"] = "solutions"
    topic_match = _TOPIC.search(text)
    if topic_match:
        state["topic_number"] = topic_match.group(1)
        state["topic"] = normalize_text(topic_match.group(2))
    headings = _editorial_headings(text)
    if re.search(r"(?im)^\s*(objective questions|true\s*/\s*false|fill in the blanks|integer type|numerical value|assertion.{0,24}reason|matrix.{0,24}match)", text):
        state["phase"] = "questions"
    if state.get("phase", "questions") != "questions":
        return [], state

    matches = list(_QUESTION.finditer(text))
    records = []
    heading_index = 0
    question_type = state.get("question_type", "unclassified")
    for index, match in enumerate(matches):
        while heading_index < len(headings) and headings[heading_index][0] < match.start():
            question_type = headings[heading_index][1]
            heading_index += 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        candidate = normalize_text(text[match.start() : end])
        # Page headers and equation artefacts are never viable questions. A
        # candidate also needs language-like content after its printed number.
        if len(candidate) < 18 or not re.match(r"^\d{1,4}(?:\s*[.)])?\s+[A-Z]", candidate):
            continue
        records.append(
            {
                "printed_number": match.group(1),
                "chapter": chapter,
                "topic": state.get("topic"),
                "topic_number": state.get("topic_number"),
                "question_type": question_type,
                "page_start": page_number,
                "page_end": page_number,
                "source_text": candidate,
                "needs_review": question_type in {"unclassified", "subjective", "passage"},
            }
        )
    if headings:
        state["question_type"] = headings[-1][1]
    return records, state


def _contents_chapters(document: fitz.Document) -> list[tuple[int, str]]:
    """Read publisher TOC page-ranges. Page labels are converted to PDF pages."""
    found: list[tuple[int, str]] = []
    for page_number in range(min(document.page_count, 12)):
        for number, title, start, _ in _CHAPTER_PAGE.findall(document[page_number].get_text("text")):
            # In this source the printed body begins at PDF page 10.  Deriving the
            # offset from the first recognised chapter keeps the extraction audited.
            found.append((int(start), normalize_text(title)))
    return sorted(set(found))


def extract_jee_pdf(source: Path, output_dir: Path) -> dict[str, Any]:
    """Create a human review inventory plus machine-readable candidate evidence."""
    output_dir.mkdir(parents=True, exist_ok=True)
    document = fitz.open(source)
    try:
        contents = _contents_chapters(document)
        if not contents:
            raise ValueError("no chapter ranges found in the opening contents pages")
        first_body_pdf_page = next(
            index + 1
            for index in range(document.page_count)
            if _TOPIC.search(document[index].get_text("text"))
        )
        first_printed_page = contents[0][0]
        offset = first_body_pdf_page - first_printed_page
        chapters = [(printed + offset, name) for printed, name in contents]
        candidates: list[dict[str, Any]] = []
        state: dict[str, Any] = {}
        chapter_index = 0
        for index, page in enumerate(document):
            page_number = index + 1
            while chapter_index + 1 < len(chapters) and page_number >= chapters[chapter_index + 1][0]:
                chapter_index += 1
                state = {}
            if page_number < chapters[0][0]:
                continue
            chapter = chapters[chapter_index][1]
            page_records, state = parse_page_text(
                page.get_text("text"), page_number=page_number, chapter=chapter, state=state
            )
            candidates.extend(page_records)
        for serial, candidate in enumerate(candidates, 1):
            candidate["candidate_id"] = f"{source.stem.lower().replace(' ', '-')}-{serial:06d}"
            candidate["provenance"] = {
                "source_file": source.name,
                "pdf_page_start": candidate["page_start"],
                "pdf_page_end": candidate["page_end"],
                "printed_number": candidate["printed_number"],
                "extraction": "native-text-editorial-headings-v1",
            }
        _write_outputs(source, output_dir, candidates, chapters)
        by_type = Counter(candidate["question_type"] for candidate in candidates)
        return {
            "source": source.name,
            "page_count": document.page_count,
            "chapter_count": len(chapters),
            "candidate_count": len(candidates),
            "question_types": dict(sorted(by_type.items())),
            "output_dir": str(output_dir),
        }
    finally:
        document.close()


def _write_outputs(source: Path, output_dir: Path, candidates: list[dict[str, Any]], chapters: list[tuple[int, str]]) -> None:
    (output_dir / "candidates.jsonl").write_text(
        "".join(json.dumps(candidate, ensure_ascii=False) + "\n" for candidate in candidates), encoding="utf-8"
    )
    with (output_dir / "question-inventory.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "chapter", "topic", "question_type", "printed_number", "page_start", "needs_review"])
        writer.writeheader()
        writer.writerows({key: candidate.get(key) for key in writer.fieldnames} for candidate in candidates)
    rows = "\n".join(
        f"<tr><td>{html.escape(str(c['candidate_id']))}</td><td>{html.escape(str(c['chapter'] or 'Unmapped'))}</td><td>{html.escape(str(c['topic'] or 'Unmapped'))}</td><td>{html.escape(c['question_type'])}</td><td>{html.escape(c['printed_number'])}</td><td>{c['page_start']}</td><td>{'Yes' if c['needs_review'] else 'No'}</td></tr>"
        for c in candidates
    )
    chapter_rows = "\n".join(f"<li>{html.escape(name)} (PDF page {page})</li>" for page, name in chapters)
    (output_dir / "review.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>qbFac candidate review</title>"
        "<style>body{font:14px system-ui;margin:32px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px;text-align:left}th{position:sticky;top:0;background:#f6f6f6}</style>"
        f"<h1>qbFac extraction review</h1><p>Source: {html.escape(source.name)}. These are candidates, not published questions.</p>"
        f"<h2>Detected chapters</h2><ol>{chapter_rows}</ol><h2>Candidates</h2><table><thead><tr><th>ID</th><th>Chapter</th><th>Topic</th><th>Type</th><th>Printed #</th><th>PDF page</th><th>Review?</th></tr></thead><tbody>{rows}</tbody></table>",
        encoding="utf-8",
    )
