# Scale-to-zero workers

The opt-in deployment replaces Redis delivery of user jobs with a PostgreSQL
queue. API changes and queue insertion commit together. A small persistent
maintenance scheduler runs assessment expiry every 60 seconds and checks the
queue every five seconds. It calls the appropriate worker's authenticated
private `/wake` endpoint only when due pending or running jobs exist.

Each worker has a small HTTP listener and runs one job per subprocess. Once
no claimable work remains, the subprocess exits, closes every database socket,
and stops polling. Enable Railway Serverless on these HTTP worker services
and redeploy them. Railway then sleeps them after its inactivity window
(currently approximately 5–10 minutes). A subsequent private HTTP request wakes
them. Cold starts can fail with 502; the next scheduler pass retries the wake.
The scheduler keeps waking running jobs, which also keeps long CPU/render tasks
active. No web browser needs to remain open.

This eliminates worker compute charges **after Railway puts the service to
sleep**, rather than immediately upon completing a task. PostgreSQL, Redis,
the API/web application and the small scheduler still have their own costs.
Redis is still used by the existing login rate limiter/readiness checks.

## Ownership, replay and recovery

`durable_jobs` records the task name, immutable arguments, resource, queue,
attempt count, generation, status and next permitted attempt time. Committed
dispatches survive API exits and failed HTTP requests. Completed records are
retained as replay tombstones. `completed` means the command was processed;
existing import/render states continue to report domain validation failures.

An execution holds a PostgreSQL **session advisory lock** on its resource.
All imports/solution runs/revalidations in one bank share a lock; standalone
imports and individual explainers have their own locks. Different resources
can run on different worker replicas. A delayed heartbeat never grants another
worker ownership. A recovery process must actually acquire the lock after the
previous database session is released. Use the direct PostgreSQL connection
or session pooling, never a transaction-mode pooler.

Every business session is bound to the connection holding that lock. Physical
connection replacement or a changed generation raises `OwnershipLost`, which
bypasses ordinary task exception handlers. Each commit validates the generation
under a row lock in the same transaction as its business writes. A disconnected
worker cannot reconnect and commit stale success or failure. PostgreSQL socket
keepalives detect dead peers; a network partition can delay lock release, but
cannot permit a second live database writer.

Recovery honors import page checkpoints, completed import states, paused or
cancelled imports, superseded solution runs, and cancelled/completed explainers.
An interrupted active render restarts from its immutable revision input.
Worker-generated asset keys contain the dispatch ID and execution generation;
an old upload cannot overwrite a replacement's assets. Provider calls can be
repeated after a crash before a checkpoint, and may incur provider charges;
this is at-least-once execution, not exactly-once external API billing.

Remote deletion cannot participate in database fencing. Cleanup performed by
durable tasks removes database references but defers physical object deletion.
Use reference-aware garbage collection for unreferenced source/output objects;
do not blindly expire published assets. API-side cleanup retains its existing
behavior. An interrupted upload can leave an unreferenced private object.

The supervisor kills the entire child process group on service shutdown or
execution timeout (default one hour), including render subprocesses. The next
wake recovers the job. Three interrupted executions or repeated uncaught task
failures exhaust recovery and set a visible failure state where applicable;
existing retry controls create a fresh dispatch. Caught domain errors retain
the tasks' existing terminal-failure behavior. Uncaught exceptions back off
before retrying. Persistently unavailable services should trigger operational
alerts rather than infinite retries.

## Deployment and cutover

The default remains `DURABLE_JOBS_ENABLED=false`; the existing Celery deployment
continues to work while the additive migration and new code are deployed.

1. Deploy this code with the flag disabled, and apply `alembic upgrade head` on
   the API. Revision `0022_durable_jobs` adds only the queue table/index.
2. Keep a maintenance window for job-creating actions during cutover. Let both
   legacy queues drain, including scheduled/reserved tasks and active renders.
   Inspect Celery active/reserved/scheduled tasks and the `celery`/`explainers`
   broker queues. Resolve any old `processing` records already stranded by
   the legacy early-acknowledgement behavior using the existing job controls.
   No legacy Redis messages are deleted or silently converted by this change.
3. Replace the single scheduler's command with `python -m app.maintenance_scheduler`
   (`apps/api/railway.scheduler.toml`). It runs expiry directly even with the flag
   disabled. Stop the old Celery beat process. Verify browser-independent expiry
   before stopping the drained legacy workers. A brief scheduler overlap is
   safe for expiry, whose row lock makes submission idempotent.
4. Change each worker start command to the one in
   `apps/api/railway.worker.toml`. Keep the normal worker's existing `Dockerfile`
   and the renderer's existing `Dockerfile.render`. Configure both workers:
   `DURABLE_JOBS_ENABLED=true`, the direct `DATABASE_URL`, all existing provider
   and storage variables, and the same sealed random `WORKER_WAKE_TOKEN` of at
   least 32 characters. Set `WORKER_QUEUE=celery` on `worker` and
   `WORKER_QUEUE=explainers` on `explainer-worker`. Use one Uvicorn process per
   container. No public domain is needed. Keep `PORT=8000` consistent with URLs.
5. Enable Serverless on both HTTP worker services and **redeploy** them for the
   setting to apply. Remove any old worker healthcheck path; the services have
   only the authenticated wake route. External uptime probes would keep them
   awake. Leave the scheduler persistent, with no cron schedule or Serverless.
6. Set the scheduler's `DURABLE_JOBS_ENABLED=true`, the same `WORKER_WAKE_TOKEN`,
   `WORKER_WAKE_URL=http://worker.railway.internal:8000`, and
   `EXPLAINER_WORKER_WAKE_URL=http://explainer-worker.railway.internal:8000`.
   Deploy it. Missing wake configuration fails startup rather than silently
   accepting a scheduler that cannot dispatch work.
7. Enable `DURABLE_JOBS_ENABLED=true` on the API and redeploy. Resume job-creating
   actions. Test a small import, solution run, figure revalidation and render;
   verify cancellation/retry and an expired assessment. After both queues drain,
   verify both Railway worker services actually sleep and wake on the next job.

For rollback, pause job creation, allow durable jobs to drain, disable the API
flag, restore the Celery worker commands and beat scheduler, then resume.
Do not downgrade/drop the queue table while durable jobs remain. If a durable
job is stuck, keep its row and inspect the resource lock, worker logs and attempt
error. Never clear ownership merely because a timestamp looks old.

## Failure fixtures

`apps/api/tests/test_durable_jobs.py` includes disposable per-test PostgreSQL
schemas, import/source fixtures, a renderer revision fixture, concurrent
assessment attempts, process-death injection, and supervisor process fixtures.
Set `TEST_DURABLE_DATABASE_URL` to an **isolated test database** to run them:

```sh
cd apps/api
TEST_DURABLE_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:PORT/TEST_DB \
  ../../.venv/bin/python -m pytest tests/test_durable_jobs.py \
  tests/test_maintenance_scheduler.py tests/test_classes.py -q
```

Tests cover transaction rollback, committed-but-unwoken dispatch, duplicate
delivery/resource exclusion, process death, completion gaps, disconnected and
stale writers, failed cold-start wakes, idle silence, credential rejection,
supervisor timeout/shutdown, bounded retries/backoff, isolated upload keys,
cancelled renders, long queue waits, and concurrent expiry while a worker is
busy and broker publishing is unavailable. PostgreSQL-only fixtures skip when
the explicit test URL is absent. Platform sleep timing itself requires the
Railway smoke checks above; unit tests cannot assert billing behavior.

Railway references: [Serverless](https://docs.railway.com/deployments/serverless)
and [configuration as code](https://docs.railway.com/config-as-code/reference).
