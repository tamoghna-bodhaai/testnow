"""Source preview and reviewed crop correction for PDF and converted DOCX."""
from contextlib import contextmanager

import fitz
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .db import get_db
from .figures import coverage, extract_figures, ground_question_spans, image_regions, is_visual_issue, option_media_issues, page_number, rect, unrotated, vector_regions
from .models import ImportJob, ImportStatus, Question, Role, SourceDocument, User
from .schemas import FigureReviewIn, QuestionIn
from .security import require, uid
from .services import audit
from .storage import get, put

router = APIRouter()


def review_question(question_id, user, db):
    question = db.get(Question, question_id)
    if not question or (user.role != Role.ADMIN and question.owner_id != user.id):
        raise HTTPException(404, "Question not found")
    return question


@contextmanager
def source_pdf(question, db):
    job = db.get(ImportJob, question.import_job_id) if question.import_job_id else None
    source_id = (question.interaction or {}).get("source_document_id") or (job.question_document_id if job else None)
    source = db.get(SourceDocument, source_id) if source_id else None
    if not source:
        raise HTTPException(422, "The original source is unavailable for this question")
    canonical = (job.extraction_meta or {}).get("canonical_pdf_key") if job else None
    if canonical:
        raw = get(canonical)
    elif source.media_type == "application/pdf":
        raw = get(source.object_key)
    else:
        from .bank_imports import _pdf_bytes
        raw = _pdf_bytes(source)
    document = fitz.open(stream=raw, filetype="pdf")
    try:
        yield document, raw, source, job
    finally:
        document.close()


@router.get("/questions/{question_id}/figure-source")
def figure_source(question_id:str, user:User=Depends(require(Role.TEACHER,Role.ADMIN)), db:Session=Depends(get_db)):
    question = review_question(question_id, user, db)
    with source_pdf(question, db) as (document, _, source, _job):
        pages = []
        for number, page in enumerate(document,1):
            with unrotated(page):
                pages.append({"page":number,"width":page.rect.width,"height":page.rect.height})
        # The source canvas remains available for corrections, but opening the
        # editor should begin at the saved crop or grounded question scope.
        item={"number":question.source_number,"stem_markdown":question.stem_markdown,
              "options":question.options or [],"source_spans":question.source_spans or [],
              "diagrams":question.diagrams or [],"visual_intent":(question.interaction or {}).get("visual_intent")}
        ground_question_spans([item], document)
        candidates=[]
        # Prefer the accepted crop.  The grounded question span is a fallback
        # for a missing/uncertain figure, not something to merge into it.
        for record in item["diagrams"]:
            number=page_number(record.get("page"),document) if isinstance(record,dict) else None
            box=rect(record.get("bbox")) if isinstance(record,dict) else None
            if number and box:
                with unrotated(document[number-1]) as page:
                    box &= page.rect
                    if not box.is_empty and box.get_area() < page.rect.get_area()*.75:
                        candidates.append((number,box))
        if not candidates:
            for record in item["source_spans"]:
                number=page_number(record.get("page"),document) if isinstance(record,dict) else None
                box=rect(record.get("bbox")) if isinstance(record,dict) else None
                if number and box:
                    with unrotated(document[number-1]) as page:
                        box &= page.rect
                        if not box.is_empty and box.get_area() < page.rect.get_area()*.75:
                            candidates.append((number,box))
        focus=None
        if candidates:
            focus_page=candidates[0][0]
            same=[box for number,box in candidates if number==focus_page]
            merged=fitz.Rect(same[0])
            for box in same[1:]: merged |= box
            with unrotated(document[focus_page-1]) as page:
                # Keep a useful context margin without reverting to the full page.
                merged=(merged+(-24,-24,24,24)) & page.rect
                focus={"page":focus_page,"bbox":[round(v,2) for v in merged]}
        return {"filename":source.filename,"pages":pages,"diagrams":question.diagrams,"stem_markdown":question.stem_markdown,
                "original_url":f"/api/teacher/imports/{_job.id}/source" if _job else None,
                "conversion_issues":(_job.extraction_meta or {}).get("docx_visual_issues",[]) if _job else [],
                "source_page":(focus or {}).get("page") or (question.interaction or {}).get("source_page") or ((question.source_spans or [{}])[0].get("page")) or 1,
                "focus":focus,
                "visual_intent":(question.interaction or {}).get("visual_intent","none")}


@router.put("/questions/{question_id}/draft")
def save_question_draft(question_id:str, data:QuestionIn, user:User=Depends(require(Role.TEACHER,Role.ADMIN)), db:Session=Depends(get_db)):
    """Allow missing choice definitions to be corrected before image approval."""
    question=review_question(question_id,user,db)
    effective_options=data.options if "options" in data.model_fields_set else question.options
    if data.kind.value in {"single_choice","multiple_choice","assertion_reason"} and len(effective_options or [])<2:
        raise HTTPException(422,"Choice questions need at least two options")
    changed_order=[o.get("value") for o in effective_options or []]!=[o.get("value") for o in question.options or []]
    allowed={"source_number","kind","stem_markdown","options","answer","solution_markdown","scoring","confidence"}
    for name in data.model_fields_set & allowed:
        setattr(question,name,getattr(data,name))
    issues=list((question.interaction or {}).get("ingestion_issues",[]))
    if len(question.options or [])>=2:issues=[i for i in issues if i!="choice_options_missing"]
    issues+=option_media_issues(question.options,question.diagrams)
    if changed_order and any(d.get("role")=="option" for d in question.diagrams or []):
        issues.append("diagram_option_ownership_uncertain")
    question.interaction={**(question.interaction or {}),"ingestion_issues":list(dict.fromkeys(issues))}
    question.approved_at=None
    job=db.get(ImportJob,question.import_job_id) if question.import_job_id else None
    if job:job.status=ImportStatus.REVIEW
    audit(db,user.id,"question.draft_saved","question",question.id,{"fields":sorted(data.model_fields_set & allowed)})
    db.commit()
    return {"id":question.id,"approved":False}


@router.get("/questions/{question_id}/figure-source/{number}")
def figure_source_page(question_id:str, number:int, user:User=Depends(require(Role.TEACHER,Role.ADMIN)), db:Session=Depends(get_db)):
    question = review_question(question_id, user, db)
    with source_pdf(question, db) as (document, _, _source, _job):
        if page_number(number,document) is None:
            raise HTTPException(404,"Source page not found")
        with unrotated(document[number-1]) as page:
            png=page.get_pixmap(matrix=fitz.Matrix(1.25,1.25),alpha=False).tobytes("png")
        return Response(png,media_type="image/png",headers={"Cache-Control":"private, max-age=60"})


@router.put("/questions/{question_id}/figures")
def review_figures(question_id:str, data:FigureReviewIn, user:User=Depends(require(Role.TEACHER,Role.ADMIN)), db:Session=Depends(get_db)):
    question = review_question(question_id, user, db)
    if not data.confirmed:
        raise HTTPException(422,"Confirm that every question and option figure matches the source")
    with source_pdf(question, db) as (document, raw, source, job):
        # A human can correct inaccurate model spans. Preserve original spans;
        # every reviewed figure independently retains its canonical page/box.
        spans=[{"page":d.get("page"),"bbox":d.get("bbox")} for d in data.diagrams]
        item={"stem_markdown":question.stem_markdown,"options":question.options,"diagrams":data.diagrams,"source_spans":spans}
        pages={d.get("page") for d in data.diagrams if isinstance(d.get("page"),int)}
        figures, issues=extract_figures(item,document,image_regions(document,pages)+vector_regions(document,pages),reviewed=True)
        if issues:
            raise HTTPException(422,"Figures need correction: "+", ".join(issues))
        if source.media_type != "application/pdf" and job and not (job.extraction_meta or {}).get("canonical_pdf_key"):
            key=f"bank-sources/{question.bank_id or 'private'}/{source.id}/canonical.pdf"
            put(key,raw,"application/pdf")
            job.extraction_meta={**(job.extraction_meta or {}),"canonical_pdf_key":key}
        revision=uid()
        saved=[]
        # New immutable keys keep earlier test versions and rollback references
        # intact. Any upload failure leaves DB references unchanged.
        for index, figure in enumerate(figures):
            png=figure.pop("png")
            key=f"reviewed-diagrams/{question.id}/{revision}-{index}.png"
            put(key,png,"image/png")
            saved.append({**figure,"object_key":key})
        previous=question.diagrams
        question.diagrams=saved
        question.approved_at=None
        question.interaction={**(question.interaction or {}),
            "ingestion_issues":[i for i in (question.interaction or {}).get("ingestion_issues",[]) if not is_visual_issue(i)],
            "figures_reviewed_by":user.id,"figure_revision":revision}
        if job:
            job.status=ImportStatus.REVIEW
            job.extraction_meta={**(job.extraction_meta or {}),"phase":"Figure correction awaiting question approval"}
        audit(db,user.id,"question.figures_reviewed","question",question.id,{"before":previous,"after":saved})
        db.commit()
        return {"diagrams":saved,"ingestion_issues":question.interaction["ingestion_issues"]}
