from datetime import datetime, timezone
import re
from fastapi import HTTPException
from sqlalchemy.orm import Session
from .models import Attempt, AttemptEvent, Question, QuestionScope, QuestionTaxonomy, TaxonomyNode, Response, Test, TestVersion, TestStatus
from .security import uid
from .readiness import require_content_valid

CHOICE_KINDS={"single_choice","multiple_choice","assertion_reason"}

def source_number_key(value:object)->tuple:
    """Natural ordering for printed identifiers: 1, 2, ... 10, 11."""
    return tuple((0,int(part)) if part.isdigit() else (1,part.lower())
                 for part in re.split(r"(\d+)",str(value or "")))

def safe_question_kind(kind:object, options:object)->str:
    """Avoid delivering legacy optionless records as unanswerable MCQs."""
    value=getattr(kind,"value",kind)
    return "subjective" if value in CHOICE_KINDS and len(options or [])<2 else str(value)

def audit(db:Session, actor:str|None, action:str, entity_type:str, entity_id:str, data:dict|None=None):
    from .models import AuditEvent
    db.add(AuditEvent(id=uid(),actor_id=actor,action=action,entity_type=entity_type,entity_id=entity_id,data=data or {},created_at=datetime.now(timezone.utc)))
def attempt_event(db:Session, attempt_id:str, event_type:str, data:dict|None=None):
    db.add(AttemptEvent(id=uid(),attempt_id=attempt_id,event_type=event_type,data=data or {}))

def delivery_blueprint(content:dict)->dict:
    """Return a safe blueprint for both new sectioned and historic flat papers."""
    questions=content.get("questions") or []
    supplied=content.get("delivery_blueprint") or {}
    sections=supplied.get("sections") if isinstance(supplied,dict) else None
    ids={q.get("id") for q in questions}
    if not isinstance(sections,list) or not sections:
        sections=[{"id":"general","title":"General","question_ids":[q.get("id") for q in questions],"navigation":"free","optional_groups":[]}]
    normalized=[]; seen=set()
    for index,section in enumerate(sections):
        if not isinstance(section,dict): continue
        question_ids=[qid for qid in section.get("question_ids",[]) if qid in ids and qid not in seen]
        seen.update(question_ids)
        normalized.append({"id":str(section.get("id") or f"section-{index+1}"),"title":str(section.get("title") or f"Section {index+1}"),"question_ids":question_ids,"navigation":"free","optional_groups":section.get("optional_groups") if isinstance(section.get("optional_groups"),list) else []})
    remaining=[q.get("id") for q in questions if q.get("id") not in seen]
    if remaining:
        normalized.append({"id":"general" if not normalized else "remaining","title":"General" if not normalized else "Other questions","question_ids":remaining,"navigation":"free","optional_groups":[]})
    return {"preset":str(supplied.get("preset") or "legacy"),"instructions":str(supplied.get("instructions") or "Read every question carefully. Use Save & Next to record an answer."),"sections":normalized,"languages":supplied.get("languages") or ["en"],"accommodations":supplied.get("accommodations") or {}}

def validate_blueprint(blueprint:dict, question_ids:list[str]):
    if not blueprint:return
    sections=blueprint.get("sections")
    if not isinstance(sections,list) or not sections:raise HTTPException(422,"A CBT blueprint requires at least one section")
    expected=set(question_ids); seen=[]
    for section in sections:
        if not isinstance(section,dict) or not str(section.get("title") or "").strip():raise HTTPException(422,"Every CBT section needs a title")
        ids=section.get("question_ids")
        if not isinstance(ids,list) or not ids:raise HTTPException(422,"Every CBT section needs questions")
        seen.extend(ids)
        for group in section.get("optional_groups",[]) or []:
            if not isinstance(group,dict) or not isinstance(group.get("question_ids"),list) or not isinstance(group.get("choose"),int):raise HTTPException(422,"Invalid optional-question group")
            if group["choose"]<1 or group["choose"]>len(group["question_ids"]):raise HTTPException(422,"Invalid optional-question choice count")
    if len(seen)!=len(set(seen)) or set(seen)!=expected:raise HTTPException(422,"Blueprint sections must contain every selected question exactly once")
def snapshot_questions(db:Session, ids:list[str], owner_id:str)->list[dict]:
    found=db.query(Question).filter(Question.id.in_(ids),Question.approved_at.is_not(None)).filter((Question.owner_id==owner_id)|(Question.scope==QuestionScope.GLOBAL)).populate_existing().with_for_update().all()
    by_id={q.id:q for q in found}
    if len(by_id)!=len(set(ids)):raise HTTPException(422,"Every selected question must be teacher-approved")
    content=[]
    for qid in ids:
        q=by_id[qid]
        require_content_valid(q)
        if q.scope == QuestionScope.GLOBAL and q.bank_id:
            from .models import ImportJob, QuestionBank
            bank=db.get(QuestionBank,q.bank_id)
            job=db.get(ImportJob,q.import_job_id) if q.import_job_id else None
            if not bank or not bank.is_global or (q.import_job_id and (not job or not job.published_at or (job.extraction_meta or {}).get("delete_requested"))):
                raise HTTPException(422,"Every selected bank question must belong to a published material")
        kind=q.kind.value
        taxonomy=[{"kind":node.kind,"name":node.name,"code":node.code} for node in db.query(TaxonomyNode).join(QuestionTaxonomy,QuestionTaxonomy.taxonomy_node_id==TaxonomyNode.id).filter(QuestionTaxonomy.question_id==q.id).all()]
        content.append({"id":q.id,"source_number":q.source_number,"kind":kind,"stem_markdown":q.stem_markdown,"options":q.options,"answer":None if kind=="subjective" and len(q.options or [])<2 else q.answer,"solution_markdown":q.solution_markdown,"scoring":q.scoring,"diagrams":q.diagrams,"interaction":q.interaction,"taxonomy":taxonomy})
    return content
def publish(db:Session,test:Test,actor_id:str,question_ids:list[str]):
    content={"questions":snapshot_questions(db,question_ids,test.owner_id),"duration_seconds":test.duration_seconds,"results_policy":test.results_policy}
    from .diagnostics import classify_skills
    skill_labels=classify_skills(content["questions"])
    for question in content["questions"]:
        question["skill"]=skill_labels[question["id"]]
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
    submission_event={"attempt.submitted":"submitted","attempt.auto_submitted":"auto_submitted","attempt.integrity_cancelled":"integrity_cancelled"}.get(action,"submitted")
    attempt_event(db,attempt.id,submission_event,{"at":submitted_at.isoformat()})
    return True

def cancel_for_integrity(db:Session, attempt:Attempt, *, event_type:str, detected_at:datetime, actor_id:str, client_time:datetime|None=None)->bool:
    """Cancel an active attempt once and preserve the first integrity incident."""
    if attempt.submitted_at:
        if attempt.integrity_flagged:
            return False
        attempt.integrity_flagged=True
        attempt.cancellation_reason=event_type
        audit(db,actor_id,"attempt.integrity_reported_after_close","attempt",attempt.id,{"event_type":event_type})
        attempt_event(db,attempt.id,"integrity_violation",{"event_type":event_type,"detected_at":detected_at.isoformat(),"client_time":client_time.isoformat() if client_time else None,"after_close":True})
        return False
    attempt.integrity_flagged=True
    attempt.cancelled_at=detected_at
    attempt.cancellation_reason=event_type
    finalize_attempt(db,attempt,submitted_at=detected_at,actor_id=actor_id,action="attempt.integrity_cancelled")
    attempt_event(db,attempt.id,"integrity_violation",{"event_type":event_type,"detected_at":detected_at.isoformat(),"client_time":client_time.isoformat() if client_time else None})
    return True

def finalize_if_expired(db:Session, attempt:Attempt, now:datetime|None=None)->bool:
    """Finalize an unfinished attempt whose server-authoritative deadline passed."""
    # The expiry worker, a resumed browser, and a normal submit can race at the
    # deadline. Re-read under a row lock before scoring so only one path writes
    # the result and audit event.
    if db.bind and db.bind.dialect.name=="postgresql":
        attempt=db.query(Attempt).filter_by(id=attempt.id).populate_existing().with_for_update().one()
    now=now or datetime.now(timezone.utc)
    ends_at=attempt.ends_at.replace(tzinfo=timezone.utc) if attempt.ends_at.tzinfo is None else attempt.ends_at.astimezone(timezone.utc)
    if attempt.submitted_at or now<ends_at:
        return False
    return finalize_attempt(db,attempt,submitted_at=ends_at,actor_id=None,action="attempt.auto_submitted")
