# TestNow Production Platform

TestNow is now a deployable monorepo: a Next.js assessment interface, FastAPI API, PostgreSQL data model, Redis/Celery worker, OpenRouter structured document extraction, and S3-compatible source storage.

## Local development

1. Copy `.env.example` into environment variables appropriate to your machine.
2. Run infrastructure and backend: `docker compose up --build`.
3. In another terminal, run `npm install && npm run dev:web`.
4. Apply migrations in production with `cd apps/api && alembic upgrade head`.

The old `server.py`, `app.js`, and static assets are intentionally retained as the non-deployed prototype reference. They are not part of the Railway deployment.

## Railway

Follow [infrastructure/railway.md](infrastructure/railway.md). Use Railway PostgreSQL, Redis, and object storage; only publish the Next.js web service.
