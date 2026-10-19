# Phase 2 — readiness and publication checks

Implemented and reviewed locally on 2026-10-19. This is code delivery, not a
production reconciliation or rollout claim.

## Contract: readiness-v1

Readiness is computed from original stored question type/content. An optionless
MCQ remains an invalid MCQ in its repair payload; it is not silently relabelled.
`approved` / `approved_at` continues to record review independently.

| Field | Meaning |
| --- | --- |
| `content_valid` | No blocking content issues under the named validator version. |
| `reviewed` | Historical/current approval exists. |
| `publishable` | Content is valid and approval exists. |
| `blocking` | Structured code, field path and repair message. |
| `advisory` | Structured nonblocking reasons. |

Publication also checks document lifecycle: queued/running extraction and active
generation block release. An acknowledged pause or stopped partial import can
release its eligible output explicitly. Preview partitions questions into eligible,
blocked and already published; these three counts sum to the scoped total. A
historically released but currently invalid question counts as blocked. Multiple
blocking reasons may apply to one question. Document publication timestamps remain
historical records; no existing assessment snapshot is rewritten.

## Validation matrix

| Content | Blocking rule | Advisory / explicit policy |
| --- | --- | --- |
| Stem | Nonblank meaningful text; reject known malformed `$$$` delimiters. | Suspect solution-fragment wording routes source inspection; never rewritten. |
| Single choice / assertion-reason | At least two meaningful options; valid integer index; no booleans/out-of-range keys or extra answer fields. | Assertion-reason uses the platform's existing choice grading format. |
| Multiple choice | Nonempty unique set of valid integer indices. | Order does not affect grading. |
| Choice options | No blank/letter-only/Not provided/Correct placeholders or duplicated text. | A stored, correctly owned option figure can supply an otherwise blank option. |
| Numerical / integer | Finite numeric value; integer questions require an integral value; finite nonnegative tolerance. | Existing default grading tolerances remain unchanged. |
| Matrix match | Nonempty unique row/column contracts; all rows mapped to valid columns with unique choices. | No invented rows or columns. |
| Subjective | Model solution required. | Manual evaluation remains the existing product policy. |
| Figures | Required source/option figures must have stored keys and valid ownership; unresolved figure findings block approval. | Diagram-only option questions do not require an extra question figure. Storage availability/semantic correctness still requires source review. |
| Objective solutions | Valid objective answer remains sufficient. | Missing/short/malformed solutions are advisory. |
| Taxonomy | No taxonomy publication block in v1. | Missing/unmapped subject, chapter and topic are labelled advisory, not classified. |
| Generation | Generated output remains unreviewed; bulk approval revalidates content. | Failure preserves trusted answers. Malformed authoritative keys require explicit repair. Concurrent human edits win over provider output. |

Import completion, draft/answer saves, approval, generated result application,
bulk generation approval, publication and new test snapshots use the same validator.
Incomplete drafts can be saved; saving invalidates their approval. Live decisions
always recompute validation and do not trust cached `interaction.readiness` metadata.

## API and UI

- Question responses expose top-level `readiness`; original `approved` stays separate.
- Import/bank responses expose full-scope readiness totals. Content-blocked and
  awaiting-review counts overlap; needs-attention is their union.
- `GET /admin/question-banks/{bank_id}/publication-preview` and the corresponding
  `/imports/{job_id}/publication-preview` return exact counts, reason counts and
  affected stable IDs without mutation.
- The existing publication POST routes revalidate locked bank/job/question rows,
  release eligible content, report partial results and return idempotent retries.
- `GET /admin/question-banks/{bank_id}/readiness-report` scans the full bank,
  including rows older than the 100-item admin payload; returns stable IDs/reasons
  and `mutated: false`. Admin permissions apply to reports and previews.
- New teacher selection/counts exclude invalid legacy-approved rows; final snapshot
  creation validates again. Publishing a saved assessment draft validates that exact
  draft rather than replacing it with newer library content.
- The admin preview displays counts/reasons before submission; the server response
  reports actual published/blocked/already-published counts. Badges, filters, editor
  explanations and loaded repair queues use the server result. The loaded queue is
  explicitly labelled as a slice; full-library retrieval is Phase 5.

## Review and verification

Manual review checked content-rule coverage, bypasses through alternate approval
routes, authorization, stale previews, publication retries, count consistency,
historical compatibility and the Phase 2 plan. Fixed findings during review:

1. Preserved diagram-only options instead of requiring a spurious extra figure.
2. Preserved figure requirement metadata in normalized option API payloads.
3. Prevented Ignore warnings from bypassing required figures or invalid keys/options.
4. Rechecked generated results and preserved concurrent edits and authoritative keys.
5. Removed the implicit optionless-MCQ-to-subjective conversion from repair payloads.
6. Kept partial stopped/paused imports eligible while active workers block release.

Validation: 47 new readiness/publication regression cases pass. Full API suite:
261 passed, three failures. The same three failures reproduce from unchanged HEAD
in an isolated baseline checkout (SQLite timestamp/detached-instance expectations
and local OCR behavior):

- `test_integrity_report_after_deadline_flags_without_replacing_expiry_submission`
- `test_expiry_worker_finalizes_attempt_without_a_browser`
- `test_real_scan_ocr_grounds_question_scopes_without_overlap`

TypeScript and production build pass (existing multiple-lockfile root warning).
Local Chrome exercised actual React + API fixtures: 1 eligible / 1 blocked preview,
partial publication and a legacy-approved null-answer repair warning; no page
horizontal overflow at 390/768/1024/1280. See `evidence/phase2-browser-results.json`
and the two editor screenshots. Local-only dependency overrides were outside the
repository; no authentication override is present in application code.

A synthetic 21,027-row in-memory SQLite scan found 2,103 blocked rows and 18,924
eligible rows in both readiness and selection. Timing was approximately one second
per scan; see `evidence/phase2-synthetic-scale.json`. This is not a production
performance qualification. Read-through legacy filtering scans candidates per
request; a persisted indexed validator result can follow after a measured migration.

## Rollout, compatibility and remaining data work

No relational migration is required. New metadata fits in existing JSON fields.
No production scan, data rewrite, withdrawal, deletion or assessment alteration was
performed. Historical approval flags and TestVersion/Attempt references are intact.

Deploy to staging, run the nonmutating report and representative source fixtures,
review policy/sample discrepancies with content operations, and qualify PostgreSQL
concurrent edits/worker behavior before production release. The remaining Phase 2.5
batch reconciliation requires a separate reversible migration with old/new values
and validator version; it is not represented as completed by this code delivery.

Keep backend readiness enforcement when rolling UI changes back. A data migration
and its reversal must not be inferred from rolling this code back. Production-scale
latency, semantic source accuracy and accessibility qualification remain release gates.

Review outcome: no unresolved blocking defect in the locally exercised Phase 2
implementation. The three baseline failures and staging/data gates remain explicit.
Proceed to Phase 3 navigation and acknowledged job state.
