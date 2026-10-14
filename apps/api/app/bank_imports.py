"""Staged, auditable importer for super-admin global question banks."""
from __future__ import annotations

import base64, json, re, subprocess
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from pypdf import PdfReader
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import SessionLocal
from .models import ImportJob, ImportStatus, Question, QuestionBank, QuestionKind, QuestionScope, QuestionTaxonomy, SourceDocument, TaxonomyNode
from .security import uid
from .storage import get, put
from .tasks import SCHEMA, SYSTEM, answer_from_key, best_page_text, diagrams_for_item, embedded_answer_key_text, expected_questions, extraction_units, native_image_regions, native_vector_regions, page_unit_content, prepare_item_for_ingestion, request_extraction, unit_requires_vision


def _pdf_bytes(source: SourceDocument) -> bytes:
    raw=get(source.object_key)
    if source.media_type == "application/pdf": return raw
    with TemporaryDirectory(prefix="bank-docx-") as directory:
        path=Path(directory) / source.filename; path.write_bytes(raw)
        converted=path.with_suffix(".pdf")
        # Keep LibreOffice isolated per job.  Besides avoiding profile locks in
        # concurrent Celery workers, this lets us use the same stored-file
        # pipeline as the teacher PDF importer after conversion.
        profile=Path(directory) / "profile"
        result=subprocess.run([
            "soffice", "--headless", "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", directory, f"-env:UserInstallation={profile.as_uri()}", str(path),
        ],check=False,capture_output=True,text=True,timeout=120)
        # Some slim LibreOffice packages return non-zero after printing the
        # harmless javaldx warning, even though they successfully write the
        # PDF.  The artifact, not that warning, is the conversion result.
        if not converted.exists() or not converted.read_bytes().startswith(b"%PDF"):
            detail=(result.stderr or result.stdout or "LibreOffice could not read the document").strip()
            raise ValueError(f"Could not convert {source.filename} to PDF: {detail[:500]}")
        return converted.read_bytes()


def _node(db, bank_id: str, parent_id: str | None, kind: str, name: str) -> TaxonomyNode:
    name=re.sub(r"\s+"," ",name).strip()[:200] or f"Unmapped {kind}"
    # PostgreSQL's regular UNIQUE constraint treats NULL parent ids as distinct.
    # Older concurrent imports therefore left duplicate root subjects behind;
    # selecting one stable row keeps those historic records importable.
    row=db.query(TaxonomyNode).filter_by(bank_id=bank_id,parent_id=parent_id,kind=kind,name=name).order_by(TaxonomyNode.created_at,TaxonomyNode.id).first()
    if row:return row
    # Models often vary case, whitespace, ampersands, or punctuation between
    # pages.  Resolve those cosmetic variants to the existing taxonomy node so
    # the bank never grows duplicate chapters/subtopics during a long import.
    canonical=lambda value:re.sub(r"[^a-z0-9]+","",str(value).lower())
    key=canonical(name)
    for candidate in db.query(TaxonomyNode).filter_by(bank_id=bank_id,parent_id=parent_id,kind=kind).order_by(TaxonomyNode.created_at,TaxonomyNode.id):
        if canonical(candidate.name)==key:return candidate
    row=TaxonomyNode(id=uid(),bank_id=bank_id,parent_id=parent_id,kind=kind,name=name);db.add(row);db.flush();return row


def _issues(item: dict, answer: object, diagrams: list[dict]) -> list[str]:
    issues=list(item.get("_ingestion_issues") or [])
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


def _save_questions(db, bank, job, source, subject, rendered, regions, items, seen_numbers, owned_pages:set[int]|None=None, answer_text:str=""):
    flagged=0; saved=0; skipped=0
    # Serialize the small taxonomy/write phase per bank.  This prevents two
    # source jobs from creating a second NULL-parent subject simultaneously.
    db.query(QuestionBank).filter_by(id=bank.id).with_for_update().one()
    subject_node=_node(db,bank.id,None,"subject",subject)
    for raw_item in items:
        item,preflight_issues=prepare_item_for_ingestion(raw_item)
        if item is None:
            skipped+=1;continue
        item["_ingestion_issues"]=preflight_issues;number=str(item["number"]).strip()
        # A source can contain a repeated number at a section boundary.  The
        # page span makes its identity stable, while repeated batch output is
        # discarded by the source-key check below.
        page=(item.get("source_spans") or [{}])[0].get("page")
        if owned_pages is not None and int(page or min(owned_pages)) not in owned_pages:
            # The trailing context page is supplied to complete a split
            # question, but ownership remains with the page where it starts.
            continue
        page=page or (min(owned_pages) if owned_pages else None)
        source_key=f"{source.sha256}:{number}:{page or 'unknown'}"
        if source_key in seen_numbers or db.query(Question.id).filter_by(bank_id=bank.id,source_key=source_key).first():
            continue
        seen_numbers.add(source_key);kind=QuestionKind(item["kind"]);diagrams=[]
        for index,diagram in enumerate(diagrams_for_item(item,regions)):
            try:
                page_no=int(diagram["page"])-1;x0,y0,x1,y1=diagram["bbox"];page_obj=rendered[page_no];clip=fitz.Rect(float(x0),float(y0),float(x1),float(y1)) & page_obj.rect
                if clip.is_empty:continue
                key=f"bank-diagrams/{bank.id}/{job.id}/{number}-{page_no + 1}-{index}.png";put(key,page_obj.get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,alpha=False).tobytes("png"),"image/png")
                diagrams.append({"object_key":key,"page":page_no+1,"bbox":[x0,y0,x1,y1],"alt":diagram.get("alt","Question diagram")})
            except Exception:continue
        if not item.get("answer"):
            item["answer"]=answer_from_key(number,item.get("options") or [],answer_text)
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
    return saved, flagged, skipped


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
        rendered=fitz.open(stream=raw,filetype="pdf");regions=native_image_regions(rendered)+native_vector_regions(rendered)
        page_texts=best_page_text(raw,reader,rendered)
        if job.answer_document_id:
            answer_source=db.get(SourceDocument,job.answer_document_id);answer_raw=_pdf_bytes(answer_source)
            answer_text="\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(answer_raw)).pages)
        else:answer_text=""
        answer_lookup_text=answer_text or embedded_answer_key_text(page_texts)
        if not settings().openrouter_api_key:raise RuntimeError("OPENROUTER_API_KEY is not configured")
        # Resume from the last durable page checkpoint.  Questions from prior
        # batches remain in place and source-key checks make a replay safe.
        checkpoint=max(0,min(page_count,int(meta.get("processed_pages") or 0)))
        # A page is the durable ownership unit.  It prevents response-output
        # truncation on 100+ question source files and gives retries a precise
        # restart point without changing the surrounding Celery infrastructure.
        ranges=[unit for unit in extraction_units(page_texts) if unit[1]>checkpoint];numbers=set()
        saved=db.query(Question).filter_by(import_job_id=job.id).count()
        flagged=sum(1 for row in db.query(Question).filter_by(import_job_id=job.id) if (row.interaction or {}).get("ingestion_issues"));skipped=int(meta.get("skipped_count") or 0);failed_pages=list(meta.get("failed_pages") or [])
        for chunk_index,(start,end) in enumerate(ranges,1):
            if start<checkpoint:start=checkpoint
            control=_control_state(db,job_id)
            if control:
                job=db.get(ImportJob,job_id)
                job.status=ImportStatus.FAILED if control=="cancelled" else ImportStatus.UPLOADED
                job.error="Cancelled by administrator" if control=="cancelled" else None
                job.extraction_meta={**(job.extraction_meta or {}),"phase":"Cancelled" if control=="cancelled" else "Paused by administrator","processed_pages":start,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":chunk_index-1};db.commit();return
            content=[{"type":"text","text":f"Subject: {subject}. {page_unit_content(page_texts,start,answer_text,end)}"}]
            # Do not turn selectable PDF text into a visual OCR job.  The
            # source PDF itself supplies vector/raster diagram regions later;
            # vision is reserved for pages with no usable text layer.
            if unit_requires_vision(page_texts,start,end):
                for page_no in range(start,min(page_count,end+1)):
                    png=rendered[page_no].get_pixmap(matrix=fitz.Matrix(1,1),alpha=False).tobytes("png")
                    content += [{"type":"text","text":f"Source page {page_no+1}"},{"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(png).decode()}}]
            job.extraction_meta={**meta,"phase":f"Extracting pages {start+1}-{end} of {page_count}","processed_pages":start,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":chunk_index-1,"question_count":saved,"flagged_count":flagged,"skipped_count":skipped};db.commit()
            payload={"model":settings().openrouter_model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":content}],"response_format":{"type":"json_schema","json_schema":SCHEMA},"provider":{"require_parameters":True},"max_tokens":settings().openrouter_max_output_tokens,"stream":False}
            try:
                minimum=1 if expected_questions("\n".join(page_texts[start:end])) else 0
                parsed=request_extraction(payload,"TestNow Bank Importer",minimum)
            except Exception as page_error:
                failed_pages.append({"page":start+1,"error":str(page_error)[:300]})
                job.extraction_meta={**meta,"phase":f"Skipped unreadable page {start+1} of {page_count}","processed_pages":end,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":chunk_index,"question_count":saved,"flagged_count":flagged,"skipped_count":skipped,"failed_pages":failed_pages};meta=dict(job.extraction_meta);db.commit();continue
            added, new_flags, new_skipped=_save_questions(db,bank,job,source,subject,rendered,regions,parsed.get("questions",[]),numbers,set(range(start+1,end+1)),answer_lookup_text);saved+=added;flagged+=new_flags;skipped+=new_skipped
            job.extraction_meta={**meta,"phase":f"Saved pages {start+1}-{end} of {page_count}","processed_pages":end,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":chunk_index,"question_count":saved,"flagged_count":flagged,"skipped_count":skipped,"failed_pages":failed_pages};meta=dict(job.extraction_meta);db.commit()
        job.status=ImportStatus.REVIEW if flagged or failed_pages else ImportStatus.APPROVED
        phase="Needs anomaly review" if flagged or failed_pages else ("Completed with invalid records skipped" if skipped else "Validated and ready for bank publication")
        job.extraction_meta={**meta,"phase":phase,"processed_pages":page_count,"total_pages":page_count,"chunks_total":len(ranges),"chunks_complete":len(ranges),"question_count":saved,"flagged_count":flagged,"skipped_count":skipped,"failed_pages":failed_pages,"model":settings().openrouter_model};db.commit()
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
