"""Coverage-first source inventory, independent of CBT release approval.

The interface returns every observed page and numbered unit. Unlinked or
ambiguous units remain in the ledger; they are never silently filtered out.
PDF/DOCX readers only supply evidence. Linking operates on the same normalized
page interface for every source layout and document role.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

import fitz

from .intake import ALLOWED_SUFFIXES, MAX_ARCHIVE_MEMBERS, MAX_DOCUMENT_BYTES, _role
from .extraction import _contents_chapters, classify_question_kind


_CHAPTER = re.compile(r"^\s*(?:chapter|ch\.)\s*(\d+)\s*[:.\-]?\s*(.*?)\s*$", re.I)
_TOPIC = re.compile(r"^\s*topic\s+(\d+)(?:\s+(.+))?\s*$", re.I)
_QUESTION = re.compile(r"^\s*(\d{1,4})(?:[.)])?[ \t]+(?=[A-Za-z(])")
_NUMBERED = re.compile(r"^\s*\d{1,4}[.)]?[ \t]+")
_ANSWER = re.compile(r"^\s*(\d{1,4})[.)][ \t]*\(([A-Da-d](?:\s*,\s*[A-Da-d])*)\)")
_INTEGER = re.compile(r"^\s*(\d{1,4})[.)][ \t]*\((-?\d+)\)[ \t]*$")
_SOLUTION = re.compile(r"^\s*(\d{1,4})[.)][ \t]+\S")
_ANSWER_HEADING = re.compile(r"^(?:answers?|answer\s*key)(?:\s*[:.])?$", re.I)
_SOLUTION_HEADING = re.compile(r"^(?:hints?\s*(?:&|and)\s*solutions?|solutions?)(?:\s*[:.])?$", re.I)
_QUESTION_HEADING = re.compile(r"^(?:objective\s+questions?|integer(?:\s+answer)?\s+type\s+questions?|numerical\s+(?:value\s+)?questions?|passage\s+based\s+problems?|assertion\s*(?:and|-)\s*reason).*$", re.I)


@dataclass(frozen=True)
class EvidenceLine:
    text: str
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class EvidencePage:
    document_id: str
    page_number: int
    role: str
    lines: tuple[EvidenceLine, ...]
    image_count: int = 0
    source_sha256: str | None = None
    raw_text: str = ""
    chapter_hint: str | None = None


def _unit(page: EvidencePage, index: int, kind: str, number: str, chapter: str | None,
          topic_number: str | None, topic: str | None, line: EvidenceLine) -> dict[str, Any]:
    return {
        "id": f"{page.document_id}:p{page.page_number}:l{index}:{kind}",
        "number": number, "chapter": chapter, "topic_number": topic_number,
        "topic": topic, "text": line.text, "source": {
            "document_id": page.document_id, "page": page.page_number,
            "line": index, "bbox": line.bbox, "sha256": page.source_sha256,
        },
    }


def inventory_pages(pages: Iterable[EvidencePage]) -> dict[str, Any]:
    """Return a complete evidence ledger and conservative question links.

    Pages must be in source reading order. A link is accepted only when both
    endpoints are unique under chapter/topic/printed-number identity.
    """
    page_rows: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    solutions: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []
    document_id: str | None = None
    chapter: str | None = None
    topic_number: str | None = None
    topic: str | None = None
    question_type: str | None = None
    phase = "questions"
    for page in pages:
        if page.document_id != document_id:
            document_id, chapter, topic_number, topic = page.document_id, None, None, None
            question_type = None
            phase = page.role if page.role in {"questions", "answer_key", "solutions"} else "questions"
        if page.role in {"answer_key", "solutions"}:
            phase = page.role
        if page.chapter_hint and page.chapter_hint != chapter:
            chapter, topic_number, topic = page.chapter_hint, None, None
            question_type = None
            if page.role == "questions":
                phase = "questions"
        page_status = "needs_ocr" if not any(line.text.strip() for line in page.lines) and page.image_count else "captured"
        page_rows.append({
            "document_id": page.document_id, "page": page.page_number, "role": page.role,
            "status": page_status, "image_count": page.image_count,
            "source_sha256": page.source_sha256, "raw_text": page.raw_text,
            "chapter_hint": page.chapter_hint,
            "lines": [{"text": line.text, "bbox": line.bbox} for line in page.lines],
        })
        if page_status == "needs_ocr" or page.role == "front_matter":
            continue
        for index, line in enumerate(page.lines):
            text = line.text.strip()
            if not text:
                continue
            if match := _CHAPTER.match(text):
                chapter = match.group(2).strip() or f"Chapter {match.group(1)}"
                topic_number, topic = None, None
                question_type = None
                if page.role == "questions":
                    phase = "questions"
                continue
            if match := _TOPIC.match(text):
                topic_number = match.group(1)
                topic = match.group(2).strip() if match.group(2) else None
                continue
            if _ANSWER_HEADING.match(text):
                phase = "answer_key"
                continue
            if _SOLUTION_HEADING.match(text):
                phase = "solutions"
                continue
            if _QUESTION_HEADING.match(text) and page.role == "questions":
                phase = "questions"
                question_type = classify_question_kind(text)
                continue
            if phase == "questions":
                match = _QUESTION.match(text)
                if match:
                    question = _unit(page, index, "question", match.group(1), chapter, topic_number, topic, line)
                    question["question_type"] = question_type or "unclassified"
                    questions.append(question)
                elif _NUMBERED.match(text):
                    unclassified.append({"reason": "numbered_line_not_question_anchor", **_unit(page, index, "unclassified", "", chapter, topic_number, topic, line)})
            elif phase == "answer_key":
                choice, integer = _ANSWER.match(text), _INTEGER.match(text)
                match = choice or integer
                if match:
                    unit = _unit(page, index, "answer", match.group(1), chapter, topic_number, topic, line)
                    unit["value"] = ([letter.strip().upper() for letter in match.group(2).split(",")]
                                     if choice else int(match.group(2)))
                    answers.append(unit)
                elif _NUMBERED.match(text):
                    unclassified.append({"reason": "unparsed_answer", **_unit(page, index, "unclassified", "", chapter, topic_number, topic, line)})
            else:
                match = _SOLUTION.match(text)
                if match:
                    solutions.append(_unit(page, index, "solution", match.group(1), chapter, topic_number, topic, line))
                elif _NUMBERED.match(text):
                    unclassified.append({"reason": "unparsed_solution", **_unit(page, index, "unclassified", "", chapter, topic_number, topic, line)})

    keyed: dict[tuple[str | None, str | None, str], list[dict[str, Any]]] = {}
    for question in questions:
        keyed.setdefault((question["chapter"], question["topic_number"], question["number"]), []).append(question)
    def index_by_chapter_number(units: list[dict[str, Any]]) -> dict[tuple[str | None, str], list[dict[str, Any]]]:
        indexed: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
        for unit in units:
            indexed.setdefault((unit["chapter"], unit["number"]), []).append(unit)
        return indexed
    answer_index = index_by_chapter_number(answers)
    solution_index = index_by_chapter_number(solutions)
    for question in questions:
        key = (question["chapter"], question["topic_number"], question["number"])
        def matching(index: dict[tuple[str | None, str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
            if question["chapter"] is None:
                return []
            return [unit for unit in index.get((question["chapter"], question["number"]), [])
                    if unit["topic_number"] is None or question["topic_number"] == unit["topic_number"]]
        answer_matches = matching(answer_index)
        solution_matches = matching(solution_index)
        if len(keyed[key]) > 1:
            question["status"] = "ambiguous_question_key"
        elif len(answer_matches) > 1 or len(solution_matches) > 1:
            question["status"] = "ambiguous_link"
        elif not answer_matches and not solution_matches:
            question["status"] = "missing_answer_and_solution"
        elif not answer_matches:
            question["status"] = "missing_answer"
        elif not solution_matches:
            question["status"] = "missing_solution"
        else:
            question["status"] = "linked"
        question["answer"] = answer_matches[0] if len(answer_matches) == 1 and len(keyed[key]) == 1 else None
        question["solution"] = solution_matches[0] if len(solution_matches) == 1 and len(keyed[key]) == 1 else None
        question["answer_match_count"] = len(answer_matches)
        question["solution_match_count"] = len(solution_matches)
    matched_answer_ids = {q["answer"]["id"] for q in questions if q["answer"]}
    matched_solution_ids = {q["solution"]["id"] for q in questions if q["solution"]}
    unmatched_answers = [answer for answer in answers if answer["id"] not in matched_answer_ids]
    unmatched_solutions = [solution for solution in solutions if solution["id"] not in matched_solution_ids]
    unresolved = sum(question["status"] != "linked" for question in questions)
    summary = {
        "pages": len(page_rows), "pages_needing_ocr": sum(row["status"] == "needs_ocr" for row in page_rows),
        "question_candidates": len(questions), "answer_observations": len(answers),
        "solution_observations": len(solutions), "linked_questions": len(questions) - unresolved,
        "unresolved_questions": unresolved, "unclassified_numbered_lines": len(unclassified),
        "unmatched_answers": len(unmatched_answers), "unmatched_solutions": len(unmatched_solutions),
        "all_pages_text_extracted": not any(row["status"] == "needs_ocr" for row in page_rows),
        "question_recall_verified": False,
    }
    return {"schema_version": "inventory-1", "summary": summary, "pages": page_rows,
            "questions": questions, "answers": answers, "solutions": solutions,
            "unmatched_answers": unmatched_answers, "unmatched_solutions": unmatched_solutions,
            "unclassified_numbered_lines": unclassified}


def _pdf_pages(raw: bytes, document_id: str, role: str) -> list[EvidencePage]:
    digest = hashlib.sha256(raw).hexdigest()
    document = fitz.open(stream=raw, filetype="pdf")
    result: list[EvidencePage] = []
    try:
        # TOC page ranges are evidence, not a publisher-specific crop recipe.
        # If the printed-to-PDF offset cannot be corroborated from the first
        # chapter heading, no chapter is inferred.
        contents = _contents_chapters(document)
        chapter_starts: list[tuple[int, str]] = []
        if len(contents) > 1:
            printed_first, first_title = contents[0]
            title_pattern = re.compile(r"\s+".join(re.escape(part) for part in first_title.split()), re.I)
            body_start = next((index for index in range(1, min(len(document), 80))
                               if title_pattern.search(document[index].get_text("text")[:350])
                               and re.search(r"(?im)^\s*(?:topic\s*1|objective\s+questions?)\b", document[index].get_text("text"))), None)
            if body_start is not None:
                offset = body_start - (printed_first - 1)
                chapter_starts = [(printed - 1 + offset, title) for printed, title in contents]
                if any(start < body_start or start >= len(document) for start, _ in chapter_starts):
                    chapter_starts = []
        for index, page in enumerate(document):
            lines: list[EvidenceLine] = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    if text:
                        lines.append(EvidenceLine(text, tuple(round(float(value), 2) for value in line["bbox"])))
            lines = _reading_order(lines, page.rect.width)
            chapter_hint = next((title for start, title in reversed(chapter_starts) if index >= start), None)
            page_role = "front_matter" if chapter_starts and index < chapter_starts[0][0] else role
            result.append(EvidencePage(document_id, index + 1, page_role, tuple(lines),
                                       len(page.get_images(full=True)), digest, page.get_text("text"), chapter_hint))
    finally:
        document.close()
    return result


def _reading_order(lines: list[EvidenceLine], width: float) -> list[EvidenceLine]:
    """Read visual bands top-to-bottom, then columns within each band."""
    cuts = sorted(line.bbox[1] for line in lines if line.bbox and (
        _ANSWER_HEADING.match(line.text) or _SOLUTION_HEADING.match(line.text)))
    def order(line: EvidenceLine) -> tuple[int, int, float, float]:
        x, y = line.bbox[:2] if line.bbox else (0.0, 0.0)
        band = sum(y >= cut for cut in cuts)
        heading = int(not (_ANSWER_HEADING.match(line.text) or _SOLUTION_HEADING.match(line.text)))
        return band, heading, float(x >= width / 2), y
    return sorted(lines, key=order)


def _docx_pages(raw: bytes, document_id: str, role: str) -> list[EvidencePage]:
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        document = ElementTree.fromstring(bundle.read("word/document.xml"))
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines = [EvidenceLine("".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)))
                 for paragraph in document.findall(".//w:p", namespace)]
        media_count = sum(name.startswith("word/media/") for name in bundle.namelist())
    return [EvidencePage(document_id, 1, role, tuple(lines), media_count,
                         hashlib.sha256(raw).hexdigest(), "\n".join(line.text for line in lines))]


def inventory_source(source: Path) -> dict[str, Any]:
    """Inventory one PDF/DOCX or a bundle of them; never create a release."""
    documents: list[tuple[str, bytes]] = []
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as bundle:
            members = [entry for entry in bundle.infolist() if not entry.is_dir()]
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("source archive has too many files")
            for entry in members:
                member = Path(entry.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"unsafe archive path: {entry.filename}")
                if member.suffix.lower() in ALLOWED_SUFFIXES:
                    if entry.file_size > MAX_DOCUMENT_BYTES:
                        raise ValueError(f"source document exceeds size limit: {entry.filename}")
                    documents.append((entry.filename, bundle.read(entry)))
    elif source.suffix.lower() in ALLOWED_SUFFIXES:
        documents.append((source.name, source.read_bytes()))
    else:
        raise ValueError("inventory supports PDF, DOCX, or a ZIP of those documents")
    if not documents:
        raise ValueError("source contains no PDF or DOCX documents")
    pages: list[EvidencePage] = []
    for name, raw in documents:
        role = _role(name)
        if Path(name).suffix.lower() == ".pdf":
            pages.extend(_pdf_pages(raw, name, role))
        else:
            pages.extend(_docx_pages(raw, name, role))
    return inventory_pages(pages)


def write_inventory(source: Path, output: Path) -> dict[str, Any]:
    if output.exists() and not output.is_dir():
        raise ValueError("inventory output must be a directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("inventory output directory must be empty")
    ledger = inventory_source(source)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps({
        "schema_version": ledger["schema_version"], "source": source.name, **ledger["summary"]
    }, indent=2) + "\n", encoding="utf-8")
    for collection in ("pages", "questions", "answers", "solutions", "unmatched_answers",
                       "unmatched_solutions", "unclassified_numbered_lines"):
        with (output / f"{collection}.jsonl").open("w", encoding="utf-8") as handle:
            for row in ledger[collection]:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return ledger["summary"]
