"""Shared geometry, visual context and validated figure persistence.

Coordinates are always top-left PDF points on an unrotated page. Native paths
are candidates, never evidence that a whole question/page is an illustration.
No model/provider/storage dependency is required for detection or validation.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from contextlib import contextmanager
import json
import hashlib
import math
import re
from io import BytesIO
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET

import fitz

VERSION = "figures_v3"


def docx_visual_inventory(raw):
    """Detect source graphics that a PDF converter may flatten or drop."""
    try:
        with ZipFile(BytesIO(raw)) as archive:
            root=ET.fromstring(archive.read("word/document.xml"))
        names=[e.tag.rsplit("}",1)[-1] for e in root.iter()]
        return {"drawings":names.count("drawing")+names.count("pict"),
                "complex":sum(names.count(n) for n in ("anchor","pict","object","chart","relIds"))}
    except (BadZipFile,KeyError,ET.ParseError):
        return {"unverified":True}


def docx_conversion_issues(inventory, regions):
    issues=[]
    if inventory.get("unverified") or inventory.get("complex"):
        issues.append("diagram_docx_complex_visual_review")
    if inventory.get("drawings") and not regions:
        issues.append("diagram_docx_visuals_missing_after_conversion")
    return issues


DIAGRAM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["page", "bbox", "alt", "role", "option_index"],
    "properties": {
        "page": {"type": "integer", "minimum": 1},
        "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
        "alt": {"type": "string"},
        "role": {"type": "string", "enum": ["question", "option", "solution"]},
        "option_index": {"type": ["integer", "null"], "minimum": 0},
    },
}
OPTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["value", "markdown", "requires_diagram"],
    "properties": {"value": {"type": "string"}, "markdown": {"type": "string"},
                   "requires_diagram": {"type": "boolean"}},
}
VISUAL_INSTRUCTIONS = """
VISUAL CONTRACT: Every source image is an unrotated page. All bbox values and
source_spans must use top-left PDF POINTS from the supplied page dimensions and
layout, never rendered pixels. Include complete figures and their labels, but
exclude surrounding question prose. Use role=question for stem figures,
role=solution for worked figures, and role=option with the zero-based
option_index for each answer-choice figure. option_index must be null for other
roles. Diagram-only choices are real options: preserve their printed order and
value, use empty markdown if no prose exists, set requires_diagram=true and
provide their individual diagram(s). Mixed text/image choices also require the
diagram. Never replace diagram choices with descriptions or merge all choices
into one stem image. Text-only choices have requires_diagram=false. Inspect
all visual pages even when a native text layer exists. If uncertain, lower
confidence; never invent figure coordinates or silently omit visual choices.
If the response schema uses box_2d instead of bbox, use normalized 0-1000
[ymin,xmin,ymax,xmax] for that field; the server converts it to PDF points.
"""


def model_visual_request(payload, model):
    """Use Gemini's documented box_2d convention, with an explicit adapter.

    Fallback providers can have different contracts; never guess a coordinate
    space from the magnitude of returned numbers.
    """
    request = deepcopy(payload)
    request["model"] = model
    if "gemini" not in model.lower():
        return request
    def adapt(node):
        if isinstance(node, dict):
            props = node.get("properties", {})
            if "bbox" in props:
                props["box_2d"] = props.pop("bbox")
                props["box_2d"]["items"] = {"type": "number", "minimum": 0, "maximum": 1000}
                props["box_2d"]["description"] = "[ymin, xmin, ymax, xmax], normalized to 0-1000 on the full source page."
                node["required"] = ["box_2d" if k == "bbox" else k for k in node.get("required", [])]
            for value in node.values():
                adapt(value)
        elif isinstance(node, list):
            for value in node:
                adapt(value)
    adapt(request.get("response_format", {}))
    request["messages"].append({"role": "user", "content": (
        "Geometry for this Gemini request: output box_2d only, as "
        "[ymin,xmin,ymax,xmax] normalized to 0-1000 on the full source page. "
        "Do not return PDF points or image pixels. This also applies to source_spans. "
        "Preserve every diagram-only option and its zero-based option_index."
    )})
    return request


def canonical_visual_response(parsed, payload, model=None):
    normalized="gemini" in str(model or payload.get("model","")).lower()
    dimensions = {}
    for message in payload.get("messages", []):
        for part in message.get("content", []) if isinstance(message.get("content"), list) else []:
            text = part.get("text", "")
            if text.startswith("SOURCE LAYOUT "):
                layout = json.loads(text[len("SOURCE LAYOUT "):])
                dimensions[layout["page"]] = (layout["width_points"], layout["height_points"])
    for item in parsed.get("questions") or []:
        if not isinstance(item, dict):
            continue
        for record in (item.get("source_spans") or []) + (item.get("diagrams") or []):
            if not isinstance(record, dict):
                continue
            if "box_2d" not in record:
                if normalized:record["bbox"]=None
                continue
            values = record.pop("box_2d")
            size = dimensions.get(record.get("page"))
            # Check y/x order before converting; rect() validates x/y order.
            try:
                y0, x0, y1, x1 = [float(v) for v in values]
                valid = size and all(math.isfinite(v) and 0 <= v <= 1000 for v in (y0,x0,y1,x1)) and y1 > y0 and x1 > x0
            except (TypeError, ValueError, OverflowError):
                valid = False
            record["bbox"] = [x0*size[0]/1000,y0*size[1]/1000,x1*size[0]/1000,y1*size[1]/1000] if valid else None
    return parsed


@contextmanager
def unrotated(page):
    rotation = page.rotation
    try:
        if rotation:
            page.set_rotation(0)
        yield page
    finally:
        if rotation:
            page.set_rotation(rotation)


def rect(value):
    try:
        if not isinstance(value, (list, tuple, fitz.Rect)) or len(value) != 4:
            return None
        values = [float(v) for v in value]
        if not all(math.isfinite(v) for v in values):
            return None
        box = fitz.Rect(values)
        return box if box.width > 0 and box.height > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def coverage(box, container):
    return (box & container).get_area() / max(box.get_area(), 0.001)


def page_number(value, document):
    # Do not allow Python's negative indexing, fractional pages, or bools.
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= len(document):
        return None
    return value


def _paths(page):
    result = []
    for path in page.get_drawings():
        raw = path.get("rect")
        if raw is None or not all(math.isfinite(v) for v in raw):
            continue
        # Clipped/out-of-page paths can have enormous reported extents. Do not
        # let them join unrelated figures merely by clamping their union later.
        if raw.width > page.rect.width or raw.height > page.rect.height:
            continue
        stroke = path.get("color")
        fill = path.get("fill")
        visible_stroke = stroke is not None and path.get("stroke_opacity", 1) > 0 and min(stroke) < .96
        visible_fill = fill is not None and path.get("fill_opacity", 1) > 0 and min(fill) < .96
        if not (visible_stroke or visible_fill):
            continue
        # Preserve thin lines; their *unpadded* area is commonly zero.
        box = (raw + (-1, -1, 1, 1)) & page.rect
        if box.is_empty or max(box.width, box.height) < 8:
            continue
        if box.get_area() > page.rect.get_area() * .65:
            continue
        result.append(box)
    return result


def _clusters(boxes, gap=6):
    groups = []
    for box in boxes:
        merged = fitz.Rect(box)
        # A bridging stroke must merge every touching component, transitively.
        while True:
            touching = [g for g in groups if (g + (-gap, -gap, gap, gap)).intersects(merged)]
            if not touching:
                break
            for group in touching:
                merged |= group
                groups.remove(group)
        groups.append(merged)
    return sorted(groups, key=lambda b: (b.y0, b.x0))


def _prose(page, box):
    words = [w for w in page.get_text("words") if coverage(fitz.Rect(w[:4]), box) > .8]
    # Labels and short captions are legitimate parts of figures. Sustained
    # sentence-like text in an otherwise vector-only crop needs review.
    long_words = sum(len(re.sub(r"\W", "", w[4])) >= 4 for w in words)
    return len(words) >= 18 and long_words >= 9


def mark_scope_conflicts(items):
    """Flag overlapping question ownership before any item is auto-approved."""
    owned = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        for span in item.get("source_spans") or []:
            if isinstance(span, dict) and (box := rect(span.get("bbox"))) is not None:
                owned.append((index, span.get("page"), box))
    for pos, (index, page, box) in enumerate(owned):
        for other, other_page, other_box in owned[pos+1:]:
            if index != other and page == other_page and max(coverage(box, other_box), coverage(other_box, box)) > .2:
                for affected in (index, other):
                    items[affected].setdefault("_visual_scope_issues", [])
                    if "diagram_question_scope_overlap" not in items[affected]["_visual_scope_issues"]:
                        items[affected]["_visual_scope_issues"].append("diagram_question_scope_overlap")
    return items


def ground_question_spans(items, document):
    """Ground born-digital question ownership in its printed number and text.

    Vision coordinates are useful proposals, but even well-formed model spans
    can omit a right-hand option column. Never enlarge a span from figure boxes
    alone: first find the actual printed question with matching stem words.
    """
    known={str(i.get("number", "")).strip().strip("Qq.)"):set(re.findall(r"[a-z]{3,}",str(i.get("stem_markdown", "")).lower())[:12])
           for i in items if isinstance(i,dict)}
    for item in items:
        if not isinstance(item,dict):
            continue
        number=re.fullmatch(r"(?:[Qq]\s*)?(\d{1,4})[.)]?",str(item.get("number","")).strip())
        if not number:
            continue
        stem_words=set(re.findall(r"[a-z]{3,}",str(item.get("stem_markdown","")).lower())[:12])
        grounded=[]
        for span in item.get("source_spans") or []:
            page_no=page_number(span.get("page"),document) if isinstance(span,dict) else None
            if page_no is None:
                grounded.append(span);continue
            with unrotated(document[page_no-1]) as page:
                words=page.get_text("words",sort=True)
                anchors=[]
                for word in words:
                    match=re.fullmatch(r"(?:[Qq])?(\d{1,4})[.)]?",word[4])
                    if match and word[7]==0:
                        line=" ".join(w[4] for w in words if w[5:7]==word[5:7] and w[7]>word[7])
                        # Unparenthesized numeric option labels can also start
                        # lines. Require question-text evidence before using
                        # one to truncate a neighboring question's ownership.
                        matching_text=len(known.get(match.group(1),set()).intersection(re.findall(r"[a-z]{3,}",line.lower())))>=2
                        if matching_text or word[4].lower().startswith("q"):
                            anchors.append((match.group(1),word))
                matching=[]
                for printed,word in anchors:
                    if printed != number.group(1):
                        continue
                    line=" ".join(w[4] for w in words if w[5:7]==word[5:7] and w[7]>word[7])
                    if len(stem_words.intersection(re.findall(r"[a-z]{3,}",line.lower())))>=2:
                        matching.append(word)
                if len(matching)!=1:
                    grounded.append(span);continue
                anchor=matching[0]
                column_x=sorted({w[0] for _,w in anchors if abs(w[0]-anchor[0])>page.rect.width*.25})
                right=min((x-8 for x in column_x if x>anchor[0]),default=page.rect.width-12)
                next_y=min((w[1]-4 for _,w in anchors if abs(w[0]-anchor[0])<30 and w[1]>anchor[1]+12),default=page.rect.height-12)
                grounded.append({"page":page_no,"bbox":[max(0,anchor[0]-5),max(0,anchor[1]-5),right,next_y]})
                item["_source_geometry"]="native_question_anchor"
        item["source_spans"]=grounded
    return items


def image_regions(document, pages=None):
    result = []
    for number, page in enumerate(document, 1):
        if pages is not None and number not in pages:
            continue
        seen = set()
        with unrotated(page):
            for info in page.get_image_info():
                box = rect(info["bbox"])
                if box is None:
                    continue
                box &= page.rect
                key = tuple(round(v, 1) for v in box)
                if key in seen or box.get_area() < 100 or min(box.width, box.height) < 6:
                    continue
                seen.add(key)
                result.append({"page": number, "bbox": list(box), "alt": "Source image", "origin": "raster"})
    return result


def vector_regions(document, pages=None):
    result = []
    for number, page in enumerate(document, 1):
        if pages is not None and number not in pages:
            continue
        with unrotated(page):
            for box in _clusters(_paths(page)):
                if min(box.width, box.height) < 12 or box.get_area() < 200:
                    continue
                if box.get_area() > page.rect.get_area() * .55 or _prose(page, box):
                    continue
                if box.width > page.rect.width * .65 and re.search(r"\b(?:instructions|answer all questions|duration|total marks)\b", page.get_textbox(box), re.I):
                    continue
                # Include adjacent labels, bounded to a small local neighborhood.
                expanded = box + (-12, -12, 12, 12)
                for word in page.get_text("words"):
                    label = fitz.Rect(word[:4])
                    if coverage(label, expanded) > .8 and len(word[4]) <= 12:
                        box |= label
                box = (box + (-3, -3, 3, 3)) & page.rect
                result.append({"page": number, "bbox": list(box), "alt": "Source vector figure", "origin": "vector"})
    return result


def visual_context(document, start, end, page_texts, regions, max_bytes):
    """Geometry for every visible page; images for graphics and scan pages.

    Includes continuation pages independently of text length on primary pages.
    A text-rich page with visual choices must not become a text-only request.
    """
    content = []
    total = 0
    for index in range(start, min(len(document), end + 1)):
        page = document[index]
        with unrotated(page):
            candidates = [r for r in regions if r["page"] == index + 1]
            blocks = [{"bbox": [round(v, 1) for v in b[:4]], "text": b[4][:1500]}
                      for b in page.get_text("blocks") if len(b) > 6 and b[6] == 0]
            layout = {"page": index + 1, "width_points": page.rect.width,
                      "height_points": page.rect.height, "coordinate_space": "pdf_points_unrotated",
                      "blocks": blocks, "figure_candidates": candidates}
            content.append({"type": "text", "text": "SOURCE LAYOUT " + json.dumps(layout, ensure_ascii=False)})
            # Paths that were excluded from safe automatic grouping may still
            # be meaningful graphics. Show them to vision rather than losing
            # them. Tiny formula rules alone do not trigger extra image work.
            visual = bool(candidates or _paths(page) or re.search(r"\b(?:figure|diagram|graph|circuit|shown)\b",page_texts[index],re.I)) or len(page_texts[index].strip()) < 240
            if visual:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
                png = pix.tobytes("png")
                total += len(png)
                if total > max_bytes:
                    raise ValueError("A rendered extraction batch exceeds the configured size limit")
                content.extend([
                    {"type": "text", "text": f"Source page {index + 1}: image {pix.width}x{pix.height} pixels; use the PDF-point layout above, not pixel coordinates."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                ])
    return content


def _spans(item, number, page):
    return [b for s in (item.get("source_spans") or []) if isinstance(s, dict)
            and s.get("page") == number and (b := rect(s.get("bbox"))) is not None
            and coverage(b, page.rect) >= .95]


def _option_at(page, box, spans, options):
    """Infer ownership only from unambiguous nearby printed option labels."""
    anchors = []
    for word in page.get_text("words"):
        label = re.fullmatch(r"\(?([A-Ha-h1-8])[.)]", word[4].strip())
        bounds = fitz.Rect(word[:4])
        if not label or not any(coverage(bounds, s) >= .9 for s in spans):
            continue
        value = label.group(1).lower()
        indices = [i for i, o in enumerate(options) if value == str(o.get("value", "")).lower()]
        if not indices:
            indices = [int(value) - 1 if value.isdigit() else ord(value) - ord("a")]
        if len(indices) == 1 and 0 <= indices[0] < len(options):
            anchors.append((indices[0], bounds))
    # A candidate covering multiple labels is a composite option strip, not
    # an individual option. Model segmentation or human review is required.
    # Labels immediately *below* an image belong to the next stacked option.
    # Expand toward preceding labels only, not into the following answer row.
    covered = {i for i, b in anchors if coverage(b,box + (-15,-15,0,0)) > .5}
    if len(covered) > 1:
        return "ambiguous"
    nearby = [(i, b) for i, b in anchors if -15 <= box.x0 - b.x1 <= 45
              and -18 <= box.y0 - b.y0 <= 35]
    return nearby[0][0] if len({i for i, _ in nearby}) == 1 else None


def extract_figures(item, document, regions, reviewed=False):
    """Return validated figures (including bytes) and explicit review reasons.

    Legacy model records without roles remain readable. Native candidates can
    recover an omitted stem figure, but never silently turn a full-page scan or
    a composite answer strip into a question diagram.
    """
    accepted, issues = [], list(item.get("_visual_scope_issues") or [])
    options = item.get("options") or []
    proposed = [(d, False) for d in (item.get("diagrams") or [])]
    if not reviewed:
        for span in item.get("source_spans") or []:
            if isinstance(span,dict) and (n:=page_number(span.get("page"),document)) is not None:
                with unrotated(document[n-1]) as page:
                    full_scan=any(r.get("origin")=="raster" and r["page"]==n
                                  and fitz.Rect(r["bbox"]).get_area()>page.rect.get_area()*.8 for r in regions)
                    if full_scan or len(page.get_text().strip())<40:
                        issues.append("diagram_scanned_source_review")
    for region in regions:
        if reviewed:
            break
        number = page_number(region.get("page"), document)
        box = rect(region.get("bbox"))
        if number is None or box is None:
            continue
        with unrotated(document[number - 1]) as page:
            spans = _spans(item, number, page)
            if not any(coverage(box, s) >= .95 for s in spans):
                continue
            # Avoid a duplicate candidate around a model-owned figure. Larger
            # composite raster images may legitimately contain several choices.
            if any(isinstance(d, dict) and d.get("page") == number
                   and (b := rect(d.get("bbox"))) is not None
                   and (coverage(b, box) >= .7 or coverage(box, b) >= .7)
                   for d, _ in proposed):
                continue
            # An unclaimed graphic is a possible model omission, not license
            # to silently attach an unrelated formula or picture to the stem.
            if not (any(o.get("requires_diagram") for o in options)
                    or re.search(r"\b(?:diagram|figure|graph|circuit|shown)\b",str(item.get("stem_markdown") or ""),re.I)):
                issues.append("diagram_unassigned_native_candidate")
                continue
            proposed.append((region, True))

    for raw, native in proposed:
        if not isinstance(raw, dict):
            issues.append("diagram_invalid_metadata")
            continue
        number = page_number(raw.get("page"), document)
        box = rect(raw.get("bbox"))
        if number is None or box is None:
            issues.append("diagram_invalid_geometry")
            continue
        with unrotated(document[number - 1]) as page:
            if coverage(box, page.rect) < .95:
                issues.append("diagram_outside_page")
                continue
            box &= page.rect
            spans = _spans(item, number, page)
            if not spans or not any(coverage(box, s) >= .95 for s in spans):
                issues.append("diagram_outside_question")
                continue
            if not reviewed:
                # Recover nearby labels/padding from a strongly matching native
                # figure without swallowing neighboring choices or prose.
                matches=[rect(r["bbox"]) for r in regions if r["page"]==number and r.get("origin")=="vector"
                         and coverage(box,fitz.Rect(r["bbox"]))>.35 and coverage(fitz.Rect(r["bbox"]),box)>.5]
                if len(matches)==1:
                    complete=box | matches[0]
                    if any(coverage(complete,s)>=.95 for s in spans) and coverage(complete,box+(-24,-24,24,24))>.98:
                        box=complete
            if min(box.width, box.height) < 6 or box.get_area() > page.rect.get_area() * .65:
                issues.append("diagram_not_bounded")
                continue
            role = raw.get("role", "question")
            option_index = raw.get("option_index")
            inferred = _option_at(page, box, spans, options)
            if inferred == "ambiguous":
                issues.append("diagram_option_ownership_uncertain")
                continue
            if role=="option" and isinstance(inferred,int) and option_index != inferred:
                issues.append("diagram_option_owner_mismatch")
                continue
            if native or "role" not in raw:
                if inferred == "ambiguous":
                    issues.append("diagram_option_ownership_uncertain")
                    continue
                if isinstance(inferred, int):
                    role, option_index = "option", inferred
                elif native and any(o.get("requires_diagram") or not str(o.get("markdown", "")).strip() for o in options):
                    issues.append("diagram_option_ownership_uncertain")
                    continue
            if role not in {"question", "option", "solution"} or (role == "option" and (
                isinstance(option_index, bool) or not isinstance(option_index, int)
                or not 0 <= option_index < len(options)
            )) or (role != "option" and option_index is not None):
                issues.append("diagram_invalid_owner")
                continue
            # The same pixels must not be duplicated or assigned to two choices.
            duplicates = [d for d in accepted if d["page"] == number
                          and max(coverage(box, fitz.Rect(d["bbox"])), coverage(fitz.Rect(d["bbox"]), box)) > .85
                          and min(coverage(box, fitz.Rect(d["bbox"])), coverage(fitz.Rect(d["bbox"]), box)) > .35]
            if duplicates:
                if any((d["role"], d["option_index"]) != (role, option_index) for d in duplicates):
                    issues.append("diagram_conflicting_owner")
                continue
            raster = any(r.get("origin") == "raster" and r["page"] == number
                         and coverage(box, fitz.Rect(r["bbox"])) > .8 for r in regions)
            if not reviewed and raster and _prose(page, box):
                issues.append("diagram_excessive_prose")
                continue
            if not reviewed and len(page.get_text().strip()) < 40:
                issues.append("diagram_scanned_source_review")
            paths = [p for p in _paths(page) if (p & box).get_area() > 1]
            if not reviewed and not raster and (not paths or _prose(page, box)):
                issues.append("diagram_text_or_no_graphics")
                continue
            # A box that cuts vector objects is likely missing part of a figure.
            if not reviewed and not raster and any(coverage(p, box + (-3, -3, 3, 3)) < .85 for p in paths):
                issues.append("diagram_clipped_graphics")
                continue
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=box, colorspace=fitz.csRGB, alpha=False)
            # Check actual rendered pixels, including white raster assets.
            gray = fitz.Pixmap(fitz.csGRAY, pix)
            if not any(v < 235 for v in gray.samples):
                issues.append("diagram_blank")
                continue
            accepted.append({"page": number, "bbox": [round(v, 3) for v in box],
                             "alt": str(raw.get("alt") or "Source figure")[:240],
                             "role": role, "option_index": option_index,
                             "extraction": VERSION, "coordinate_space": "pdf_points_unrotated",
                             "png": pix.tobytes("png")})
    for index, option in enumerate(options):
        visual = option.get("requires_diagram") or not str(option.get("markdown") or "").strip()
        if visual and not any(d["role"] == "option" and d["option_index"] == index for d in accepted):
            issues.append(f"option_diagram_missing:{index}")
    if not reviewed and not accepted and re.search(r"\b(?:diagram|figure|graph|circuit|shown)\b", str(item.get("stem_markdown") or ""), re.I):
        issues.append("diagram_expected_missing")
    return accepted, list(dict.fromkeys(issues))


def save_figures(item, document, regions, prefix, put, reviewed=False):
    figures, issues = extract_figures(item, document, regions, reviewed=reviewed)
    saved = []
    for index, figure in enumerate(figures):
        png = figure.pop("png")
        key = f"{prefix}-{figure['page']}-{index}-{hashlib.sha256(png).hexdigest()[:16]}.png"
        try:
            put(key, png, "image/png")
        except Exception:
            # Do not discard the question or hide a partially missing option set.
            issues.append(f"diagram_storage_failed:{index}")
            continue
        saved.append({**figure, "object_key": key})
    return saved, list(dict.fromkeys(issues))


def is_visual_issue(issue):
    return str(issue).startswith(("diagram_", "option_diagram_"))


def option_media_issues(options, diagrams):
    issues = []
    for index, option in enumerate(options or []):
        if (option.get("requires_diagram") or not str(option.get("markdown") or option.get("text") or "").strip()) and not any(
            d.get("role") == "option" and d.get("option_index") == index for d in diagrams or []
        ):
            issues.append(f"option_diagram_missing:{index}")
    for diagram in diagrams or []:
        index = diagram.get("option_index")
        if diagram.get("role") == "option" and (isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(options or [])):
            issues.append("diagram_invalid_owner")
    return issues
