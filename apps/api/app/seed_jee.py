"""Native, deterministic JEE worksheet seeding.

Run inside the API container after migrations, for example:
  python -m app.seed_jee --owner-email utils@bodhaai.tech \
    /imports/PHYSICS_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf

This intentionally does not call a model. It stores the original PDF, extracts
native text and options, detects the worksheet's printed type sections, and
renders each question's source area to PNG. The source rendering preserves
formula layout, graphs, and vector figures even where native text extraction is
imperfect. Imported questions remain reviewable until an answer key is checked.
"""
from __future__ import annotations
import argparse, hashlib, re
from datetime import datetime, timezone
from pathlib import Path
import fitz
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import Question, QuestionBank, QuestionKind, QuestionScope, QuestionTaxonomy, SourceDocument, TaxonomyNode, User
from .security import uid
from .storage import put

BANK_SLUG="jee-advanced"
QUESTION=re.compile(r"(?m)^\s*(\d{1,3})\.\s+")
OPTION=re.compile(r"(?=\s*\([A-D]\)\s*)")

def subject_from_filename(name:str)->str:
    upper=name.upper()
    if "PHYSICS" in upper:return "Physics"
    if "CHEM" in upper:return "Chemistry"
    if "MATH" in upper:return "Mathematics"
    raise ValueError("Filename must identify Physics, Chemistry, or Mathematics")

def kind_for(text:str, previous:QuestionKind)->QuestionKind:
    upper=text.upper()
    if "MATRIX MATCH" in upper or "MATCH THE FOLLOWING" in upper:return QuestionKind.MATRIX
    if "MULTIPLE CORRECT" in upper:return QuestionKind.MULTIPLE
    if "NUMERICAL VALUE" in upper:return QuestionKind.NUMERICAL
    if "INTEGER TYPE" in upper:return QuestionKind.INTEGER
    if "ASSERTION" in upper:return QuestionKind.ASSERTION
    if "SINGLE CORRECT" in upper:return QuestionKind.SINGLE
    return previous

def main_question_starts(page:fitz.Page, text:str)->list[tuple[re.Match,float]]:
    """Use left-margin printed numbers only.

    Matrix-match columns also contain numbered entries, but they are printed in
    a later column. Looking at block x-coordinates prevents those entries from
    becoming fake questions.
    """
    blocks=page.get_text("blocks",sort=True)
    numbers=[]
    for block in blocks:
        marker=re.match(r"\s*(\d{1,3})\.\s+",block[4] or "")
        if marker and block[0] <= 105:numbers.append((marker.group(1),block[1]))
    matches=list(QUESTION.finditer(text)); cursor=0; starts=[]
    for number,y in numbers:
        chosen=next((match for match in matches if match.start()>=cursor and match.group(1)==number),None)
        if chosen:
            starts.append((chosen,y));cursor=chosen.end()
    return starts

def chapter_for(text:str, fallback:str)->str:
    lines=[" ".join(line.split()) for line in text.splitlines() if line.strip()]
    for line in lines[:18]:
        if re.search(r"Advanced Problem Package",line,re.I):
            cleaned=re.sub(r"^.*?Advanced Problem Package\s*", "", line, flags=re.I).strip(" -|")
            if len(cleaned)>3:return cleaned[:180]
        match=re.search(r"APP \| (?:Physics|Chemistry)\s+\d+\s+(.+)$",line,re.I)
        if match:
            cleaned=match.group(1).strip(" -|")
            if len(cleaned)>3:return cleaned[:180]
    return fallback

def clean_markdown(value:str)->str:
    return re.sub(r"[ \t]+", " ", value.replace("\u00ad", "")).strip()

def parse_question(chunk:str, number:str, kind:QuestionKind)->tuple[str,list[dict]]:
    body=QUESTION.sub("", chunk, count=1).strip()
    pieces=OPTION.split(body)
    stem=clean_markdown(pieces[0])
    options=[]
    for piece in pieces[1:]:
        match=re.match(r"\s*\(([A-D])\)\s*(.*)", piece, re.S)
        if match: options.append({"value":match.group(1).lower(),"markdown":clean_markdown(match.group(2))})
    # Native PDFs sometimes omit recognisable option markers. Keep the complete
    # question in the source rendering instead of inventing choices.
    return stem or f"Question {number}",options

def find_or_create_node(db:Session, bank_id:str, parent_id:str|None, kind:str, name:str)->TaxonomyNode:
    row=db.query(TaxonomyNode).filter_by(bank_id=bank_id,parent_id=parent_id,kind=kind,name=name).first()
    if not row:
        row=TaxonomyNode(id=uid(),bank_id=bank_id,parent_id=parent_id,kind=kind,name=name)
        db.add(row);db.flush()
    return row

def source_crop(page:fitz.Page, top:float, bottom:float)->bytes:
    clip=fitz.Rect(0,max(0,top-8),page.rect.width,min(page.rect.height,bottom-4))
    return page.get_pixmap(matrix=fitz.Matrix(1.7,1.7),clip=clip,alpha=False).tobytes("png")

def seed_pdf(db:Session, owner:User, path:Path, publish:bool=False)->int:
    raw=path.read_bytes(); subject=subject_from_filename(path.name)
    bank=db.query(QuestionBank).filter_by(slug=BANK_SLUG).first()
    if not bank:
        bank=QuestionBank(id=uid(),slug=BANK_SLUG,name="JEE Advanced Question Bank",exam_code="JEE_ADVANCED",is_global=True);db.add(bank);db.flush()
    checksum=hashlib.sha256(raw).hexdigest()
    source=db.query(SourceDocument).filter_by(owner_id=owner.id,sha256=checksum).first()
    if not source:
        source=SourceDocument(id=uid(),owner_id=owner.id,filename=path.name,object_key=f"seed-sources/{bank.id}/{uid()}-{path.name}",sha256=checksum,media_type="application/pdf",size_bytes=len(raw));put(source.object_key,raw,"application/pdf");db.add(source);db.flush()
    existing={(str((q.interaction or {}).get("source_document_id")),int((q.interaction or {}).get("source_page",-1)),q.source_number) for q in db.query(Question).filter_by(bank_id=bank.id).all()}
    subject_node=find_or_create_node(db,bank.id,None,"subject",subject)
    document=fitz.open(path)
    chapter="Unmapped chapter";kind=QuestionKind.SINGLE;created=0
    for page_number,page in enumerate(document,1):
        text=page.get_text("text",sort=True)
        chapter=chapter_for(text,chapter)
        chapter_node=find_or_create_node(db,bank.id,subject_node.id,"chapter",chapter)
        matches=main_question_starts(page,text)
        for index,(match,top) in enumerate(matches):
            if (source.id,page_number,match.group(1)) in existing:continue
            kind=kind_for(text[:match.start()],kind)
            end=matches[index+1][0].start() if index+1<len(matches) else len(text)
            stem,options=parse_question(text[match.start():end],match.group(1),kind)
            # Do not duplicate a page fragment that is visibly only a continuation.
            if len(stem)<8:continue
            next_y=page.rect.height
            if index+1<len(matches):
                next_y=matches[index+1][1]
            image=source_crop(page,top,next_y)
            key=f"seed-diagrams/{bank.id}/{subject.lower()}/{page_number}-{match.group(1)}-{uid()}.png";put(key,image,"image/png")
            interaction={"source_document_id":source.id,"source_page":page_number,"seed_status":"needs_answer_review","taxonomy_status":"chapter_mapped"}
            if kind==QuestionKind.MATRIX:interaction["response_format"]="row_to_many_columns"
            if kind==QuestionKind.NUMERICAL:interaction["tolerance"]=1e-6
            question=Question(id=uid(),owner_id=owner.id,bank_id=bank.id,scope=QuestionScope.GLOBAL,source_number=match.group(1),kind=kind,stem_markdown=stem,options=options,answer=None,solution_markdown=None,diagrams=[{"object_key":key,"page":page_number,"bbox":[0,top,page.rect.width,next_y],"alt":"Source rendering (includes any figure or graph)"}],source_spans=[{"page":page_number,"bbox":[0,top,page.rect.width,next_y]}],interaction=interaction,confidence=1.0,approved_at=datetime.now(timezone.utc) if publish else None)
            if publish and not question.answer:raise ValueError("Native seed import cannot publish without reviewed answer keys")
            db.add(question);db.flush();db.add(QuestionTaxonomy(question_id=question.id,taxonomy_node_id=subject_node.id));db.add(QuestionTaxonomy(question_id=question.id,taxonomy_node_id=chapter_node.id));created+=1
            existing.add((source.id,page_number,match.group(1)))
        if page_number%20==0:db.commit()
    db.commit();return created

def main():
    parser=argparse.ArgumentParser(description="Seed global JEE questions from clean worksheet PDFs without an LLM")
    parser.add_argument("pdfs",nargs="+",type=Path);parser.add_argument("--owner-email",required=True);parser.add_argument("--publish",action="store_true");parser.add_argument("--reset",action="store_true",help="replace existing generated JEE bank questions before importing")
    args=parser.parse_args();db=SessionLocal()
    try:
        owner=db.query(User).filter_by(email=args.owner_email.lower()).first()
        if not owner:raise SystemExit("Seed owner does not exist; start the API once or use the configured admin email")
        if args.reset:
            bank=db.query(QuestionBank).filter_by(slug=BANK_SLUG).first()
            if bank:
                db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id.in_([q.id for q in db.query(Question.id).filter_by(bank_id=bank.id)])).delete(synchronize_session=False)
                db.query(Question).filter_by(bank_id=bank.id).delete(synchronize_session=False)
                db.query(TaxonomyNode).filter_by(bank_id=bank.id).delete(synchronize_session=False)
                db.commit()
        total=sum(seed_pdf(db,owner,path,args.publish) for path in args.pdfs)
        print(f"Created {total} JEE seed questions in review status.")
    finally:db.close()

if __name__=="__main__":main()
