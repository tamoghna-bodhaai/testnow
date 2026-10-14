"""Staged, auditable importer for super-admin global question banks."""
from __future__ import annotations

import base64, json, re, subprocess
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz, httpx
from pypdf import PdfReader

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
        subprocess.run(["soffice","--headless","--convert-to","pdf","--outdir",directory,str(path)],check=True,capture_output=True,timeout=120)
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


def run_bank_import(job_id: str) -> None:
    db=SessionLocal();rendered=None
    try:
        job=db.get(ImportJob,job_id)
        if not job or not job.bank_id:raise ValueError("Bank import job not found")
        bank=db.get(QuestionBank,job.bank_id);source=db.get(SourceDocument,job.question_document_id)
        if not bank or not source:raise ValueError("Bank or source document not found")
        meta=dict(job.extraction_meta or {});subject=str(meta.get("subject") or "Unclassified")
        job.status=ImportStatus.PROCESSING;job.extraction_meta={**meta,"phase":"Reading source"};db.commit()
        raw=_pdf_bytes(source);reader=PdfReader(BytesIO(raw))
        if len(reader.pages)>settings().max_pdf_pages:raise ValueError(f"Document exceeds {settings().max_pdf_pages} pages")
        native="\n".join(f"--- PAGE {i+1} ---\n{p.extract_text() or ''}" for i,p in enumerate(reader.pages))
        rendered=fitz.open(stream=raw,filetype="pdf");regions=native_image_regions(rendered)
        content=[{"type":"text","text":f"Subject: {subject}. Detect the chapter and subtopic for every question. Extract only complete exam questions. Native text:\n{native[:100000]}"}]
        for page_no,page in enumerate(rendered,1):
            png=page.get_pixmap(matrix=fitz.Matrix(1.35,1.35),alpha=False).tobytes("png")
            content += [{"type":"text","text":f"Source page {page_no}"},{"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(png).decode()}}]
        if job.answer_document_id:
            answer_source=db.get(SourceDocument,job.answer_document_id);answer_raw=_pdf_bytes(answer_source)
            answer_text="\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(answer_raw)).pages)
            content[0]["text"]+="\nANSWER/SOLUTION SOURCE (authoritative):\n"+answer_text[:50000]
        if not settings().openrouter_api_key:raise RuntimeError("OPENROUTER_API_KEY is not configured")
        job.extraction_meta={**meta,"phase":"Extracting structured questions"};db.commit()
        payload={"model":settings().openrouter_model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":content}],"response_format":{"type":"json_schema","json_schema":SCHEMA},"provider":{"require_parameters":True},"stream":False}
        response=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings().openrouter_api_key}","HTTP-Referer":"https://testnow.app","X-Title":"TestNow Bank Importer"},json=payload,timeout=settings().openrouter_timeout_seconds);response.raise_for_status()
        parsed=json.loads(response.json()["choices"][0]["message"]["content"]);numbers=set();flagged=0
        subject_node=_node(db,bank.id,None,"subject",subject)
        for item in parsed.get("questions",[]):
            item=normalize_item(item);validate(item);number=str(item["number"]).strip()
            if number in numbers:raise ValueError("Duplicate source question number")
            numbers.add(number);kind=QuestionKind(item["kind"]);diagrams=[]
            for index,diagram in enumerate(diagrams_for_item(item,regions)):
                try:
                    page_no=int(diagram["page"])-1;x0,y0,x1,y1=diagram["bbox"];page=rendered[page_no];clip=fitz.Rect(float(x0),float(y0),float(x1),float(y1)) & page.rect
                    if clip.is_empty:continue
                    key=f"bank-diagrams/{bank.id}/{job.id}/{number}-{index}.png";put(key,page.get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,alpha=False).tobytes("png"),"image/png")
                    diagrams.append({"object_key":key,"page":page_no+1,"bbox":[x0,y0,x1,y1],"alt":diagram.get("alt","Question diagram")})
                except Exception:continue
            answer=item.get("answer");issues=_issues(item,answer,diagrams);flagged+=bool(issues)
            chapter=_node(db,bank.id,subject_node.id,"chapter",str(item.get("chapter") or "Unmapped chapter"));topic_name=str(item.get("topic") or "").strip();topic=_node(db,bank.id,chapter.id,"topic",topic_name) if topic_name else None
            interaction={"import_mode":"admin_bank","source_document_id":source.id,"source_page":(item.get("source_spans") or [{}])[0].get("page"),"ingestion_issues":issues,"model":settings().openrouter_model}
            q=Question(id=uid(),owner_id=job.owner_id,bank_id=bank.id,scope=QuestionScope.GLOBAL,import_job_id=job.id,source_number=number,source_key=f"{source.sha256}:{number}",kind=kind,stem_markdown=item["stem_markdown"],options=item["options"],answer=answer,solution_markdown=item.get("solution_markdown"),confidence=item.get("confidence"),source_spans=item.get("source_spans") or [],diagrams=diagrams,interaction=interaction,approved_at=None if issues else datetime.now(timezone.utc))
            db.add(q);db.flush()
            for taxonomy in (subject_node,chapter,topic):
                if taxonomy:db.add(QuestionTaxonomy(question_id=q.id,taxonomy_node_id=taxonomy.id))
        job.status=ImportStatus.REVIEW if flagged else ImportStatus.APPROVED
        job.extraction_meta={**meta,"phase":"Needs anomaly review" if flagged else "Validated and ready for bank publication","question_count":len(numbers),"flagged_count":flagged,"model":settings().openrouter_model};db.commit()
    except Exception as exc:
        if 'job' in locals() and job:
            job.status=ImportStatus.FAILED;job.error=str(exc)[:2000];job.extraction_meta={**(job.extraction_meta or {}),"phase":"Import failed"};db.commit()
        raise
    finally:
        if rendered:rendered.close()
        db.close()
