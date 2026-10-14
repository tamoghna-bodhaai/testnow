from datetime import datetime, timedelta, timezone
import csv, io, re
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import exists, func, or_
from sqlalchemy.orm import Session
from .config import settings
from .db import Base, engine, get_db
from .models import AccountStatus, Attempt, Classroom, ClassMembership, Enrollment, ImportJob, ImportStatus, Question, QuestionBank, QuestionKind, QuestionScope, QuestionTaxonomy, Response as AnswerResponse, Role, SourceDocument, TaxonomyNode, Test, TestClassAssignment, TestStatus, TestVersion, User
from .schemas import AnswerKeyIn, ClassroomCreate, EnrolIn, LoginIn, QuestionBankCreate, QuestionIn, QuestionOut, ResponseIn, RosterUpdate, StudentCreate, StudentUpdate, TeacherCreate, TeacherUpdate, TestClassAssignmentsIn, TestCreate
from .security import COOKIE, current_user, hash_password, new_session, require, revoke_sessions, uid, verify_password
from .services import audit, ensure_live, publish, score, safe_question_kind, snapshot_questions
from .storage import client as storage_client, digest, get, put, signed_get
from .tasks import extract_import, extract_bank_import
from .seed import ensure_demo_data

app=FastAPI(title="TestNow API",version="1.0.0")
app.add_middleware(CORSMiddleware,allow_origins=settings().cors_origins.split(","),allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
@app.on_event("startup")
def startup():
    # Alembic is the production migration authority; this supports a clean local first boot.
    if "localhost" in settings().database_url or settings().database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    production=not ("localhost" in settings().database_url or settings().database_url.startswith("sqlite"))
    if production and settings().seed_demo_data:
        raise RuntimeError("SEED_DEMO_DATA must be false in production")
    if settings().seed_demo_data:
        from .db import SessionLocal
        db=SessionLocal()
        try: ensure_demo_data(db, settings().super_admin_email)
        finally: db.close()
    # The production administrator is deliberately configured, never derived from
    # a public request. Local development may use seeded demo data instead.
    configured_code=settings().super_admin_access_code
    if production and not configured_code:
        raise RuntimeError("SUPER_ADMIN_ACCESS_CODE is required in production")
    if configured_code and not re.fullmatch(r"\d{5}",configured_code):
        raise RuntimeError("SUPER_ADMIN_ACCESS_CODE must be exactly five digits")
    if configured_code:
        from .db import SessionLocal
        db=SessionLocal()
        try:
            email=settings().super_admin_email.lower()
            admin=db.query(User).filter_by(email=email).first()
            if not admin:
                admin=User(id=uid(),name="Super Admin",email=email,password_hash="",access_code_hash=hash_password(configured_code),role=Role.ADMIN,status=AccountStatus.ACTIVE,is_verified=True,access_code_issued_at=datetime.now(timezone.utc))
                db.add(admin);db.commit()
            elif admin.role!=Role.ADMIN or admin.status!=AccountStatus.ACTIVE or not admin.access_code_hash:
                admin.role=Role.ADMIN;admin.status=AccountStatus.ACTIVE;admin.access_code_hash=hash_password(configured_code);admin.access_code_issued_at=datetime.now(timezone.utc);db.commit()
        finally: db.close()
@app.get("/health")
def health(db:Session=Depends(get_db)): db.execute(__import__("sqlalchemy").text("SELECT 1"));return {"status":"ok"}
@app.get("/ready")
def ready(db:Session=Depends(get_db)):
    """Readiness covers every required internal dependency, not just Postgres."""
    from redis import Redis
    db.execute(__import__("sqlalchemy").text("SELECT 1"))
    try:
        Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
        storage_client().head_bucket(Bucket=settings().s3_bucket)
    except Exception as exc:
        raise HTTPException(503,"A required service is unavailable") from exc
    return {"status":"ready"}

@app.post("/auth/login")
def login(data:LoginIn,response:Response,db:Session=Depends(get_db)):
    from redis import Redis
    key=f"login-attempts:{data.email.lower()}"
    try:
        limiter=Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
        if int(limiter.get(key) or 0)>=10:raise HTTPException(429,"Too many sign-in attempts. Try again later")
    except HTTPException: raise
    except Exception as exc: raise HTTPException(503,"Sign-in is temporarily unavailable") from exc
    user=db.query(User).filter_by(email=data.email.lower()).first()
    # The super-admin intentionally uses the teacher-facing login choice so the
    # privileged portal is not discoverable from the public interface.
    role_matches=bool(user) and (user.role==data.role or (data.role==Role.TEACHER and user.role==Role.ADMIN))
    if not user or not role_matches or user.status!=AccountStatus.ACTIVE or not user.access_code_hash or not verify_password(data.access_code,user.access_code_hash):
        attempts=limiter.incr(key)
        if attempts==1:limiter.expire(key,300)
        raise HTTPException(429 if attempts>10 else 401,"Too many sign-in attempts. Try again later" if attempts>10 else "Invalid email, role, or access code")
    limiter.delete(key);user.last_login_at=datetime.now(timezone.utc);new_session(response,db,user);db.commit();return user_public(user)
@app.post("/auth/logout",status_code=204)
def logout(request:Request,response:Response,user:User=Depends(current_user),db:Session=Depends(get_db)):
    import hashlib
    from .models import Session as UserSession
    raw=request.cookies.get(COOKIE); row=db.get(UserSession,hashlib.sha256(raw.encode()).hexdigest()) if raw else None
    if row: row.revoked_at=datetime.now(timezone.utc);db.commit()
    response.delete_cookie(COOKIE,path="/")
@app.get("/auth/me")
def me(user:User=Depends(current_user)):return user_public(user)
def user_public(u:User):return {"id":u.id,"name":u.name,"email":u.email,"role":u.role.value,"status":u.status.value,"is_verified":u.is_verified,"last_login_at":u.last_login_at}

def set_access_code(user:User, code:str, db:Session):
    user.access_code_hash=hash_password(code);user.access_code_issued_at=datetime.now(timezone.utc);user.status=AccountStatus.ACTIVE;revoke_sessions(db,user.id)

def teacher_view(teacher:User,db:Session):
    classes=db.query(Classroom).filter_by(owner_id=teacher.id).all();class_ids=[row.id for row in classes]
    student_ids={row.student_id for row in db.query(ClassMembership).filter(ClassMembership.class_id.in_(class_ids)).all()} if class_ids else set()
    tests=db.query(Test).filter_by(owner_id=teacher.id).all();test_ids=[row.id for row in tests]
    attempts=db.query(Attempt).filter(Attempt.test_id.in_(test_ids)).all() if test_ids else []
    return {**user_public(teacher),"batch_count":len(classes),"student_count":len(student_ids),"test_count":len(tests),"attempt_count":len(attempts),"submitted_count":sum(1 for row in attempts if row.submitted_at)}

@app.get("/admin/dashboard")
def admin_dashboard(user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    teachers=db.query(User).filter_by(role=Role.TEACHER).order_by(User.created_at.desc()).all()
    return {"summary":{"teachers":len(teachers),"active_teachers":sum(1 for teacher in teachers if teacher.status==AccountStatus.ACTIVE),"suspended_teachers":sum(1 for teacher in teachers if teacher.status==AccountStatus.SUSPENDED),"students":db.query(User).filter_by(role=Role.STUDENT).count()},"teachers":[teacher_view(teacher,db) for teacher in teachers]}

def bank_view(bank:QuestionBank,db:Session):
    questions=db.query(Question).filter_by(bank_id=bank.id,scope=QuestionScope.GLOBAL)
    jobs=db.query(ImportJob).filter_by(bank_id=bank.id)
    return {"id":bank.id,"slug":bank.slug,"name":bank.name,"exam_code":bank.exam_code,"status":bank.status,"revision":bank.revision,"is_global":bank.is_global,"question_count":questions.count(),"published_count":questions.filter(Question.approved_at.is_not(None)).count(),"import_count":jobs.count(),"flagged_count":sum(1 for q in questions if (q.interaction or {}).get("ingestion_issues"))}

def bank_slug(value:str,db:Session)->str:
    base=re.sub(r"[^a-z0-9]+","-",value.lower()).strip("-")[:60] or "question-bank"; candidate=base;index=2
    while db.query(QuestionBank).filter_by(slug=candidate).first():candidate=f"{base[:54]}-{index}";index+=1
    return candidate

@app.get("/admin/question-banks")
def admin_question_banks(user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    return [bank_view(bank,db) for bank in db.query(QuestionBank).order_by(QuestionBank.created_at.desc())]

@app.post("/admin/question-banks",status_code=201)
def create_question_bank(data:QuestionBankCreate,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=QuestionBank(id=uid(),slug=bank_slug(data.name,db),name=data.name.strip(),exam_code=(data.exam_code or "GENERAL").strip() or "GENERAL",is_global=False,status="draft",revision=1)
    db.add(bank);audit(db,user.id,"question_bank.created","question_bank",bank.id,{"name":bank.name});db.commit();return bank_view(bank,db)

@app.get("/admin/question-banks/{bank_id}")
def admin_question_bank(bank_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank:raise HTTPException(404,"Question bank not found")
    jobs=db.query(ImportJob).filter_by(bank_id=bank.id).order_by(ImportJob.created_at.desc()).all()
    questions=db.query(Question).filter_by(bank_id=bank.id).order_by(Question.created_at.desc()).all()
    return {**bank_view(bank,db),"imports":[import_view(job,db) for job in jobs],"questions":[question_view(q,db) for q in questions]}

def source_stem(filename:str)->str:
    value=re.sub(r"\.(pdf|docx)$","",filename,flags=re.I)
    value=re.sub(r"(answer[_ -]?key|answers?|solutions?)","",value,flags=re.I)
    return re.sub(r"[^a-z0-9]+","",value.lower())

async def store_bank_source(file:UploadFile,user:User,bank:QuestionBank,db:Session)->SourceDocument:
    name=re.sub(r"[^A-Za-z0-9._ -]","_",file.filename or "source")
    extension=name.rsplit(".",1)[-1].lower() if "." in name else ""
    if extension not in {"pdf","docx"}:raise HTTPException(415,"Only PDF and DOCX source files are accepted")
    raw=await file.read()
    if not raw or len(raw)>settings().max_upload_bytes:raise HTTPException(422,"Invalid or oversized source document")
    if extension=="pdf" and not raw.startswith(b"%PDF"):raise HTTPException(422,"Invalid PDF")
    checksum=digest(raw);existing=db.query(SourceDocument).filter(SourceDocument.sha256==checksum,SourceDocument.object_key.like(f"bank-sources/{bank.id}/%")).first()
    if existing:return existing
    media="application/pdf" if extension=="pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    source=SourceDocument(id=uid(),owner_id=user.id,filename=name,object_key=f"bank-sources/{bank.id}/{uid()}-{name}",sha256=checksum,media_type=media,size_bytes=len(raw));put(source.object_key,raw,media);db.add(source);db.flush();return source

@app.post("/admin/question-banks/{bank_id}/imports",status_code=202)
async def create_bank_imports(bank_id:str,subject:str,question_files:list[UploadFile]=File(...),answer_files:list[UploadFile]=File(default=[]),user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank or bank.status=="published":raise HTTPException(409,"Create a revision import for a published bank")
    subject=subject.strip()
    if not subject:raise HTTPException(422,"Subject is required")
    questions=[await store_bank_source(file,user,bank,db) for file in question_files]
    answers=[await store_bank_source(file,user,bank,db) for file in answer_files]
    answer_by_stem={source_stem(item.filename):item for item in answers}; jobs=[]
    for source in questions:
        paired=answer_by_stem.get(source_stem(source.filename)) or (answers[0] if len(questions)==len(answers)==1 else None)
        meta={"subject":subject,"source_role":"combined" if not paired else "question","pairing":"matched" if paired else "missing_answer_source","phase":"Queued for extraction"}
        job=ImportJob(id=uid(),owner_id=user.id,bank_id=bank.id,question_document_id=source.id,answer_document_id=paired.id if paired else None,status=ImportStatus.UPLOADED,extraction_meta=meta);db.add(job);jobs.append(job)
    bank.status="processing";audit(db,user.id,"question_bank.import_created","question_bank",bank.id,{"subject":subject,"files":len(questions)});db.commit()
    for job in jobs:extract_bank_import.delay(job.id)
    return {"bank":bank_view(bank,db),"imports":[import_view(job,db) for job in jobs]}

@app.post("/admin/question-banks/{bank_id}/questions/{question_id}/resolve")
def resolve_bank_question(bank_id:str,question_id:str,data:QuestionIn,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    q=db.get(Question,question_id);bank=db.get(QuestionBank,bank_id)
    if not bank or not q or q.bank_id!=bank.id:raise HTTPException(404,"Question not found")
    if q.kind in {QuestionKind.SINGLE,QuestionKind.MULTIPLE,QuestionKind.ASSERTION} and len(data.options)<2:raise HTTPException(422,"Choice questions need at least two options")
    # Review edits may correct rendered content and scoring, but must never
    # erase the immutable source spans/diagram provenance captured at ingest.
    for name,value in data.model_dump().items():
        if name not in {"source_spans","diagrams","interaction"}:setattr(q,name,value)
    if q.kind!=QuestionKind.SUBJECTIVE and not q.answer:raise HTTPException(422,"A reviewed answer is required")
    q.interaction={**(q.interaction or {}),"ingestion_issues":[],"reviewed_by":user.id};q.approved_at=datetime.now(timezone.utc)
    job=db.get(ImportJob,q.import_job_id)
    if job and not db.query(Question).filter_by(import_job_id=job.id,approved_at=None).first():job.status=ImportStatus.APPROVED;job.extraction_meta={**(job.extraction_meta or {}),"phase":"Review complete","flagged_count":0}
    db.commit();return question_view(q,db)

@app.post("/admin/question-banks/{bank_id}/publish")
def publish_question_bank(bank_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank:raise HTTPException(404,"Question bank not found")
    jobs=db.query(ImportJob).filter_by(bank_id=bank.id).all();questions=db.query(Question).filter_by(bank_id=bank.id).all()
    if not jobs:raise HTTPException(422,"Upload at least one source document before publishing")
    if any(job.status!=ImportStatus.APPROVED for job in jobs):raise HTTPException(422,"Resolve failed or pending imports before publishing")
    if not questions or any(not q.approved_at or (q.interaction or {}).get("ingestion_issues") for q in questions):raise HTTPException(422,"Resolve every flagged question before publishing")
    bank.is_global=True;bank.status="published";audit(db,user.id,"question_bank.published","question_bank",bank.id,{"questions":len(questions)});db.commit();return bank_view(bank,db)

@app.get("/admin/teachers")
def admin_teachers(user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    return [teacher_view(teacher,db) for teacher in db.query(User).filter_by(role=Role.TEACHER).order_by(User.created_at.desc())]

@app.post("/admin/teachers",status_code=201)
def create_teacher(data:TeacherCreate,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    if db.query(User).filter_by(email=data.email).first():raise HTTPException(409,"Email already belongs to an account")
    teacher=User(id=uid(),name=data.name.strip(),email=data.email,password_hash="",role=Role.TEACHER,status=AccountStatus.ACTIVE,is_verified=True)
    set_access_code(teacher,data.access_code,db);db.add(teacher);audit(db,user.id,"teacher.created","user",teacher.id);db.commit();return teacher_view(teacher,db)

@app.put("/admin/teachers/{teacher_id}")
def update_teacher(teacher_id:str,data:TeacherUpdate,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    teacher=db.get(User,teacher_id)
    if not teacher or teacher.role!=Role.TEACHER:raise HTTPException(404,"Teacher not found")
    if data.email and data.email!=teacher.email:
        if db.query(User).filter(User.email==data.email,User.id!=teacher.id).first():raise HTTPException(409,"Email already belongs to an account")
        teacher.email=data.email
    if data.name:teacher.name=data.name.strip()
    if data.status:
        if data.status==AccountStatus.PENDING_CODE:raise HTTPException(422,"Teachers must be active or suspended")
        teacher.status=data.status
        if data.status==AccountStatus.SUSPENDED:revoke_sessions(db,teacher.id)
    if data.access_code:set_access_code(teacher,data.access_code,db)
    audit(db,user.id,"teacher.updated","user",teacher.id,{"status":teacher.status.value});db.commit();return teacher_view(teacher,db)

def teacher_owns_student(teacher:User,student_id:str,db:Session)->bool:
    return db.query(ClassMembership).join(Classroom,ClassMembership.class_id==Classroom.id).filter(Classroom.owner_id==teacher.id,ClassMembership.student_id==student_id).first() is not None

@app.get("/students")
def list_students(user:User=Depends(require(Role.TEACHER)),db:Session=Depends(get_db)):
    rows=db.query(User).join(ClassMembership,User.id==ClassMembership.student_id).join(Classroom,ClassMembership.class_id==Classroom.id).filter(Classroom.owner_id==user.id,User.role==Role.STUDENT).distinct().order_by(User.name).all()
    return [user_public(row) for row in rows]

@app.post("/students",status_code=201)
def create_student(data:StudentCreate,user:User=Depends(require(Role.TEACHER)),db:Session=Depends(get_db)):
    classroom=classroom_for_owner(data.class_id,user,db);student=db.query(User).filter_by(email=data.email).first();created=False
    if student and student.role!=Role.STUDENT:raise HTTPException(409,"Email belongs to a non-student account")
    if not student:
        student=User(id=uid(),name=data.name.strip(),email=data.email,password_hash="",role=Role.STUDENT,status=AccountStatus.ACTIVE,is_verified=True);set_access_code(student,data.access_code,db);db.add(student);db.flush();created=True
    elif not teacher_owns_student(user,student.id,db):
        # A global student can be shared only through the dedicated membership
        # endpoint; a new teacher must never take over their profile or code.
        raise HTTPException(409,"Student already exists. Add an existing student to the class without changing account details.")
    else:
        student.name=data.name.strip();set_access_code(student,data.access_code,db)
    if not db.get(ClassMembership,{"class_id":classroom.id,"student_id":student.id}):db.add(ClassMembership(class_id=classroom.id,student_id=student.id))
    audit(db,user.id,"student.created" if created else "student.updated","user",student.id,{"class_id":classroom.id});db.commit();return {"student":user_public(student),"created":created}

@app.put("/students/{student_id}")
def update_student(student_id:str,data:StudentUpdate,user:User=Depends(require(Role.TEACHER)),db:Session=Depends(get_db)):
    student=db.get(User,student_id)
    if not student or student.role!=Role.STUDENT or not teacher_owns_student(user,student_id,db):raise HTTPException(404,"Student not found")
    if data.name:student.name=data.name.strip()
    if data.access_code:set_access_code(student,data.access_code,db)
    audit(db,user.id,"student.updated","user",student.id);db.commit();return user_public(student)

@app.delete("/students/{student_id}",status_code=204)
def delete_student(student_id:str,user:User=Depends(require(Role.TEACHER)),db:Session=Depends(get_db)):
    student=db.get(User,student_id)
    if not student or student.role!=Role.STUDENT or not teacher_owns_student(user,student_id,db):raise HTTPException(404,"Student not found")
    if db.query(ClassMembership).filter_by(student_id=student.id).count() or db.query(Attempt).filter_by(student_id=student.id).count():raise HTTPException(409,"Remove every batch membership and retain students with assessment history")
    db.delete(student);audit(db,user.id,"student.deleted","user",student_id);db.commit()

async def store_upload(file:UploadFile,user:User,db:Session)->SourceDocument:
    if file.content_type!="application/pdf":raise HTTPException(415,"Only PDF uploads are accepted")
    raw=await file.read()
    if not raw.startswith(b"%PDF") or len(raw)>settings().max_upload_bytes:raise HTTPException(422,"Invalid or oversized PDF")
    checksum=digest(raw); existing=db.query(SourceDocument).filter_by(owner_id=user.id,sha256=checksum).first()
    if existing:return existing
    source=SourceDocument(id=uid(),owner_id=user.id,filename=re.sub(r"[^A-Za-z0-9._ -]","_",file.filename or "paper.pdf"),object_key=f"documents/{user.id}/{uid()}.pdf",sha256=checksum,media_type="application/pdf",size_bytes=len(raw));put(source.object_key,raw,source.media_type);db.add(source);db.commit();return source
@app.post("/teacher/imports",status_code=202)
async def create_import(question_paper:UploadFile=File(...),answer_key:UploadFile=File(...),user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    paper=await store_upload(question_paper,user,db); key=await store_upload(answer_key,user,db)
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
@app.put("/teacher/imports/{job_id}/questions/{question_id}/answer-key")
def save_answer_key(job_id:str,question_id:str,data:AnswerKeyIn,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    q=db.get(Question,question_id);job=db.get(ImportJob,job_id)
    if not job or not q or q.import_job_id!=job.id or (job.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Question not found")
    if q.approved_at:raise HTTPException(409,"Approved questions cannot be edited")
    q.answer=canonical_answer(data.answer,q.options)
    q.confidence=data.confidence if data.confidence is not None else (min(1.0,max(0.0,float(q.confidence))) if q.confidence is not None else 0.0)
    audit(db,user.id,"question.answer_key_saved","question",q.id);db.commit();return question_view(q)
@app.post("/teacher/imports/{job_id}/questions/{question_id}/approve")
def approve_question(job_id:str,question_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    q=db.get(Question,question_id);job=db.get(ImportJob,job_id)
    if not job or not q or q.import_job_id!=job.id or job.owner_id!=user.id:raise HTTPException(404,"Question not found")
    if not q.answer:raise HTTPException(422,"Select and save an answer key before approving this question")
    # Confidence is audit metadata, never a reason to block teacher approval. Legacy
    # imports may have omitted it or stored it on a 0-100 scale.
    q.confidence=min(1.0,max(0.0,float(q.confidence))) if q.confidence is not None else 0.0
    q.approved_at=datetime.now(timezone.utc)
    db.flush()
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
def canonical_answer(answer,options):
    """Store one stable answer format that matches the exam client's response shape."""
    if not isinstance(answer,dict):raise HTTPException(422,"Answer key must be an object")
    raw=answer.get("options",answer.get("option",answer.get("value",answer.get("correct_option"))))
    if isinstance(raw,list):
        selected=[]
        for item in raw:
            if isinstance(item,int) and 0<=item<len(options or []): selected.append(item);continue
            if isinstance(item,str):
                for index,option in enumerate(normalized_options(options)):
                    if item.strip().lower() in {str(index),str(index+1),chr(65+index).lower(),option["value"].lower()}:
                        selected.append(index);break
        if selected:return {"options":sorted(set(selected))}
        raise HTTPException(422,"Choose one or more valid options")
    if isinstance(raw,int) and 0<=raw<len(options or []):return {"option":raw}
    if isinstance(raw,str):
        for index,option in enumerate(normalized_options(options)):
            if raw.strip().lower() in {str(index),str(index+1),chr(65+index).lower(),option["value"].lower()}:
                return {"option":index}
    # Numerical / subjective answer keys keep their reviewed structured value.
    if raw is not None:return {"value":raw}
    raise HTTPException(422,"Choose a valid option or provide an answer value")
def question_view(q:Question, db:Session|None=None):
    confidence=q.confidence
    if confidence is not None: confidence=min(1.0,max(0.0,float(confidence)))
    taxonomy=[]
    if db and q.bank_id:
        taxonomy=[{"id":node.id,"parent_id":node.parent_id,"kind":node.kind,"name":node.name,"code":node.code} for node in db.query(TaxonomyNode).join(QuestionTaxonomy,QuestionTaxonomy.taxonomy_node_id==TaxonomyNode.id).filter(QuestionTaxonomy.question_id==q.id).order_by(TaxonomyNode.kind,TaxonomyNode.name)]
    options=normalized_options(q.options); kind=safe_question_kind(q.kind,options)
    answer=None if kind=="subjective" and len(options)<2 else q.answer
    return {"id":q.id,"source_number":q.source_number,"kind":kind,"stem_markdown":q.stem_markdown,"options":options,"answer":answer,"solution_markdown":q.solution_markdown,"scoring":q.scoring,"diagrams":q.diagrams,"source_spans":q.source_spans,"interaction":q.interaction or {},"confidence":confidence,"approved":bool(q.approved_at),"bank_id":q.bank_id,"scope":q.scope.value,"taxonomy":taxonomy}
@app.get("/questions")
def questions(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    rows=db.query(Question).filter((Question.owner_id==user.id)|((Question.scope==QuestionScope.GLOBAL)&(Question.approved_at.is_not(None)))).order_by(Question.created_at.desc())
    return [question_view(q,db) for q in rows]
@app.get("/question-banks")
def question_banks(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    return [{"id":b.id,"slug":b.slug,"name":b.name,"exam_code":b.exam_code,"is_global":b.is_global,"question_count":db.query(Question).filter_by(bank_id=b.id,scope=QuestionScope.GLOBAL).filter(Question.approved_at.is_not(None)).count()} for b in db.query(QuestionBank).filter_by(is_global=True).order_by(QuestionBank.name)]
@app.get("/question-banks/{bank_id}/taxonomy")
def bank_taxonomy(bank_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    if not db.get(QuestionBank,bank_id):raise HTTPException(404,"Question bank not found")
    return [{"id":n.id,"parent_id":n.parent_id,"kind":n.kind,"name":n.name,"code":n.code} for n in db.query(TaxonomyNode).filter_by(bank_id=bank_id).order_by(TaxonomyNode.kind,TaxonomyNode.name)]
def global_bank_query(db:Session,bank_id:str,subject_id:str|None=None,chapter_id:str|None=None,subtopic_id:str|None=None,published:bool=True):
    """One-row-per-question query shared by listing and live availability."""
    query=db.query(Question).filter(Question.bank_id==bank_id,Question.scope==QuestionScope.GLOBAL)
    if published:query=query.filter(Question.approved_at.is_not(None))
    # Correlated predicates avoid joining question rows (which contain JSON
    # fields) and keep counts correct even if taxonomy links are duplicated.
    if subject_id:
        query=query.filter(exists().where(
            QuestionTaxonomy.question_id==Question.id,
            QuestionTaxonomy.taxonomy_node_id==TaxonomyNode.id,
            TaxonomyNode.id==subject_id,
            TaxonomyNode.kind=="subject",
        ))
    if chapter_id:
        query=query.filter(exists().where(
            QuestionTaxonomy.question_id==Question.id,
            QuestionTaxonomy.taxonomy_node_id==TaxonomyNode.id,
            TaxonomyNode.id==chapter_id,
            TaxonomyNode.kind=="chapter",
        ))
    if subtopic_id:
        query=query.filter(exists().where(
            QuestionTaxonomy.question_id==Question.id,
            QuestionTaxonomy.taxonomy_node_id==TaxonomyNode.id,
            TaxonomyNode.id==subtopic_id,
            TaxonomyNode.kind=="topic",
        ))
    return query
@app.get("/question-banks/{bank_id}/question-counts")
def bank_question_counts(
    bank_id:str,
    subject_id:str|None=None,
    chapter_id:str|None=None,
    subtopic_id:str|None=None,
    user:User=Depends(require(Role.TEACHER,Role.ADMIN)),
    db:Session=Depends(get_db),
):
    """Published question availability, grouped by type for the active taxonomy filter."""
    bank=db.get(QuestionBank,bank_id)
    if not bank or not bank.is_global:raise HTTPException(404,"Question bank not found")
    query=global_bank_query(db,bank_id,subject_id,chapter_id,subtopic_id)
    rows=query.with_entities(Question.kind,func.count(Question.id)).group_by(Question.kind).all()
    counts={kind.value:int(total) for kind,total in rows}
    return {"total":sum(counts.values()),"counts":counts}
@app.get("/question-banks/{bank_id}/questions")
def bank_questions(
    bank_id:str,
    subject_id:str|None=None,
    chapter_id:str|None=None,
    subtopic_id:str|None=None,
    kinds:list[str]=Query(default=[]),
    page:int=Query(default=1,ge=1),
    page_size:int=Query(default=25,ge=1,le=100),
    include_review:bool=False,
    user:User=Depends(require(Role.TEACHER,Role.ADMIN)),
    db:Session=Depends(get_db),
):
    """Paginated global-bank selection.  Filters always stay server side."""
    bank=db.get(QuestionBank,bank_id)
    if not bank or not bank.is_global:raise HTTPException(404,"Question bank not found")
    if include_review and user.role!=Role.ADMIN:raise HTTPException(403,"Only administrators can inspect unpublished bank questions")
    valid_kinds={kind.value for kind in QuestionKind}
    if unknown:=set(kinds)-valid_kinds:raise HTTPException(422,f"Unsupported question type: {sorted(unknown)[0]}")
    query=global_bank_query(db,bank_id,subject_id,chapter_id,subtopic_id,published=not include_review)
    if kinds:query=query.filter(Question.kind.in_([QuestionKind(value) for value in kinds]))
    total=query.count()
    rows=query.order_by(Question.created_at.desc(),Question.id).offset((page-1)*page_size).limit(page_size).all()
    return {"items":[question_view(row,db) for row in rows],"page":page,"page_size":page_size,"total":total,"has_more":page*page_size<total}
@app.get("/media/questions/{question_id}/{index}")
def question_media(question_id:str,index:int,user:User=Depends(current_user),db:Session=Depends(get_db)):
    q=db.get(Question,question_id)
    if not q or index<0 or index>=len(q.diagrams):raise HTTPException(404,"Media not found")
    allowed=q.owner_id==user.id or q.scope==QuestionScope.GLOBAL or user.role==Role.ADMIN
    if not allowed and user.role==Role.STUDENT:
        for a in db.query(Attempt).filter_by(student_id=user.id).all():
            version=db.get(TestVersion,a.version_id)
            if version and question_id in {item["id"] for item in version.content.get("questions",[])}:allowed=True;break
    if not allowed:raise HTTPException(403,"Media not available")
    # Do not redirect the browser to an internal object-store hostname. In local and
    # private deployments that hostname is unreachable from the browser, producing a
    # broken image despite successful diagram extraction.
    return StreamingResponse(iter([get(q.diagrams[index]["object_key"])]),media_type="image/png",headers={"Cache-Control":"private, max-age=300"})
@app.post("/questions",status_code=201)
def create_question(data:QuestionIn,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    q=Question(id=uid(),owner_id=user.id,approved_at=datetime.now(timezone.utc),**data.model_dump());db.add(q);audit(db,user.id,"question.created","question",q.id);db.commit();return question_view(q)

def classroom_for_owner(class_id:str,user:User,db:Session)->Classroom:
    classroom=db.get(Classroom,class_id)
    if not classroom or (classroom.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Class not found")
    return classroom
def classroom_view(classroom:Classroom,db:Session,include_students:bool=False):
    members=db.query(ClassMembership).filter_by(class_id=classroom.id).all()
    result={"id":classroom.id,"name":classroom.name,"description":classroom.description,"student_count":len(members),"created_at":classroom.created_at}
    if include_students:
        result["students"]=[user_public(db.get(User,m.student_id)) for m in members if db.get(User,m.student_id)]
    return result
@app.get("/classes")
def list_classes(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    query=db.query(Classroom)
    if user.role!=Role.ADMIN:query=query.filter_by(owner_id=user.id)
    return [classroom_view(c,db) for c in query.order_by(Classroom.created_at.desc())]
@app.post("/classes",status_code=201)
def create_class(data:ClassroomCreate,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    if db.query(Classroom).filter_by(owner_id=user.id,name=data.name.strip()).first():raise HTTPException(409,"A class with this name already exists")
    classroom=Classroom(id=uid(),owner_id=user.id,name=data.name.strip(),description=data.description.strip() if data.description else None)
    db.add(classroom);audit(db,user.id,"class.created","class",classroom.id);db.commit();return classroom_view(classroom,db)
@app.get("/classes/{class_id}")
def get_class(class_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    return classroom_view(classroom_for_owner(class_id,user,db),db,True)
@app.put("/classes/{class_id}")
def update_class(class_id:str,data:ClassroomCreate,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    classroom=classroom_for_owner(class_id,user,db); classroom.name=data.name.strip();classroom.description=data.description.strip() if data.description else None;db.commit();return classroom_view(classroom,db)
@app.delete("/classes/{class_id}",status_code=204)
def delete_class(class_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    classroom=classroom_for_owner(class_id,user,db)
    db.query(TestClassAssignment).filter_by(class_id=classroom.id).delete(synchronize_session=False)
    db.query(ClassMembership).filter_by(class_id=classroom.id).delete(synchronize_session=False)
    db.delete(classroom);audit(db,user.id,"class.deleted","class",class_id);db.commit()
@app.post("/classes/{class_id}/members")
def add_class_members(class_id:str,data:RosterUpdate,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    classroom=classroom_for_owner(class_id,user,db);added=[];existing=[];unknown=[]
    for email in dict.fromkeys(str(x).lower() for x in data.emails):
        student=db.query(User).filter_by(email=email,role=Role.STUDENT).first()
        if not student:unknown.append(email);continue
        if db.get(ClassMembership,{"class_id":classroom.id,"student_id":student.id}):existing.append(email);continue
        db.add(ClassMembership(class_id=classroom.id,student_id=student.id));added.append(email)
    audit(db,user.id,"class.roster_updated","class",classroom.id,{"added":added,"unknown":unknown});db.commit()
    return {"added":added,"existing":existing,"unknown":unknown,"classroom":classroom_view(classroom,db,True)}
@app.delete("/classes/{class_id}/members/{student_id}",status_code=204)
def remove_class_member(class_id:str,student_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    classroom=classroom_for_owner(class_id,user,db);membership=db.get(ClassMembership,{"class_id":classroom.id,"student_id":student_id})
    if not membership:raise HTTPException(404,"Class member not found")
    db.delete(membership);db.flush();student=db.get(User,student_id)
    deleted=False
    if student and student.role==Role.STUDENT and not db.query(ClassMembership).filter_by(student_id=student_id).count() and not db.query(Attempt).filter_by(student_id=student_id).count():
        db.delete(student);deleted=True
    audit(db,user.id,"class.member_removed","class",classroom.id,{"student_id":student_id,"student_deleted":deleted});db.commit()

@app.post("/classes/{class_id}/students/import")
async def import_class_students(class_id:str,file:UploadFile=File(...),user:User=Depends(require(Role.TEACHER)),db:Session=Depends(get_db)):
    classroom=classroom_for_owner(class_id,user,db)
    if not (file.filename or "").lower().endswith(".csv"):raise HTTPException(415,"Upload a CSV file")
    try:
        rows=list(csv.DictReader(io.StringIO((await file.read()).decode("utf-8-sig"))))
    except UnicodeDecodeError:raise HTTPException(422,"CSV must be UTF-8 encoded")
    if not rows or not {"name","email","access_code"}.issubset({str(key).strip().lower() for key in (rows[0].keys() if rows else [])}):raise HTTPException(422,"CSV headers must be name,email,access_code")
    result={"created":0,"updated":0,"linked":0,"errors":[]};seen=set()
    for number,row in enumerate(rows,start=2):
        normalized={str(key).strip().lower():str(value or "").strip() for key,value in row.items()}
        name,email,code=normalized.get("name",""),normalized.get("email",""),normalized.get("access_code","")
        if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email) or not re.fullmatch(r"\d{5}",code) or email.lower() in seen:
            result["errors"].append({"row":number,"email":email or None,"error":"Expected unique name, valid email, and 5-digit access code"});continue
        seen.add(email.lower());student=db.query(User).filter_by(email=email.lower()).first()
        if student and student.role!=Role.STUDENT:
            result["errors"].append({"row":number,"email":email,"error":"Email belongs to a non-student account"});continue
        if not student:
            student=User(id=uid(),name=name,email=email.lower(),password_hash="",role=Role.STUDENT,status=AccountStatus.ACTIVE,is_verified=True);set_access_code(student,code,db);db.add(student);db.flush();result["created"]+=1
        elif not teacher_owns_student(user,student.id,db):
            result["errors"].append({"row":number,"email":email,"error":"Student belongs to another teacher; add membership without changing account details"});continue
        else:
            student.name=name;set_access_code(student,code,db);result["updated"]+=1
        if not db.get(ClassMembership,{"class_id":classroom.id,"student_id":student.id}):db.add(ClassMembership(class_id=classroom.id,student_id=student.id));result["linked"]+=1
    audit(db,user.id,"class.students_imported","class",classroom.id,result);db.commit();return result

@app.post("/tests",status_code=201)
def create_test(data:TestCreate,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    if data.closes_at and data.opens_at and data.closes_at<=data.opens_at:raise HTTPException(422,"Closing time must be after opening time")
    duration_seconds=data.duration_minutes*60
    test=Test(id=uid(),owner_id=user.id,title=data.title,subject=data.subject,duration_seconds=duration_seconds,opens_at=data.opens_at,closes_at=data.closes_at,max_attempts=data.max_attempts,results_policy=data.results_policy);db.add(test);db.flush()
    draft=TestVersion(id=uid(),test_id=test.id,ordinal=1,content={"questions":snapshot_questions(db,data.question_ids,user.id),"duration_seconds":duration_seconds,"results_policy":data.results_policy});db.add(draft);audit(db,user.id,"test.created","test",test.id);db.commit();return test_view(test,db)
@app.get("/tests")
def list_tests(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):return [test_view(t,db) for t in db.query(Test).filter_by(owner_id=user.id).order_by(Test.created_at.desc())]
@app.get("/tests/{test_id}")
def test_detail(test_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    t=test_for_owner(test_id,user,db)
    return test_view(t,db,True)
@app.post("/tests/{test_id}/publish")
def publish_test(test_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    t=test_for_owner(test_id,user,db)
    draft=db.query(TestVersion).filter_by(test_id=t.id).order_by(TestVersion.ordinal.desc()).first()
    if not draft:raise HTTPException(422,"Test has no draft")
    # Make a separately immutable snapshot for delivery.
    content=draft.content; v=TestVersion(id=uid(),test_id=t.id,ordinal=draft.ordinal+1,content=content,published_at=datetime.now(timezone.utc));db.add(v);t.status=TestStatus.PUBLISHED;t.published_version_id=v.id;audit(db,user.id,"test.published","test",t.id,{"version":v.ordinal});db.commit();return test_view(t,db)
@app.post("/tests/{test_id}/enrolments",status_code=201)
def enrol(test_id:str,data:EnrolIn,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    t=db.get(Test,test_id);student=db.query(User).filter_by(email=data.email.lower(),role=Role.STUDENT).first()
    if not t or (t.owner_id!=user.id and user.role!=Role.ADMIN) or not student:raise HTTPException(404,"Test or student not found")
    if user.role==Role.TEACHER and not teacher_owns_student(user,student.id,db):raise HTTPException(404,"Test or student not found")
    if not db.get(Enrollment,{"test_id":t.id,"student_id":student.id}):db.add(Enrollment(test_id=t.id,student_id=student.id));audit(db,user.id,"student.enrolled","test",t.id,{"student_id":student.id});db.commit()
    return {"ok":True}
def test_for_owner(test_id:str,user:User,db:Session)->Test:
    test=db.get(Test,test_id)
    if not test or (test.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Test not found")
    return test
def assigned_classrooms(test:Test,db:Session):
    assignments=db.query(TestClassAssignment).filter_by(test_id=test.id).all()
    return [db.get(Classroom,a.class_id) for a in assignments if db.get(Classroom,a.class_id)]
def assigned_student_ids(test:Test,db:Session)->set[str]:
    ids={row.student_id for row in db.query(Enrollment).filter_by(test_id=test.id)}
    class_ids=[row.class_id for row in db.query(TestClassAssignment).filter_by(test_id=test.id)]
    if class_ids:ids.update(row.student_id for row in db.query(ClassMembership).filter(ClassMembership.class_id.in_(class_ids)))
    return ids
def student_can_access(test:Test|None,student_id:str,db:Session)->bool:
    return bool(test) and student_id in assigned_student_ids(test,db)
@app.put("/tests/{test_id}/classes")
def replace_test_classes(test_id:str,data:TestClassAssignmentsIn,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    test=test_for_owner(test_id,user,db); class_ids=list(dict.fromkeys(data.class_ids))
    classrooms=[db.get(Classroom,class_id) for class_id in class_ids]
    if any(c is None or (c.owner_id!=user.id and user.role!=Role.ADMIN) for c in classrooms):raise HTTPException(422,"Every assigned class must belong to you")
    db.query(TestClassAssignment).filter_by(test_id=test.id).delete(synchronize_session=False)
    db.add_all(TestClassAssignment(test_id=test.id,class_id=class_id) for class_id in class_ids)
    audit(db,user.id,"test.classes_updated","test",test.id,{"class_ids":class_ids});db.commit();return test_view(test,db,True)
@app.get("/tests/{test_id}/analytics")
def test_analytics(test_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    test=test_for_owner(test_id,user,db); student_ids=assigned_student_ids(test,db); rows=[];started=submitted=0
    students=db.query(User).filter(User.id.in_(student_ids)).order_by(User.name).all() if student_ids else []
    for student in students:
        attempts=db.query(Attempt).filter_by(test_id=test.id,student_id=student.id).order_by(Attempt.started_at.desc()).all()
        latest=attempts[0] if attempts else None
        if attempts:started+=1
        if any(a.submitted_at for a in attempts):submitted+=1
        status="not_started" if not latest else "submitted" if latest.submitted_at else "in_progress"
        rows.append({"student":user_public(student),"status":status,"score":latest.score if latest else None,"started_at":latest.started_at if latest else None,"submitted_at":latest.submitted_at if latest else None,"attempt_count":len(attempts)})
    return {"test_id":test.id,"summary":{"assigned":len(student_ids),"started":started,"submitted":submitted,"pending":len(student_ids)-started},"students":rows}
def test_view(t:Test,db:Session,include=False):
    v=db.get(TestVersion,t.published_version_id) if t.published_version_id else db.query(TestVersion).filter_by(test_id=t.id).order_by(TestVersion.ordinal.desc()).first();classes=assigned_classrooms(t,db);student_ids=assigned_student_ids(t,db);out={"id":t.id,"title":t.title,"subject":t.subject,"status":t.status.value,"duration_seconds":t.duration_seconds,"opens_at":t.opens_at,"closes_at":t.closes_at,"max_attempts":t.max_attempts,"results_policy":t.results_policy,"question_count":len(v.content.get("questions",[])) if v else 0,"class_count":len(classes),"assigned_student_count":len(student_ids)}
    if include:
        out["classes"]=[classroom_view(c,db) for c in classes]
        if v:out["questions"]=v.content["questions"]
    return out

@app.get("/student/tests")
def student_tests(user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    now=datetime.now(timezone.utc);rows=[]
    for test in db.query(Test).filter_by(status=TestStatus.PUBLISHED).all():
        if not student_can_access(test,user.id,db):continue
        availability="upcoming" if test.opens_at and now<test.opens_at else "closed" if test.closes_at and now>test.closes_at else "available"
        attempts=db.query(Attempt).filter_by(test_id=test.id,student_id=user.id).order_by(Attempt.started_at.desc()).all()
        latest=attempts[0] if attempts else None
        latest_attempt=None
        if latest:
            latest_attempt={
                "id":latest.id,
                "status":"submitted" if latest.submitted_at else "in_progress" if now<latest.ends_at else "expired",
                "started_at":latest.started_at,
                "submitted_at":latest.submitted_at,
                "score":latest.score,
            }
        rows.append({**test_view(test,db),"availability":availability,"attempts_remaining":max(test.max_attempts-len(attempts),0),"latest_attempt":latest_attempt})
    return rows
@app.get("/student/classes")
def student_classes(user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    """Return the current student's class enrolments for their portal profile."""
    class_ids=[row.class_id for row in db.query(ClassMembership).filter_by(student_id=user.id).all()]
    classes=db.query(Classroom).filter(Classroom.id.in_(class_ids)).order_by(Classroom.name).all() if class_ids else []
    return [{"id":classroom.id,"name":classroom.name,"description":classroom.description} for classroom in classes]
@app.post("/tests/{test_id}/attempts",status_code=201)
def start_attempt(test_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    test=db.get(Test,test_id)
    if not test or not student_can_access(test,user.id,db):raise HTTPException(403,"You are not assigned to this assessment")
    ensure_live(test)
    # Serialize starts for this student/test on Postgres so concurrent browser
    # requests cannot both pass the max-attempt count.
    if db.bind and db.bind.dialect.name=="postgresql":
        db.execute(__import__("sqlalchemy").text("SELECT pg_advisory_xact_lock(hashtext(:key))"),{"key":f"attempt:{test_id}:{user.id}"})
    active=db.query(Attempt).filter_by(test_id=test_id,student_id=user.id,submitted_at=None).first()
    if active:return attempt_view(active,db)
    prior=db.query(Attempt).filter_by(test_id=test_id,student_id=user.id).count()
    if prior>=test.max_attempts:raise HTTPException(409,"Attempt limit reached")
    now=datetime.now(timezone.utc); ends=min(now+timedelta(seconds=test.duration_seconds),test.closes_at) if test.closes_at else now+timedelta(seconds=test.duration_seconds)
    a=Attempt(id=uid(),test_id=test.id,version_id=test.published_version_id,student_id=user.id,started_at=now,ends_at=ends);db.add(a);audit(db,user.id,"attempt.started","attempt",a.id);db.commit();return attempt_view(a,db)
@app.get("/attempts/{attempt_id}")
def get_attempt(attempt_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    if not a or a.student_id!=user.id or not student_can_access(db.get(Test,a.test_id),user.id,db):raise HTTPException(404,"Attempt not found")
    return attempt_view(a,db)
@app.put("/attempts/{attempt_id}/responses/{question_id}")
def save_response(attempt_id:str,question_id:str,data:ResponseIn,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    if not a or a.student_id!=user.id or a.submitted_at or not student_can_access(db.get(Test,a.test_id),user.id,db):raise HTTPException(409,"Attempt cannot be changed")
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
    if not a or a.student_id!=user.id or not student_can_access(db.get(Test,a.test_id),user.id,db):raise HTTPException(404,"Attempt not found")
    if a.submitted_at:return attempt_view(a,db)
    if datetime.now(timezone.utc)>=a.ends_at:raise HTTPException(409,"Attempt has expired and cannot be submitted")
    version=db.get(TestVersion,a.version_id); responses={r.question_id:r.answer for r in db.query(AnswerResponse).filter_by(attempt_id=a.id)};a.score,_=score(version.content,responses);a.submitted_at=datetime.now(timezone.utc);audit(db,user.id,"attempt.submitted","attempt",a.id);db.commit();return attempt_view(a,db)
@app.get("/results/{attempt_id}")
def result(attempt_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    if not a or a.student_id!=user.id or not a.submitted_at:raise HTTPException(404,"Result not found")
    test=db.get(Test,a.test_id); policy=test.results_policy; release=policy.get("release_at")
    if policy.get("mode")!="immediate" and (not release or datetime.now(timezone.utc)<datetime.fromisoformat(release.replace("Z","+00:00"))):raise HTTPException(403,"Results have not been released")
    responses={r.question_id:r.answer for r in db.query(AnswerResponse).filter_by(attempt_id=a.id)};_,items=score(db.get(TestVersion,a.version_id).content,responses);return {"attempt":attempt_view(a,db),"questions":items}
def attempt_view(a:Attempt,db:Session):
    v=db.get(TestVersion,a.version_id);resp={r.question_id:{"answer":r.answer,"marked_for_review":r.marked_for_review} for r in db.query(AnswerResponse).filter_by(attempt_id=a.id)};return {"id":a.id,"test_id":a.test_id,"started_at":a.started_at,"ends_at":a.ends_at,"submitted_at":a.submitted_at,"score":a.score,"revision":a.revision,"questions":[{k:x[k] for k in ("id","source_number","kind","stem_markdown","options","diagrams","scoring","interaction") if k in x} for x in v.content["questions"]],"responses":resp}
