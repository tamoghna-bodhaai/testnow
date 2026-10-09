from datetime import datetime, timedelta, timezone
import re
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .config import settings
from .db import Base, engine, get_db
from .models import Attempt, Enrollment, ImportJob, ImportStatus, Question, Response as AnswerResponse, Role, SourceDocument, Test, TestStatus, TestVersion, User
from .schemas import EnrolIn, LoginIn, QuestionIn, QuestionOut, RegisterIn, ResponseIn, TestCreate
from .security import COOKIE, current_user, hash_password, new_session, require, uid, verify_password
from .services import audit, ensure_live, publish, score, snapshot_questions
from .storage import digest, put, signed_get
from .tasks import extract_import

app=FastAPI(title="TestNow API",version="1.0.0")
app.add_middleware(CORSMiddleware,allow_origins=settings().cors_origins.split(","),allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
@app.on_event("startup")
def startup():
    # Alembic is the production migration authority; this supports a clean local first boot.
    if "localhost" in settings().database_url: Base.metadata.create_all(engine)
@app.get("/health")
def health(db:Session=Depends(get_db)): db.execute(__import__("sqlalchemy").text("SELECT 1"));return {"status":"ok"}
@app.get("/ready")
def ready(db:Session=Depends(get_db)): db.execute(__import__("sqlalchemy").text("SELECT 1"));return {"status":"ready"}

@app.post("/auth/register",status_code=201)
def register(data:RegisterIn,response:Response,db:Session=Depends(get_db)):
    if db.query(User).filter_by(email=data.email.lower()).first():raise HTTPException(409,"Email already registered")
    user=User(id=uid(),name=data.name,email=data.email.lower(),password_hash=hash_password(data.password),role=data.role,is_verified=False);db.add(user);db.commit();new_session(response,db,user);audit(db,user.id,"user.registered","user",user.id);db.commit();return user_public(user)
@app.post("/auth/login")
def login(data:LoginIn,response:Response,db:Session=Depends(get_db)):
    user=db.query(User).filter_by(email=data.email.lower()).first()
    if not user or not verify_password(data.password,user.password_hash):raise HTTPException(401,"Invalid email or password")
    new_session(response,db,user);return user_public(user)
@app.post("/auth/logout",status_code=204)
def logout(request:Request,response:Response,user:User=Depends(current_user),db:Session=Depends(get_db)):
    import hashlib
    from .models import Session as UserSession
    raw=request.cookies.get(COOKIE); row=db.get(UserSession,hashlib.sha256(raw.encode()).hexdigest()) if raw else None
    if row: row.revoked_at=datetime.now(timezone.utc);db.commit()
    response.delete_cookie(COOKIE,path="/")
@app.get("/auth/me")
def me(user:User=Depends(current_user)):return user_public(user)
def user_public(u:User):return {"id":u.id,"name":u.name,"email":u.email,"role":u.role.value,"is_verified":u.is_verified}

async def store_upload(file:UploadFile,user:User,db:Session)->SourceDocument:
    if file.content_type!="application/pdf":raise HTTPException(415,"Only PDF uploads are accepted")
    raw=await file.read()
    if not raw.startswith(b"%PDF") or len(raw)>settings().max_upload_bytes:raise HTTPException(422,"Invalid or oversized PDF")
    checksum=digest(raw); existing=db.query(SourceDocument).filter_by(owner_id=user.id,sha256=checksum).first()
    if existing:return existing
    source=SourceDocument(id=uid(),owner_id=user.id,filename=re.sub(r"[^A-Za-z0-9._ -]","_",file.filename or "paper.pdf"),object_key=f"documents/{user.id}/{uid()}.pdf",sha256=checksum,media_type="application/pdf",size_bytes=len(raw));put(source.object_key,raw,source.media_type);db.add(source);db.commit();return source
@app.post("/teacher/imports",status_code=202)
async def create_import(question_paper:UploadFile=File(...),answer_key:UploadFile|None=File(None),user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    paper=await store_upload(question_paper,user,db); key=await store_upload(answer_key,user,db) if answer_key else None
    job=ImportJob(id=uid(),owner_id=user.id,question_document_id=paper.id,answer_document_id=key.id if key else None,status=ImportStatus.UPLOADED,extraction_meta={"phase":"Queued for extraction"});db.add(job);audit(db,user.id,"import.created","import",job.id);db.commit();extract_import.delay(job.id);return import_view(job,db)
@app.get("/teacher/imports")
def list_imports(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    query=db.query(ImportJob)
    if user.role!=Role.ADMIN: query=query.filter_by(owner_id=user.id)
    return [import_view(job,db) for job in query.order_by(ImportJob.created_at.desc()).limit(25)]
@app.get("/teacher/imports/{job_id}")
def get_import(job_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or (job.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Import not found")
    return {**import_view(job,db),"questions":[question_view(q) for q in db.query(Question).filter_by(import_job_id=job.id).order_by(Question.source_number)]}
@app.get("/teacher/imports/{job_id}/source")
def import_source(job_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or (job.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Import not found")
    return RedirectResponse(signed_get(db.get(SourceDocument,job.question_document_id).object_key))
@app.post("/teacher/imports/{job_id}/reextract",status_code=202)
def reextract_import(job_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or (job.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Import not found")
    if db.query(Question).filter_by(import_job_id=job.id).filter(Question.approved_at.is_not(None)).first():raise HTTPException(409,"Approved questions cannot be replaced; create a new import instead")
    db.query(Question).filter_by(import_job_id=job.id).delete(synchronize_session=False)
    job.status=ImportStatus.UPLOADED;job.error=None;job.extraction_meta={"phase":"Queued for improved extraction"};audit(db,user.id,"import.reextraction_requested","import",job.id);db.commit();extract_import.delay(job.id)
    return import_view(job,db)
@app.put("/teacher/imports/{job_id}/questions/{question_id}")
def edit_import_question(job_id:str,question_id:str,data:QuestionIn,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    q=db.get(Question,question_id);job=db.get(ImportJob,job_id)
    if not job or not q or q.import_job_id!=job.id or job.owner_id!=user.id:raise HTTPException(404,"Question not found")
    if q.approved_at:raise HTTPException(409,"Approved questions cannot be edited; duplicate for a new version")
    for name,value in data.model_dump().items():setattr(q,name,value)
    db.commit();return question_view(q)
@app.post("/teacher/imports/{job_id}/questions/{question_id}/approve")
def approve_question(job_id:str,question_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    q=db.get(Question,question_id);job=db.get(ImportJob,job_id)
    if not job or not q or q.import_job_id!=job.id or job.owner_id!=user.id:raise HTTPException(404,"Question not found")
    if not q.answer or q.confidence is None:raise HTTPException(422,"An answer and extraction confidence are required before approval")
    q.approved_at=datetime.now(timezone.utc)
    pending=db.query(Question).filter_by(import_job_id=job.id,approved_at=None).first()
    if not pending: job.status=ImportStatus.APPROVED; job.extraction_meta={**(job.extraction_meta or {}),"phase":"Review complete"}
    db.commit();return question_view(q)
def import_view(job:ImportJob,db:Session|None=None):
    source=db.get(SourceDocument,job.question_document_id) if db else None
    return {"id":job.id,"status":job.status.value,"error":job.error,"meta":job.extraction_meta,"created_at":job.created_at,"filename":source.filename if source else None}
def normalized_options(options):
    output=[]
    for index,option in enumerate(options or []):
        if isinstance(option,str): output.append({"value":chr(97+index),"markdown":option})
        elif isinstance(option,dict): output.append({"value":str(option.get("value",chr(97+index))),"markdown":str(option.get("markdown") or option.get("text") or "")})
    return output
def question_view(q:Question):
    confidence=q.confidence
    if confidence is not None: confidence=min(1.0,max(0.0,float(confidence)))
    return {"id":q.id,"source_number":q.source_number,"kind":q.kind.value,"stem_markdown":q.stem_markdown,"options":normalized_options(q.options),"answer":q.answer,"solution_markdown":q.solution_markdown,"scoring":q.scoring,"diagrams":q.diagrams,"source_spans":q.source_spans,"confidence":confidence,"approved":bool(q.approved_at)}
@app.get("/questions")
def questions(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    return [question_view(q) for q in db.query(Question).filter_by(owner_id=user.id).order_by(Question.created_at.desc())]
@app.get("/media/questions/{question_id}/{index}")
def question_media(question_id:str,index:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    q=db.get(Question,question_id)
    if not q or index<0 or index>=len(q.diagrams):raise HTTPException(404,"Media not found")
    allowed=q.owner_id==user.id or user.role==Role.ADMIN
    if not allowed and user.role==Role.STUDENT:
        for a in db.query(Attempt).filter_by(student_id=user.id).all():
            version=db.get(TestVersion,a.version_id)
            if version and question_id in {item["id"] for item in version.content.get("questions",[])}:allowed=True;break
    if not allowed:raise HTTPException(403,"Media not available")
    return RedirectResponse(signed_get(q.diagrams[index]["object_key"]))
@app.post("/questions",status_code=201)
def create_question(data:QuestionIn,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    q=Question(id=uid(),owner_id=user.id,approved_at=datetime.now(timezone.utc),**data.model_dump());db.add(q);audit(db,user.id,"question.created","question",q.id);db.commit();return question_view(q)

@app.post("/tests",status_code=201)
def create_test(data:TestCreate,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    if data.closes_at and data.opens_at and data.closes_at<=data.opens_at:raise HTTPException(422,"Closing time must be after opening time")
    test=Test(id=uid(),owner_id=user.id,title=data.title,subject=data.subject,duration_seconds=data.duration_seconds,opens_at=data.opens_at,closes_at=data.closes_at,max_attempts=data.max_attempts,results_policy=data.results_policy);db.add(test);db.flush()
    draft=TestVersion(id=uid(),test_id=test.id,ordinal=1,content={"questions":snapshot_questions(db,data.question_ids),"duration_seconds":data.duration_seconds,"results_policy":data.results_policy});db.add(draft);audit(db,user.id,"test.created","test",test.id);db.commit();return test_view(test,db)
@app.get("/tests")
def list_tests(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):return [test_view(t,db) for t in db.query(Test).filter_by(owner_id=user.id).order_by(Test.created_at.desc())]
@app.get("/tests/{test_id}")
def test_detail(test_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    t=db.get(Test,test_id)
    if not t or t.owner_id!=user.id:raise HTTPException(404,"Test not found")
    return test_view(t,db,True)
@app.post("/tests/{test_id}/publish")
def publish_test(test_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    t=db.get(Test,test_id)
    if not t or t.owner_id!=user.id:raise HTTPException(404,"Test not found")
    draft=db.query(TestVersion).filter_by(test_id=t.id).order_by(TestVersion.ordinal.desc()).first()
    if not draft:raise HTTPException(422,"Test has no draft")
    # Make a separately immutable snapshot for delivery.
    content=draft.content; v=TestVersion(id=uid(),test_id=t.id,ordinal=draft.ordinal+1,content=content,published_at=datetime.now(timezone.utc));db.add(v);t.status=TestStatus.PUBLISHED;t.published_version_id=v.id;audit(db,user.id,"test.published","test",t.id,{"version":v.ordinal});db.commit();return test_view(t,db)
@app.post("/tests/{test_id}/enrolments",status_code=201)
def enrol(test_id:str,data:EnrolIn,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    t=db.get(Test,test_id);student=db.query(User).filter_by(email=data.email.lower(),role=Role.STUDENT).first()
    if not t or t.owner_id!=user.id or not student:raise HTTPException(404,"Test or student not found")
    if not db.get(Enrollment,{"test_id":t.id,"student_id":student.id}):db.add(Enrollment(test_id=t.id,student_id=student.id));audit(db,user.id,"student.enrolled","test",t.id,{"student_id":student.id});db.commit()
    return {"ok":True}
def test_view(t:Test,db:Session,include=False):
    v=db.get(TestVersion,t.published_version_id) if t.published_version_id else db.query(TestVersion).filter_by(test_id=t.id).order_by(TestVersion.ordinal.desc()).first();out={"id":t.id,"title":t.title,"subject":t.subject,"status":t.status.value,"duration_seconds":t.duration_seconds,"opens_at":t.opens_at,"closes_at":t.closes_at,"max_attempts":t.max_attempts,"results_policy":t.results_policy,"question_count":len(v.content.get("questions",[])) if v else 0}
    if include and v:out["questions"]=v.content["questions"]
    return out

@app.get("/student/tests")
def student_tests(user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    rows=db.query(Test).join(Enrollment,Enrollment.test_id==Test.id).filter(Enrollment.student_id==user.id,Test.status==TestStatus.PUBLISHED).all();return [test_view(t,db) for t in rows]
@app.post("/tests/{test_id}/attempts",status_code=201)
def start_attempt(test_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    test=db.get(Test,test_id); enrolled=db.get(Enrollment,{"test_id":test_id,"student_id":user.id})
    if not test or not enrolled:raise HTTPException(403,"You are not enrolled")
    ensure_live(test); active=db.query(Attempt).filter_by(test_id=test_id,student_id=user.id,submitted_at=None).first()
    if active:return attempt_view(active,db)
    prior=db.query(Attempt).filter_by(test_id=test_id,student_id=user.id).count()
    if prior>=test.max_attempts:raise HTTPException(409,"Attempt limit reached")
    now=datetime.now(timezone.utc); ends=min(now+timedelta(seconds=test.duration_seconds),test.closes_at) if test.closes_at else now+timedelta(seconds=test.duration_seconds)
    a=Attempt(id=uid(),test_id=test.id,version_id=test.published_version_id,student_id=user.id,started_at=now,ends_at=ends);db.add(a);audit(db,user.id,"attempt.started","attempt",a.id);db.commit();return attempt_view(a,db)
@app.get("/attempts/{attempt_id}")
def get_attempt(attempt_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    if not a or a.student_id!=user.id:raise HTTPException(404,"Attempt not found")
    return attempt_view(a,db)
@app.put("/attempts/{attempt_id}/responses/{question_id}")
def save_response(attempt_id:str,question_id:str,data:ResponseIn,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    if not a or a.student_id!=user.id or a.submitted_at:raise HTTPException(409,"Attempt cannot be changed")
    if datetime.now(timezone.utc)>=a.ends_at:raise HTTPException(409,"Attempt has expired")
    content=db.get(TestVersion,a.version_id).content
    if question_id not in {q["id"] for q in content["questions"]}:raise HTTPException(422,"Question is not part of this attempt")
    if data.revision!=a.revision:raise HTTPException(409,"Attempt changed in another session")
    response=db.get(AnswerResponse,{"attempt_id":a.id,"question_id":question_id})
    if not response:response=AnswerResponse(attempt_id=a.id,question_id=question_id,answer=data.answer,marked_for_review=data.marked_for_review);db.add(response)
    else:response.answer=data.answer;response.marked_for_review=data.marked_for_review
    a.revision+=1;db.commit();return {"revision":a.revision,"server_time":datetime.now(timezone.utc)}
@app.post("/attempts/{attempt_id}/submit")
def submit(attempt_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    if not a or a.student_id!=user.id:raise HTTPException(404,"Attempt not found")
    if a.submitted_at:return attempt_view(a,db)
    version=db.get(TestVersion,a.version_id); responses={r.question_id:r.answer for r in db.query(AnswerResponse).filter_by(attempt_id=a.id)};a.score,_=score(version.content,responses);a.submitted_at=datetime.now(timezone.utc);audit(db,user.id,"attempt.submitted","attempt",a.id);db.commit();return attempt_view(a,db)
@app.get("/results/{attempt_id}")
def result(attempt_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    if not a or a.student_id!=user.id or not a.submitted_at:raise HTTPException(404,"Result not found")
    test=db.get(Test,a.test_id); policy=test.results_policy; release=policy.get("release_at")
    if policy.get("mode")!="immediate" and (not release or datetime.now(timezone.utc)<datetime.fromisoformat(release.replace("Z","+00:00"))):raise HTTPException(403,"Results have not been released")
    responses={r.question_id:r.answer for r in db.query(AnswerResponse).filter_by(attempt_id=a.id)};_,items=score(db.get(TestVersion,a.version_id).content,responses);return {"attempt":attempt_view(a,db),"questions":items}
def attempt_view(a:Attempt,db:Session):
    v=db.get(TestVersion,a.version_id);resp={r.question_id:{"answer":r.answer,"marked_for_review":r.marked_for_review} for r in db.query(AnswerResponse).filter_by(attempt_id=a.id)};return {"id":a.id,"test_id":a.test_id,"started_at":a.started_at,"ends_at":a.ends_at,"submitted_at":a.submitted_at,"score":a.score,"revision":a.revision,"questions":[{k:x[k] for k in ("id","kind","stem_markdown","options","diagrams","scoring")} for x in v.content["questions"]],"responses":resp}
