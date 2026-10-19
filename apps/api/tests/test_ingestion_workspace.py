from fastapi.testclient import TestClient
from app.main import app,bank_import_question_page,get_bank_import
from app.db import get_db
from app.security import current_user
from app.models import ImportJob,User
from test_readiness import db,add_question


def test_179_question_import_is_paginated_as_bounded_summaries(db):
    for index in range(179):add_question(db,f'q-{index:03}',answer=None if index<50 else {'option':0})
    admin=db.get(User,'admin')
    first=bank_import_question_page('bank','job','all','',1,25,admin,db)
    second=bank_import_question_page('bank','job','all','',2,25,admin,db)
    assert first['total']==179 and len(first['items'])==25
    assert {item['id'] for item in first['items']}.isdisjoint(item['id'] for item in second['items'])
    assert all('options' not in item and 'solution_markdown' not in item for item in first['items'])
    attention=bank_import_question_page('bank','job','attention','',1,25,admin,db)
    assert attention['total']==50 and attention['counts']['published']==129
    assert 'questions' not in get_bank_import('bank','job',admin,db,include_questions=False)
    assert len(get_bank_import('bank','job',admin,db)['questions'])==179


def client_for(db):
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[current_user]=lambda:db.get(User,'admin')
    return TestClient(app)


def test_partial_upload_returns_per_file_outcomes_and_retry_does_not_restart_success(db,monkeypatch):
    monkeypatch.setattr('app.main.put',lambda *args:None)
    calls=[];monkeypatch.setattr('app.main.extract_bank_import.delay',lambda qid:calls.append(qid))
    client=client_for(db)
    try:
        files=[('question_files',('good.pdf',b'%PDF-1.4 fixture','application/pdf')),('question_files',('bad.txt',b'bad','text/plain'))]
        response=client.post('/admin/question-banks/bank/imports',data={'subject':'Physics'},files=files)
        assert response.status_code==202
        assert [item['status'] for item in response.json()['outcomes']]==['queued','failed']
        retry=client.post('/admin/question-banks/bank/imports',data={'subject':'Physics'},files=files)
        assert [item['status'] for item in retry.json()['outcomes']]==['already_imported','failed']
        assert len(calls)==1 and db.query(ImportJob).count()==2
    finally:app.dependency_overrides.clear()


def test_ambiguous_answer_pair_is_reported_without_starting_extraction(db,monkeypatch):
    monkeypatch.setattr('app.main.put',lambda *args:None)
    calls=[];monkeypatch.setattr('app.main.extract_bank_import.delay',lambda qid:calls.append(qid))
    client=client_for(db)
    try:
        response=client.post('/admin/question-banks/bank/imports',data={'subject':'Physics'},files=[
            ('question_files',('chapter.pdf',b'%PDF-1.4 questions','application/pdf')),
            ('answer_files',('chapter_answers.pdf',b'%PDF-1.4 answers1','application/pdf')),
            ('answer_files',('chapter_solutions.pdf',b'%PDF-1.4 answers2','application/pdf'))])
        assert response.status_code==202
        question=[item for item in response.json()['outcomes'] if item['role']=='question'][0]
        assert question['status']=='failed' and 'Multiple answer' in question['error'] and not calls
    finally:app.dependency_overrides.clear()


def test_worker_queue_failure_is_recoverable_per_source(db,monkeypatch):
    monkeypatch.setattr('app.main.put',lambda *args:None)
    def unavailable(_qid):raise RuntimeError('Queue unavailable')
    monkeypatch.setattr('app.main.extract_bank_import.delay',unavailable)
    client=client_for(db)
    try:
        response=client.post('/admin/question-banks/bank/imports',data={'subject':'Physics'},files=[('question_files',('paper.pdf',b'%PDF-1.4 paper','application/pdf'))])
        result=response.json()['outcomes'][0]
        assert result['status']=='failed' and db.get(ImportJob,result['job_id']).status.value=='failed'
    finally:app.dependency_overrides.clear()


def test_summary_and_detail_endpoints_enforce_document_membership(db):
    add_question(db)
    client=client_for(db)
    try:
        assert client.get('/admin/question-banks/bank/imports/job/questions').json()['total']==1
        assert client.get('/admin/question-banks/bank/imports/job/questions/q').json()['id']=='q'
        assert client.get('/admin/question-banks/other/imports/job/questions/q').status_code==404
        assert client.get('/admin/question-banks/bank/imports/job/questions?review_filter=bad').status_code==422
        config=client.get('/admin/import-config').json()
        assert config['max_file_bytes']>0 and config['extensions']==['pdf','docx']
    finally:app.dependency_overrides.clear()
