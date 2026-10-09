import base64, json, re
from celery import Celery
from pypdf import PdfReader
from io import BytesIO
import fitz
from sqlalchemy.orm import Session
from .config import settings
from .db import SessionLocal
from .models import ImportJob, ImportStatus, Question, QuestionKind, SourceDocument
from .security import uid
from .storage import get, put

celery_app=Celery("testnow",broker=settings().redis_url,backend=settings().redis_url)
SYSTEM="""You extract educational examination papers. Return only strict JSON matching the supplied schema. Preserve all mathematical notation as valid LaTex delimited by $...$ or $$...$$. Never render a whole question as an image. Return diagrams only as page/bounding-box references. Keep each question, all options, and answer key separate."""
SCHEMA={"name":"exam_questions","strict":True,"schema":{"type":"object","additionalProperties":False,"required":["questions"],"properties":{"questions":{"type":"array","items":{"type":"object","additionalProperties":False,"required":["number","kind","stem_markdown","options","answer","solution_markdown","confidence","source_spans","diagrams"],"properties":{"number":{"type":"string"},"kind":{"type":"string","enum":["single_choice","multiple_choice","numerical","integer","assertion_reason","subjective"]},"stem_markdown":{"type":"string"},"options":{"type":"array"},"answer":{"type":["object","null"]},"solution_markdown":{"type":["string","null"]},"confidence":{"type":"number"},"source_spans":{"type":"array"},"diagrams":{"type":"array"}}}}}}}
def validate(item:dict):
    if not item.get("stem_markdown"):raise ValueError("Blank question stem")
    if item["kind"] in ("single_choice","multiple_choice") and len(item.get("options",[]))<2:raise ValueError("Choice question requires two options")
    if "$$$" in item["stem_markdown"]:raise ValueError("Malformed LaTex delimiters")
@celery_app.task(bind=True,autoretry_for=(Exception,),retry_backoff=True,max_retries=2)
def extract_import(self, job_id:str):
    db:Session=SessionLocal(); job=db.get(ImportJob,job_id)
    try:
      job.status=ImportStatus.PROCESSING; db.commit(); doc=db.get(SourceDocument, job.question_document_id); raw=get(doc.object_key)
      reader=PdfReader(BytesIO(raw)); text="\n".join(f"\n--- PAGE {i+1} ---\n{p.extract_text() or ''}" for i,p in enumerate(reader.pages))
      # Full rendered pages are the VLM input. We preserve mathematical layout and diagrams,
      # while native extraction acts only as context for reading order and recovery checks.
      rendered=fitz.open(stream=raw,filetype="pdf")
      vision_content=[{"type":"text","text":f"Extract every question from this paper. Native layout context:\n{text[:100000]}"}]
      for page_number,page in enumerate(rendered,1):
          png=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes("png")
          vision_content.append({"type":"text","text":f"Source page {page_number}"})
          vision_content.append({"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(png).decode()}})
      key_text=""
      if job.answer_document_id:
          key_doc=db.get(SourceDocument,job.answer_document_id)
          key_text="\nANSWER KEY DOCUMENT:\n"+"\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(get(key_doc.object_key))).pages)
          vision_content[0]["text"]+=key_text[:40000]
      if not settings().openrouter_api_key: raise RuntimeError("OPENROUTER_API_KEY is not configured")
      import httpx
      payload={"model":settings().openrouter_model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":vision_content}],"response_format":{"type":"json_schema","json_schema":SCHEMA},"provider":{"require_parameters":True},"stream":False}
      response=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings().openrouter_api_key}","HTTP-Referer":"https://testnow.app","X-Title":"TestNow"},json=payload,timeout=settings().openrouter_timeout_seconds); response.raise_for_status()
      parsed=json.loads(response.json()["choices"][0]["message"]["content"]); numbers=set()
      for item in parsed["questions"]:
        validate(item)
        if item["number"] in numbers:raise ValueError("Duplicate source question number")
        numbers.add(item["number"]); kind=QuestionKind(item["kind"])
        diagrams=[]
        for n,diagram in enumerate(item["diagrams"]):
          # VLM gives {page, bbox:[x0,y0,x1,y1]} in original PDF-point coordinates.
          try:
            page_no=int(diagram["page"])-1; x0,y0,x1,y1=diagram["bbox"]; page=rendered[page_no]
            clip=fitz.Rect(float(x0),float(y0),float(x1),float(y1)) & page.rect
            if clip.is_empty: raise ValueError("empty diagram box")
            png=page.get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,alpha=False).tobytes("png")
            key=f"diagrams/{job.id}/{item['number']}-{n}.png";put(key,png,"image/png")
            diagrams.append({"object_key":key,"page":page_no+1,"bbox":[x0,y0,x1,y1],"alt":diagram.get("alt","Question diagram")})
          except Exception: continue
        db.add(Question(id=uid(),owner_id=job.owner_id,import_job_id=job.id,source_number=item["number"],kind=kind,stem_markdown=item["stem_markdown"],options=item["options"],answer=item["answer"],solution_markdown=item["solution_markdown"],confidence=item["confidence"],source_spans=item["source_spans"],diagrams=diagrams))
      job.status=ImportStatus.REVIEW; job.extraction_meta={"model":settings().openrouter_model,"question_count":len(parsed["questions"])};db.commit()
    except Exception as exc:
      job.status=ImportStatus.FAILED;job.error=str(exc)[:2000];db.commit();raise
    finally:db.close()
