import pytest
from fastapi import HTTPException
from app.models import ImportJob,ImportStatus,User,AuditEvent
from app.main import pause_bank_import,resume_bank_import,cancel_bank_import,retry_bank_import,bank_import_issues,import_view
from app.bank_imports import _finish_controlled_import,_checkpoint
from app.job_state import extraction_state
from test_readiness import db,add_question


def test_running_pause_is_requested_until_worker_checkpoint_and_duplicate_resume_dispatches_once(db,monkeypatch):
    job=db.get(ImportJob,'job');job.status=ImportStatus.PROCESSING;db.commit();admin=db.get(User,'admin')
    first=pause_bank_import('bank','job',admin,db)
    assert first['lifecycle']['state']=='pause_requested'
    assert pause_bank_import('bank','job',admin,db)['lifecycle']['state']=='pause_requested'
    with pytest.raises(HTTPException):resume_bank_import('bank','job',admin,db)
    _finish_controlled_import(db,'job','paused',{})
    assert extraction_state(job)=='paused'
    calls=[];monkeypatch.setattr('app.main.extract_bank_import.delay',lambda qid:calls.append(qid))
    assert resume_bank_import('bank','job',admin,db)['lifecycle']['state']=='queued'
    assert resume_bank_import('bank','job',admin,db)['lifecycle']['state']=='queued'
    assert calls==['job']
    assert db.query(AuditEvent).filter_by(action='question_bank.import_paused').count()==1


def test_queued_pause_acknowledges_without_claiming_worker_heartbeat(db):
    job=db.get(ImportJob,'job');job.status=ImportStatus.UPLOADED;db.commit()
    result=pause_bank_import('bank','job',db.get(User,'admin'),db)
    assert result['lifecycle']['state']=='paused'
    assert result['lifecycle']['pause_acknowledged_at']
    assert result['lifecycle']['heartbeat_at'] is None


def test_running_cancel_acknowledgement_and_retry_clear_durable_flags(db,monkeypatch):
    job=db.get(ImportJob,'job');job.status=ImportStatus.PROCESSING;db.commit();admin=db.get(User,'admin')
    assert cancel_bank_import('bank','job',admin,db)['lifecycle']['state']=='cancellation_requested'
    assert cancel_bank_import('bank','job',admin,db)['lifecycle']['state']=='cancellation_requested'
    _finish_controlled_import(db,'job','cancelled',{})
    assert extraction_state(job)=='cancelled'
    calls=[];monkeypatch.setattr('app.main.extract_bank_import.delay',lambda qid:calls.append(qid))
    assert retry_bank_import('bank','job',admin,db)['lifecycle']['state']=='queued'
    retry_bank_import('bank','job',admin,db)
    assert calls==['job'] and not job.extraction_meta['cancel_requested']


def test_queued_cancel_is_terminal_and_requests_are_idempotent(db):
    job=db.get(ImportJob,'job');job.status=ImportStatus.UPLOADED;db.commit();admin=db.get(User,'admin')
    assert cancel_bank_import('bank','job',admin,db)['lifecycle']['state']=='cancelled'
    assert cancel_bank_import('bank','job',admin,db)['lifecycle']['state']=='cancelled'
    with pytest.raises(HTTPException):resume_bank_import('bank','job',admin,db)


def test_checkpoint_retains_control_request_and_records_worker_activity(db):
    job=db.get(ImportJob,'job');job.extraction_meta={'pause_requested':True};db.commit()
    current,meta=_checkpoint(db,'job',{'processed_pages':4,'total_pages':80});db.commit()
    assert meta['pause_requested'] and meta['worker_heartbeat_at']
    lifecycle=import_view(current,db)['lifecycle']
    assert lifecycle['processed']==4 and lifecycle['total']==80 and lifecycle['unit']=='source pages'


def test_paginated_diagnostics_expose_full_58_messages_and_legacy_retention_gap(db):
    job=db.get(ImportJob,'job');admin=db.get(User,'admin')
    errors=[{'question_id':f'q-{i}','source_number':str(i),'message':f'failure {i}'} for i in range(58)]
    job.extraction_meta={'solution_generation':{'failed':58,'errors':errors[-50:],'error_log':errors}}
    db.commit()
    seen=[]
    for page in range(1,4):
        result=bank_import_issues('bank','job','generation',page,20,admin,db)
        assert result['total']==58 and not result['legacy_logs_missing'];seen.extend(item['question_id'] for item in result['items'])
    assert len(seen)==len(set(seen))==58
    assert 'error_log' not in import_view(job,db)['meta']['solution_generation']
    job.extraction_meta={'solution_generation':{'failed':58,'errors':errors[-50:]}};db.commit()
    assert bank_import_issues('bank','job','generation',1,20,admin,db)['legacy_logs_missing']


def test_job_controls_and_diagnostics_enforce_bank_identity(db):
    admin=db.get(User,'admin')
    for action in (pause_bank_import,resume_bank_import,cancel_bank_import,retry_bank_import):
        with pytest.raises(HTTPException) as exc:action('another-bank','job',admin,db)
        assert exc.value.status_code==404
    with pytest.raises(HTTPException):bank_import_issues('another-bank','job','extraction',1,20,admin,db)


def test_latest_cancel_request_wins_when_worker_was_about_to_acknowledge_pause(db):
    job=db.get(ImportJob,'job');job.status=ImportStatus.PROCESSING;job.extraction_meta={'pause_requested':True,'cancel_requested':True};db.commit()
    _finish_controlled_import(db,'job','paused',{})
    assert extraction_state(job)=='cancelled'
    assert job.extraction_meta['cancelled_acknowledged_at']
