"""Small HTTP wake listener; idle listeners keep no database/broker sockets."""
import asyncio
import hmac
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from .config import settings

log = logging.getLogger(__name__)


class Supervisor:
    def __init__(self):
        self.kick = asyncio.Event()
        self.child = None

    async def terminate(self):
        child = self.child
        if child is not None:
            # Includes renderer/ffmpeg grandchildren, which otherwise outlive
            # their worker and can continue using resources after a timeout.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await child.wait()

    async def run(self):
        try:
            while True:
                await self.kick.wait()
                self.kick.clear()
                while True:
                    try:
                        self.child = await asyncio.create_subprocess_exec(
                            sys.executable, "-m", "app.durable_worker", start_new_session=True)
                    except OSError:
                        log.exception("Could not start execution; will retry on the next wake")
                        break
                    try:
                        code = await asyncio.wait_for(self.child.wait(), settings().worker_job_timeout_seconds)
                    except asyncio.TimeoutError:
                        log.error("Worker timed out; terminating execution for recovery")
                        await self.terminate()
                        code = 11
                    except asyncio.CancelledError:
                        await self.terminate()
                        raise
                    finally:
                        # Even a normally exited worker may have left an
                        # orphaned renderer. Durable render commands remain
                        # in this group so every exit cleans up descendants.
                        await self.terminate()
                        self.child = None
                    if code == 10:  # Database has no claimable work; stop all polling.
                        break
                    if code != 0:
                        # Database outages and fenced/crashed children must not
                        # cause an unbounded restart loop or keep service awake.
                        break
        finally:
            await self.terminate()


supervisor = Supervisor()


@asynccontextmanager
async def lifespan(app):
    config = settings()
    if not config.durable_jobs_enabled or not config.worker_wake_token or len(config.worker_wake_token) < 32:
        raise RuntimeError("Enable durable jobs and configure a wake token of at least 32 characters")
    if config.worker_queue not in {"celery", "explainers"}:
        raise RuntimeError("Unsupported worker queue")
    task = asyncio.create_task(supervisor.run())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.post("/wake", status_code=202)
async def wake(authorization: str | None = Header(default=None)):
    expected = "Bearer " + (settings().worker_wake_token or "")
    if not settings().worker_wake_token or not hmac.compare_digest(authorization or "", expected):
        raise HTTPException(401, "Invalid wake credential")
    supervisor.kick.set()
    return {"accepted": True}
