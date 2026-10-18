from datetime import datetime, timedelta, timezone
import csv, io, logging, re
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import exists, func, or_
from sqlalchemy.orm import Session
from .config import settings
from .db import Base, engine, get_db
from .models import AccountStatus, Attempt, AttemptEvent, Classroom, ClassMembership, Enrollment, ImportJob, ImportStatus, Question, QuestionBank, QuestionKind, QuestionScope, QuestionTaxonomy, Response as AnswerResponse, Role, SourceDocument, TaxonomyNode, Test, TestClassAssignment, TestStatus, TestVersion, User
from .schemas import AnswerKeyIn, ClassroomCreate, EnrolIn, ImportTestCreate, IntegrityViolationIn, LoginIn, QuestionBankCreate, QuestionBankUpdate, QuestionIn, QuestionOut, ResponseIn, RosterUpdate, StudentCreate, StudentUpdate, SubmitIn, TeacherCreate, TeacherUpdate, TestClassAssignmentsIn, TestCreate
from .security import COOKIE, current_user, hash_password, new_session, require, revoke_sessions, uid, verify_password
from .services import attempt_event, audit, cancel_for_integrity, delivery_blueprint, ensure_live, finalize_attempt, finalize_if_expired, publish, safe_question_kind, score, snapshot_questions, source_number_key, validate_blueprint
from .storage import client as storage_client, delete_prefix, digest, get, put, signed_get
from .tasks import extract_import, extract_bank_import, generate_bank_solutions
from .figure_repairs import revalidate_bank_figures
from .seed import ensure_demo_data
from .figures import is_visual_issue, option_media_issues
from .solution_generation import ANSWER_KINDS as ANSWER_GENERATION_KINDS, approve_generation, has_usable_answer, missing_generation_fields, queue_generation

app=FastAPI(title="TestNow API",version="1.0.0")
def as_utc(value:datetime)->datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
def attempt_duration_seconds(attempt:Attempt)->int|None:
    if not attempt.submitted_at:return None
    return max(0,int((as_utc(attempt.submitted_at)-as_utc(attempt.started_at)).total_seconds()))
from .figure_review import router as figure_review_router
app.include_router(figure_review_router)
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
    live=questions.outerjoin(ImportJob,ImportJob.id==Question.import_job_id).filter(Question.approved_at.is_not(None),or_(Question.import_job_id.is_(None),ImportJob.published_at.is_not(None))).count()
    return {"id":bank.id,"slug":bank.slug,"name":bank.name,"exam_code":bank.exam_code,"status":bank.status,"revision":bank.revision,"is_global":bank.is_global,"question_count":questions.count(),"published_count":live,"import_count":jobs.count(),"flagged_count":sum(1 for q in questions if (q.interaction or {}).get("ingestion_issues"))}

def bank_taxonomy_summary(bank_id:str,db:Session)->dict:
    """Compact full-bank classification counts for the admin review screen.

    The editable question payload is intentionally capped at 100 rows; this
    summary is not.  It prevents that preview cap from being presented as the
    total ingestion result.
    """
    question_ids=[row[0] for row in db.query(Question.id).filter_by(bank_id=bank_id).all()]
    nodes={row.id:row for row in db.query(TaxonomyNode).filter_by(bank_id=bank_id).all()}
    links={question_id:[] for question_id in question_ids}
    if question_ids:
        for question_id,node_id in db.query(QuestionTaxonomy.question_id,QuestionTaxonomy.taxonomy_node_id).filter(QuestionTaxonomy.question_id.in_(question_ids)):
            if node_id in nodes:links.setdefault(question_id,[]).append(nodes[node_id])
    tree={}
    for question_id in question_ids:
        attached=links.get(question_id,[])
        subject=next((node.name for node in attached if node.kind=="subject"),"Unclassified subject")
        chapter=next((node.name for node in attached if node.kind=="chapter"),"Unclassified chapter")
        topic=next((node.name for node in attached if node.kind=="topic"),"Unclassified topic")
        branch=tree.setdefault(subject,{"count":0,"chapters":{}});branch["count"]+=1
        chapter_branch=branch["chapters"].setdefault(chapter,{"count":0,"topics":{}});chapter_branch["count"]+=1
        chapter_branch["topics"][topic]=chapter_branch["topics"].get(topic,0)+1
    subjects=[]
    for name,subject in sorted(tree.items()):
        chapters=[]
        for chapter_name,chapter in sorted(subject["chapters"].items()):
            chapters.append({"name":chapter_name,"count":chapter["count"],"topics":[{"name":topic,"count":count} for topic,count in sorted(chapter["topics"].items())]})
        subjects.append({"name":name,"count":subject["count"],"chapters":chapters})
    return {"total":len(question_ids),"subjects":subjects}

def bank_slug(value:str,db:Session)->str:
    base=re.sub(r"[^a-z0-9]+","-",value.lower()).strip("-")[:60] or "question-bank"; candidate=base;index=2
    while db.query(QuestionBank).filter_by(slug=candidate).first():candidate=f"{base[:54]}-{index}";index+=1
    return candidate

@app.get("/admin/question-banks")
def admin_question_banks(user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    return [bank_view(bank,db) for bank in db.query(QuestionBank).filter(QuestionBank.status!="deleted").order_by(QuestionBank.created_at.desc())]

@app.post("/admin/question-banks",status_code=201)
def create_question_bank(data:QuestionBankCreate,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=QuestionBank(id=uid(),slug=bank_slug(data.name,db),name=data.name.strip(),exam_code=(data.exam_code or "GENERAL").strip() or "GENERAL",is_global=False,status="draft",revision=1)
    db.add(bank);audit(db,user.id,"question_bank.created","question_bank",bank.id,{"name":bank.name});db.commit();return bank_view(bank,db)

@app.get("/admin/question-banks/{bank_id}")
def admin_question_bank(bank_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank or bank.status=="deleted":raise HTTPException(404,"Question bank not found")
    jobs=db.query(ImportJob).filter_by(bank_id=bank.id).order_by(ImportJob.created_at.desc()).all()
    # Keep the management view responsive for large published banks.  The
    # summary retains the total count; this is the newest reviewable slice.
    questions=db.query(Question).filter_by(bank_id=bank.id).order_by(Question.created_at.desc()).limit(100).all()
    return {**bank_view(bank,db),"imports":[import_view(job,db) for job in jobs],"questions":[question_view(q,db) for q in questions],"taxonomy_summary":bank_taxonomy_summary(bank.id,db)}

@app.put("/admin/question-banks/{bank_id}")
def update_question_bank(bank_id:str,data:QuestionBankUpdate,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank or bank.status=="deleted":raise HTTPException(404,"Question bank not found")
    bank.name=data.name.strip();bank.exam_code=(data.exam_code or "GENERAL").strip() or "GENERAL"
    audit(db,user.id,"question_bank.updated","question_bank",bank.id,{"name":bank.name,"exam_code":bank.exam_code});db.commit()
    return bank_view(bank,db)

@app.post("/admin/question-banks/{bank_id}/revision",status_code=201)
def create_question_bank_revision(bank_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    original=db.get(QuestionBank,bank_id)
    if not original or original.status!="published":raise HTTPException(409,"Only a published bank can be revised")
    revision=QuestionBank(id=uid(),slug=bank_slug(f"{original.name} revision {original.revision + 1}",db),name=original.name,exam_code=original.exam_code,is_global=False,status="draft",revision=original.revision+1)
    db.add(revision);db.flush()
    node_ids={}
    for node in db.query(TaxonomyNode).filter_by(bank_id=original.id).order_by(TaxonomyNode.created_at):
        copied=TaxonomyNode(id=uid(),bank_id=revision.id,parent_id=node_ids.get(node.parent_id),kind=node.kind,name=node.name,code=node.code);db.add(copied);node_ids[node.id]=copied.id
    db.flush()
    for question in db.query(Question).filter_by(bank_id=original.id).all():
        copied=Question(id=uid(),owner_id=user.id,bank_id=revision.id,scope=QuestionScope.GLOBAL,import_job_id=None,source_number=question.source_number,source_key=f"revision-{revision.revision}-{question.id}",kind=question.kind,stem_markdown=question.stem_markdown,options=question.options,answer=question.answer,solution_markdown=question.solution_markdown,scoring=question.scoring,diagrams=question.diagrams,source_spans=question.source_spans,interaction={**(question.interaction or {}),"copied_from_bank":original.id},confidence=question.confidence,approved_at=question.approved_at)
        db.add(copied);db.flush()
        for link in db.query(QuestionTaxonomy).filter_by(question_id=question.id):
            if link.taxonomy_node_id in node_ids:db.add(QuestionTaxonomy(question_id=copied.id,taxonomy_node_id=node_ids[link.taxonomy_node_id]))
    audit(db,user.id,"question_bank.revision_created","question_bank",revision.id,{"from":original.id,"revision":revision.revision});db.commit()
    return bank_view(revision,db)

@app.delete("/admin/question-banks/{bank_id}",status_code=204)
def delete_question_bank(bank_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank or bank.status=="deleted":raise HTTPException(404,"Question bank not found")
    # A soft delete keeps published test attempts and audit history valid while
    # immediately removing the bank from teachers and the admin workspace.
    bank.status="deleted";bank.is_global=False
    audit(db,user.id,"question_bank.deleted","question_bank",bank.id,{"name":bank.name});db.commit()

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
async def create_bank_imports(bank_id:str,subject:str=Form(...),question_files:list[UploadFile]=File(...),answer_files:list[UploadFile]=File(default=[]),user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank or bank.status=="deleted":raise HTTPException(404,"Question bank not found")
    subject=subject.strip()
    if not subject:raise HTTPException(422,"Subject is required")
    # Browsers include an empty file part for an unselected picker.  Ignore it
    # server-side as well as client-side so the API is safe to call directly.
    question_files=[file for file in question_files if file.filename]
    answer_files=[file for file in answer_files if file.filename]
    if not question_files:raise HTTPException(422,"Choose at least one PDF or DOCX question document")
    questions=[await store_bank_source(file,user,bank,db) for file in question_files]
    answers=[await store_bank_source(file,user,bank,db) for file in answer_files]
    answer_by_stem={source_stem(item.filename):item for item in answers}; jobs=[]; jobs_to_dispatch=[]
    seen_source_ids=set()
    for source in questions:
        if source.id in seen_source_ids:continue
        seen_source_ids.add(source.id)
        paired=answer_by_stem.get(source_stem(source.filename)) or (answers[0] if len(questions)==len(answers)==1 else None)
        existing_job=db.query(ImportJob).filter_by(bank_id=bank.id,question_document_id=source.id).order_by(ImportJob.created_at.desc()).first()
        if existing_job:
            # Resume a job that failed before this importer became idempotent.
            # Existing questions are skipped by the worker; any missing ones are
            # completed without creating a second copy of the source document.
            if existing_job.status==ImportStatus.FAILED:
                existing_job.status=ImportStatus.UPLOADED;existing_job.error=None
                if paired:existing_job.answer_document_id=paired.id
                existing_job.extraction_meta={**(existing_job.extraction_meta or {}),"subject":subject,"phase":"Queued to resume the failed import"}
                jobs_to_dispatch.append(existing_job)
            jobs.append(existing_job)
            continue
        meta={"subject":subject,"source_role":"combined" if not paired else "question","pairing":"matched" if paired else "missing_answer_source","phase":"Queued for extraction"}
        job=ImportJob(id=uid(),owner_id=user.id,bank_id=bank.id,question_document_id=source.id,answer_document_id=paired.id if paired else None,status=ImportStatus.UPLOADED,extraction_meta=meta);db.add(job);jobs.append(job);jobs_to_dispatch.append(job)
    # A live bank remains available while new documents are extracted.  Any
    # clean or subsequently reviewed questions join that same published bank.
    if bank.status!="published":bank.status="processing"
    audit(db,user.id,"question_bank.import_created","question_bank",bank.id,{"subject":subject,"files":len(seen_source_ids)});db.commit()
    for job in jobs_to_dispatch:extract_bank_import.delay(job.id)
    return {"bank":bank_view(bank,db),"imports":[import_view(job,db) for job in jobs]}

@app.post("/admin/question-banks/{bank_id}/figure-revalidation")
def start_figure_revalidation(bank_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank:raise HTTPException(404,"Question bank not found")
    run_id=uid()
    audit(db,user.id,"question_bank.figure_revalidation_started","question_bank",bank.id,{"run_id":run_id})
    db.commit()
    revalidate_bank_figures.delay(bank.id,run_id,user.id)
    return {"run_id":run_id,"status":"queued"}

@app.get("/admin/question-banks/{bank_id}/figure-revalidation")
def figure_revalidation_status(bank_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    if not db.get(QuestionBank,bank_id):raise HTTPException(404,"Question bank not found")
    jobs=db.query(ImportJob).filter_by(bank_id=bank_id).order_by(ImportJob.updated_at.desc()).all()
    runs=[(job.extraction_meta or {}).get("figure_revalidation") for job in jobs]
    runs=[run for run in runs if isinstance(run,dict)]
    return max(runs,key=lambda run:run.get("finished_at") or run.get("started_at") or "") if runs else {"status":"idle"}

@app.get("/admin/question-banks/{bank_id}/figure-revalidation/{run_id}/report")
def figure_revalidation_report(bank_id:str,run_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    if not db.get(QuestionBank,bank_id):raise HTTPException(404,"Question bank not found")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}",run_id):raise HTTPException(404,"Report not found")
    try: raw=get(f"figure-revalidation/{bank_id}/{run_id}.json")
    except Exception: raise HTTPException(404,"Report not ready")
    return Response(raw,media_type="application/json",headers={"Content-Disposition":f'attachment; filename="figure-revalidation-{run_id}.json"'})

@app.post("/admin/question-banks/{bank_id}/questions/{question_id}/resolve")
def resolve_bank_question(bank_id:str,question_id:str,data:QuestionIn,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db),ignore_warnings:bool=False):
    q=db.get(Question,question_id);bank=db.get(QuestionBank,bank_id)
    if not bank or not q or q.bank_id!=bank.id:raise HTTPException(404,"Question not found")
    warnings=list((q.interaction or {}).get("ingestion_issues", []))
    visual_warnings=any(is_visual_issue(issue) for issue in warnings) or option_media_issues(data.options,q.diagrams)
    if visual_warnings and not ignore_warnings:
        raise HTTPException(422,"Review and save the question and option figures before approving this question")
    if any(d.get("role")=="option" for d in q.diagrams or []) and [o.get("value") for o in data.options] != [o.get("value") for o in q.options or []]:
        raise HTTPException(422,"Option order is linked to figures; preserve option values and order")
    if q.kind in {QuestionKind.SINGLE,QuestionKind.MULTIPLE,QuestionKind.ASSERTION} and len(data.options)<2:raise HTTPException(422,"Choice questions need at least two options")
    # Review edits may correct rendered content and scoring, but must never
    # erase the immutable source spans/diagram provenance captured at ingest.
    for name,value in data.model_dump().items():
        if name not in {"source_spans","diagrams","interaction"}:setattr(q,name,value)
    if q.kind!=QuestionKind.SUBJECTIVE and not q.answer:raise HTTPException(422,"A reviewed answer is required")
    interaction={**(q.interaction or {})}
    if ignore_warnings and warnings:
        # Keep the original automated findings for accountability, but remove
        # them from the active review queue after an administrator accepts them.
        interaction["ignored_ingestion_issues"]=warnings
        interaction["warnings_ignored_by"]=user.id
        interaction["warnings_ignored_at"]=datetime.now(timezone.utc).isoformat()
        audit(db,user.id,"question.warnings_ignored","question",q.id,{"warnings":warnings})
    generation=dict(interaction.get("solution_generation") or {})
    if generation.get("status")=="completed":
        generation.update({"approved_at":datetime.now(timezone.utc).isoformat(),"approved_by":user.id})
        interaction["solution_generation"]=generation
        audit(db,user.id,"question_solution_generation.approved","question",q.id,{"run_id":generation.get("run_id"),"mode":"individual"})
    interaction.update({"ingestion_issues":[],"reviewed_by":user.id})
    q.interaction=interaction;q.approved_at=datetime.now(timezone.utc)
    job=db.get(ImportJob,q.import_job_id)
    if job and not db.query(Question).filter_by(import_job_id=job.id,approved_at=None).first():
        incomplete=bool((job.extraction_meta or {}).get("failed_pages"))
        job.status=ImportStatus.REVIEW if incomplete else ImportStatus.APPROVED
        job.extraction_meta={**(job.extraction_meta or {}),"phase":"Extraction incomplete: source pages skipped" if incomplete else "Review complete","flagged_count":0}
    db.commit();return question_view(q,db)

@app.post("/admin/question-banks/{bank_id}/imports/{job_id}/retry",status_code=202)
def retry_bank_import(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id);job=db.get(ImportJob,job_id)
    if not bank or not job or job.bank_id!=bank.id:raise HTTPException(404,"Import not found")
    if job.status!=ImportStatus.FAILED:raise HTTPException(409,"Only a failed import can be retried")
    job.status=ImportStatus.UPLOADED;job.error=None
    job.extraction_meta={**(job.extraction_meta or {}),"phase":"Queued to retry the failed import"}
    audit(db,user.id,"question_bank.import_retried","question_bank",bank.id,{"job_id":job.id,"filename":db.get(SourceDocument,job.question_document_id).filename})
    db.commit();extract_bank_import.delay(job.id)
    return import_view(job,db)

def clear_bank_import_output(db:Session,job:ImportJob)->int:
    """Remove one material's derived records before a clean re-extraction.

    The uploaded source documents intentionally remain: a re-extraction must
    use the exact same input bytes and answer-key pairing.  Taxonomy links are
    removed explicitly so this works on every supported database, including
    local SQLite installations where foreign-key cascades may be disabled.
    """
    question_ids=[row[0] for row in db.query(Question.id).filter_by(import_job_id=job.id).all()]
    if question_ids:
        db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id.in_(question_ids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.id.in_(question_ids)).delete(synchronize_session=False)
    # Diagram crops are derived data as well.  Their prefix is scoped to the
    # import id, never to the complete question bank.
    if job.bank_id:
        delete_prefix(f"bank-diagrams/{job.bank_id}/{job.id}/")
    return len(question_ids)

@app.post("/admin/question-banks/{bank_id}/imports/{job_id}/reextract",status_code=202)
def reextract_bank_import(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    """Start this stored source again from page one with no stale output."""
    job=db.get(ImportJob,job_id)
    if not job or job.bank_id!=bank_id:raise HTTPException(404,"Import not found")
    meta=dict(job.extraction_meta or {})
    active=job.status==ImportStatus.PROCESSING or (job.status==ImportStatus.UPLOADED and not meta.get("pause_requested"))
    if active:raise HTTPException(409,"Pause or cancel this material and wait for its current page batch to stop before re-extracting it")
    if job.published_at:raise HTTPException(409,"This source material is live. Create a bank revision before replacing a published material")
    removed=clear_bank_import_output(db,job)
    subject=meta.get("subject") or "Unclassified"
    job.status=ImportStatus.UPLOADED;job.error=None;job.published_at=None
    job.extraction_meta={"subject":subject,"source_role":meta.get("source_role","combined"),"pairing":meta.get("pairing","missing_answer_source"),"phase":"Queued for clean re-extraction","reextraction_count":int(meta.get("reextraction_count") or 0)+1,"reextracted_at":datetime.now(timezone.utc).isoformat()}
    audit(db,user.id,"question_bank.import_reextracted","import",job.id,{"bank_id":bank_id,"filename":db.get(SourceDocument,job.question_document_id).filename,"cleared_questions":removed})
    db.commit();extract_bank_import.delay(job.id)
    return import_view(job,db)

@app.get("/admin/question-banks/{bank_id}/imports/{job_id}")
def get_bank_import(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or job.bank_id!=bank_id:raise HTTPException(404,"Import not found")
    return {**import_view(job,db),"questions":[question_view(q,db) for q in db.query(Question).filter_by(import_job_id=job.id).order_by(Question.created_at.desc()).all()]}

def _generation_job(bank_id:str,job_id:str,db:Session)->ImportJob:
    job=db.get(ImportJob,job_id)
    if not job or job.bank_id!=bank_id:raise HTTPException(404,"Import not found")
    if job.published_at:raise HTTPException(409,"This source document is live. Create a bank revision before generating answers or solutions")
    if job.status in {ImportStatus.UPLOADED,ImportStatus.PROCESSING}:raise HTTPException(409,"Wait for source extraction to finish before generating answers or solutions")
    return job

@app.post("/admin/question-banks/{bank_id}/imports/{job_id}/solution-generation",status_code=202)
def start_import_solution_generation(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    job=_generation_job(bank_id,job_id,db)
    try:state=queue_generation(db,job,user.id)
    except ValueError as exc:raise HTTPException(409,str(exc))
    except LookupError as exc:raise HTTPException(422,str(exc))
    db.commit();generate_bank_solutions.delay(job.id,state["run_id"],user.id,None)
    return state

@app.post("/admin/question-banks/{bank_id}/questions/{question_id}/solution-generation",status_code=202)
def start_question_solution_generation(bank_id:str,question_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    question=db.get(Question,question_id)
    if not question or question.bank_id!=bank_id or not question.import_job_id:raise HTTPException(404,"Question not found")
    job=_generation_job(bank_id,question.import_job_id,db)
    try:state=queue_generation(db,job,user.id,question)
    except ValueError as exc:raise HTTPException(409,str(exc))
    except LookupError as exc:raise HTTPException(422,str(exc))
    db.commit();generate_bank_solutions.delay(job.id,state["run_id"],user.id,question.id)
    return state

@app.post("/admin/question-banks/{bank_id}/imports/{job_id}/solution-generation/{run_id}/approve")
def approve_import_solution_generation(bank_id:str,job_id:str,run_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    job=_generation_job(bank_id,job_id,db)
    try:approved=approve_generation(db,job,run_id,user.id)
    except ValueError as exc:raise HTTPException(409,str(exc))
    db.commit()
    return {"approved_count":approved,"import":import_view(job,db)}

@app.post("/admin/question-banks/{bank_id}/imports/{job_id}/pause")
def pause_bank_import(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or job.bank_id!=bank_id:raise HTTPException(404,"Import not found")
    if job.status not in {ImportStatus.UPLOADED,ImportStatus.PROCESSING}:raise HTTPException(409,"Only queued or processing imports can be paused")
    job.extraction_meta={**(job.extraction_meta or {}),"pause_requested":True,"paused_at":datetime.now(timezone.utc).isoformat(),"phase":"Pause requested — finishing the current page batch"}
    audit(db,user.id,"question_bank.import_paused","import",job.id,{"bank_id":bank_id});db.commit();return import_view(job,db)

@app.post("/admin/question-banks/{bank_id}/imports/{job_id}/resume",status_code=202)
def resume_bank_import(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or job.bank_id!=bank_id:raise HTTPException(404,"Import not found")
    if not (job.extraction_meta or {}).get("pause_requested"):raise HTTPException(409,"This import is not paused")
    job.status=ImportStatus.UPLOADED;job.error=None;job.extraction_meta={**(job.extraction_meta or {}),"pause_requested":False,"paused_at":None,"phase":"Queued to resume"}
    audit(db,user.id,"question_bank.import_resumed","import",job.id,{"bank_id":bank_id});db.commit();extract_bank_import.delay(job.id);return import_view(job,db)

@app.post("/admin/question-banks/{bank_id}/imports/{job_id}/cancel")
def cancel_bank_import(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or job.bank_id!=bank_id:raise HTTPException(404,"Import not found")
    if job.status not in {ImportStatus.UPLOADED,ImportStatus.PROCESSING}:raise HTTPException(409,"Only queued or processing imports can be cancelled")
    job.extraction_meta={**(job.extraction_meta or {}),"cancel_requested":True,"phase":"Cancellation requested — finishing the current page batch"}
    audit(db,user.id,"question_bank.import_cancelled","import",job.id,{"bank_id":bank_id});db.commit();return import_view(job,db)

@app.delete("/admin/question-banks/{bank_id}/imports/{job_id}",status_code=204)
def delete_bank_import(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or job.bank_id!=bank_id:raise HTTPException(404,"Import not found")
    if job.status==ImportStatus.PROCESSING or (job.status==ImportStatus.UPLOADED and not (job.extraction_meta or {}).get("pause_requested")):raise HTTPException(409,"Pause this material and wait for the current batch to finish before deleting it")
    # Paused and cancelled materials are deliberately deletable.  A material
    # may have a useful partial extraction, so publication is offered first in
    # the UI; deletion is the administrator's explicit destructive choice.
    clear_bank_import_output(db,job)
    db.delete(job);audit(db,user.id,"question_bank.import_deleted","import",job_id,{"bank_id":bank_id});db.commit()

@app.post("/admin/question-banks/{bank_id}/publish")
def publish_question_bank(bank_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id)
    if not bank:raise HTTPException(404,"Question bank not found")
    jobs=db.query(ImportJob).filter_by(bank_id=bank.id,published_at=None).with_for_update().all();ready=[]
    if any(((job.extraction_meta or {}).get("solution_generation") or {}).get("status") in {"queued","running"} for job in jobs):raise HTTPException(409,"Wait for answer and solution generation to finish before publishing")
    for job in jobs:
        if db.query(Question).filter_by(import_job_id=job.id).filter(Question.approved_at.is_not(None)).first():ready.append(job)
    manual_ready=db.query(Question).filter_by(bank_id=bank.id,import_job_id=None).filter(Question.approved_at.is_not(None)).count()
    if not ready and not manual_ready and not bank.is_global:raise HTTPException(422,"Complete review for at least one source document before publishing")
    now=datetime.now(timezone.utc)
    for job in ready:job.published_at=now
    bank.is_global=True;bank.status="published"
    audit(db,user.id,"question_bank.published","question_bank",bank.id,{"published_materials":len(ready),"pending_materials":len(jobs)-len(ready)});db.commit();return bank_view(bank,db)

@app.post("/admin/question-banks/{bank_id}/imports/{job_id}/publish")
def publish_bank_import(bank_id:str,job_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    bank=db.get(QuestionBank,bank_id);job=db.query(ImportJob).filter_by(id=job_id).with_for_update().first()
    if not bank or not job or job.bank_id!=bank.id:raise HTTPException(404,"Import not found")
    if job.published_at:raise HTTPException(409,"This source document is already live")
    if (((job.extraction_meta or {}).get("solution_generation") or {}).get("status") in {"queued","running"}):raise HTTPException(409,"Wait for answer and solution generation to finish before publishing")
    approved=db.query(Question).filter_by(import_job_id=job.id).filter(Question.approved_at.is_not(None)).count()
    if not approved:raise HTTPException(422,"This source document has no approved questions to publish")
    job.published_at=datetime.now(timezone.utc);bank.is_global=True;bank.status="published"
    audit(db,user.id,"question_bank.material_published","import",job.id,{"bank_id":bank.id,"questions":approved,"partial":job.status!=ImportStatus.APPROVED});db.commit();return import_view(job,db)

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
def list_imports(limit:int=Query(default=25,ge=1,le=500),user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    query=db.query(ImportJob)
    if user.role!=Role.ADMIN: query=query.filter_by(owner_id=user.id)
    return [import_view(job,db) for job in query.order_by(ImportJob.created_at.desc()).limit(limit)]
@app.get("/teacher/import-sources")
def list_import_sources(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    query=db.query(ImportJob)
    if user.role!=Role.ADMIN:query=query.filter_by(owner_id=user.id)
    views=[]
    for job in query.order_by(ImportJob.created_at.desc()):
        view=import_view(job,db)
        if view["approved_count"]:views.append(view)
    return views
@app.get("/teacher/imports/{job_id}")
def get_import(job_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or (job.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Import not found")
    questions=db.query(Question).filter_by(import_job_id=job.id).all()
    questions.sort(key=lambda question:source_number_key(question.source_number))
    return {**import_view(job,db),"questions":[question_view(q) for q in questions]}
@app.delete("/teacher/imports/{job_id}",status_code=204)
def delete_import(job_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    """Remove a completed import and its reviewable question records.

    Active workers and tests that already use the extracted questions are kept
    intact so deletion cannot race an extraction or break an existing test's
    saved figures.
    """
    job=db.get(ImportJob,job_id)
    if not job or (job.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Import not found")
    if job.status in {ImportStatus.UPLOADED,ImportStatus.PROCESSING}:
        raise HTTPException(409,"Wait for the active extraction to finish before deleting this import")
    question_ids={row[0] for row in db.query(Question.id).filter_by(import_job_id=job.id).all()}
    if question_ids:
        for version in db.query(TestVersion).yield_per(50):
            saved_ids={item.get("id") for item in (version.content or {}).get("questions",[]) if isinstance(item,dict)}
            if question_ids & saved_ids:
                raise HTTPException(409,"This import cannot be deleted because its questions are used in a test")
        db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id.in_(question_ids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.id.in_(question_ids)).delete(synchronize_session=False)
    db.delete(job)
    audit(db,user.id,"import.deleted","import",job_id,{"question_count":len(question_ids)})
    db.commit()
    # Source documents are intentionally retained for auditability and may be
    # shared by duplicate uploads. Derived crops are unique to this import.
    try: delete_prefix(f"diagrams/{job_id}/")
    except Exception: pass
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
    if any(is_visual_issue(issue) for issue in (q.interaction or {}).get("ingestion_issues", [])) or option_media_issues(q.options,q.diagrams):
        raise HTTPException(422,"Review and save the question and option figures before approving this question")
    if not q.answer:raise HTTPException(422,"Select and save an answer key before approving this question")
    # Confidence is audit metadata, never a reason to block teacher approval. Legacy
    # imports may have omitted it or stored it on a 0-100 scale.
    q.confidence=min(1.0,max(0.0,float(q.confidence))) if q.confidence is not None else 0.0
    q.approved_at=datetime.now(timezone.utc)
    db.flush()
    pending=db.query(Question).filter_by(import_job_id=job.id,approved_at=None).first()
    if not pending:
        incomplete=bool((job.extraction_meta or {}).get("failed_pages"))
        job.status=ImportStatus.REVIEW if incomplete else ImportStatus.APPROVED
        job.extraction_meta={**(job.extraction_meta or {}),"phase":"Extraction incomplete: source pages skipped" if incomplete else "Review complete"}
    db.commit();return question_view(q)
def import_view(job:ImportJob,db:Session|None=None):
    source=db.get(SourceDocument,job.question_document_id) if db else None
    questions=db.query(Question).filter_by(import_job_id=job.id) if db else None
    total=questions.count() if questions is not None else 0
    approved=questions.filter(Question.approved_at.is_not(None)).count() if questions is not None else 0
    meta=job.extraction_meta or {}
    errors=[{**entry,"message":entry.get("message") or entry.get("error") or "Extraction failed"}
            for entry in list(meta.get("error_log") or meta.get("failed_pages") or [])[-10:]]
    eligible=sum(1 for kind,answer,options,solution in questions.with_entities(Question.kind,Question.answer,Question.options,Question.solution_markdown) if ((kind in ANSWER_GENERATION_KINDS and not has_usable_answer(kind,answer,options or [])) or not str(solution or "").strip())) if questions is not None else 0
    return {"id":job.id,"status":job.status.value,"error":job.error,"meta":meta,"errors":errors,"created_at":job.created_at,"filename":source.filename if source else None,"source_id":source.id if source else None,"question_count":total,"approved_count":approved,"published":bool(job.published_at),"published_at":job.published_at,"generation_eligible_count":eligible}
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
def question_view(q:Question, db:Session|None=None, taxonomy:list[dict]|None=None):
    confidence=q.confidence
    if confidence is not None:
        # Imported legacy records can contain a non-numeric confidence value.
        # It is optional display metadata, so do not let one such record make
        # the whole teacher question library (or a successful test refresh)
        # return a 500 response.
        try:
            confidence=min(1.0,max(0.0,float(confidence)))
        except (TypeError,ValueError):
            confidence=None
    if taxonomy is None:
        taxonomy=[]
        if db and q.bank_id:
            taxonomy=[{"id":node.id,"parent_id":node.parent_id,"kind":node.kind,"name":node.name,"code":node.code} for node in db.query(TaxonomyNode).join(QuestionTaxonomy,QuestionTaxonomy.taxonomy_node_id==TaxonomyNode.id).filter(QuestionTaxonomy.question_id==q.id).order_by(TaxonomyNode.kind,TaxonomyNode.name)]
    options=normalized_options(q.options); kind=safe_question_kind(q.kind,options)
    answer=None if kind=="subjective" and len(options)<2 else q.answer
    interaction=dict(q.interaction or {})
    # Older imports predate source_filename in interaction.  Enrich their review
    # payloads without changing the original source provenance.
    if db and q.import_job_id and not interaction.get("source_filename"):
        job=db.get(ImportJob,q.import_job_id)
        source=db.get(SourceDocument,job.question_document_id) if job else None
        if source:interaction["source_filename"]=source.filename
    missing=missing_generation_fields(q)
    return {"id":q.id,"source_number":q.source_number,"kind":kind,"stem_markdown":q.stem_markdown,"options":options,"answer":answer,"solution_markdown":q.solution_markdown,"scoring":q.scoring,"diagrams":q.diagrams,"source_spans":q.source_spans,"interaction":interaction,"confidence":confidence,"approved":bool(q.approved_at),"bank_id":q.bank_id,"import_job_id":q.import_job_id,"scope":q.scope.value,"taxonomy":taxonomy,"generation_eligible":bool(missing),"generation_missing_fields":missing}

def question_views(rows:list[Question],db:Session)->list[dict]:
    """Serialize a page of questions without one taxonomy query per question."""
    question_ids=[question.id for question in rows]
    taxonomy_by_question={question_id:[] for question_id in question_ids}
    if question_ids:
        links=(db.query(QuestionTaxonomy.question_id,TaxonomyNode)
               .join(TaxonomyNode,TaxonomyNode.id==QuestionTaxonomy.taxonomy_node_id)
               .filter(QuestionTaxonomy.question_id.in_(question_ids))
               .order_by(TaxonomyNode.kind,TaxonomyNode.name))
        for question_id,node in links:
            taxonomy_by_question[question_id].append({"id":node.id,"parent_id":node.parent_id,"kind":node.kind,"name":node.name,"code":node.code})
    return [question_view(question,db,taxonomy_by_question[question.id]) for question in rows]

@app.get("/questions")
def questions(
    page:int|None=Query(default=None,ge=1),
    page_size:int=Query(default=20,ge=1,le=100),
    approved:bool|None=Query(default=None),
    import_job_id:str|None=Query(default=None),
    user:User=Depends(require(Role.TEACHER,Role.ADMIN)),
    db:Session=Depends(get_db),
):
    owner_id=user.id
    if import_job_id:
        job=db.get(ImportJob,import_job_id)
        if not job or (job.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Import not found")
        owner_id=job.owner_id
    rows=db.query(Question).filter((Question.owner_id==owner_id)|((Question.scope==QuestionScope.GLOBAL)&(Question.approved_at.is_not(None)))).order_by(Question.created_at.desc())
    if approved is True:rows=rows.filter(Question.approved_at.is_not(None))
    elif approved is False:rows=rows.filter(Question.approved_at.is_(None))
    if import_job_id:rows=rows.filter(Question.import_job_id==import_job_id)
    if page is not None:
        total=rows.count()
        approved_total=rows.filter(Question.approved_at.is_not(None)).count()
        items=rows.offset((page-1)*page_size).limit(page_size).all()
        return {"items":question_views(items,db),"page":page,"page_size":page_size,"total":total,"approved_total":approved_total,"has_more":page*page_size<total}
    # Do not make the whole teacher library unavailable because a legacy import
    # has malformed optional display metadata. It remains repairable in review.
    visible=[]
    for question in rows:
        try:
            visible.append(question_view(question,db))
        except (KeyError, TypeError, ValueError) as exc:
            logging.getLogger(__name__).warning("Skipping malformed question %s in library: %s",question.id,exc)
    return visible
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
    if published:
        # Each uploaded source material needs its own publication confirmation.
        # Manual/revision questions have no import job and remain approval-based.
        query=query.outerjoin(ImportJob,ImportJob.id==Question.import_job_id).filter(Question.approved_at.is_not(None),or_(Question.import_job_id.is_(None),ImportJob.published_at.is_not(None)))
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
    return {"items":question_views(rows,db),"page":page,"page_size":page_size,"total":total,"has_more":page*page_size<total}
@app.get("/media/questions/{question_id}/{index}")
def question_media(question_id:str,index:int,user:User=Depends(current_user),db:Session=Depends(get_db),asset:str|None=None):
    q=db.get(Question,question_id)
    if not q or index<0:raise HTTPException(404,"Media not found")
    allowed=q.owner_id==user.id or q.scope==QuestionScope.GLOBAL or user.role==Role.ADMIN
    if not allowed and user.role==Role.STUDENT:
        for a in db.query(Attempt).filter_by(student_id=user.id).all():
            version=db.get(TestVersion,a.version_id)
            if version and question_id in {item["id"] for item in version.content.get("questions",[])}:allowed=True;break
    if not allowed:raise HTTPException(403,"Media not available")
    if asset is None:
        if index>=len(q.diagrams):raise HTTPException(404,"Media not found")
        key=q.diagrams[index]["object_key"]
    else:
        # Never pass an arbitrary requested object key to storage. A repaired
        # question can change its diagram ordering, while saved test versions
        # must continue to render their original immutable images.
        key=next((d["object_key"] for d in q.diagrams if d.get("object_key")==asset),None)
        if key is None:
            versions=db.query(TestVersion)
            if user.role==Role.STUDENT:
                versions=versions.filter(TestVersion.id.in_(db.query(Attempt.version_id).filter_by(student_id=user.id)))
            elif user.role!=Role.ADMIN:
                versions=versions.join(Test,Test.id==TestVersion.test_id).filter(Test.owner_id==user.id)
            for version in versions.yield_per(50):
                recorded=next((item for item in version.content.get("questions",[]) if item.get("id")==question_id),None)
                if recorded and any(d.get("object_key")==asset for d in recorded.get("diagrams",[])):
                    key=asset;break
        if key is None:raise HTTPException(404,"Media not found")
    # Do not redirect the browser to an internal object-store hostname. In local and
    # private deployments that hostname is unreachable from the browser, producing a
    # broken image despite successful diagram extraction.
    diagram=next((item for item in q.diagrams if item.get("object_key")==key),{})
    return StreamingResponse(iter([get(key)]),media_type=diagram.get("media_type","image/png"),headers={"Cache-Control":"private, max-age=300"})
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

def create_test_record(data:TestCreate|ImportTestCreate,question_ids:list[str],user:User,db:Session,*,publish_now:bool=False,question_owner_id:str|None=None,allow_reviewed_subjective:bool=False)->Test:
    if data.closes_at and data.opens_at and data.closes_at<=data.opens_at:raise HTTPException(422,"Closing time must be after opening time")
    duration_seconds=data.duration_minutes*60
    test=Test(id=uid(),owner_id=user.id,title=data.title,subject=data.subject,duration_seconds=duration_seconds,opens_at=data.opens_at,closes_at=data.closes_at,max_attempts=data.max_attempts,results_policy=data.results_policy);db.add(test);db.flush()
    selected=snapshot_questions(db,question_ids,question_owner_id or user.id)
    if data.delivery_blueprint:
        validate_blueprint(data.delivery_blueprint,question_ids)
        if not allow_reviewed_subjective and any(question["kind"]=="subjective" for question in selected):raise HTTPException(422,"Subjective questions need manual evaluation and cannot be published in a CBT blueprint")
    for question in selected:
        question["scoring"]={**(question.get("scoring") or {}),"correct":data.marks_per_question}
    draft=TestVersion(id=uid(),test_id=test.id,ordinal=1,content={"questions":selected,"duration_seconds":duration_seconds,"results_policy":data.results_policy,"delivery_blueprint":data.delivery_blueprint})
    db.add(draft)
    if publish_now:
        published=TestVersion(id=uid(),test_id=test.id,ordinal=2,content=draft.content,published_at=datetime.now(timezone.utc))
        db.add(published);test.status=TestStatus.PUBLISHED;test.published_version_id=published.id
        audit(db,user.id,"test.published","test",test.id,{"version":published.ordinal,"source":"approved_import"})
    audit(db,user.id,"test.created","test",test.id,{"marks_per_question":data.marks_per_question});db.commit()
    return test

@app.post("/tests",status_code=201)
def create_test(data:TestCreate,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    test=create_test_record(data,data.question_ids,user,db)
    return test_view(test,db)

@app.post("/teacher/imports/{job_id}/tests",status_code=201)
def create_import_test(job_id:str,data:ImportTestCreate,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    job=db.get(ImportJob,job_id)
    if not job or (job.owner_id!=user.id and user.role!=Role.ADMIN):raise HTTPException(404,"Import not found")
    if job.status!=ImportStatus.APPROVED:raise HTTPException(409,"Approve every question in this paper before creating its test")
    if db.query(Question).filter_by(import_job_id=job.id,approved_at=None).first():raise HTTPException(409,"Approve every question in this paper before creating its test")
    source_questions=(db.query(Question).filter_by(import_job_id=job.id)
                      .filter(Question.approved_at.is_not(None)).all())
    source_questions.sort(key=lambda question:source_number_key(question.source_number))
    if not source_questions:raise HTTPException(422,"This paper has no approved questions")
    question_ids=[question.id for question in source_questions]
    supplied=data.delivery_blueprint or {}
    supplied_sections=supplied.get("sections") if isinstance(supplied.get("sections"),list) else []
    supplied_section=supplied_sections[0] if supplied_sections and isinstance(supplied_sections[0],dict) else {}
    blueprint={"preset":str(supplied.get("preset") or "source-paper"),"instructions":str(supplied.get("instructions") or "Read every question carefully. Use Save & Next to record an answer."),"languages":supplied.get("languages") or ["en"],"accommodations":supplied.get("accommodations") or {},"sections":[{"id":str(supplied_section.get("id") or "section-1"),"title":str(supplied_section.get("title") or "Section 1"),"question_ids":question_ids,"navigation":"free","optional_groups":[]}]}
    payload=data.model_copy(update={"delivery_blueprint":blueprint})
    # Every imported answer was explicitly reviewed before the job could reach
    # APPROVED, so the source-paper path can preserve written questions too.
    test=create_test_record(payload,question_ids,user,db,publish_now=True,question_owner_id=job.owner_id,allow_reviewed_subjective=True)
    return test_view(test,db)
@app.get("/tests")
def list_tests(user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):return [test_view(t,db) for t in db.query(Test).filter_by(owner_id=user.id).order_by(Test.created_at.desc())]
@app.get("/tests/{test_id}")
def test_detail(test_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    t=test_for_owner(test_id,user,db)
    return test_view(t,db,True)
@app.delete("/tests/{test_id}",status_code=204)
def delete_test(test_id:str,user:User=Depends(require(Role.TEACHER,Role.ADMIN)),db:Session=Depends(get_db)):
    """Remove an assessment that has never been started by a student.

    Started tests are preserved as part of the student's assessment record;
    teachers can still unassign them, but cannot erase that history.
    """
    test=test_for_owner(test_id,user,db)
    if db.query(Attempt).filter_by(test_id=test.id).first():
        raise HTTPException(409,"A test with student attempts cannot be deleted")
    db.query(TestClassAssignment).filter_by(test_id=test.id).delete(synchronize_session=False)
    db.query(Enrollment).filter_by(test_id=test.id).delete(synchronize_session=False)
    db.query(TestVersion).filter_by(test_id=test.id).delete(synchronize_session=False)
    db.delete(test)
    audit(db,user.id,"test.deleted","test",test_id,{"title":test.title})
    db.commit()
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
    test=test_for_owner(test_id,user,db); assigned_ids=assigned_student_ids(test,db); rows=[]
    all_attempts=db.query(Attempt).filter_by(test_id=test.id).order_by(Attempt.started_at.desc()).all()
    appeared_ids={attempt.student_id for attempt in all_attempts}; student_ids=assigned_ids|appeared_ids
    students=db.query(User).filter(User.id.in_(student_ids)).order_by(User.name).all() if student_ids else []
    attempts_by_student={student.id:[] for student in students}
    for attempt in all_attempts:attempts_by_student.setdefault(attempt.student_id,[]).append(attempt)
    now=datetime.now(timezone.utc)
    for attempt in all_attempts:finalize_if_expired(db,attempt,now)
    attempt_ids=[attempt.id for attempt in all_attempts]
    responses_by_attempt={attempt_id:{} for attempt_id in attempt_ids}
    responses=db.query(AnswerResponse).filter(AnswerResponse.attempt_id.in_(attempt_ids)).all() if attempt_ids else []
    for response in responses:responses_by_attempt[response.attempt_id][response.question_id]=response.answer
    incidents_by_attempt={attempt_id:[] for attempt_id in attempt_ids}
    incidents=(db.query(AttemptEvent).filter(AttemptEvent.attempt_id.in_(attempt_ids),AttemptEvent.event_type=="integrity_violation").order_by(AttemptEvent.created_at).all() if attempt_ids else [])
    for event in incidents:incidents_by_attempt[event.attempt_id].append(event)
    version_ids={attempt.version_id for attempt in all_attempts}
    versions={version.id:version for version in db.query(TestVersion).filter(TestVersion.id.in_(version_ids)).all()} if version_ids else {}
    current_classes=assigned_classrooms(test,db); class_names={classroom.id:classroom.name for classroom in current_classes}
    class_memberships={classroom.id:set() for classroom in current_classes}
    current_class_ids=list(class_memberships)
    memberships=db.query(ClassMembership).filter(ClassMembership.class_id.in_(current_class_ids)).all() if current_class_ids else []
    for membership in memberships:
        historic=attempts_by_student.get(membership.student_id,[])
        if not historic or not any(attempt.cohort_classes for attempt in historic):class_memberships[membership.class_id].add(membership.student_id)
    for attempt in all_attempts:
        for classroom in attempt.cohort_classes or []:
            class_id=str(classroom.get("id") or ""); name=str(classroom.get("name") or "")
            if class_id and name:
                class_names.setdefault(class_id,name);class_memberships.setdefault(class_id,set()).add(attempt.student_id)

    def attempt_metrics(attempt:Attempt)->dict:
        version=versions[attempt.version_id]
        total,review=score(version.content,responses_by_attempt.get(attempt.id,{}))
        maximum=sum(float((question.get("scoring") or {}).get("correct",4)) for question in version.content.get("questions",[]))
        attempt_incidents=incidents_by_attempt.get(attempt.id,[])
        return {"id":attempt.id,"status":"cancelled" if attempt.cancelled_at else "submitted" if attempt.submitted_at else "in_progress","score":attempt.score if attempt.score is not None else total if attempt.submitted_at else None,"max_score":maximum,"percentage":round((float(attempt.score if attempt.score is not None else total)/maximum)*100,1) if attempt.submitted_at and maximum else None,"correct":sum(1 for item in review if item["response"] is not None and item["correct"]),"incorrect":sum(1 for item in review if item["response"] is not None and not item["correct"]),"unanswered":sum(1 for item in review if item["response"] is None),"started_at":attempt.started_at,"submitted_at":attempt.submitted_at,"duration_seconds":attempt_duration_seconds(attempt),"integrity_flagged":attempt.integrity_flagged,"cancellation_reason":attempt.cancellation_reason,"incidents":[{"event_type":event.data.get("event_type",attempt.cancellation_reason),"detected_at":event.data.get("detected_at") or event.created_at,"client_time":event.data.get("client_time")} for event in attempt_incidents]}

    for student in students:
        attempts=attempts_by_student[student.id]
        latest=attempts[0] if attempts else None
        details=[attempt_metrics(attempt) for attempt in attempts]
        status="not_started" if not latest else "cancelled" if latest.cancelled_at else "submitted" if latest.submitted_at else "in_progress"
        valid_scores=[float(a.score) for a in attempts if a.submitted_at and not a.cancelled_at and a.score is not None]
        rows.append({"student":user_public(student),"classes":[{"id":class_id,"name":class_names[class_id]} for class_id,members in class_memberships.items() if student.id in members],"status":status,"score":latest.score if latest else None,"percentage":details[0]["percentage"] if details else None,"started_at":latest.started_at if latest else None,"submitted_at":latest.submitted_at if latest else None,"duration_seconds":details[0]["duration_seconds"] if details else None,"attempt_count":len(attempts),"integrity_flagged":any(a.integrity_flagged for a in attempts),"best_score":max(valid_scores) if valid_scores else None,"average_score":round(sum(valid_scores)/len(valid_scores),2) if valid_scores else None,"attempts":details})
    def aggregate(ids:set[str],class_id:str|None=None)->dict:
        selected=[]
        for student_id in ids:
            attempts=attempts_by_student.get(student_id,[])
            if class_id:
                attempts=[attempt for attempt in attempts if any(str(item.get("id"))==class_id for item in (attempt.cohort_classes or [])) or (not attempt.cohort_classes and student_id in class_memberships.get(class_id,set()))]
            selected.append(attempts)
        latest=[attempts[0] for attempts in selected if attempts]
        completed=[next((attempt for attempt in attempts if attempt.submitted_at and not attempt.cancelled_at),None) for attempts in selected]
        completed=[attempt for attempt in completed if attempt]
        scores=[float(attempt.score) for attempt in completed if attempt.score is not None]
        percentages=[]
        for attempt in completed:
            version=versions[attempt.version_id]; maximum=sum(float((q.get("scoring") or {}).get("correct",4)) for q in version.content.get("questions",[]))
            if maximum and attempt.score is not None:percentages.append(float(attempt.score)/maximum*100)
        cancelled=sum(1 for attempts in selected if any(attempt.cancelled_at for attempt in attempts));flagged=sum(1 for attempts in selected if any(attempt.integrity_flagged for attempt in attempts))
        return {"assigned":len(ids),"started":len(latest),"submitted":len(completed),"pending":len(ids)-len(latest),"cancelled":cancelled,"flagged":flagged,"average_score":round(sum(scores)/len(scores),2) if scores else None,"average_percentage":round(sum(percentages)/len(percentages),1) if percentages else None,"highest_score":max(scores) if scores else None,"completion_rate":round(len(completed)/len(ids)*100,1) if ids else 0,"flag_rate":round(flagged/len(ids)*100,1) if ids else 0}
    summary=aggregate(student_ids)
    summary["currently_assigned"]=len(assigned_ids)
    batches=[{"classroom":{"id":class_id,"name":class_names[class_id]},**aggregate(members,class_id)} for class_id,members in class_memberships.items()]
    db.commit()
    return {"test_id":test.id,"summary":summary,"batches":batches,"students":rows}
def test_view(t:Test,db:Session,include=False):
    v=db.get(TestVersion,t.published_version_id) if t.published_version_id else db.query(TestVersion).filter_by(test_id=t.id).order_by(TestVersion.ordinal.desc()).first();classes=assigned_classrooms(t,db);student_ids=assigned_student_ids(t,db);out={"id":t.id,"title":t.title,"subject":t.subject,"status":t.status.value,"duration_seconds":t.duration_seconds,"opens_at":t.opens_at,"closes_at":t.closes_at,"max_attempts":t.max_attempts,"results_policy":t.results_policy,"question_count":len(v.content.get("questions",[])) if v else 0,"class_count":len(classes),"assigned_student_count":len(student_ids),"assigned_class_names":[c.name for c in classes]}
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
        for attempt in attempts:
            finalize_if_expired(db,attempt,now)
        latest=attempts[0] if attempts else None
        latest_attempt=None
        if latest:
            latest_attempt={
                "id":latest.id,
                "status":"cancelled" if latest.cancelled_at else "submitted" if latest.submitted_at else "in_progress",
                "started_at":latest.started_at,
                "submitted_at":latest.submitted_at,
                "duration_seconds":attempt_duration_seconds(latest),
                "score":latest.score,
                "integrity_flagged":latest.integrity_flagged,
                "cancellation_reason":latest.cancellation_reason,
            }
        rows.append({**test_view(test,db),"availability":availability,"attempts_remaining":max(test.max_attempts-len(attempts),0),"latest_attempt":latest_attempt})
    db.commit()
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
    if active:
        if finalize_if_expired(db,active):db.commit()
        return attempt_view(active,db)
    prior=db.query(Attempt).filter_by(test_id=test_id,student_id=user.id).count()
    if prior>=test.max_attempts:raise HTTPException(409,"Attempt limit reached")
    now=datetime.now(timezone.utc); ends=min(now+timedelta(seconds=test.duration_seconds),test.closes_at) if test.closes_at else now+timedelta(seconds=test.duration_seconds)
    cohorts=[{"id":classroom.id,"name":classroom.name} for classroom in assigned_classrooms(test,db) if db.get(ClassMembership,{"class_id":classroom.id,"student_id":user.id})]
    a=Attempt(id=uid(),test_id=test.id,version_id=test.published_version_id,student_id=user.id,started_at=now,ends_at=ends,cohort_classes=cohorts);db.add(a);audit(db,user.id,"attempt.started","attempt",a.id);attempt_event(db,a.id,"started",{"ends_at":ends.isoformat(),"cohort_classes":cohorts});db.commit();return attempt_view(a,db)
@app.get("/attempts/{attempt_id}")
def get_attempt(attempt_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    # Assignment is only an entry gate. Removing a candidate from a class must
    # never strand an already-issued timed attempt.
    if not a or a.student_id!=user.id:raise HTTPException(404,"Attempt not found")
    if finalize_if_expired(db,a):db.commit()
    return attempt_view(a,db)
@app.put("/attempts/{attempt_id}/responses/{question_id}")
def save_response(attempt_id:str,question_id:str,data:ResponseIn,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.query(Attempt).filter_by(id=attempt_id).with_for_update().first()
    if not a or a.student_id!=user.id or a.submitted_at:raise HTTPException(409,"Attempt cannot be changed")
    if datetime.now(timezone.utc)>=as_utc(a.ends_at):raise HTTPException(409,"Attempt has expired")
    content=db.get(TestVersion,a.version_id).content
    if question_id not in {q["id"] for q in content["questions"]}:raise HTTPException(422,"Question is not part of this attempt")
    if data.revision!=a.revision:raise HTTPException(409,"Attempt changed in another session")
    response=db.get(AnswerResponse,{"attempt_id":a.id,"question_id":question_id})
    if not response:response=AnswerResponse(attempt_id=a.id,question_id=question_id,answer=data.answer,marked_for_review=data.marked_for_review,visited_at=datetime.now(timezone.utc));db.add(response)
    else:response.answer=data.answer;response.marked_for_review=data.marked_for_review;response.visited_at=response.visited_at or datetime.now(timezone.utc)
    a.revision+=1;attempt_event(db,a.id,"response_saved",{"question_id":question_id,"marked_for_review":data.marked_for_review});db.commit();return {"revision":a.revision,"server_time":datetime.now(timezone.utc)}
@app.post("/attempts/{attempt_id}/questions/{question_id}/visit")
def visit_question(attempt_id:str,question_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.query(Attempt).filter_by(id=attempt_id).with_for_update().first()
    if not a or a.student_id!=user.id or a.submitted_at:raise HTTPException(404,"Attempt not found")
    if datetime.now(timezone.utc)>=as_utc(a.ends_at):raise HTTPException(409,"Attempt has expired")
    content=db.get(TestVersion,a.version_id).content
    if question_id not in {q["id"] for q in content["questions"]}:raise HTTPException(422,"Question is not part of this attempt")
    response=db.get(AnswerResponse,{"attempt_id":a.id,"question_id":question_id})
    if not response: db.add(AnswerResponse(attempt_id=a.id,question_id=question_id,answer=None,marked_for_review=False,visited_at=datetime.now(timezone.utc)))
    elif not response.visited_at: response.visited_at=datetime.now(timezone.utc)
    attempt_event(db,a.id,"question_visited",{"question_id":question_id});db.commit();return {"ok":True}
@app.post("/attempts/{attempt_id}/submit")
def submit(attempt_id:str,data:SubmitIn,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.query(Attempt).filter_by(id=attempt_id).with_for_update().first()
    if not a or a.student_id!=user.id:raise HTTPException(404,"Attempt not found")
    if a.submitted_at:return attempt_view(a,db)
    now=datetime.now(timezone.utc)
    if not finalize_if_expired(db,a,now):
        if data.revision!=a.revision:raise HTTPException(409,"An answer is still being saved. Review the latest responses and submit again.")
        finalize_attempt(db,a,submitted_at=now,actor_id=user.id,action="attempt.submitted")
    db.commit();return attempt_view(a,db)

@app.post("/attempts/{attempt_id}/integrity-violation")
def integrity_violation(attempt_id:str,data:IntegrityViolationIn,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.query(Attempt).filter_by(id=attempt_id).with_for_update().first()
    if not a or a.student_id!=user.id:raise HTTPException(404,"Attempt not found")
    detected_at=datetime.now(timezone.utc)
    finalize_if_expired(db,a,detected_at)
    cancel_for_integrity(db,a,event_type=data.event_type,detected_at=detected_at,actor_id=user.id,client_time=data.client_time)
    db.commit()
    return attempt_view(a,db)
@app.get("/results/{attempt_id}")
def result(attempt_id:str,user:User=Depends(require(Role.STUDENT)),db:Session=Depends(get_db)):
    a=db.get(Attempt,attempt_id)
    if not a or a.student_id!=user.id or not a.submitted_at:raise HTTPException(404,"Result not found")
    if a.cancelled_at:raise HTTPException(403,"Cancelled attempts do not have a student result")
    test=db.get(Test,a.test_id); policy=test.results_policy; release=policy.get("release_at")
    if policy.get("mode")!="immediate" and (not release or datetime.now(timezone.utc)<datetime.fromisoformat(release.replace("Z","+00:00"))):raise HTTPException(403,"Results have not been released")
    responses={r.question_id:r.answer for r in db.query(AnswerResponse).filter_by(attempt_id=a.id)};_,items=score(db.get(TestVersion,a.version_id).content,responses);return {"attempt":attempt_view(a,db),"questions":items}
def attempt_view(a:Attempt,db:Session):
    v=db.get(TestVersion,a.version_id)
    resp={r.question_id:{"answer":r.answer,"marked_for_review":r.marked_for_review,"visited_at":r.visited_at} for r in db.query(AnswerResponse).filter_by(attempt_id=a.id)}
    questions=[{k:snapshot[k] for k in ("id","source_number","kind","stem_markdown","options","diagrams","scoring","interaction") if k in snapshot} for snapshot in v.content["questions"]]
    return {"id":a.id,"test_id":a.test_id,"started_at":a.started_at,"ends_at":a.ends_at,"submitted_at":a.submitted_at,"duration_seconds":attempt_duration_seconds(a),"score":a.score,"revision":a.revision,"cancelled_at":a.cancelled_at,"cancellation_reason":a.cancellation_reason,"integrity_flagged":a.integrity_flagged,"delivery_blueprint":delivery_blueprint(v.content),"questions":questions,"responses":resp}
