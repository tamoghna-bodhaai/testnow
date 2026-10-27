"""Wake pending/running queues; retry wake failures on the next scheduler pass."""
import logging

import httpx

from .config import settings
from .db import SessionLocal
from .durable_jobs import outstanding_queues

log = logging.getLogger(__name__)


def validate_configuration():
    config = settings()
    if not config.worker_wake_token or len(config.worker_wake_token) < 32:
        raise RuntimeError("Configure a wake token of at least 32 characters")
    if not config.worker_wake_url or not config.explainer_worker_wake_url:
        raise RuntimeError("Configure both private worker wake URLs")


def reconcile():
    config = settings()
    with SessionLocal() as db:
        queues = outstanding_queues(db)
    urls = {"celery": config.worker_wake_url, "explainers": config.explainer_worker_wake_url}
    for queue in sorted(queues):
        try:
            # The first wake after Railway sleep may return 502 or time out.
            # Jobs remain in PostgreSQL and are retried at the next pass.
            response = httpx.post(urls[queue].rstrip("/") + "/wake",
                headers={"Authorization": "Bearer " + config.worker_wake_token}, timeout=3.0)
            response.raise_for_status()
        except Exception:
            log.exception("Could not wake %s worker; dispatch remains durable", queue)
