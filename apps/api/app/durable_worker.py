"""Run one database job per process. No broker or idle polling.

The dedicated PostgreSQL connection owns a session advisory lock and binds
every business Session. Losing that physical connection permanently fences
the execution. A replacement can claim only after PostgreSQL releases the
lock, rather than guessing death from an expired heartbeat.
"""
import hashlib
import logging
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from .config import settings
from .job_context import Execution, OwnershipLost, current_execution
from .models import DurableJob

log = logging.getLogger(__name__)


def lock_key(resource):
    return int.from_bytes(hashlib.sha256(resource.encode()).digest()[:8], "big", signed=True)


def worker_engine():
    url = settings().database_url
    if not url.startswith("postgresql"):
        raise RuntimeError("Durable workers require PostgreSQL session locks (direct connection, no transaction pooler)")
    options = str(make_url(url).query.get("options", ""))
    options += f" -c statement_timeout=60000 -c idle_in_transaction_session_timeout={(settings().worker_job_timeout_seconds + 60) * 1000}"
    return create_engine(url, poolclass=NullPool, connect_args={
        "application_name": "testnow-durable-worker",
        "connect_timeout": 10, "keepalives": 1, "keepalives_idle": 30,
        "keepalives_interval": 10, "keepalives_count": 3,
        "options": options,
    })


def claim(connection, job_id):
    row = connection.execute(select(DurableJob.__table__).where(DurableJob.id == job_id)).mappings().first()
    connection.commit()
    if not row or row["status"] not in {"pending", "running"}:
        return None
    key = lock_key(row["resource"])
    acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
    connection.commit()
    if not acquired:
        return None
    # The connection is never returned to a pool. Closing it releases the lock
    # on every exit path, including exceptions before ownership is recorded.
    row = connection.execute(select(DurableJob.__table__).where(DurableJob.id == job_id).with_for_update()).mappings().one()
    now = datetime.now(timezone.utc)
    if row["status"] not in {"pending", "running"} or row["available_at"] > now:
        connection.rollback()
        return None
    values = dict(row)
    values["generation"] += 1
    values["exhausted"] = values["attempts"] >= settings().worker_max_attempts
    if not values["exhausted"]:
        values["attempts"] += 1
    connection.execute(update(DurableJob).where(DurableJob.id == job_id).values(
        status="running", generation=values["generation"], attempts=values["attempts"], updated_at=now))
    connection.commit()
    return values


def finish(connection, job, status, error=None, delay=0):
    result = connection.execute(update(DurableJob).where(
        DurableJob.id == job["id"], DurableJob.generation == job["generation"], DurableJob.status == "running",
    ).values(status=status, error=error, updated_at=datetime.now(timezone.utc),
             available_at=datetime.now(timezone.utc) + timedelta(seconds=delay)))
    if result.rowcount != 1:
        connection.rollback()
        raise OwnershipLost("Completion was fenced")
    connection.commit()


def prepare(job):
    """Check durable business checkpoints before replaying an interrupted job."""
    from .db import SessionLocal
    from .models import ExplainerRevision, ImportJob, ImportStatus
    name, args = job["task"], job["args"]
    with SessionLocal() as db:
        if name == "app.explainers.render_explainer":
            row = db.get(ExplainerRevision, args[0])
            if row is None or row.status in {"ready", "cancelled", "failed"}:
                return False
            if row.status != "queued":
                row.status = "queued"
                row.progress_message = "Recovering interrupted generation"
                db.commit()
        elif name in {"app.tasks.extract_import", "app.tasks.extract_bank_import"}:
            row = db.get(ImportJob, args[0])
            if row is None or row.status in {ImportStatus.REVIEW, ImportStatus.APPROVED}:
                return False
            if name == "app.tasks.extract_bank_import":
                from .bank_imports import _control_state, _finish_controlled_import
                control = _control_state(db, row.id)
                if control:
                    # Explicit admin controls take precedence even after the
                    # last allowed execution was interrupted.
                    _finish_controlled_import(db, row.id, control, {})
                    return False
        elif name == "app.tasks.generate_bank_solutions":
            row = db.get(ImportJob, args[0])
            state = (row.extraction_meta or {}).get("solution_generation", {}) if row else {}
            if state.get("run_id") != args[1] or state.get("status") not in {"queued", "running"}:
                return False
        elif name == "app.figure_repairs.revalidate_bank_figures":
            rows = db.query(ImportJob).filter_by(bank_id=args[0]).all()
            if rows and all((row.extraction_meta or {}).get("figure_revalidation", {}).get("run_id") == args[1]
                            and (row.extraction_meta or {}).get("figure_revalidation", {}).get("status") == "completed"
                            for row in rows):
                return False
    return True


def fail_interrupted(job):
    """Expose exhausted crash recovery through existing UI failure states."""
    from .db import SessionLocal
    from .models import ExplainerRevision, ImportJob, ImportStatus
    message = "Worker interrupted repeatedly; retry this job"
    with SessionLocal() as db:
        if job["task"] == "app.explainers.render_explainer":
            row = db.get(ExplainerRevision, job["args"][0])
            if row and row.status not in {"ready", "cancelled", "failed"}:
                row.status = "failed"; row.error = message; row.progress_message = message
        elif job["task"] in {"app.tasks.extract_import", "app.tasks.extract_bank_import"}:
            row = db.get(ImportJob, job["args"][0])
            if row and row.status in {ImportStatus.UPLOADED, ImportStatus.PROCESSING}:
                row.status = ImportStatus.FAILED; row.error = message
        elif job["task"] == "app.tasks.generate_bank_solutions":
            row = db.get(ImportJob, job["args"][0])
            state = dict((row.extraction_meta or {}).get("solution_generation", {})) if row else {}
            if state.get("run_id") == job["args"][1] and state.get("status") in {"queued", "running"}:
                row.extraction_meta = {**row.extraction_meta, "solution_generation": {**state, "status": "failed", "error": message, "phase": message}}
        elif job["task"] == "app.figure_repairs.revalidate_bank_figures":
            for row in db.query(ImportJob).filter_by(bank_id=job["args"][0]):
                state = dict((row.extraction_meta or {}).get("figure_revalidation", {}))
                if state.get("run_id") == job["args"][1] and state.get("status") == "running":
                    row.extraction_meta = {**row.extraction_meta, "figure_revalidation": {**state, "status": "failed", "errors": [{"error": message}]}}
        db.commit()


def execute(connection, job, handler=None):
    owner = Execution(connection, connection.connection.dbapi_connection, job["id"], job["generation"])
    token = current_execution.set(owner)
    try:
        if handler is None and not prepare(job):
            finish(connection, job, "completed")
            return
        if job["exhausted"]:
            fail_interrupted(job)
            finish(connection, job, "failed", "Recovery attempts exhausted")
            return
        if handler is None:
            from .tasks import celery_app
            from . import figure_repairs  # register the revalidation task
            from .durable_jobs import TASKS
            if job["task"] not in TASKS:
                raise ValueError("Unsupported durable task")
            handler = celery_app.tasks[job["task"]].run
        handler(*job["args"])
        owner.check()
        finish(connection, job, "completed")
    except OwnershipLost:
        # Never reconnect to persist a success/failure from an old execution.
        connection.rollback()
        raise
    except Exception as exc:
        connection.rollback()
        owner.check()
        log.exception("Durable job %s failed", job["id"])
        final = job["attempts"] >= settings().worker_max_attempts
        if final:
            fail_interrupted(job)
        finish(connection, job, "failed" if final else "pending", str(exc)[:2000], delay=min(300, 5 * 2 ** job["attempts"]))
    finally:
        current_execution.reset(token)


def run_one(queue):
    if not settings().durable_jobs_enabled or queue not in {"celery", "explainers"}:
        raise RuntimeError("Durable worker is not configured")
    engine = worker_engine()
    try:
        with engine.connect() as connection:
            candidates = connection.scalars(select(DurableJob.id).where(
                DurableJob.queue == queue, DurableJob.status.in_(["pending", "running"]),
                DurableJob.available_at <= datetime.now(timezone.utc),
            ).order_by(DurableJob.created_at, DurableJob.id)).all()
        for job_id in candidates:
            with engine.connect() as connection:
                job = claim(connection, job_id)
                if job is None:
                    continue
                execute(connection, job)
                return True
        return False
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        sys.exit(0 if run_one(settings().worker_queue) else 10)
    except OwnershipLost:
        log.error("Execution fenced after database ownership loss")
        sys.exit(11)
