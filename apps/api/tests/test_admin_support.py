from datetime import datetime,timezone
import json
from fastapi.testclient import TestClient
from app.db import get_db
from app.main import app
from app.models import AccountStatus,Attempt,AuditEvent,Classroom,ClassMembership,ImportJob,Question,Role,Session as UserSession,Test as Assessment,TestVersion as Version,User
from app.security import current_user,hash_password,verify_password
from test_readiness import db,add_question


def setup_support(db):
    teacher=User(id='teacher',name='Teacher',email='teacher@example.com',role=Role.TEACHER,status=AccountStatus.ACTIVE,access_code_hash=hash_password('11111'))
    other=User(id='other',name='Other',email='other@example.com',role=Role.TEACHER,status=AccountStatus.ACTIVE)
    student=User(id='student',name='Student',email='student@example.com',role=Role.STUDENT,status=AccountStatus.ACTIVE)
    batch=Classroom(id='class',owner_id=teacher.id,name='Batch A')
    test=Assessment(id='test',owner_id=teacher.id,title='Mechanics',subject='Physics',duration_seconds=60)
    foreign=Assessment(id='foreign',owner_id=other.id,title='Other teacher test',subject='Physics',duration_seconds=60)
    version=Version(id='version',test_id=test.id,ordinal=1,content={'questions':[]})
    attempt=Attempt(id='attempt',student_id=student.id,test_id=test.id,version_id=version.id,started_at=datetime.now(timezone.utc),ends_at=datetime(2030,1,1,tzinfo=timezone.utc))
    db.add_all([teacher,other,student,batch,test,foreign,version,attempt,ClassMembership(class_id=batch.id,student_id=student.id)]);db.commit()


def client_for(db,role='admin'):
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[current_user]=lambda:db.get(User,role)
    return TestClient(app)


def test_support_is_admin_only_scoped_and_never_returns_credentials(db):
    setup_support(db);client=client_for(db)
    try:
        profile=client.get('/admin/teachers/teacher').json()
        assert profile['counts']=={'class':1,'student':1,'test':1,'attempt':1}
        for kind in ['class','student','test','attempt']:
            result=client.get(f'/admin/teachers/teacher/resources?kind={kind}')
            assert result.status_code==200 and result.json()['total']==1
        assert client.get('/admin/teachers/teacher/resources/test/foreign').status_code==404
        context=client.get('/admin/teachers/teacher/resources/attempt/attempt')
        assert context.status_code==200
        assert 'password_hash' not in context.text and 'access_code_hash' not in context.text
        app.dependency_overrides[current_user]=lambda:db.get(User,'teacher')
        assert client.get('/admin/teachers/teacher').status_code==403
        assert client.get('/admin/teachers/teacher/resources/test/test').status_code==403
    finally:app.dependency_overrides.clear()


def test_recovery_revokes_sessions_preserves_suspension_and_audit_excludes_code(db):
    setup_support(db);teacher=db.get(User,'teacher');teacher.status=AccountStatus.SUSPENDED
    session=UserSession(id='session',user_id=teacher.id,expires_at=datetime(2030,1,1,tzinfo=timezone.utc));db.add(session);db.commit();client=client_for(db)
    try:
        result=client.put('/admin/teachers/teacher',json={'access_code':'22222'})
        assert result.status_code==200 and result.json()['status']=='suspended'
        db.refresh(teacher);db.refresh(session)
        assert verify_password('22222',teacher.access_code_hash) and session.revoked_at is not None
        event=db.query(AuditEvent).filter_by(action='teacher.updated').one()
        assert event.data['access_recovered'] and '22222' not in json.dumps(event.data)
        assert client.put('/admin/teachers/teacher',json={'status':'active'}).json()['status']=='active'
        assert client.put('/admin/teachers/student',json={'access_code':'22222'}).status_code==404
    finally:app.dependency_overrides.clear()


def test_profile_edit_and_suspend_are_audited_without_changing_assessment_history(db):
    setup_support(db);client=client_for(db)
    try:
        assert client.put('/admin/teachers/teacher',json={'name':'Updated Teacher','email':'new@example.com'}).status_code==200
        result=client.put('/admin/teachers/teacher',json={'status':'suspended'})
        assert result.status_code==200 and result.json()['status']=='suspended'
        assert db.get(Version,'version') is not None and db.get(Attempt,'attempt') is not None
        assert db.query(AuditEvent).filter_by(action='teacher.updated').count()==2
    finally:app.dependency_overrides.clear()


def test_referenced_material_cannot_be_deleted_or_reextracted(db,monkeypatch):
    question=add_question(db)
    db.add_all([Assessment(id='test',owner_id='admin',title='Existing test',subject='Physics',duration_seconds=60),Version(id='version',test_id='test',ordinal=1,content={'questions':[{'id':question.id}]})]);db.commit()
    def no_delete(_prefix):raise AssertionError('Referenced assets must not be deleted')
    monkeypatch.setattr('app.import_cleanup.delete_prefix',no_delete)
    client=client_for(db)
    try:
        assert client.delete('/admin/question-banks/bank/imports/job').status_code==409
        assert client.post('/admin/question-banks/bank/imports/job/reextract').status_code==409
        assert client.delete('/admin/question-banks/bank/imports/job/questions/q').status_code==409
        assert db.get(Question,'q') is not None and db.get(ImportJob,'job') is not None
    finally:app.dependency_overrides.clear()


def test_removed_bank_is_restorable_with_prior_visibility_and_legacy_default_is_private(db):
    client=client_for(db)
    try:
        assert client.delete('/admin/question-banks/bank').status_code==204
        assert client.get('/admin/question-banks').json()==[]
        assert client.get('/admin/question-banks?include_deleted=true').json()[0]['status']=='deleted'
        restored=client.post('/admin/question-banks/bank/restore')
        assert restored.status_code==200 and restored.json()['status']=='draft' and not restored.json()['is_global']
        assert client.post('/admin/question-banks/bank/restore').status_code==200
    finally:app.dependency_overrides.clear()


def test_material_cleanup_preserves_figures_reused_by_a_bank_revision(db,monkeypatch):
    original=add_question(db,diagrams=[{'role':'question','object_key':'bank-diagrams/bank/job/figure.png'}])
    clone=add_question(db,'clone',diagrams=original.diagrams);clone.import_job_id=None;db.commit()
    client=client_for(db)
    try:
        assert client.delete('/admin/question-banks/bank/imports/job').status_code==409
        assert db.get(Question,'clone').diagrams[0]['object_key']=='bank-diagrams/bank/job/figure.png'
    finally:app.dependency_overrides.clear()
