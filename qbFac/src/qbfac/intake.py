"""Safe, deterministic first-pass inspection of raw question-bank archives.

This module deliberately establishes source evidence before any OCR or model
call. It does not guess questions from page screenshots.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz


ALLOWED_SUFFIXES = {".pdf", ".docx"}
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 5_000


def _role(name: str) -> str:
    normalized = name.lower().replace("_", " ").replace("-", " ")
    if re.search(r"\b(answer\s*(key|sheet)|keys?)\b", normalized):
        return "answer_key"
    if re.search(r"\b(solution|solved)\b", normalized):
        return "solutions"
    return "questions"


def _inspect_pdf(raw: bytes) -> dict[str, Any]:
    document = fitz.open(stream=raw, filetype="pdf")
    try:
        text = "".join(page.get_text("text") for page in document)
        image_count = sum(len(page.get_images(full=True)) for page in document)
        return {
            "kind": "digital_pdf" if len(text.strip()) >= 40 else "scanned_pdf",
            "page_count": document.page_count,
            "native_text_characters": len(text.strip()),
            "embedded_image_count": image_count,
            "needs_ocr": len(text.strip()) < 40,
        }
    finally:
        document.close()


def _inspect_docx(raw: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(BytesIO(raw)) as document:
        media = [name for name in document.namelist() if name.startswith("word/media/")]
        xml = document.read("word/document.xml") if "word/document.xml" in document.namelist() else b""
    text = re.sub(r"<[^>]+>", " ", xml.decode("utf-8", errors="ignore"))
    return {
        "kind": "docx",
        "page_count": None,
        "native_text_characters": len(re.sub(r"\s+", " ", text).strip()),
        "embedded_image_count": len(media),
        "needs_ocr": False,
    }


def inspect_source_archive(archive: Path) -> dict[str, Any]:
    """Return a manifest for a raw ZIP without extracting files to disk.

    The manifest drives source-family routing. It is intentionally cheap and
    deterministic, so a corpus can be clustered before expensive processing.
    """
    with zipfile.ZipFile(archive) as bundle:
        members = [entry for entry in bundle.infolist() if not entry.is_dir()]
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("source archive has too many files")
        documents = []
        for entry in members:
            member_path = Path(entry.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"unsafe archive path: {entry.filename}")
            suffix = member_path.suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                continue
            if entry.file_size > MAX_DOCUMENT_BYTES:
                raise ValueError(f"source document exceeds size limit: {entry.filename}")
            raw = bundle.read(entry)
            inspection = _inspect_pdf(raw) if suffix == ".pdf" else _inspect_docx(raw)
            documents.append({
                "source_id": hashlib.sha256(raw).hexdigest(),
                "filename": entry.filename,
                "role": _role(entry.filename),
                "size_bytes": len(raw),
                **inspection,
            })
    if not documents:
        raise ValueError("source archive contains no PDF or DOCX material")
    return {"archive": archive.name, "documents": documents}
