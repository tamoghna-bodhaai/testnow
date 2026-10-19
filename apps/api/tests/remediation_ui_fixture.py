"""Explicitly opted-in, disposable UI fixture. Never imported by app.main."""
import os
if os.environ.get("TESTNOW_UI_FIXTURE")!="1" or not os.environ.get("DATABASE_URL","").startswith("sqlite"):
    raise RuntimeError("This fixture requires TESTNOW_UI_FIXTURE=1 and an explicit disposable SQLite database")
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.db import Base,get_db
from app.security import current_user
from app.models import *
import tempfile
fixture_directory=tempfile.TemporaryDirectory(prefix='testnow-phase5-ui-')
fixture_engine=create_engine(f'sqlite+pysqlite:///{fixture_directory.name}/fixture.sqlite',connect_args={'check_same_thread':False})
Base.metadata.create_all(fixture_engine)
FixtureSession=sessionmaker(bind=fixture_engine,autoflush=False)
with FixtureSession() as db:
    admin=User(id='admin',name='Local fixture admin',email='fixture@example.com',role=Role.ADMIN,status=AccountStatus.ACTIVE)
    db.add_all([admin,QuestionBank(id='bank',slug='phase2-fixture',name='Phase 2 readiness fixture',exam_code='JEE',is_global=False),SourceDocument(id='source',owner_id='admin',filename='Properties_of_Matter_readiness_fixture.pdf',object_key='fixture.pdf',sha256='fixture',media_type='application/pdf',size_bytes=1),ImportJob(id='job',owner_id='admin',bank_id='bank',question_document_id='source',status=ImportStatus.REVIEW)])
    for qid,number,answer in [('valid','18',{'option':0}),('legacy-null','51',None)]:
        db.add(Question(id=qid,source_number=number,kind=QuestionKind.SINGLE,owner_id='admin',bank_id='bank',import_job_id='job',scope=QuestionScope.GLOBAL,stem_markdown='What is two plus two?',options=[{'value':'a','markdown':'4'},{'value':'b','markdown':'5'}],answer=answer,approved_at=datetime.now(timezone.utc)))
    db.commit()
def fixture_db():
    with FixtureSession() as db:yield db
def fixture_user():
    with FixtureSession() as db:return db.get(User,'admin')
app.dependency_overrides[get_db]=fixture_db
app.dependency_overrides[current_user]=fixture_user
import asyncio
from fastapi.responses import JSONResponse
with FixtureSession() as db:
    for key in ['A','B','C','D']:
        db.add(QuestionBank(id=key,slug='race-'+key,name='Race bank '+key,exam_code='JEE',is_global=False))
    db.commit()
@app.middleware('http')
async def race_latency(request,call_next):
    suffix=request.url.path.removeprefix('/admin/question-banks/')
    if suffix in {'A','B','C','D'}:
        await asyncio.sleep({'A':.4,'B':.8,'C':.1,'D':.2}[suffix])
        if suffix=='D':return JSONResponse({'detail':'Fixture bank unavailable'},status_code=503)
    return await call_next(request)
with FixtureSession() as db:
    for index in range(2,179):
        db.add(Question(id=f'fixture-{index:03}',source_number=str(index+50),kind=QuestionKind.SINGLE,owner_id='admin',bank_id='bank',import_job_id='job',scope=QuestionScope.GLOBAL,stem_markdown=f'Question {index}: what is two plus two?',options=[{'value':'a','markdown':'4'},{'value':'b','markdown':'5'}],answer={'option':0},approved_at=datetime.now(timezone.utc)))
    db.commit()
with FixtureSession() as db:
    db.add(Question(id='older-orphan',source_number='old-51',kind=QuestionKind.NUMERICAL,owner_id='admin',bank_id='bank',scope=QuestionScope.GLOBAL,stem_markdown='Calculate two minus two.',options=[],answer=None,approved_at=datetime(2020,1,1,tzinfo=timezone.utc)))
    db.add_all([TaxonomyNode(id='subject',bank_id='bank',name='Physics',kind='subject'),TaxonomyNode(id='chapter',bank_id='bank',name='Matter',kind='chapter',parent_id='subject'),TaxonomyNode(id='topic',bank_id='bank',name='Elasticity',kind='topic',parent_id='chapter')]);db.commit()
import app.figure_review as fixture_figures
import fitz
fixture_document=fitz.open()
for index in range(2):
    fixture_page=fixture_document.new_page(width=400,height=500);fixture_page.insert_text((30,50),f'Original source page {index+1}: Calculate two minus two. Answer: 0.')
fixture_pdf=fixture_document.tobytes();fixture_document.close()
fixture_figures.get=lambda _key:fixture_pdf
from app.security import hash_password
with FixtureSession() as db:
    teacher=User(id='teacher',name='Support Teacher',email='support.teacher@example.com',role=Role.TEACHER,status=AccountStatus.SUSPENDED,access_code_hash=hash_password('11111'))
    student=User(id='student',name='Support Student',email='support.student@example.com',role=Role.STUDENT,status=AccountStatus.ACTIVE)
    test=Test(id='support-test',owner_id='teacher',title='Support Mechanics Test',subject='Physics',duration_seconds=1800,status=TestStatus.PUBLISHED,published_version_id='support-version')
    version=TestVersion(id='support-version',test_id='support-test',ordinal=1,published_at=datetime.now(timezone.utc),content={'questions':[{'id':'saved-question','kind':'single_choice','stem_markdown':'What is two plus two?','options':[{'markdown':'4'},{'markdown':'5'}],'answer':{'option':0}}]})
    attempt=Attempt(id='support-attempt',test_id='support-test',version_id='support-version',student_id='student',started_at=datetime.now(timezone.utc),ends_at=datetime(2030,1,1,tzinfo=timezone.utc),submitted_at=datetime.now(timezone.utc),score=4)
    db.add_all([teacher,student,Classroom(id='support-class',owner_id='teacher',name='Support Batch'),ClassMembership(class_id='support-class',student_id='student'),test,version,attempt,Response(attempt_id='support-attempt',question_id='saved-question',answer={'option':0}),Session(id='support-session',user_id='teacher',expires_at=datetime(2030,1,1,tzinfo=timezone.utc))]);db.commit()

# All storage/queue seams remain local to this fixture process.
import app.main as fixture_main
from types import SimpleNamespace
fixture_objects={'source.pdf':fixture_pdf}
def fixture_put(key,raw,media_type=None):fixture_objects[key]=raw
def fixture_get(key):
    if key not in fixture_objects:raise ValueError('Unknown local fixture object')
    return fixture_objects[key]
fixture_main.put=fixture_put
fixture_main.get=fixture_get
fixture_figures.put=fixture_put
fixture_figures.get=fixture_get
fixture_main.extract_bank_import.delay=lambda job_id:SimpleNamespace(id=f'fixture-{job_id}')
fixture_main.generate_bank_solutions.delay=lambda *args:SimpleNamespace(id='fixture-generation')
