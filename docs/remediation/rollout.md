# Release and data gates

Code completion and local qualification do not constitute a staging or production
release. No production scan, data reconciliation, deployment or live account action
was performed during this implementation.

## Reproduce local verification

Install `apps/api/requirements-dev.txt` in Python 3.12 and the repository npm dependencies.
OCR tests need Tesseract and its English data. The runner discovers `TESSDATA_PREFIX`
when possible. Run sequentially from the repository root:

```
/path/to/python scripts/verify-remediation.py
TESTNOW_TEST_PYTHON=/path/to/python node scripts/verify-remediation-browser.mjs
```

The second command requires Node with native WebSocket and Google Chrome (override
`TESTNOW_TEST_CHROME` if needed). Do not run Next dev and build simultaneously against
the same checkout. It launches disposable API/storage/queue fixtures and Chrome on
loopback ports, then stops their process groups. It does not use real authentication,
S3, Celery or a model provider. `remediation_ui_fixture.py` refuses startup without
explicit fixture opt-in and a SQLite database. Browser output is under `evidence/phase7`.

## Staging sequence

1. Back up the database and verify restore. Apply additive migrations
   `0016_taxonomy_aliases` then `0017_review_pagination_indexes` using the project's
   normal Alembic deployment procedure. Qualify PostgreSQL index creation locks and
   query plans against representative data; SQLite plans are only local evidence.
2. Deploy the compatible API before the UI. The original import detail response keeps
   full questions by default; the new UI explicitly requests bounded metadata and
   paginated summaries. Legacy edit routes remain compatible. New admin content
   writes require revision/reason; new figure writes submit If-Match. The backend
   readiness check is authoritative regardless of UI versions.
3. Run authenticated upload → partial failure/retry → source verification → repair →
   approve → preview → publish with real PDFs/DOCX, object storage, model provider
   and Celery. Exercise worker restart, pause/resume/cancel races and heartbeat
   recovery. Validate independent PostgreSQL sessions for concurrent edits,
   publication, revision creation and destructive reference checks.
4. Export `GET /admin/question-banks/{bank_id}/readiness-report` for every actual bank.
   Preserve stable IDs, validator version and reasons. Confirm the actual inventory
   against the audited 21,027 records; the checked-in 21,027-row trace is synthetic.
   Review representative failures/policy advisories with content operations. Do not
   change historical approval/publication flags merely to match a new count.
5. If data reconciliation is approved, execute a separately reviewed batched procedure
   recording old/new values, actor, reason and validator version, with an inverse
   operation and before/after aggregate checks. This implementation supplies the
   nonmutating report and future-selection guards; it does not include or execute
   a production reconciliation script. Existing published-version withdrawal or
   replacement requires its own explicit policy.
6. Qualify native 200% browser zoom, screen reader use, contrast and all core keyboard
   flows on the agreed reference device/network. The 640-CSS-pixel/2x scale browser
   check is equivalent reflow evidence, not native zoom or an accessibility audit.
   No WCAG-conformance or 100–200 ms production response claim is made.
7. Release the UI to a controlled audience, inspect errors/readiness discrepancies,
   then expand after content operations complete representative end-to-end tasks.
   No feature-flag service was added; preserve a known compatible UI artifact for
   rollback. Full-bank validation currently scans candidates: qualify latency and
   memory at actual/larger scale before expanding rollout.

## Rollback

Stop rollout for wrong-bank actions, newly eligible invalid content, data loss,
broken assessment references, unrecoverable worker states or severe accessibility
regressions. Restore the prior UI while retaining backend validation and compatible
responses. Reverting all API code would also remove integrity enforcement and is not
an acceptable UI rollback. Preserve a validated API artifact or forward-fix it.

`0017` can be downgraded by dropping its two indexes. For `0016`, reverse any applied
alias operation from its audited affected links and pre-existing target links before
removing the nullable alias column; dropping schema alone does not reverse data.
Take a backup and verify reversal in staging. Parent taxonomy nodes with children
remain guarded pending a separately designed hierarchy migration.

Assessment snapshots are immutable. Reference checks block source deletion/re-extraction
when snapshots or other questions reuse content/assets. Soft-removed banks can be
restored (unknown legacy visibility defaults private). Permanently deleted unreferenced
uploads require their original files; do not promise undelete. Keep all data-operation
ledgers and object-store backups separate from code rollback.
