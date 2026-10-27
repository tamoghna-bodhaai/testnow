import pytest

from app import maintenance_scheduler


def test_failed_expiry_does_not_prevent_maintenance_or_retry(monkeypatch):
    now = [0.0]
    calls = []

    def expiry():
        calls.append(("expiry", now[0]))
        if now[0] == 0:
            raise RuntimeError("database unavailable")

    def stale():
        calls.append(("stale", now[0]))

    def sleep(seconds):
        now[0] += seconds
        if now[0] > 60:
            raise StopIteration

    monkeypatch.setattr(maintenance_scheduler.finalize_expired_attempts, "run", expiry)
    monkeypatch.setattr(maintenance_scheduler.fail_stale_explainers, "run", stale)
    monkeypatch.setattr(maintenance_scheduler.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(maintenance_scheduler.time, "sleep", sleep)
    with pytest.raises(StopIteration):
        maintenance_scheduler.run_forever()
    assert calls == [("expiry", 0), ("stale", 0), ("expiry", 60)]


def test_worker_wake_failures_do_not_stop_assessment_expiry(monkeypatch):
    from app.config import settings
    now = [0.0]
    expiry = []
    wakes = []
    def unavailable():
        wakes.append(now[0])
        raise ConnectionError("Worker is unavailable")
    def sleep(seconds):
        now[0] += seconds
        if now[0] > 60:
            raise StopIteration
    monkeypatch.setattr(settings(), "durable_jobs_enabled", True)
    monkeypatch.setattr("app.worker_reconciler.validate_configuration", lambda: None)
    monkeypatch.setattr("app.worker_reconciler.reconcile", unavailable)
    monkeypatch.setattr(maintenance_scheduler.finalize_expired_attempts, "run", lambda: expiry.append(now[0]))
    monkeypatch.setattr(maintenance_scheduler.fail_stale_explainers, "run", lambda: None)
    monkeypatch.setattr(maintenance_scheduler.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(maintenance_scheduler.time, "sleep", sleep)
    with pytest.raises(StopIteration):
        maintenance_scheduler.run_forever()
    assert expiry == [0, 60]
    assert wakes == list(range(0, 61, 5))
