"""Database queue and compatibility seam for legacy Celery dispatch."""
from datetime import datetime, timezone

from sqlalchemy import select

from .config import settings
from .models import DurableJob, ImportJob
from .security import uid


TASKS = {
    "app.tasks.extract_import", "app.tasks.extract_bank_import",
    "app.tasks.generate_bank_solutions", "app.figure_repairs.revalidate_bank_figures",
    "app.explainers.render_explainer",
}


def enqueue(db, task, args, queue="celery"):
    name = task if isinstance(task, str) else task.name
    if name not in TASKS or queue not in {"celery", "explainers"}:
        raise ValueError("Unsupported durable task")
    if name == "app.explainers.render_explainer":
        if queue != "explainers":
            raise ValueError("Explainers require the render queue")
        resource = "explainer:" + args[0]
    elif name == "app.figure_repairs.revalidate_bank_figures":
        resource = "bank:" + args[0]
    else:
        job = db.get(ImportJob, args[0])
        if job is None:
            raise ValueError("Import does not exist")
        # Revalidation, imports and solutions for a bank share one execution
        # lock, including when a retry/resume creates a second queue record.
        resource = "bank:" + job.bank_id if job.bank_id else "import:" + job.id
    record = DurableJob(id=uid(), task=name, args=list(args), queue=queue, resource=resource)
    db.add(record)
    return record


def commit_task(db, task, *args):
    """Commit business state and dispatch together when durable mode is enabled."""
    if settings().durable_jobs_enabled:
        enqueue(db, task, args)
        db.commit()
    else:
        db.commit()
        task.delay(*args)


def outstanding_queues(db):
    return set(db.scalars(select(DurableJob.queue).where(
        DurableJob.status.in_(["pending", "running"]),
        DurableJob.available_at <= datetime.now(timezone.utc),
    ).distinct()))
