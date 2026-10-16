"""Geometry-first recipe for Arihant's two-column JEE mathematics book.

Only questions with a complete visual crop, an unambiguous printed choice key,
and a bounded worked-solution crop are released. Everything else is reported.
"""

from __future__ import annotations

import json
import re
import base64
import html
from collections import Counter
from pathlib import Path
from typing import Any

import fitz

from .extraction import _contents_chapters, classify_question_kind
from .release import build_release


_TOPIC = re.compile(r"^[ \t]*Topic[ \t]+(\d+)[ \t]+([^\n]+)", re.I)
_NUMBER = re.compile(r"^\s*(\d{1,3})\.?\s+[A-Za-z(]", re.S)
_SOLUTION_NUMBER = re.compile(r"^[ \t]*(\d{1,3})\.[ \t]+[A-Za-z(]", re.S)
_KEY = re.compile(r"(?m)^\s*(\d{1,3})\.\s*\(([a-d](?:\s*,\s*[a-d])*)\)")
_OPTION = re.compile(r"\(([a-d])\)", re.I)


def _blocks(page: fitz.Page) -> list[dict[str, Any]]:
    return [
        {"rect": fitz.Rect(raw[:4]), "text": raw[4].strip()}
        for raw in page.get_text("blocks") if len(raw) > 4 and isinstance(raw[4], str) and raw[4].strip()
    ]


def _chapter_ranges(document: fitz.Document) -> list[tuple[int, int, str]]:
    contents = _contents_chapters(document)
    if not contents:
        raise ValueError("Arihant contents pages do not contain chapter ranges")
    first_body = next((i for i, page in enumerate(document) if re.search(r"(?m)^Topic\s+1\s+", page.get_text("text"))), None)
    if first_body is None:
        raise ValueError("could not locate the first chapter body")
    offset = first_body - (contents[0][0] - 1)
    starts = [(printed - 1 + offset, title) for printed, title in contents]
    return [(start, starts[index + 1][0] if index + 1 < len(starts) else len(document), title) for index, (start, title) in enumerate(starts)]


def _phase_pages(document: fitz.Document, start: int, end: int) -> tuple[list[int], list[int], list[int]]:
    answer_page = next((i for i in range(start, end) if re.search(r"(?m)^\s*Answers\s*$", document[i].get_text("text"))), None)
    if answer_page is None:
        return [], [], []
    return list(range(start, answer_page)), [answer_page], list(range(answer_page, end))


def _question_regions(document: fitz.Document, pages: list[int], chapter: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    topic_number: str | None = None
    topic_name: str | None = None
    kind = "single_choice"
    for page_index in pages:
        page = document[page_index]
        blocks = _blocks(page)
        headings = sorted(
            [(block["rect"].y0, match.group(1), match.group(2).strip())
             for block in blocks if (match := _TOPIC.match(block["text"]))],
            key=lambda value: value[0],
        )
        section_boundaries = [block["rect"].y0 for block in blocks if classify_question_kind(block["text"].splitlines()[0])]
        text = page.get_text("text")
        if "Objective Questions II" in text or "Objective Question II" in text:
            kind = "mixed"
        # A page can contain a topic change. Assign the newest heading above
        # each question; headings spanning the page apply to both columns.
        starts = []
        for block in blocks:
            rect = block["rect"]
            if not (60 <= rect.x0 <= 105 or 330 <= rect.x0 <= 365):
                continue
            match = _NUMBER.match(block["text"])
            if match and 1 <= int(match.group(1)) <= 300:
                starts.append((rect.y0, 0 if rect.x0 < 320 else 1, match.group(1)))
        starts.sort()
        for y, column, number in starts:
            applicable = [heading for heading in headings if heading[0] < y]
            if applicable:
                _, topic_number, topic_name = applicable[-1]
            if not topic_number or not topic_name:
                continue
            later = [other_y for other_y, other_col, _ in starts if other_col == column and other_y > y + 2]
            later += [heading_y for heading_y, _, _ in headings if heading_y > y + 2]
            later += [boundary for boundary in section_boundaries if boundary > y + 2]
            bottom = min(later) - 8 if later else min(page.rect.height - 48, 770)
            left, right = (62, 326) if column == 0 else (332, page.rect.width - 55)
            rect = fitz.Rect(left, max(0, y - 3), right, bottom)
            if rect.height < 24:
                continue
            fragment = page.get_text("text", clip=rect)
            options = set(letter.lower() for letter in _OPTION.findall(fragment))
            records.append({
                "chapter": chapter, "topic_number": topic_number, "topic": topic_name,
                "number": number, "page": page_index + 1, "rect": rect,
                "option_labels": options, "kind_hint": kind,
            })
        if headings:
            _, topic_number, topic_name = headings[-1]
    return records


def _answers(document: fitz.Document, pages: list[int]) -> dict[tuple[str, str], dict[str, Any]]:
    answers: dict[tuple[str, str], dict[str, Any]] = {}
    topic: str | None = None
    for page_index in pages:
        page = document[page_index]
        text = page.get_text("text")
        if not re.search(r"(?m)^\s*Answers\s*$", text):
            continue
        key_text = text.split("Answers", 1)[1]
        # The first named topic marks the start of the worked solutions.
        key_text = re.split(r"(?m)^Topic[ \t]+\d+[ \t]+\S", key_text, maxsplit=1)[0]
        for line in key_text.splitlines():
            heading = re.match(r"^\s*Topic\s+(\d+)\s*$", line)
            if heading:
                topic = heading.group(1)
                continue
            match = _KEY.match(line)
            if not match or topic is None:
                continue
            selected = [letter.strip().upper() for letter in match.group(2).split(",")]
            key = (topic, match.group(1))
            if key in answers and answers[key]["selected"] != selected:
                answers[key]["ambiguous"] = True
            else:
                answers[key] = {"selected": selected, "page": page_index + 1}
    return answers


def _solution_regions(document: fitz.Document, pages: list[int]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    topic: str | None = None
    in_solutions = False
    for page_index in pages:
        page = document[page_index]
        blocks = _blocks(page)
        starts: list[tuple[float, int, str, str]] = []
        headings: list[tuple[float, str]] = []
        for block in blocks:
            rect = block["rect"]
            heading = _TOPIC.match(block["text"])
            if heading and heading.group(2).strip():
                headings.append((rect.y0, heading.group(1)))
            if not (60 <= rect.x0 <= 105 or 330 <= rect.x0 <= 365):
                continue
            match = _SOLUTION_NUMBER.match(block["text"])
            if match:
                starts.append((rect.y0, 0 if rect.x0 < 320 else 1, match.group(1), block["text"]))
        headings.sort()
        if headings:
            in_solutions = True
        if not in_solutions:
            continue
        starts.sort()
        for y, column, number, text in starts:
            applicable = [heading for heading in headings if heading[0] < y]
            if applicable:
                topic = applicable[-1][1]
            if topic is None:
                continue
            later = [other_y for other_y, other_col, _, _ in starts if other_col == column and other_y > y + 2]
            later += [heading_y for heading_y, _ in headings if heading_y > y + 2]
            # Reject a solution that might continue onto another page.
            if not later:
                continue
            bottom = min(later) - 2
            left, right = (62, 326) if column == 0 else (332, page.rect.width - 55)
            rect = fitz.Rect(left, max(0, y - 3), right, bottom)
            if rect.height < 26:
                continue
            key = (topic, number)
            found.setdefault(key, []).append({"page": page_index + 1, "rect": rect})
        if headings:
            topic = headings[-1][1]
    return found


def _png(document: fitz.Document, page: int, rect: fitz.Rect) -> bytes:
    return document[page - 1].get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False).tobytes("png")


def _checked_png(document: fitz.Document, page: int, rect: fitz.Rect) -> bytes | None:
    pixmap = document[page - 1].get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
    samples = pixmap.samples
    for y in range(max(0, pixmap.height - 2), pixmap.height):
        row = samples[y * pixmap.stride : y * pixmap.stride + pixmap.width * pixmap.n]
        if any(channel < 160 for channel in row):
            return None
    return pixmap.tobytes("png")


def _review_html(items: list[dict[str, Any]], assets: dict[str, bytes]) -> str:
    def picture(path: str) -> str:
        encoded = base64.b64encode(assets[path]).decode("ascii")
        return f'<img src="data:image/png;base64,{encoded}" alt="Source crop">'

    cards = []
    for item in items:
        taxonomy = item["taxonomy"]
        options = "".join(
            f'<div class="option"><b>{option["id"]}</b>{picture(option["content"]["value"])}</div>'
            for option in item["interaction"]["options"]
        )
        selected = ", ".join(item["answer"]["selected"])
        cards.append(
            f'<article data-chapter="{html.escape(taxonomy["chapter"], quote=True)}" '
            f'data-topic="{html.escape(taxonomy["topic"], quote=True)}">'
            f'<small>{html.escape(taxonomy["chapter"])} / {html.escape(taxonomy["topic"])} · '
            f'{html.escape(item["id"])}</small>'
            f'{picture(item["content"]["stem"]["value"])}'
            f'<div class="options">{options}</div>'
            f'<details><summary>Answer and worked solution</summary><p>Correct: {html.escape(selected)}</p>'
            f'{picture(item["solution"]["value"])}</details></article>'
        )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>qbFac visual item review</title>'
        '<style>body{font:14px system-ui;margin:24px auto;max-width:950px;color:#17213a}header{position:sticky;top:0;background:white;padding:10px 0;border-bottom:1px solid #ddd}input{padding:9px;width:min(95%,550px)}article{border:1px solid #ddd;border-radius:10px;padding:18px;margin:18px 0}small{display:block;margin-bottom:12px;color:#57627a}img{display:block;max-width:100%;height:auto;margin:8px 0}.options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.option{border:1px solid #ccd4e4;padding:8px;border-radius:7px}.option b{display:block}details{margin-top:14px;padding:10px;background:#f1f4fa}summary{cursor:pointer;font-weight:700}@media(max-width:650px){.options{grid-template-columns:1fr}}</style>'
        f'<header><h1>qbFac visual item review</h1><p>{len(items)} items. Filter by chapter or topic.</p><input id="filter" aria-label="Filter items" placeholder="Search chapter or topic" oninput="const q=this.value.toLowerCase();document.querySelectorAll(\'article\').forEach(e=>e.hidden=!(e.dataset.chapter+\' \'+e.dataset.topic).toLowerCase().includes(q))"></header>'
        + "".join(cards) + "</html>"
    )


def _choice_regions(page: fitz.Page, question: fitz.Rect) -> tuple[fitz.Rect, dict[str, fitz.Rect]] | None:
    """Split a question crop at printed option markers, retaining the artwork."""
    labels: dict[str, fitz.Rect] = {}
    for letter in "abcd":
        found = [rect for rect in page.search_for(f"({letter})") if question.contains(rect)]
        if len(found) != 1:
            return None
        labels[letter.upper()] = found[0]
    first = min(rect.y0 for rect in labels.values())
    stem = fitz.Rect(question.x0, question.y0, question.x1, first - 1)
    if stem.height < 16:
        return None
    option_regions: dict[str, fitz.Rect] = {}
    rows = sorted({round(rect.y0 / 6) for rect in labels.values()})
    markers = sorted(labels.items(), key=lambda entry: (round(entry[1].y0 / 6), entry[1].x0))
    for letter, rect in markers:
        same_row_right = [other.x0 for other in labels.values() if other.x0 > rect.x0 + 3 and abs(other.y0 - rect.y0) < 6]
        lower = [other.y0 for other in labels.values() if other.y0 > rect.y0 + 5]
        right = min(same_row_right) - 2 if same_row_right else question.x1
        bottom = min(lower) - 1 if lower else question.y1
        if len(rows) == 1:
            # All choices share one baseline. Any later ink belongs to the
            # following question or a page ornament, not to these choices.
            bottom = min(bottom, max(other.y1 for other in labels.values()) + 8)
        crop = fitz.Rect(max(question.x0, rect.x0 - 2), rect.y0 - 2, right, bottom)
        if crop.width < 22 or crop.height < 8:
            return None
        option_regions[letter] = crop
    return stem, option_regions


def build_arihant_release(
    source: Path, output: Path, report_path: Path, *, chapter_filter: str | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Build a verified visual-first CBT release from unambiguous source rows."""
    document = fitz.open(source)
    items: list[dict[str, Any]] = []
    assets: dict[str, bytes] = {}
    reasons: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    try:
        for start, end, chapter in _chapter_ranges(document):
            if chapter_filter and chapter_filter.lower() not in chapter.lower():
                continue
            question_pages, answer_pages, solution_pages = _phase_pages(document, start, end)
            if not answer_pages:
                reasons["no_answer_page"] += 1
                continue
            questions = _question_regions(document, question_pages, chapter)
            keys = _answers(document, answer_pages)
            solutions = _solution_regions(document, solution_pages)
            occurrence: Counter[tuple[str, str]] = Counter()
            for question in questions:
                key = (question["topic_number"], question["number"])
                occurrence[key] += 1
            for question in questions:
                key = (question["topic_number"], question["number"])
                reason = None
                answer = keys.get(key)
                solution_regions = solutions.get(key, [])
                if occurrence[key] != 1:
                    reason = "duplicate_question_number"
                elif question["option_labels"] != {"a", "b", "c", "d"}:
                    reason = "incomplete_or_ambiguous_options"
                elif not answer or answer.get("ambiguous"):
                    reason = "missing_or_ambiguous_answer"
                elif not solution_regions or len(solution_regions) != 1:
                    reason = "missing_or_ambiguous_solution"
                elif any(letter.lower() not in question["option_labels"] for letter in answer["selected"]):
                    reason = "answer_not_in_options"
                choice_regions = _choice_regions(document[question["page"] - 1], question["rect"]) if not reason else None
                if not reason and choice_regions is None:
                    reason = "option_geometry_unresolved"
                if reason:
                    reasons[reason] += 1
                    if len(examples) < 100:
                        examples.append({"chapter": chapter, "topic": question["topic"], "number": question["number"], "page": question["page"], "reason": reason})
                    continue
                if max_items is not None and len(items) >= max_items:
                    break
                selected = answer["selected"]
                kind = "single_choice" if len(selected) == 1 else "multiple_select"
                profile = "jee-main-paper-1" if kind == "single_choice" else "jee-advanced"
                item_id = f"arihant-maths-p{question['page']}-t{key[0]}-q{key[1]}"
                stem_rect, option_rects = choice_regions
                question_asset = f"assets/{item_id}-stem.png"
                solution_asset = f"assets/{item_id}-solution.png"
                option_assets = {}
                rendered_options = {}
                for letter, rect in option_rects.items():
                    path = f"assets/{item_id}-option-{letter}.png"
                    option_assets[letter] = path
                    rendered_options[path] = _checked_png(document, question["page"], rect)
                if any(payload is None for payload in rendered_options.values()):
                    reasons["option_crop_touches_edge"] += 1
                    continue
                sol = solution_regions[0]
                solution_png = _checked_png(document, sol["page"], sol["rect"])
                if solution_png is None:
                    reasons["solution_crop_touches_edge"] += 1
                    continue
                assets[question_asset] = _png(document, question["page"], stem_rect)
                assets.update(rendered_options)
                assets[solution_asset] = solution_png
                span = lambda role, page, rect: {"role": role, "page": page, "bbox": [round(value, 2) for value in rect]}
                items.append({
                    "id": item_id, "revision": 1, "exam_profiles": [profile],
                    "taxonomy": {"subject": "Mathematics", "chapter": chapter, "topic": question["topic"]},
                    "interaction": {"kind": kind, "options": [
                        {"id": letter, "content": {"format": "asset", "value": option_assets[letter]}} for letter in "ABCD"
                    ]},
                    "content": {"stem": {"format": "asset", "value": question_asset}},
                    "answer": {"selected": selected},
                    "solution": {"format": "asset", "value": solution_asset},
                    "scoring": {"type": "exact", "correct": 4, "incorrect": -1, "unanswered": 0},
                    "provenance": {"source_id": source.name, "printed_number": question["number"], "spans": [
                        span("question", question["page"], question["rect"]),
                        {"role": "answer", "page": answer["page"], "bbox": None},
                        span("solution", sol["page"], sol["rect"]),
                    ]},
                    "quality": {"status": "approved", "approved_by": "arihant-geometry-recipe-v1", "checks": ["structure", "answer", "solution", "taxonomy", "visual"], "approval_mode": "deterministic"},
                })
            if max_items is not None and len(items) >= max_items:
                break
        report: dict[str, Any] = {"source": source.name, "generated_items": len(items), "excluded": dict(reasons), "examples": examples}
        if items:
            report["release"] = build_release(
                release_id=f"arihant-maths-visual-v1-{len(items)}", title="Arihant JEE Mathematics visual items",
                items=items, assets=assets, output=output,
            )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if items:
            review_path = report_path.with_suffix(".html")
            review_path.write_text(_review_html(items, assets), encoding="utf-8")
            report["review_html"] = str(review_path)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return report
    finally:
        document.close()
