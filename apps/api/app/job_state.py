"""Additive extraction lifecycle contract; publication and generation stay separate."""
from datetime import datetime, timezone


def extraction_state(job):
    meta=job.extraction_meta or {}
    if meta.get("cancel_requested"):
        return "cancelled" if job.status.value=="failed" and (meta.get("cancelled_acknowledged_at") or job.error=="Cancelled by administrator") else "cancellation_requested"
    if meta.get("pause_requested"):
        return "paused" if job.status.value=="uploaded" else "pause_requested"
    return {"uploaded":"queued","processing":"running","review":"completed","approved":"completed","failed":"failed"}[job.status.value]


def lifecycle_view(job):
    meta=job.extraction_meta or {}
    return {"state":extraction_state(job),"heartbeat_at":meta.get("worker_heartbeat_at"),
            "last_activity_at":job.updated_at,"pause_acknowledged_at":meta.get("paused_acknowledged_at"),
            "cancel_acknowledged_at":meta.get("cancelled_acknowledged_at"),
            "stage":"extraction","processed":meta.get("processed_pages",0),"total":meta.get("total_pages"),"unit":"source pages"}


def heartbeat():
    return datetime.now(timezone.utc).isoformat()
