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
SYSTEM="""You extract educational examination papers into teacher-reviewable records. Return only JSON matching the supplied schema.

Read the full page image and native text together. A question starts at its printed number and ends immediately before the next printed number; never emit fragments, page headers, instructions, or a standalone option as a question. Its source_spans must cover the complete source area for that question, including its options and any associated figure. Preserve ordinary prose as readable Markdown. Preserve every equation as valid LaTeX wrapped in $...$ (or $$...$$ for a display equation), including its backslashes. Each option must be one complete semantic option in its markdown field, never JSON encoded as text. The separately supplied answer-key document is authoritative: map its answer to the question option, and return its corresponding worked explanation in solution_markdown whenever available. If an illustration, ray diagram, graph, circuit, table, or labelled figure belongs to a question, add an exact page and PDF-point bounding box for it. Do not make a whole-question screenshot a diagram. If a value cannot be read confidently, leave the relevant answer null and lower confidence. Confidence is a number from 0 to 1."""
OPTION_SCHEMA={"type":"object","additionalProperties":False,"required":["value","markdown"],"properties":{"value":{"type":"string"},"markdown":{"type":"string"}}}
DIAGRAM_SCHEMA={"type":"object","additionalProperties":False,"required":["page","bbox","alt"],"properties":{"page":{"type":"integer","minimum":1},"bbox":{"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4},"alt":{"type":"string"}}}
SPAN_SCHEMA={"type":"object","additionalProperties":False,"required":["page","bbox"],"properties":{"page":{"type":"integer","minimum":1},"bbox":{"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4}}}
SCHEMA={"name":"exam_questions","strict":True,"schema":{"type":"object","additionalProperties":False,"required":["questions"],"properties":{"questions":{"type":"array","items":{"type":"object","additionalProperties":False,"required":["number","kind","stem_markdown","options","answer","solution_markdown","confidence","source_spans","diagrams"],"properties":{"number":{"type":"string"},"kind":{"type":"string","enum":["single_choice","multiple_choice","numerical","integer","assertion_reason","subjective"]},"stem_markdown":{"type":"string"},"options":{"type":"array","items":OPTION_SCHEMA},"answer":{"type":["object","null"]},"solution_markdown":{"type":["string","null"]},"confidence":{"type":"number","minimum":0,"maximum":1},"source_spans":{"type":"array","items":SPAN_SCHEMA},"diagrams":{"type":"array","items":DIAGRAM_SCHEMA}}}}}}}

def normalize_item(item:dict)->dict:
    """Accept legacy provider variations, but never store presentation JSON as an option."""
    options=[]
    for index, option in enumerate(item.get("options") or []):
        if isinstance(option, str):
            options.append({"value":chr(97+index),"markdown":option})
        elif isinstance(option, dict):
            options.append({"value":str(option.get("value",chr(97+index))),"markdown":str(option.get("markdown") or option.get("text") or "")})
    item["options"]=options
    try:item["confidence"]=min(1.0,max(0.0,float(item.get("confidence",0))))
    except (TypeError,ValueError):item["confidence"]=0.0
    return item
def validate(item:dict):
    if not item.get("stem_markdown"):raise ValueError("Blank question stem")
    if item["kind"] in ("single_choice","multiple_choice") and len(item.get("options",[]))<2:raise ValueError("Choice question requires two options")
    if "$$$" in item["stem_markdown"]:raise ValueError("Malformed LaTex delimiters")

def infer_paper_title(reader:PdfReader, text:str, fallback:str)->str:
    metadata=reader.metadata or {}
    title=str(metadata.get("/Title") or "").strip()
    if title and title.lower() not in {"untitled","null"}:return title[:160]
    for line in text.splitlines()[:40]:
        clean=" ".join(line.split()).strip("-:| ")
        if 8<=len(clean)<=140 and not re.match(r"^(page|question|section)\s*\d",clean,re.I):return clean
    return fallback.rsplit(".",1)[0][:160]

def native_image_regions(document):
    """Find illustrations embedded in the PDF independently of model detection."""
    regions=[]
    for page_number,page in enumerate(document,1):
        seen=set()
        for image in page.get_images(full=True):
            xref=image[0]
            for rect in page.get_image_rects(xref):
                key=(page_number,round(rect.x0,1),round(rect.y0,1),round(rect.x1,1),round(rect.y1,1))
                # Ignore tiny raster marks; real diagrams have a meaningful visual area.
                if key in seen or rect.width*rect.height<900:continue
                seen.add(key)
                regions.append({"page":page_number,"bbox":[rect.x0,rect.y0,rect.x1,rect.y1],"alt":"Diagram extracted from the source paper"})
    return regions

def intersects(a,b):
    return max(a[0],b[0])<min(a[2],b[2]) and max(a[1],b[1])<min(a[3],b[3])

def diagrams_for_item(item:dict,native_regions:list[dict]):
    """Merge model-labelled figures with actual PDF image regions inside source spans."""
    diagrams=list(item.get("diagrams") or [])
    spans=item.get("source_spans") or []
    present={(d.get("page"),tuple(d.get("bbox") or [])) for d in diagrams if isinstance(d,dict)}
    for region in native_regions:
        if (region["page"],tuple(region["bbox"])) in present:continue
        for span in spans:
            if not isinstance(span,dict) or span.get("page")!=region["page"]:continue
            bbox=span.get("bbox") or []
            if len(bbox)==4 and intersects(region["bbox"],bbox):
                diagrams.append(region);present.add((region["page"],tuple(region["bbox"])));break
    return diagrams
@celery_app.task(bind=True,autoretry_for=(Exception,),retry_backoff=True,max_retries=2)
def extract_import(self, job_id:str):
    db:Session=SessionLocal(); job=db.get(ImportJob,job_id)
    try:
      job.status=ImportStatus.PROCESSING; job.extraction_meta={"phase":"Reading the question paper"}; db.commit(); doc=db.get(SourceDocument, job.question_document_id); raw=get(doc.object_key)
      reader=PdfReader(BytesIO(raw)); text="\n".join(f"\n--- PAGE {i+1} ---\n{p.extract_text() or ''}" for i,p in enumerate(reader.pages)); paper_title=infer_paper_title(reader,text,doc.filename)
      # Full rendered pages are the VLM input. We preserve mathematical layout and diagrams,
      # while native extraction acts only as context for reading order and recovery checks.
      rendered=fitz.open(stream=raw,filetype="pdf")
      native_regions=native_image_regions(rendered)
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
      job.extraction_meta={"phase":"Extracting structured questions"}; db.commit()
      import httpx
      payload={"model":settings().openrouter_model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":vision_content}],"response_format":{"type":"json_schema","json_schema":SCHEMA},"provider":{"require_parameters":True},"stream":False}
      response=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings().openrouter_api_key}","HTTP-Referer":"https://testnow.app","X-Title":"TestNow"},json=payload,timeout=settings().openrouter_timeout_seconds); response.raise_for_status()
      parsed=json.loads(response.json()["choices"][0]["message"]["content"]); numbers=set()
      job.extraction_meta={"phase":"Saving extracted questions"}; db.commit()
      for item in parsed["questions"]:
        item=normalize_item(item)
        validate(item)
        if item["number"] in numbers:raise ValueError("Duplicate source question number")
        numbers.add(item["number"]); kind=QuestionKind(item["kind"])
        diagrams=[]
        for n,diagram in enumerate(diagrams_for_item(item,native_regions)):
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
      job.status=ImportStatus.REVIEW; job.extraction_meta={"model":settings().openrouter_model,"paper_title":paper_title,"question_count":len(parsed["questions"]),"phase":"Ready for review"};db.commit()
    except Exception as exc:
      job.status=ImportStatus.FAILED;job.error=str(exc)[:2000];job.extraction_meta={**(job.extraction_meta or {}),"phase":"Import failed"};db.commit();raise
    finally:db.close()
