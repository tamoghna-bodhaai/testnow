"""Staged, auditable importer for super-admin global question banks."""
from __future__ import annotations

import base64, json, re, subprocess
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz, httpx
from pypdf import PdfReader
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import SessionLocal
from .models import ImportJob, ImportStatus, Question, QuestionBank, QuestionKind, QuestionScope, QuestionTaxonomy, SourceDocument, TaxonomyNode
from .security import uid
from .storage import get, put
from .tasks import SCHEMA, SYSTEM, diagrams_for_item, native_image_regions, normalize_item, validate


def _pdf_bytes(source: SourceDocument) -> bytes:
    raw=get(source.object_key)
    if source.media_type == "application/pdf": return raw
    with TemporaryDirectory(prefix="bank-docx-") as directory:
        path=Path(directory) / source.filename; path.write_bytes(raw)
        try:
            subprocess.run(["soffice","--headless","--convert-to","pdf","--outdir",directory,str(path)],check=True,capture_output=True,text=True,timeout=120)
        except subprocess.CalledProcessError as exc:
            detail=(exc.stderr or exc.stdout or "LibreOffice could not read the document").strip()
            raise ValueError(f"Could not convert {source.filename} to PDF: {detail[:500]}") from exc
        converted=path.with_suffix(".pdf")
        if not converted.exists(): raise ValueError("DOCX conversion did not produce a PDF")
        return converted.read_bytes()


def _node(db, bank_id: str, parent_id: str | None, kind: str, name: str) -> TaxonomyNode:
    name=re.sub(r"\s+"," ",name).strip()[:200] or f"Unmapped {kind}"
    row=db.query(TaxonomyNode).filter_by(bank_id=bank_id,parent_id=parent_id,kind=kind,name=name).one_or_none()
    if row:return row
    row=TaxonomyNode(id=uid(),bank_id=bank_id,parent_id=parent_id,kind=kind,name=name);db.add(row);db.flush();return row


def _issues(item: dict, answer: object, diagrams: list[dict]) -> list[str]:
    issues=[]
    if not answer and item.get("kind") != "subjective":issues.append("missing_answer")
    if float(item.get("confidence") or 0)<.75:issues.append("low_confidence")
    if "$$$" in str(item.get("stem_markdown") or ""):issues.append("invalid_latex")
    if item.get("diagrams") and not diagrams:issues.append("diagram_unavailable")
    if not str(item.get("chapter") or "").strip():issues.append("taxonomy_uncertain")
    return issues


def _answer_from_solution(item: dict) -> dict | None:
    """Recover an unambiguous MCQ key that the model wrote only in its solution.

    We deliberately accept just explicit statements such as "correct option is C";
    anything less clear remains a review flag.
    """
    if item.get("kind") not in {"single_choice", "assertion_reason"} or item.get("answer"):
        return item.get("answer")
    solution=str(item.get("solution_markdown") or "")
    match=re.search(r"(?:correct\s+(?:option|answer)|answer)\s*(?:is|:)?\s*\(?\s*([A-D])\s*\)?",solution,re.I)
    if not match:
        return None
    index=ord(match.group(1).upper())-ord("A")
    return {"option":index} if index < len(item.get("options") or []) else None


def _failure_message(exc: Exception) -> str:
    message=str(exc).strip() or exc.__class__.__name__
    if "Document exceeds" in message:
        return f"{message}. Split this source into smaller chapter files, then upload the parts."
    if isinstance(exc, IntegrityError) or "uq_question_bank_source_key" in message:
        return "This source was already imported into this bank. Its existing questions were kept; no duplicate was created."
    return message


def _control_state(db, job_id: str) -> str | None:
    """Read admin controls between page batches without holding stale ORM state."""
    db.expire_all()
    current=db.get(ImportJob,job_id)
    meta=(current.extraction_meta or {}) if current else {}
    if meta.get("cancel_requested"): return "cancelled"
    if meta.get("pause_requested"): return "paused"
    return None


def _save_questions(db, bank, job, source, subject, rendered, regions, items, seen_numbers):
    flagged=0; saved=0
    subject_node=_node(db,bank.id,None,"subject",subject)
    for item in items:
        item=normalize_item(item);validate(item);number=str(item["number"]).strip()
        # A source can contain a repeated number at a section boundary.  The
        # page span makes its identity stable, while repeated batch output is
        # discarded by the source-key check below.
        page=(item.get("source_spans") or [{}])[0].get("page")
        source_key=f"{source.sha256}:{number}:{page or 'unknown'}"
        if source_key in seen_numbers or db.query(Question.id).filter_by(bank_id=bank.id,source_key=source_key).first() or db.query(Question.id).filter_by(import_job_id=job.id,source_number=number).first():
            continue
        seen_numbers.add(source_key);kind=QuestionKind(item["kind"]);diagrams=[]
        for index,diagram in enumerate(diagrams_for_item(item,regions)):
            try:
                page_no=int(diagram["page"])-1;x0,y0,x1,y1=diagram["bbox"];page_obj=rendered[page_no];clip=fitz.Rect(float(x0),float(y0),float(x1),float(y1)) & page_obj.rect
                if clip.is_empty:continue
                key=f"bank-diagrams/{bank.id}/{job.id}/{number}-{page_no + 1}-{index}.png";put(key,page_obj.get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,alpha=False).tobytes("png"),"image/png")
                diagrams.append({"object_key":key,"page":page_no+1,"bbox":[x0,y0,x1,y1],"alt":diagram.get("alt","Question diagram")})
            except Exception:continue
        answer=_answer_from_solution(item);issues=_issues(item,answer,diagrams);flagged+=bool(issues)
        chapter=_node(db,bank.id,subject_node.id,"chapter",str(item.get("chapter") or "Unmapped chapter"));topic_name=str(item.get("topic") or "").strip();topic=_node(db,bank.id,chapter.id,"topic",topic_name) if topic_name else None
        interaction={"import_mode":"admin_bank","source_document_id":source.id,"source_filename":source.filename,"source_page":page,"ingestion_issues":issues,"model":settings().openrouter_model,"answer_inferred_from_solution":bool(answer and not item.get("answer"))}
        q=Question(id=uid(),owner_id=job.owner_id,bank_id=bank.id,scope=QuestionScope.GLOBAL,import_job_id=job.id,source_number=number,source_key=source_key,kind=kind,stem_markdown=item["stem_markdown"],options=item["options"],answer=answer,solution_markdown=item.get("solution_markdown"),confidence=item.get("confidence"),source_spans=item.get("source_spans") or [],diagrams=diagrams,interaction=interaction,approved_at=None if issues else datetime.now(timezone.utc))
        try:
            with db.begin_nested():db.add(q);db.flush()
        except IntegrityError:continue
        for taxonomy in (subject_node,chapter,topic):
            if taxonomy:db.add(QuestionTaxonomy(question_id=q.id,taxonomy_node_id=taxonomy.id))
        saved+=1
    return saved, flagged


def run_bank_import(job_id: str) -> None:
    db=SessionLocal();rendered=None
    try:
        job=db.get(ImportJob,job_id)
        if not job or not job.bank_id:raise ValueError("Bank import job not found")
        bank=db.get(QuestionBank,job.bank_id);source=db.get(SourceDocument,job.question_document_id)
        if not bank or not source:raise ValueError("Bank or source document not found")
        meta=dict(job.extraction_meta or {});subject=str(meta.get("subject") or "Unclassified")
        job.status=ImportStatus.PROCESSING;job.extraction_meta={**meta,"phase":"Reading source"};db.commit()
        raw=_pdf_bytes(source);reader=PdfReader(BytesIO(raw));page_count=len(reader.pages)
        if not page_count:raise ValueError("The document contains no pages")
        rendered=fitz.open(stream=raw,filetype="pdf");regions=native_image_regions(rendered)
        if job.answer_document_id:
            answer_source=db.get(SourceDocument,job.answer_document_id);answer_raw=_pdf_bytes(answer_source)
            answer_text="\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(answer_raw)).pages)
        else:answer_text=""
        if not settings().openrouter_api_key:raise RuntimeError("OPENROUTER_API_KEY is not configured")
        chunk_size=max(1,settings().bank_import_chunk_pages);ranges=list(range(0,page_count,chunk_size));numbers=set();flagged=0;saved=0
        for chunk_index,start in enumerate(ranges,1):
            control=_control_state(db,job_id)
            if control:
                job=db.get(ImportJob,job_id)
                job.status=ImportStatus.FAILED if control=="cancelled" else ImportStatus.UPLOADED
                job.error="Cancelled by administrator" if control=="cancelled" else None
                job.extraction_meta={**(job.extraction_meta or {}),"phase":"Cancelled" if control=="cancelled" else "Paused by administrator","processed_pages":start,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":chunk_index-1};db.commit();return
            end=min(page_count,start+chunk_size);native="\n".join(f"--- PAGE {i+1} ---\n{reader.pages[i].extract_text() or ''}" for i in range(start,end))
            content=[{"type":"text","text":f"Subject: {subject}. Extract only complete exam questions beginning on source pages {start+1}-{end}; preserve the original source page numbers. Detect chapter/subtopic. Native text:\n{native[:100000]}"}]
            for page_no in range(start,end):
                png=rendered[page_no].get_pixmap(matrix=fitz.Matrix(1,1),alpha=False).tobytes("png")
                content += [{"type":"text","text":f"Source page {page_no+1}"},{"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(png).decode()}}]
            if answer_text:content[0]["text"]+="\nANSWER/SOLUTION SOURCE (authoritative):\n"+answer_text[:50000]
            job.extraction_meta={**meta,"phase":f"Extracting pages {start+1}-{end} of {page_count}","processed_pages":start,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":chunk_index-1,"question_count":saved,"flagged_count":flagged};db.commit()
            payload={"model":settings().openrouter_model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":content}],"response_format":{"type":"json_schema","json_schema":SCHEMA},"provider":{"require_parameters":True},"stream":False}
            response=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings().openrouter_api_key}","HTTP-Referer":"https://testnow.app","X-Title":"TestNow Bank Importer"},json=payload,timeout=settings().openrouter_timeout_seconds);response.raise_for_status()
            parsed=json.loads(response.json()["choices"][0]["message"]["content"]);added, new_flags=_save_questions(db,bank,job,source,subject,rendered,regions,parsed.get("questions",[]),numbers);saved+=added;flagged+=new_flags
            job.extraction_meta={**meta,"phase":f"Saved pages {start+1}-{end} of {page_count}","processed_pages":end,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":chunk_index,"question_count":saved,"flagged_count":flagged};db.commit()
        job.status=ImportStatus.REVIEW if flagged else ImportStatus.APPROVED
        job.extraction_meta={**meta,"phase":"Needs anomaly review" if flagged else "Validated and ready for bank publication","processed_pages":page_count,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":len(ranges),"question_count":saved,"flagged_count":flagged,"model":settings().openrouter_model};db.commit()
    except Exception as exc:
        db.rollback()
        failed_job=db.get(ImportJob,job_id)
        if failed_job:
            failed_job.status=ImportStatus.FAILED;failed_job.error=_failure_message(exc)[:2000];failed_job.extraction_meta={**(failed_job.extraction_meta or {}),"phase":"Import failed"};db.commit()
        # This is a terminal per-document failure.  Do not re-raise it into
        # Celery's generic retry machinery or the UI will oscillate forever.
        return
    finally:
        if rendered:rendered.close()
        db.close()
