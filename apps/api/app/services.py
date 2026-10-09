from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy.orm import Session
from .models import Attempt, Question, Response, Test, TestVersion, TestStatus
from .security import uid

def audit(db:Session, actor:str|None, action:str, entity_type:str, entity_id:str, data:dict|None=None):
    from .models import AuditEvent
    db.add(AuditEvent(id=uid(),actor_id=actor,action=action,entity_type=entity_type,entity_id=entity_id,data=data or {}))
def snapshot_questions(db:Session, ids:list[str])->list[dict]:
    found=db.query(Question).filter(Question.id.in_(ids),Question.approved_at.is_not(None)).all()
    by_id={q.id:q for q in found}
    if len(by_id)!=len(set(ids)):raise HTTPException(422,"Every selected question must be teacher-approved")
    return [{"id":q.id,"source_number":q.source_number,"kind":q.kind.value,"stem_markdown":q.stem_markdown,"options":q.options,"answer":q.answer,"solution_markdown":q.solution_markdown,"scoring":q.scoring,"diagrams":q.diagrams} for qid in ids for q in [by_id[qid]]]
def publish(db:Session,test:Test,actor_id:str,question_ids:list[str]):
    content={"questions":snapshot_questions(db,question_ids),"duration_seconds":test.duration_seconds,"results_policy":test.results_policy}
    number=(db.query(TestVersion).filter_by(test_id=test.id).count()+1); version=TestVersion(id=uid(),test_id=test.id,ordinal=number,content=content,published_at=datetime.now(timezone.utc))
    db.add(version); test.status=TestStatus.PUBLISHED; test.published_version_id=version.id; audit(db,actor_id,"test.published","test",test.id,{"version":number}); db.commit(); return version
def ensure_live(test:Test):
    now=datetime.now(timezone.utc)
    if test.status != TestStatus.PUBLISHED or not test.published_version_id:raise HTTPException(409,"Test is not published")
    if test.opens_at and now<test.opens_at:raise HTTPException(403,"Test has not opened")
    if test.closes_at and now>test.closes_at:raise HTTPException(403,"Test is closed")
def score(content:dict,responses:dict[str,dict|None])->tuple[float,list[dict]]:
    total=0.0; review=[]
    for q in content["questions"]:
        given=responses.get(q["id"]); answer=q.get("answer"); policy=q.get("scoring",{}); correct=given is not None and given==answer
        points=policy.get("unanswered",0) if given is None else policy.get("correct",4) if correct else policy.get("incorrect",-1)
        total+=points; review.append({**q,"response":given,"correct":correct,"points":points})
    return total,review
