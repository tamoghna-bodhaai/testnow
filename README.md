# TestNow Production Platform

TestNow is now a deployable monorepo: a Next.js assessment interface, FastAPI API, PostgreSQL data model, Redis/Celery worker, OpenRouter structured document extraction, and S3-compatible source storage.

## Local development

1. Copy `.env.example` into environment variables appropriate to your machine.
2. Run infrastructure and backend: `docker compose up --build`.
3. In another terminal, run `npm install && npm run dev:web`.
4. Apply migrations in production with `cd apps/api && alembic upgrade head`.

## Local demo accounts

Docker Compose enables `SEED_DEMO_DATA` for local use. It creates the **Demo Physics** class and these accounts, all with access code `12345`:

- `utils@bodhaai.tech` (super administrator)
- `teacher@testnow.local`
- `student1@testnow.local`
- `student2@testnow.local`

The seed flag defaults to `false` and must remain disabled in shared and production environments.

The old `server.py`, `app.js`, and static assets are intentionally retained as the non-deployed prototype reference. They are not part of the Railway deployment.

## Railway

Follow [infrastructure/railway.md](infrastructure/railway.md). Use Railway PostgreSQL, Redis, and object storage; only publish the Next.js web service.

Production requires a sealed `SUPER_ADMIN_ACCESS_CODE` containing exactly five digits. The first API startup creates `utils@bodhaai.tech` as the super administrator. It manages teacher codes and statuses; teachers manage student codes and class/batch rosters.

The production access-code migration intentionally starts teacher and student data from a clean slate. It deletes legacy teacher/student accounts and their assessment history, so it must not be run where those records need to be retained.
