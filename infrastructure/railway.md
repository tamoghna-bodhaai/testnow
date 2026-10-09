# Railway production topology

Create one Railway project with six services:

1. `web`: root directory `.`, Dockerfile `apps/web/Dockerfile`, public domain, `API_INTERNAL_URL=http://api.railway.internal:8000`.
2. `api`: root directory `apps/api`, Dockerfile `Dockerfile`, private only, healthcheck `/ready`, pre-deploy command `alembic upgrade head`.
3. `worker`: root directory `apps/api`, Dockerfile `Dockerfile`, start command `celery -A app.tasks.celery_app worker --loglevel=INFO`, private only.
4. Railway PostgreSQL, expose its `DATABASE_URL` to API and worker.
5. Railway Redis, expose its `REDIS_URL` to API and worker.
6. Railway S3-compatible bucket, expose endpoint/bucket/access credentials to API and worker.

Set `SESSION_SECRET`, `OPENROUTER_API_KEY`, all S3 keys, and error-monitoring DSNs as sealed variables. Point a custom domain at `web`; do not publish the API or worker. Configure a production alert for API readiness failure, Redis failure, Celery task failure, and import jobs remaining `processing` for more than ten minutes.
