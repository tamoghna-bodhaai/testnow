"""Failure fixtures for durable dispatch. PostgreSQL tests are explicitly opt-in.

TEST_DURABLE_DATABASE_URL must point at a disposable test database. Each test
creates/removes only its own randomly named schema. No production data is read.
"""
import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import Base, SessionLocal, WorkerSession
from app.durable_jobs import commit_task, enqueue
from app.durable_worker import claim, execute, finish
from app.job_context import Execution, OwnershipLost, asset_key, current_execution
from app.models import DurableJob, ImportJob, ImportStatus, Role, SourceDocument, User


@pytest.fixture
def postgres(monkeypatch):
    url = os.environ.get("TEST_DURABLE_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DURABLE_DATABASE_URL for PostgreSQL failure-path tests")
    schema = "durable_fixture_" + uuid.uuid4().hex
    root = create_engine(url, poolclass=NullPool)
    with root.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(test_url, poolclass=NullPool)
    Base.metadata.create_all(engine)
    old_bind = SessionLocal.kw["bind"]
    SessionLocal.configure(bind=engine)
    monkeypatch.setattr(settings(), "durable_jobs_enabled", True)
    monkeypatch.setattr("app.durable_worker.worker_engine", lambda: engine)
    try:
        yield engine, test_url.render_as_string(hide_password=False)
    finally:
        SessionLocal.configure(bind=old_bind)
        engine.dispose()
        with root.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()


@pytest.fixture
def queued(postgres):
    engine, _ = postgres
    with SessionLocal() as db:
        db.add(User(id="teacher", email="teacher@example.test", name="Teacher", role=Role.TEACHER))
        db.flush()
        db.add(SourceDocument(id="source", owner_id="teacher", filename="fixture.pdf", object_key="fixture.pdf",
                              sha256="fixture", media_type="application/pdf", size_bytes=1))
        db.flush()
        db.add(ImportJob(id="import", owner_id="teacher", question_document_id="source", status=ImportStatus.UPLOADED))
        db.flush()
        record = enqueue(db, "app.tasks.extract_import", ["import"])
        job_id = record.id
        db.commit()
    return engine, job_id


def test_dispatch_rolls_back_with_business_state_and_never_publishes(queued, monkeypatch):
    engine, job_id = queued
    from app.tasks import extract_import
    monkeypatch.setattr(extract_import, "delay", lambda *args: pytest.fail("Broker publish in durable mode"))
    with SessionLocal() as db:
        job = db.get(ImportJob, "import")
        job.error = "uncommitted"
        enqueue(db, extract_import, [job.id])
        db.rollback()
    with SessionLocal() as db:
        assert db.get(ImportJob, "import").error is None
        assert db.query(DurableJob).count() == 1
        commit_task(db, extract_import, "import")
    # Simulates API exit immediately after commit: no wake or broker message
    # was emitted, but the reconciler can still discover the dispatch.
    with SessionLocal() as db:
        assert db.query(DurableJob).filter_by(status="pending").count() == 2


def test_duplicate_delivery_and_resource_lock_exclude_second_owner(queued):
    engine, job_id = queued
    with SessionLocal() as db:
        second_id = enqueue(db, "app.tasks.extract_import", ["import"]).id
        db.commit()
    with engine.connect() as first, engine.connect() as second:
        job = claim(first, job_id)
        assert claim(second, job_id) is None
        assert claim(second, second_id) is None  # different dispatch, same import
        calls = []
        execute(first, job, handler=lambda *args: calls.append(args))
        assert calls == [("import",)]
        assert claim(second, job_id) is None  # completed tombstone suppresses replay


def test_crash_after_checkpoint_recovers_without_repeating_completed_import(queued):
    engine, job_id = queued
    with engine.connect() as first:
        job = claim(first, job_id)
        token = current_execution.set(Execution(first, first.connection.dbapi_connection, job_id, job["generation"]))
        try:
            with SessionLocal() as db:
                row = db.get(ImportJob, "import")
                row.status = ImportStatus.REVIEW
                db.commit()
        finally:
            current_execution.reset(token)
        # Socket close here models death after the business commit but before
        # the queue completion commit. Its session lock is released.
    with engine.connect() as replacement:
        replay = claim(replacement, job_id)
        assert replay["generation"] == 2
        execute(replacement, replay)  # prepare sees the completed checkpoint
    with SessionLocal() as db:
        assert db.get(ImportJob, "import").status == ImportStatus.REVIEW
        assert db.get(DurableJob, job_id).status == "completed"


def test_stale_generation_cannot_commit_or_publish_completion(queued):
    engine, job_id = queued
    with engine.connect() as connection:
        job = claim(connection, job_id)
        owner = Execution(connection, connection.connection.dbapi_connection, job_id, job["generation"])
        token = current_execution.set(owner)
        try:
            with SessionLocal() as db:
                row = db.get(ImportJob, "import")
                row.error = "stale write"
                with engine.begin() as other:
                    other.execute(update(DurableJob).where(DurableJob.id == job_id).values(generation=99))
                with pytest.raises(OwnershipLost):
                    db.commit()
                db.rollback()
            with pytest.raises(OwnershipLost):
                finish(connection, job, "completed")
        finally:
            current_execution.reset(token)
    with SessionLocal() as db:
        assert db.get(ImportJob, "import").error is None


def test_database_disconnect_fences_reconnect_and_allows_recovery(queued):
    engine, job_id = queued
    with engine.connect() as first:
        job = claim(first, job_id)
        owner = Execution(first, first.connection.dbapi_connection, job_id, job["generation"])
        first.invalidate()  # physical connection and its advisory lock are lost
        with engine.connect() as second:
            recovered = claim(second, job_id)
            assert recovered["generation"] == 2
            token = current_execution.set(owner)
            try:
                with SessionLocal() as db:
                    with pytest.raises(OwnershipLost):
                        db.get(ImportJob, "import")
                with pytest.raises(OwnershipLost):
                    asset_key("fixtures/output.mp4")
            finally:
                current_execution.reset(token)


def test_actual_process_death_releases_ownership(queued, postgres):
    engine, job_id = queued
    _, url = postgres
    script = """
import os, sys
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from app.durable_worker import claim
engine = create_engine(sys.argv[1], poolclass=NullPool)
with engine.connect() as connection:
    assert claim(connection, sys.argv[2])
    os._exit(77)
"""
    process = subprocess.run([sys.executable, "-c", script, url, job_id], timeout=20, capture_output=True, text=True)
    assert process.returncode == 77, process.stderr
    with engine.connect() as connection:
        recovered = claim(connection, job_id)
        assert recovered["attempts"] == 2
        execute(connection, recovered, handler=lambda *args: None)


def test_repeated_interruptions_are_bounded_and_visible_in_import_ui(queued, monkeypatch):
    engine, job_id = queued
    monkeypatch.setattr(settings(), "worker_max_attempts", 1)
    with engine.connect() as first:
        assert claim(first, job_id)
    with engine.connect() as second:
        job = claim(second, job_id)
        assert job["exhausted"]
        execute(second, job)
    with SessionLocal() as db:
        assert db.get(DurableJob, job_id).status == "failed"
        assert db.get(ImportJob, "import").status == ImportStatus.FAILED
        assert "interrupted" in db.get(ImportJob, "import").error


def test_execution_upload_keys_differ_after_recovery(queued):
    engine, job_id = queued
    keys = []
    for _ in range(2):
        with engine.connect() as connection:
            job = claim(connection, job_id)
            token = current_execution.set(Execution(connection, connection.connection.dbapi_connection, job_id, job["generation"]))
            try:
                keys.append(asset_key("fixture/output.mp4"))
            finally:
                current_execution.reset(token)
    assert keys[0] != keys[1]
    assert all(key.startswith("fixture/") and key.endswith(".mp4") for key in keys)


@pytest.mark.parametrize("control, expected", [("pause_requested", ImportStatus.UPLOADED), ("cancel_requested", ImportStatus.FAILED)])
def test_admin_control_wins_over_exhausted_crash_recovery(queued, monkeypatch, control, expected):
    from app.models import QuestionBank
    engine, job_id = queued
    monkeypatch.setattr(settings(), "worker_max_attempts", 1)
    with SessionLocal() as db:
        db.add(QuestionBank(id="bank", name="Fixture", slug="fixture", exam_code="JEE")); db.flush()
        row = db.get(ImportJob, "import")
        row.bank_id = "bank"
        row.status = ImportStatus.PROCESSING
        row.extraction_meta = {control: True}
        record = db.get(DurableJob, job_id)
        record.task = "app.tasks.extract_bank_import"
        record.resource = "bank:bank"
        db.commit()
    with engine.connect() as first:
        assert claim(first, job_id)
    with engine.connect() as replacement:
        execute(replacement, claim(replacement, job_id))
    with SessionLocal() as db:
        assert db.get(ImportJob, "import").status == expected
        assert db.get(DurableJob, job_id).status == "completed"


def test_reconciler_retries_502_and_sends_no_idle_requests(queued, monkeypatch):
    from app.worker_reconciler import reconcile
    engine, job_id = queued
    monkeypatch.setattr(settings(), "worker_wake_url", "http://worker.internal:8000")
    monkeypatch.setattr(settings(), "worker_wake_token", "x" * 32)
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(502 if len(calls) == 1 else 202, request=httpx.Request("POST", url))
    monkeypatch.setattr("app.worker_reconciler.httpx.post", post)
    reconcile(); reconcile()
    assert calls == ["http://worker.internal:8000/wake"] * 2
    with engine.begin() as connection:
        connection.execute(update(DurableJob).values(status="completed"))
    reconcile()
    assert len(calls) == 2


def test_wake_listener_rejects_unauthorized_requests_and_coalesces_wakes(monkeypatch):
    from app import worker_service
    monkeypatch.setattr(settings(), "durable_jobs_enabled", True)
    monkeypatch.setattr(settings(), "worker_wake_token", "x" * 32)
    class IdleSupervisor:
        def __init__(self):
            self.kick = asyncio.Event()
        async def run(self):
            await asyncio.Future()
    supervisor = IdleSupervisor()
    monkeypatch.setattr(worker_service, "supervisor", supervisor)
    with TestClient(worker_service.app) as client:
        assert client.post("/wake").status_code == 401
        assert not supervisor.kick.is_set()
        for _ in range(3):
            assert client.post("/wake", headers={"Authorization": "Bearer " + "x" * 32}).status_code == 202
        assert supervisor.kick.is_set()


@pytest.mark.parametrize("timeout", [False, True])
def test_supervisor_kills_execution_group_on_shutdown_or_timeout(monkeypatch, timeout):
    from app.worker_service import Supervisor
    async def scenario():
        started = asyncio.Event()
        killed = asyncio.Event()
        class Child:
            pid = 123456
            returncode = None
            async def wait(self):
                await killed.wait()
                return self.returncode
        child = Child()
        async def spawn(*args, **kwargs):
            assert kwargs["start_new_session"] is True
            started.set()
            return child
        def killpg(pid, sig):
            assert pid == child.pid
            child.returncode = -9
            killed.set()
        monkeypatch.setattr("app.worker_service.asyncio.create_subprocess_exec", spawn)
        monkeypatch.setattr("app.worker_service.os.killpg", killpg)
        monkeypatch.setattr(settings(), "worker_job_timeout_seconds", 0.01 if timeout else 60)
        supervisor = Supervisor()
        task = asyncio.create_task(supervisor.run())
        supervisor.kick.set()
        await asyncio.wait_for(started.wait(), 1)
        if timeout:
            await asyncio.wait_for(killed.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.returncode == -9
    asyncio.run(scenario())


def test_real_renderer_descendant_stays_in_supervised_group_and_is_killed(monkeypatch, tmp_path):
    from app.worker_service import Supervisor
    from pathlib import Path
    pid_file = tmp_path / "renderer.pid"
    script = """
import sys
from app.job_context import current_execution
from app.explainer_generation import run
current_execution.set(object())
run([sys.executable, '-c', 'import os,time,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)', sys.argv[1]])
"""
    async def scenario():
        spawn = asyncio.create_subprocess_exec
        child_holder = []
        async def replacement(*args, **kwargs):
            child = await spawn(sys.executable, "-c", script, str(pid_file), **kwargs)
            child_holder.append(child)
            return child
        monkeypatch.setattr("app.worker_service.asyncio.create_subprocess_exec", replacement)
        supervisor = Supervisor()
        task = asyncio.create_task(supervisor.run())
        supervisor.kick.set()
        try:
            async def started():
                while not pid_file.exists():
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(started(), 10)
            renderer_pid = int(pid_file.read_text())
            assert os.getpgid(renderer_pid) == child_holder[0].pid
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        stat = Path(f"/proc/{renderer_pid}/stat")
        assert not stat.exists() or stat.read_text().split()[2] == "Z"
    asyncio.run(scenario())


def test_supervisor_recovers_from_process_creation_failure_on_next_wake(monkeypatch):
    from app.worker_service import Supervisor
    async def scenario():
        failed = asyncio.Event()
        retried = asyncio.Event()
        calls = []
        class EmptyChild:
            pid = 123456
            returncode = 10
            async def wait(self):
                return 10
        async def spawn(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                failed.set()
                raise OSError("Temporary process limit")
            retried.set()
            return EmptyChild()
        def no_group(*args):
            raise ProcessLookupError()
        monkeypatch.setattr("app.worker_service.asyncio.create_subprocess_exec", spawn)
        monkeypatch.setattr("app.worker_service.os.killpg", no_group)
        supervisor = Supervisor()
        task = asyncio.create_task(supervisor.run())
        try:
            supervisor.kick.set()
            await asyncio.wait_for(failed.wait(), 1)
            supervisor.kick.set()
            await asyncio.wait_for(retried.wait(), 1)
            assert not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(scenario())


def test_renderer_recovery_retains_cancellation_and_protects_long_queue_wait(postgres):
    from app.models import ExplainerRevision, Test, TestStatus, TestVersion
    from app.tasks import fail_stale_explainers
    from app.durable_worker import prepare
    engine, _ = postgres
    with SessionLocal() as db:
        db.add(User(id="teacher", email="teacher@example.test", name="Teacher", role=Role.TEACHER)); db.flush()
        db.add(Test(id="test", owner_id="teacher", title="Fixture", subject="Physics", status=TestStatus.PUBLISHED, duration_seconds=60)); db.flush()
        db.add(TestVersion(id="version", test_id="test", ordinal=1, content={"questions": []})); db.flush()
        db.add(ExplainerRevision(id="revision", test_id="test", version_id="version", question_id="question", requested_by="teacher", status="rendering", updated_at=datetime.now(timezone.utc)-timedelta(hours=1)))
        job_id = enqueue(db, "app.explainers.render_explainer", ["revision"], queue="explainers").id
        db.commit()
    assert fail_stale_explainers.run() == {"failed": 0}
    with engine.connect() as connection:
        job = claim(connection, job_id)
        token = current_execution.set(Execution(connection, connection.connection.dbapi_connection, job_id, job["generation"]))
        try:
            assert prepare(job) is True
        finally:
            current_execution.reset(token)
    with SessionLocal() as db:
        assert db.get(ExplainerRevision, "revision").status == "queued"
        db.get(ExplainerRevision, "revision").status = "cancelled"
        db.commit()
    with engine.connect() as connection:
        job = claim(connection, job_id)
        execute(connection, job)
    with SessionLocal() as db:
        assert db.get(ExplainerRevision, "revision").status == "cancelled"
        assert db.get(DurableJob, job_id).status == "completed"


def test_expiry_runs_with_worker_lock_held_and_concurrent_retries_submit_once(queued, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.models import Attempt, AuditEvent, Test, TestStatus, TestVersion
    from app.tasks import finalize_expired_attempts
    engine, job_id = queued
    deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    with SessionLocal() as db:
        db.add(User(id="student", email="student@example.test", name="Student", role=Role.STUDENT)); db.flush()
        db.add(Test(id="test", owner_id="teacher", title="Fixture", subject="Physics", status=TestStatus.PUBLISHED, duration_seconds=60)); db.flush()
        db.add(TestVersion(id="version", test_id="test", ordinal=1, content={"questions": []})); db.flush()
        db.add(Attempt(id="attempt", test_id="test", version_id="version", student_id="student", started_at=deadline-timedelta(minutes=1), ends_at=deadline))
        db.commit()
    def unavailable(*args, **kwargs):
        raise ConnectionError("Redis unavailable")
    monkeypatch.setattr("app.tasks.celery_app.send_task", unavailable)
    # Synchronize the two expiry passes after their initial stale read, as can
    # happen when browser submission and the scheduler race at the deadline.
    from app.services import finalize_if_expired
    barrier = Barrier(2)
    def race(db, attempt, now):
        barrier.wait(timeout=10)
        return finalize_if_expired(db, attempt, now)
    monkeypatch.setattr("app.tasks.finalize_if_expired", race)
    with engine.connect() as busy:
        assert claim(busy, job_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: finalize_expired_attempts.run(), range(2)))
    assert sum(result["finalized"] for result in results) == 1
    with SessionLocal() as db:
        assert db.get(Attempt, "attempt").submitted_at == deadline
        assert db.query(AuditEvent).filter_by(action="attempt.auto_submitted").count() == 1


def test_task_exception_retries_without_losing_dispatch_and_respects_backoff(queued):
    engine, job_id = queued
    def transient(*args):
        raise RuntimeError("provider temporarily unavailable")
    with engine.connect() as connection:
        execute(connection, claim(connection, job_id), handler=transient)
    with SessionLocal() as db:
        row = db.get(DurableJob, job_id)
        assert row.status == "pending" and row.attempts == 1
        assert "temporarily unavailable" in row.error
    with engine.connect() as connection:
        assert claim(connection, job_id) is None
    with engine.begin() as connection:
        connection.execute(update(DurableJob).where(DurableJob.id == job_id).values(available_at=datetime.now(timezone.utc)))
    with engine.connect() as connection:
        execute(connection, claim(connection, job_id), handler=lambda *args: None)
    with SessionLocal() as db:
        assert db.get(DurableJob, job_id).status == "completed"


def test_durable_cleanup_cannot_delete_replacement_assets(queued, monkeypatch):
    from app.storage import delete_prefix
    engine, job_id = queued
    monkeypatch.setattr("app.storage.client", lambda: pytest.fail("Remote deletion is not fenced"))
    with engine.connect() as connection:
        job = claim(connection, job_id)
        token = current_execution.set(Execution(connection, connection.connection.dbapi_connection, job_id, job["generation"]))
        try:
            delete_prefix("fixture/source/")
        finally:
            current_execution.reset(token)


def test_real_import_resumes_at_page_checkpoint_after_execution_crash(queued, monkeypatch):
    import re
    import fitz
    from app import tasks
    from app.models import Question
    engine, job_id = queued
    document = fitz.open()
    for number in (1, 2):
        page = document.new_page(width=400, height=500)
        page.insert_text((30, 50), f"{number}. What is two plus two?")
    raw = document.tobytes()
    document.close()
    monkeypatch.setattr(settings(), "openrouter_api_key", "fixture-only")
    monkeypatch.setattr(tasks, "get", lambda key: raw)
    monkeypatch.setattr(tasks, "infer_paper_title", lambda *args: "Failure fixture")
    monkeypatch.setattr(tasks, "extraction_units", lambda texts: [(0, 1), (1, 2)])
    monkeypatch.setattr(tasks, "visual_context", lambda *args: [])
    monkeypatch.setattr(tasks, "save_figures", lambda *args: ([], []))
    calls = []
    class Crash(BaseException):
        pass
    def provider(payload, *args):
        page = int(re.search(r"BEGINS on primary source pages (\d+)", payload["messages"][1]["content"][0]["text"])[1])
        calls.append(page)
        if calls == [1, 2]:
            raise Crash()
        return {"questions": [{"number": str(page), "kind": "single_choice", "stem_markdown": "What is two plus two?",
            "options": [{"value": "a", "markdown": "4"}, {"value": "b", "markdown": "5"}],
            "answer": {"option": 0}, "solution_markdown": "Adding two and two gives four.", "confidence": 1,
            "source_spans": [{"page": page, "bbox": [20, 20, 380, 100]}], "diagrams": []}]}
    monkeypatch.setattr(tasks, "request_extraction", provider)
    with engine.connect() as first:
        with pytest.raises(Crash):
            execute(first, claim(first, job_id))
    with SessionLocal() as db:
        assert db.query(Question).count() == 1
        assert db.get(ImportJob, "import").extraction_meta["processed_pages"] == 1
        assert db.get(DurableJob, job_id).status == "running"
    with engine.connect() as replacement:
        execute(replacement, claim(replacement, job_id))
    assert calls == [1, 2, 2]
    with SessionLocal() as db:
        assert db.query(Question).count() == 2
        assert db.get(ImportJob, "import").status == ImportStatus.REVIEW
        assert db.get(DurableJob, job_id).status == "completed"


def test_additive_queue_migration_preserves_business_tables(postgres):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    engine, _ = postgres
    # Alembic's script folder is not a Python package (and shares its name with
    # the installed library), so load this revision by its explicit file path.
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0022_durable_jobs.py"
    spec = importlib.util.spec_from_file_location("queue_migration_fixture", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        DurableJob.__table__.drop(connection)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert "durable_jobs" in inspect(connection).get_table_names()
        migration.downgrade()
        assert "durable_jobs" not in inspect(connection).get_table_names()
        assert "import_jobs" in inspect(connection).get_table_names()


def test_http_wake_runs_real_worker_then_closes_children_and_database_sockets(queued, postgres):
    import socket
    import time
    from pathlib import Path
    engine, job_id = queued
    _, url = postgres
    with engine.begin() as connection:
        connection.execute(update(ImportJob).where(ImportJob.id == "import").values(status=ImportStatus.REVIEW))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = uuid.uuid4().hex
    environment = {**os.environ, "DATABASE_URL": url, "DURABLE_JOBS_ENABLED": "true",
                   "WORKER_WAKE_TOKEN": token, "WORKER_QUEUE": "celery"}
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.worker_service:app",
                                "--host", "127.0.0.1", "--port", str(port)], env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                response = httpx.post(base + "/wake", headers={"Authorization": "Bearer " + token}, timeout=1)
                if response.status_code == 202:
                    break
            except httpx.TransportError:
                pass
            assert process.poll() is None, process.stdout.read()
            assert time.monotonic() < deadline, "Wake listener failed to start"
            time.sleep(0.05)
        while True:
            with engine.connect() as connection:
                status = connection.scalar(select(DurableJob.status).where(DurableJob.id == job_id))
                sockets = connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE application_name='testnow-durable-worker'"))
            children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().strip()
            if status == "completed" and not children and sockets == 0:
                break
            assert time.monotonic() < deadline, f"Worker did not idle: {status}, {children}, {sockets}"
            time.sleep(0.05)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
