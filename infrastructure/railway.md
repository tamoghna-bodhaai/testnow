# Railway production topology

Create one Railway project with seven services:

1. `web`: root directory `.`, Dockerfile `apps/web/Dockerfile`, public domain, `API_INTERNAL_URL=http://api.railway.internal:8000`.
2. `api`: root directory `apps/api`, Dockerfile `Dockerfile`, private only, healthcheck `/ready`, pre-deploy command `alembic upgrade head`.
3. `worker`: root directory `apps/api`, Dockerfile `Dockerfile`, start command `celery -A app.tasks.celery_app worker --loglevel=INFO`, private only.
4. `scheduler`: root directory `apps/api`, Dockerfile `Dockerfile`, start command `celery -A app.tasks.celery_app beat --loglevel=INFO`, private only. Give it the same `DATABASE_URL`, `REDIS_URL`, S3 configuration, and sealed variables as the worker; it submits expired assessment attempts every minute.
5. Railway PostgreSQL, expose its `DATABASE_URL` to API, worker, and scheduler.
6. Railway Redis, expose its `REDIS_URL` to API, worker, and scheduler.
7. Railway S3-compatible bucket, expose endpoint/bucket/access credentials to API, worker, and scheduler.

Set `SUPER_ADMIN_ACCESS_CODE` (exactly five digits), `OPENROUTER_API_KEY`, all S3 keys, and error-monitoring DSNs as sealed variables. `SUPER_ADMIN_ACCESS_CODE` bootstraps `utils@bodhaai.tech` exactly once; it is never returned by the API. Keep `SEED_DEMO_DATA=false` in production (the API refuses to start otherwise), and set `MAX_PDF_PAGES` / `MAX_RENDERED_PDF_BYTES` to safe limits for your worker capacity. Point a custom domain at `web`; do not publish the API or worker. Configure a production alert for API readiness failure, Redis failure, Celery task failure, and import jobs remaining `processing` for more than ten minutes.

Deploy `alembic upgrade head` before the web service rollout. Existing password accounts move to `pending_code` and cannot sign in until the super-admin reissues a five-digit access code. Keep `COOKIE_SECURE=true` and set `CORS_ORIGINS` only to the production web domain. Alert if the scheduler stops publishing its periodic task, because browser-independent automatic submission depends on it.

The access-code launch migration intentionally removes all historical teacher/student accounts and assessment data while preserving the super-admin account. It is irreversible. Database object references are removed; use the storage provider's lifecycle policy to remove any now-orphaned uploaded source files.
