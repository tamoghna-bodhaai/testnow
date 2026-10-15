import base64, json, re, time
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
from .figures import DIAGRAM_SCHEMA, OPTION_SCHEMA, VISUAL_INSTRUCTIONS, image_regions, vector_regions, save_figures, visual_context, model_visual_request, canonical_visual_response, mark_scope_conflicts, ground_question_spans, rect

celery_app=Celery("testnow",broker=settings().redis_url,backend=settings().redis_url)
SYSTEM="""You extract educational examination papers into teacher-reviewable records. Return only JSON matching the supplied schema.

Read the full page image and native text together. A question starts at its printed number and ends immediately before the next printed number; never emit fragments, page headers, instructions, or a standalone option as a question. Its source_spans must cover the complete source area for that question, including its options and any associated figure. Preserve ordinary prose as readable Markdown. Preserve every equation as valid LaTeX wrapped in $...$ (or $$...$$ for a display equation), including its backslashes. Each option must be one complete semantic option in its markdown field, never JSON encoded as text. If choices are visibly labelled (A)-(D), (1)-(4), or similar, this is an objective question: return every option and classify it as single_choice or multiple_choice; do not call it subjective merely because an answer key is absent. The separately supplied answer-key document is authoritative: map its answer to the question option, and return its corresponding worked explanation in solution_markdown whenever available. Reuse the exact same chapter/topic spelling for repeated headings; do not make a question wording into a topic or subtopic. Format each solution as readable Markdown: use blank paragraphs, numbered steps for sequential working, and display math for substantial equations. Never flatten a multi-step solution into one paragraph. If an illustration, ray diagram, graph, circuit, table, or labelled figure belongs to a question, add an exact page and PDF-point bounding box for it. Do not make a whole-question screenshot a diagram. If a value cannot be read confidently, leave the relevant answer null and lower confidence. Confidence is a number from 0 to 1."""
SYSTEM += VISUAL_INSTRUCTIONS
SPAN_SCHEMA={"type":"object","additionalProperties":False,"required":["page","bbox"],"properties":{"page":{"type":"integer","minimum":1},"bbox":{"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4}}}
SCHEMA={"name":"exam_questions","strict":True,"schema":{"type":"object","additionalProperties":False,"required":["questions"],"properties":{"questions":{"type":"array","items":{"type":"object","additionalProperties":False,"required":["number","chapter","topic","kind","stem_markdown","options","answer","solution_markdown","confidence","source_spans","diagrams"],"properties":{"number":{"type":"string"},"chapter":{"type":"string"},"topic":{"type":"string"},"kind":{"type":"string","enum":["single_choice","multiple_choice","numerical","integer","matrix_match","assertion_reason","subjective"]},"stem_markdown":{"type":"string"},"options":{"type":"array","items":OPTION_SCHEMA},"answer":{"type":["object","null"]},"solution_markdown":{"type":["string","null"]},"confidence":{"type":"number","minimum":0,"maximum":1},"source_spans":{"type":"array","items":SPAN_SCHEMA},"diagrams":{"type":"array","items":DIAGRAM_SCHEMA}}}}}}}

_INLINE_OPTION=re.compile(r"(?:^|\n)\s*(?:\(?([A-Ha-h])\)|([A-Ha-h])[.)])\s*(.+?)(?=(?:\n\s*(?:\(?[A-Ha-h]\)|[A-Ha-h][.)])\s*)|$)",re.S)
# Coaching books frequently print a question number as `1 Let ...` rather
# than `1. Let ...`.  The old expression only saw the latter (and therefore
# considered the first real Arihant question page empty).  Options do not
# match because they are parenthesised letters, not a bare question number.
_QUESTION_START=re.compile(
    r"(?mi)^\s*(?:q(?:uestion)?\s*)?\d{1,4}(?:\s*[.)])?\s+"
    r"(?=(?:let|if|for|find|given|which|what|the|in|from|prove|show|all|"
    r"calculate|determine|evaluate|match|assertion|statement|read)\b)"
)
_ANSWER_HEADING=re.compile(r"\b(?:answer\s*key|answers?\b|solutions?\b|answer\s*&\s*solution)",re.I)
_ANSWER_CELL=re.compile(r"(?<!\d)\d{1,4}(?!\d)\s*(?:[.)-]\s*)?\(?[A-H]\)?(?=\s|$)",re.I)

def options_embedded_in_stem(stem:object)->tuple[str,list[dict]]:
    """Recover choices when a provider flattened a visibly labelled MCQ.

    This is intentionally conservative: only labelled, newline-separated
    choices are removed from the stem.  It turns a review-hostile subjective
    record back into a usable MCQ without guessing at unlabelled prose.
    """
    value=str(stem or "")
    matches=list(_INLINE_OPTION.finditer(value))
    if len(matches)<2:return value,[]
    labels=[(match.group(1) or match.group(2)).lower() for match in matches]
    if len(set(labels))!=len(labels):return value,[]
    options=[]
    for match,label in zip(matches,labels):
        markdown=match.group(3).strip()
        if not markdown:return value,[]
        options.append({"value":label,"markdown":markdown})
    return value[:matches[0].start()].rstrip(),options

def normalize_item(item:dict)->dict:
    """Accept legacy provider variations, but never store presentation JSON as an option."""
    options=[]
    spans=[]
    for span in item.get("source_spans") or []:
        if not isinstance(span,dict) or isinstance(span.get("page"),bool) or not isinstance(span.get("page"),int) or span["page"]<1 or rect(span.get("bbox")) is None:
            item.setdefault("_visual_scope_issues",[]).append("diagram_invalid_source_geometry")
            continue
        spans.append(span)
    if "source_spans" in item:
        item["source_spans"]=spans
        if not spans:item.setdefault("_visual_scope_issues",[]).append("diagram_source_missing")
    for index, option in enumerate(item.get("options") or []):
        if isinstance(option, str):
            options.append({"value":chr(97+index),"markdown":option})
        elif isinstance(option, dict):
            clean={"value":str(option.get("value",chr(97+index))),"markdown":str(option.get("markdown") or option.get("text") or "")}
            if "requires_diagram" in option:clean["requires_diagram"]=bool(option["requires_diagram"])
            options.append(clean)
    if len(options)<2:
        stem,recovered=options_embedded_in_stem(item.get("stem_markdown"))
        if recovered:
            item["stem_markdown"]=stem;options=recovered
            if item.get("kind")=="subjective":item["kind"]="single_choice"
    visual_indices={d.get("option_index") for d in (item.get("diagrams") or []) if isinstance(d,dict)
                    and d.get("role")=="option" and isinstance(d.get("option_index"),int)
                    and not isinstance(d.get("option_index"),bool) and 0<=d["option_index"]<8}
    if visual_indices and max(visual_indices)>=len(options):
        count=max(visual_indices)+1
        if visual_indices | set(range(len(options))) == set(range(count)) and count>=2:
            options += [{"value":chr(97+i),"markdown":"","requires_diagram":True} for i in range(len(options),count)]
            item["_visual_options_recovered"]=True
            if item.get("kind")=="subjective":item["kind"]="single_choice"
    item["options"]=options
    item["answer"]=normalize_answer(item.get("answer"),options)
    try:item["confidence"]=min(1.0,max(0.0,float(item.get("confidence",0))))
    except (TypeError,ValueError):item["confidence"]=0.0
    return item
def normalize_answer(answer,options:list[dict]):
    """Convert provider answer variants to the response shape used for scoring."""
    if not isinstance(answer,dict):return None
    # Matrix answers do not use the multiple-choice option array.  Preserve the
    # labelled row-to-column map in the exact shape expected by the exam UI.
    if isinstance(answer.get("matches"),dict):
        matches={}
        for row,choices in answer["matches"].items():
            if isinstance(choices,list) and str(row).strip():
                matches[str(row).strip()]=sorted({str(choice).strip() for choice in choices if str(choice).strip()})
        return {"matches":matches} if matches else None
    raw=answer.get("options",answer.get("option",answer.get("value",answer.get("correct_option"))))
    if isinstance(raw,list):
        selected=[]
        for item in raw:
            if isinstance(item,int) and 0<=item<len(options): selected.append(item);continue
            if isinstance(item,str):
                needle=item.strip().lower()
                for index,option in enumerate(options):
                    if needle in {str(index),str(index+1),chr(65+index).lower(),str(option.get("value","")).lower()}:
                        selected.append(index);break
        return {"options":sorted(set(selected))} if selected else None
    if isinstance(raw,int) and 0<=raw<len(options):return {"option":raw}
    if isinstance(raw,str):
        needle=raw.strip().lower()
        for index,option in enumerate(options):
            if needle in {str(index),str(index+1),chr(65+index).lower(),str(option.get("value","")).lower()}:
                return {"option":index}
    return {"value":raw} if raw is not None else None

def answer_from_key(number:object, options:list[dict], key_text:str)->dict|None:
    """Map an explicit numbered answer-key entry without guessing solutions.

    Long answer keys often lie beyond the model prompt cap.  This native
    fallback accepts only a question-number followed immediately by one of the
    displayed A-H labels, so prose in a solution cannot become a false key.
    """
    if not key_text:return None
    prefix=r"(?<!\d)(?:q(?:uestion)?\s*)?"+re.escape(str(number).strip())+r"(?!\d)\s*(?:[).:-]\s*)?(?:ans(?:wer)?\s*[:=-]?\s*)?"
    match=re.search(prefix+r"\(?([A-H])\)?(?=\s|$)",key_text,re.I)
    if match:
        choice=match.group(1).lower()
        for index,option in enumerate(options):
            if choice in {str(index+1),str(option.get("value","")).lower(),chr(97+index)}:
                return {"option":index}
    # Numerical/integer keys conventionally contain a literal after their
    # printed question number.  Preserve it as the value scoring shape.
    numeric=re.search(prefix+r"([-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[-+]?\d+)?)\b",key_text,re.I)
    if numeric:
        value=numeric.group(1)
        if value.isdigit() and 1<=int(value)<=len(options):return {"option":int(value)-1}
        return {"value":value}
    return None
def validate(item:dict):
    if not item.get("stem_markdown"):raise ValueError("Blank question stem")
    if item["kind"] in ("single_choice","multiple_choice","assertion_reason") and len(item.get("options",[]))<2:raise ValueError("Choice question requires two options")
    if "$$$" in item["stem_markdown"]:raise ValueError("Malformed LaTex delimiters")

def prepare_item_for_ingestion(raw_item:dict)->tuple[dict|None,list[str]]:
    """Make a model record safe to persist without letting one bad record stop a job.

    A choice item with missing choices is a recoverable classification error: keep
    its source text as a written question and require human review.  Other invalid
    records are skipped by the caller, which records their count in the job meta.
    """
    try:
        item=normalize_item(raw_item)
    except (AttributeError, TypeError, ValueError) as exc:
        return None,[f"invalid_record:{str(exc) or exc.__class__.__name__}"]
    issues=[]
    if item.get("_visual_options_recovered"):issues.append("diagram_options_recovered")
    if item.get("kind") in ("single_choice","multiple_choice","assertion_reason") and len(item.get("options") or [])<2:
        item["kind"]="subjective";item["answer"]=None;issues.append("choice_options_missing")
    try:
        validate(item)
    except (KeyError, TypeError, ValueError) as exc:
        return None,issues+[f"invalid_record:{str(exc) or exc.__class__.__name__}"]
    return item,issues

def infer_paper_title(reader:PdfReader, text:str, fallback:str)->str:
    metadata=reader.metadata or {}
    title=str(metadata.get("/Title") or "").strip()
    if title and title.lower() not in {"untitled","null"}:return title[:160]
    for line in text.splitlines()[:40]:
        clean=" ".join(line.split()).strip("-:| ")
        if 8<=len(clean)<=140 and not re.match(r"^(page|question|section)\s*\d",clean,re.I):return clean
    return fallback.rsplit(".",1)[0][:160]

def best_page_text(raw:bytes, reader:PdfReader, rendered:fitz.Document)->list[str]:
    """Use the richer of pypdf and MuPDF text on each page.

    PDFs with custom/CID fonts frequently have a perfectly usable MuPDF text
    layer while pypdf returns an empty or scrambled one.  This is deliberately
    page-local so a single bad page cannot poison a long-running import.
    """
    result=[]
    for index, page in enumerate(reader.pages):
        pypdf_text=(page.extract_text() or "").strip()
        try:mupdf_text=(rendered[index].get_text("text") or "").strip()
        except Exception:mupdf_text=""
        result.append(mupdf_text if len(mupdf_text)>len(pypdf_text) else pypdf_text)
    return result

def expected_questions(text:str)->int:
    """A conservative signal used to prevent successful-looking empty calls."""
    return len(_QUESTION_START.findall(text or ""))

def extraction_units(page_texts:list[str], max_pages:int=8, max_characters:int=7_000,
                     max_questions:int=9)->list[tuple[int,int]]:
    """Make bounded native-text batches, following the proven reference design.

    The previous page/image batching created hundreds of expensive serial model
    calls and supplied far more source than the JSON response could reliably
    contain.  Keep a dense page by itself; otherwise join sparse front matter
    only while its text and expected question count fit a response-sized unit.
    """
    units=[];start=0
    while start<len(page_texts):
        end=start;characters=0;questions=0
        while end<len(page_texts) and end-start<max_pages:
            text=page_texts[end] or ""; found=expected_questions(text)
            if end>start and (characters+len(text)>max_characters or (questions and questions+found>max_questions)):
                break
            characters+=len(text);questions+=found;end+=1
            # A page that is already a full text chunk must be sent by itself.
            if characters>=max_characters or questions>=max_questions:break
        units.append((start,end));start=end
    return units

def unit_requires_vision(page_texts:list[str], start:int, end:int)->bool:
    """Use image input only when the native PDF text is genuinely unusable.

    Diagrams are collected from the PDF independently, so attaching every
    rendered page to a text-rich request only slows extraction and degrades
    question-boundary accuracy.
    """
    return any(len((page_texts[index] or "").strip())<240 for index in range(start,end))

def embedded_answer_key_text(page_texts:list[str])->str:
    """Locate answer-table pages in a combined question-and-solution PDF."""
    candidates=[]
    for index,text in enumerate(page_texts):
        cells=len(_ANSWER_CELL.findall(text))
        if _ANSWER_HEADING.search(text) or cells>=6:candidates.append(index)
    return "\n".join(page_texts[index] for index in candidates)

def page_unit_content(page_texts:list[str], start:int, key_text:str, end:int|None=None)->str:
    """Return owned source pages plus one read-only continuation page."""
    end=end or start+1
    primary="\n".join(f"PRIMARY PAGE {index+1}:\n{page_texts[index][:70000]}" for index in range(start,min(end,len(page_texts))))
    continuation=page_texts[end] if end<len(page_texts) else ""
    text=(f"Extract every question that BEGINS on primary source pages {start+1}-{end}. "
          f"The following page, when present, is continuation context only: never emit a question that begins there. "
          f"Do not stop after a sample; return every complete question beginning on the primary pages.\n{primary}")
    if continuation:text+=f"\n\nCONTINUATION CONTEXT PAGE {end+1}:\n{continuation[:30000]}"
    if key_text:text+=f"\n\nANSWER KEY DOCUMENT (authoritative):\n{key_text[:40000]}"
    return text

def request_extraction(payload:dict, title:str, minimum_questions:int=0)->dict:
    """Make a bounded, retriable provider request for one durable page unit."""
    import httpx
    models=[payload["model"]]
    if settings().openrouter_fallback_model and settings().openrouter_fallback_model not in models:
        models.append(settings().openrouter_fallback_model)
    last_error=None
    for model in models:
        request=model_visual_request(payload,model)
        for attempt in range(3):
            try:
                response=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings().openrouter_api_key}","HTTP-Referer":"https://testnow.app","X-Title":title},json=request,timeout=settings().openrouter_timeout_seconds)
                response.raise_for_status()
                parsed=json.loads(response.json()["choices"][0]["message"]["content"])
                if len(parsed.get("questions") or [])<minimum_questions:
                    raise ValueError(
                        f"Provider returned {len(parsed.get('questions') or [])} questions "
                        f"for a source unit with at least {minimum_questions} detectable questions"
                    )
                return canonical_visual_response(parsed,payload,model)
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error=exc
                if attempt<2:time.sleep(2**attempt)
    raise RuntimeError(f"Extraction provider failed after retries: {last_error}")

def native_image_regions(document):
    return image_regions(document)


def native_vector_regions(document):
    return vector_regions(document)


@celery_app.task(bind=True,autoretry_for=(Exception,),retry_backoff=True,max_retries=2)
def extract_import(self, job_id:str):
    db:Session=SessionLocal(); rendered=None; job=db.get(ImportJob,job_id)
    try:
      meta=dict(job.extraction_meta or {})
      job.status=ImportStatus.PROCESSING; job.extraction_meta={**meta,"phase":"Reading the question paper"}; db.commit(); doc=db.get(SourceDocument, job.question_document_id); raw=get(doc.object_key)
      reader=PdfReader(BytesIO(raw))
      if len(reader.pages)>settings().max_pdf_pages: raise ValueError(f"PDF exceeds the {settings().max_pdf_pages}-page limit")
      page_count=len(reader.pages)
      if not page_count: raise ValueError("The PDF contains no pages")
      title_text="\n".join(f"\n--- PAGE {i+1} ---\n{p.extract_text() or ''}" for i,p in enumerate(reader.pages[:3])); paper_title=infer_paper_title(reader,title_text,doc.filename)
      rendered=fitz.open(stream=raw,filetype="pdf")
      page_texts=best_page_text(raw,reader,rendered)
      native_regions=native_image_regions(rendered)+native_vector_regions(rendered)
      key_text=""
      if job.answer_document_id:
          key_doc=db.get(SourceDocument,job.answer_document_id)
          key_text="\nANSWER KEY DOCUMENT:\n"+"\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(get(key_doc.object_key))).pages)
      # Combined books often append a compact answer table.  Use it for native
      # answer lookup even when no separately uploaded key exists.
      answer_lookup_text=key_text or embedded_answer_key_text(page_texts)
      if not settings().openrouter_api_key: raise RuntimeError("OPENROUTER_API_KEY is not configured")
      checkpoint=max(0,min(page_count,int(meta.get("processed_pages") or 0)))
      # Question numbers repeat across sections in many papers.  Couple the
      # number with its owned starting page, which also makes a resumed page
      # safe to replay after a worker interruption.
      numbers={(str(number),int((spans or [{}])[0].get("page") or 0)) for number,spans in db.query(Question.source_number,Question.source_spans).filter_by(import_job_id=job.id).all() if number}
      failed_pages=list(meta.get("failed_pages") or [])
      units=[unit for unit in extraction_units(page_texts) if unit[1]>checkpoint]
      for start,end in units:
        if start<checkpoint:start=checkpoint
        vision_content=[{"type":"text","text":page_unit_content(page_texts,start,key_text,end)}]
        vision_content += visual_context(rendered,start,end,page_texts,native_regions,settings().max_rendered_pdf_bytes)
        job.extraction_meta={**meta,"phase":f"Extracting pages {start+1}-{end} of {page_count}","processed_pages":start,"total_pages":page_count,"question_count":len(numbers)};db.commit()
        payload={"model":settings().openrouter_model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":vision_content}],"response_format":{"type":"json_schema","json_schema":SCHEMA},"provider":{"require_parameters":True},"max_tokens":settings().openrouter_max_output_tokens,"stream":False}
        try:
          minimum=1 if expected_questions("\n".join(page_texts[start:end])) else 0
          parsed=request_extraction(payload,"TestNow",minimum)
        except Exception as page_error:
          # A single corrupt page or transient provider failure must not strand
          # hundreds of already checkpointed pages.  Surface the exact page in
          # review metadata and continue with the next durable unit.
          failed_pages.append({"page":start+1,"error":str(page_error)[:300]})
          job.extraction_meta={**meta,"phase":f"Skipped unreadable page {start+1} of {page_count}","processed_pages":end,"total_pages":page_count,"question_count":len(numbers),"failed_pages":failed_pages};meta=dict(job.extraction_meta);db.commit();continue
        skipped=0
        for raw_item in mark_scope_conflicts(ground_question_spans(parsed.get("questions",[]),rendered)):
          item,preflight_issues=prepare_item_for_ingestion(raw_item)
          if item is None:
            skipped+=1;continue
          if not item.get("answer"):
            item["answer"]=answer_from_key(item.get("number"),item.get("options") or [],answer_lookup_text)
          number=str(item["number"])
          owned_page=int((item.get("source_spans") or [{}])[0].get("page") or start+1)
          # Reject a model returning continuation-owned records, rather than
          # depending on a global number dedupe that loses valid questions.
          if owned_page not in range(start+1,end+1) or (number,owned_page) in numbers: continue
          numbers.add((number,owned_page));kind=QuestionKind(item["kind"])
          diagrams,visual_issues=save_figures(item,rendered,native_regions,f"diagrams/{job.id}/{owned_page}-{number}",put)
          db.add(Question(id=uid(),owner_id=job.owner_id,import_job_id=job.id,source_number=number,kind=kind,stem_markdown=item["stem_markdown"],options=item["options"],answer=item["answer"],solution_markdown=item.get("solution_markdown"),confidence=item["confidence"],source_spans=item["source_spans"],diagrams=diagrams,interaction={"ingestion_issues":preflight_issues+visual_issues,"source_document_id":doc.id}))
        job.extraction_meta={**meta,"phase":f"Saved pages {start+1}-{end} of {page_count}","processed_pages":end,"total_pages":page_count,"question_count":len(numbers),"skipped_count":int(meta.get("skipped_count") or 0)+skipped,"failed_pages":failed_pages};meta=dict(job.extraction_meta);db.commit()
      phase="Ready for review" if not failed_pages else f"Ready for review — {len(failed_pages)} page(s) need re-extraction"
      job.status=ImportStatus.REVIEW; job.extraction_meta={**meta,"model":settings().openrouter_model,"paper_title":paper_title,"processed_pages":page_count,"total_pages":page_count,"question_count":len(numbers),"failed_pages":failed_pages,"phase":phase};db.commit()
    except Exception as exc:
      job.status=ImportStatus.FAILED;job.error=str(exc)[:2000];job.extraction_meta={**(job.extraction_meta or {}),"phase":"Import failed"};db.commit();raise
    finally:
      if rendered: rendered.close()
      db.close()

@celery_app.task(bind=True)
def extract_bank_import(self, job_id: str):
    from .bank_imports import run_bank_import
    # The bank importer records a durable terminal state itself.  Retrying every
    # exception made permanent validation failures look like live processing.
    return run_bank_import(job_id)
