from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy.orm import Session
from .models import Attempt, Question, QuestionScope, Response, Test, TestVersion, TestStatus
from .security import uid

CHOICE_KINDS={"single_choice","multiple_choice","assertion_reason"}

def safe_question_kind(kind:object, options:object)->str:
    """Avoid delivering legacy optionless records as unanswerable MCQs."""
    value=getattr(kind,"value",kind)
    return "subjective" if value in CHOICE_KINDS and len(options or [])<2 else str(value)

def audit(db:Session, actor:str|None, action:str, entity_type:str, entity_id:str, data:dict|None=None):
    from .models import AuditEvent
    db.add(AuditEvent(id=uid(),actor_id=actor,action=action,entity_type=entity_type,entity_id=entity_id,data=data or {}))
def snapshot_questions(db:Session, ids:list[str], owner_id:str)->list[dict]:
    found=db.query(Question).filter(Question.id.in_(ids),Question.approved_at.is_not(None)).filter((Question.owner_id==owner_id)|(Question.scope==QuestionScope.GLOBAL)).all()
    by_id={q.id:q for q in found}
    if len(by_id)!=len(set(ids)):raise HTTPException(422,"Every selected question must be teacher-approved")
    content=[]
    for qid in ids:
        q=by_id[qid]; kind=safe_question_kind(q.kind,q.options)
        content.append({"id":q.id,"source_number":q.source_number,"kind":kind,"stem_markdown":q.stem_markdown,"options":q.options,"answer":None if kind=="subjective" and len(q.options or [])<2 else q.answer,"solution_markdown":q.solution_markdown,"scoring":q.scoring,"diagrams":q.diagrams,"interaction":q.interaction})
    return content
def publish(db:Session,test:Test,actor_id:str,question_ids:list[str]):
    content={"questions":snapshot_questions(db,question_ids,test.owner_id),"duration_seconds":test.duration_seconds,"results_policy":test.results_policy}
    number=(db.query(TestVersion).filter_by(test_id=test.id).count()+1); version=TestVersion(id=uid(),test_id=test.id,ordinal=number,content=content,published_at=datetime.now(timezone.utc))
    db.add(version); test.status=TestStatus.PUBLISHED; test.published_version_id=version.id; audit(db,actor_id,"test.published","test",test.id,{"version":number}); db.commit(); return version
def ensure_live(test:Test):
    now=datetime.now(timezone.utc)
    if test.status != TestStatus.PUBLISHED or not test.published_version_id:raise HTTPException(409,"Test is not published")
    if test.opens_at and now<test.opens_at:raise HTTPException(403,"Test has not opened")
    if test.closes_at and now>test.closes_at:raise HTTPException(403,"Test is closed")
def answers_match(question:dict,given:dict|None)->bool:
    if given is None:return False
    expected=question.get("answer") or {}
    kind=question.get("kind")
    if kind in {"numerical","integer"}:
        try:
            supplied=float(given.get("value")); target=float(expected.get("value"))
            tolerance=float((question.get("interaction") or {}).get("tolerance", 0 if kind=="integer" else 1e-6))
            return abs(supplied-target)<=tolerance
        except (TypeError,ValueError): return False
    if kind=="matrix_match":
        return given.get("matches")==expected.get("matches")
    if kind=="multiple_choice":
        try:
            return sorted(set(given.get("options",[])))==sorted(set(expected.get("options",[])))
        except TypeError:
            return False
    return given==expected
def score(content:dict,responses:dict[str,dict|None])->tuple[float,list[dict]]:
    total=0.0; review=[]
    for q in content["questions"]:
        given=responses.get(q["id"]); policy=q.get("scoring",{}); correct=answers_match(q,given)
        points=policy.get("unanswered",0) if given is None else policy.get("correct",4) if correct else policy.get("incorrect",-1)
        total+=points; review.append({**q,"response":given,"correct":correct,"points":points})
    return total,review

def finalize_attempt(db:Session, attempt:Attempt, *, submitted_at:datetime, actor_id:str|None, action:str)->bool:
    """Score and submit an attempt exactly once using its saved responses."""
    if attempt.submitted_at:
        return False
    version=db.get(TestVersion,attempt.version_id)
    responses={response.question_id:response.answer for response in db.query(Response).filter_by(attempt_id=attempt.id)}
    attempt.score,_=score(version.content,responses)
    attempt.submitted_at=submitted_at
    audit(db,actor_id,action,"attempt",attempt.id)
    return True

def finalize_if_expired(db:Session, attempt:Attempt, now:datetime|None=None)->bool:
    """Finalize an unfinished attempt whose server-authoritative deadline passed."""
    # The expiry worker, a resumed browser, and a normal submit can race at the
    # deadline. Re-read under a row lock before scoring so only one path writes
    # the result and audit event.
    if db.bind and db.bind.dialect.name=="postgresql":
        attempt=db.query(Attempt).filter_by(id=attempt.id).with_for_update().one()
    now=now or datetime.now(timezone.utc)
    ends_at=attempt.ends_at.replace(tzinfo=timezone.utc) if attempt.ends_at.tzinfo is None else attempt.ends_at.astimezone(timezone.utc)
    if attempt.submitted_at or now<ends_at:
        return False
    return finalize_attempt(db,attempt,submitted_at=ends_at,actor_id=None,action="attempt.auto_submitted")
