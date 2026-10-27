"""Run periodic maintenance without depending on a Celery worker.

This replaces Celery beat. With durable jobs enabled it also reconciles worker
wake-ups. Expiry runs directly regardless of worker or broker availability.
"""
import logging
import time

from .tasks import fail_stale_explainers, finalize_expired_attempts
from .config import settings

log = logging.getLogger(__name__)


def run_forever():
    jobs = [("expiry", finalize_expired_attempts.run, 60),
            ("stale explainers", fail_stale_explainers.run, 300)]
    if settings().durable_jobs_enabled:
        from .worker_reconciler import reconcile, validate_configuration
        validate_configuration()
        jobs.append(("worker wake", reconcile, 5))
    next_run = {name: 0.0 for name, _, _ in jobs}
    while True:
        for name, run, interval in jobs:
            if time.monotonic() < next_run[name]:
                continue
            try:
                run()
            except Exception:
                log.exception("Periodic %s failed; will retry", name)
            finally:
                # Do not spin on an unavailable database, or start a backlog
                # of overlapping executions after a slow maintenance pass.
                next_run[name] = time.monotonic() + interval
        time.sleep(max(0.1, min(next_run.values()) - time.monotonic()))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
