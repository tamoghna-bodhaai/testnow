# TestNow Production Platform

TestNow is now a deployable monorepo: a Next.js assessment interface, FastAPI API, PostgreSQL data model, Redis/Celery worker, OpenRouter structured document extraction, and S3-compatible source storage.

## Local development

1. Copy `.env.example` into environment variables appropriate to your machine.
2. Run infrastructure and backend: `docker compose up --build`.
3. In another terminal, run `npm install && npm run dev:web`.
4. Apply migrations in production with `cd apps/api && alembic upgrade head`.

## Local demo accounts

Docker Compose enables `SEED_DEMO_DATA` for local use. It creates the **Demo Physics** class and these accounts, all with access code `12345`:

- `admin@testnow.local` (local super administrator)
- `teacher@testnow.local`
- `student1@testnow.local`
- `student2@testnow.local`

The seed flag defaults to `false` and is rejected by the API in production. The real super-admin address is never seeded with a known demo code.

The old `server.py`, `app.js`, and static assets are intentionally retained as the non-deployed prototype reference. They are not part of the Railway deployment.

## Native JEE seed bank

The three supplied clean JEE Advanced worksheets can be seeded without a model or
network extraction. The native importer uses PDF text for question/type/chapter
segmentation and saves a rendered source crop with every question, retaining
graphs and figures. It creates global **review** records; answer keys must be
reviewed before a question is published to all teachers.

After bringing up the API dependencies and applying migrations, run this inside
the API container (or an environment with the same database and object-store
settings):

```bash
python -m app.seed_jee --owner-email utils@bodhaai.tech \
  /imports/CHEMISTRY_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf \
  /imports/MATHS_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf \
  /imports/PHYSICS_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf
```

The PDFs are intentionally not committed to the repository. Mount or copy them
to `/imports` in the API environment before running the command.

## Railway

Follow [infrastructure/railway.md](infrastructure/railway.md). Use Railway PostgreSQL, Redis, and object storage; only publish the Next.js web service.

Production requires a sealed `SUPER_ADMIN_ACCESS_CODE` containing exactly five digits. The first API startup creates `utils@bodhaai.tech` as the super administrator. It manages teacher codes and statuses; teachers manage student codes and class/batch rosters.

The production access-code migration intentionally starts teacher and student data from a clean slate. It deletes legacy teacher/student accounts and their assessment history, so it must not be run where those records need to be retained.
