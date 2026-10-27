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

## Text-first JEE seed bank

Use the OpenRouter seeder for the supplied clean JEE Advanced worksheets. It
stores a Markdown/LaTex question record, type, chapter, verified answer and
worked solution; it saves an image only when the model identifies a bounded
diagram. It never uses a full-question crop as the question itself.

First run its no-write estimate. The final command stages the replacement,
checks that no existing global-bank question is referenced by a saved test,
and swaps it only after extraction and answer/solution verification succeed.
Questions below the confidence gate remain unpublished for review.

After bringing up the API dependencies and applying migrations, run this inside
the API container (or an environment with the same database and object-store
settings):

```bash
python -m app.seed_jee_openrouter --owner-email utils@bodhaai.tech --replace --dry-run \
  /imports/CHEMISTRY_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf \
  /imports/MATHS_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf \
  /imports/PHYSICS_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf

python -m app.seed_jee_openrouter --owner-email utils@bodhaai.tech --replace \
  /imports/CHEMISTRY_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf \
  /imports/MATHS_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf \
  /imports/PHYSICS_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS_clean.pdf
```

The PDFs are intentionally not committed to the repository. Mount or copy them
to `/imports` in the API environment before running the command.

## Physics Bank 2

The existing global records are retained as **Question Bank 1**. To create the
separate generic-JEE **Physics Bank 2**, copy only the 23 supplied PDF files
(not their DOCX companions) into `/imports`, then run this inside the API
environment after migrations:

```bash
python -m app.import_physics_bank2 --owner-email utils@bodhaai.tech \
  /imports/*.pdf
```

The import uses the answer keys embedded in each PDF and makes questions with a
parsed answer immediately selectable. Worked solutions are retained when found,
but are not required for publication. The command is idempotent and never
modifies Question Bank 1. Use `--dry-run` first to estimate the extraction
work without changing the database or object storage.

## Question Bank 3 DOCX corpus

Question Bank 3 imports the physics, chemistry, and maths DOCX corpus as
structured Markdown/LaTeX questions. The original DOCX files are retained for
audit; LibreOffice creates temporary PDFs only to preserve page geometry while
extracting bounded diagrams. It never stores a whole-question image in place of
question text. Chemistry question files are paired with their corresponding
answer-and-solution files; an exact duplicate solution file is skipped.

Mount the folder containing the `physics`, `chemistry`, and `maths`
subdirectories at `/imports/docx-conversion`, then run inside the API image:

```bash
python -m app.import_qbank3_docx --owner-email utils@bodhaai.tech --dry-run \
  /imports/docx-conversion

python -m app.import_qbank3_docx --owner-email utils@bodhaai.tech \
  /imports/docx-conversion
```

The importer is safe to resume. It publishes validated records into the global
**Question Bank 3** (`qbank-3`) immediately, grouped by subject, chapter, topic,
and existing question type. Chemistry Chapter 4 remains in the bank as
unscored subjective material because its matching answer-and-solution source
was not supplied.

## Railway

Follow [infrastructure/railway.md](infrastructure/railway.md). Use Railway PostgreSQL, Redis, and object storage; only publish the Next.js web service.

For workers that sleep between jobs, use the opt-in [scale-to-zero deployment](infrastructure/railway-scale-to-zero.md), which includes transactional dispatch, fenced recovery, and independent assessment expiry.

Production requires a sealed `SUPER_ADMIN_ACCESS_CODE` containing exactly five digits. The first API startup creates `utils@bodhaai.tech` as the super administrator. It manages teacher codes and statuses; teachers manage student codes and class/batch rosters.

The production access-code migration intentionally starts teacher and student data from a clean slate. It deletes legacy teacher/student accounts and their assessment history, so it must not be run where those records need to be retained.

# Video explainers

Teachers can generate and review a Manim video for a question in a published assessment from its **Explainers** tab. The dedicated `explainer-worker` consumes the `explainers` queue; it needs the same database, Redis, object store, and OpenRouter access as the API. New drafts use a black mathematical canvas, a fixed graph and annotation layout, equation-driven curves, and Mistral's Himanshu Indian voice by default. Its speech model and voice remain configurable with `EXPLAINER_TTS_MODEL` and `EXPLAINER_TTS_VOICE`. A teacher must publish a completed draft before it appears beside the worked solution in a student's released result.

The tab reports the current stage (waiting, formulating, narrating, building, or finalizing) and can cancel an active draft. Cancellation is recorded immediately; the worker stops at its next checkpoint, including during Manim and FFmpeg commands. An in-flight model or speech request may finish before the worker stops, but a cancelled draft cannot become ready or be published.
