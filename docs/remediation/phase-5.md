# Phase 5 — full-library repair tools

Implemented and reviewed locally, 2026-10-19. Production deployment must run the
additive `0016_taxonomy_aliases` migration first.

The admin library now retrieves every question with stable server pagination and
search by stable ID/source number/content, plus subject/chapter/topic, type,
readiness, issue, source and page filters. Unlinked legacy questions have their own
filter and addressable question route. Missing-classification filters query real
classification links; Unclassified is explicit. Summaries remain bounded at 25.

The new editor supplies option rows and answer controls for choice/assertion-reason
questions, numeric/integer inputs and tolerance, matrix rows/columns/mapping controls,
and a subjective model-answer field. Advanced JSON uses the same strict content
schema and validator; ordinary MCQ editing no longer requires JSON. Changing type
clears the incompatible draft answer. Original figure/source provenance cannot be
overwritten through content JSON.

Admin content writes require a current revision token and change/review reason,
lock and recheck the record, invalidate approval on draft saves, and audit the actor
and reason. Classification IDs participate in the revision token. Session-scoped
recovery copies preserve unsaved work on failure/navigation. Concurrent edits return
409 without replacing local input. Recovering onto a newer revision requires explicit
comparison/acceptance rather than automatically overwriting the other reviewer's work.
Older compatibility edit endpoints remain available; figure editor writes now send
If-Match, while the new admin content endpoint requires its revision precondition.

Source verification displays one original page with page navigation. Legacy missing
sources are explicit. Audited relinking verifies bank membership and page bounds,
retains previous links/spans, invalidates approval and flags existing figures for
reverification. Figure correction retains versioned assets/provenance. Private source
reads enforce author/bank ownership rather than trusting forged source metadata.

Leaf taxonomy aliases can be previewed and applied within the same parent. Stable
IDs, original codes and source nodes are retained; question links move to the canonical
node and the audit records affected IDs and pre-existing target links. The additive
nullable alias target has a tested schema downgrade. Parent nodes with children are
explicitly guarded: their consolidation needs a hierarchy migration to avoid silently
reparenting existing chapter/topic references. The normal importer resolves known
aliases, and bank revisions retain alias targets.

Review fixes included numeric JSON page filtering across SQLite/PostgreSQL semantics,
source ownership validation, preserving original taxonomy codes via a dedicated alias
column, stale figure-write checks, and guarding local edits while generation/source
operations refresh data.

Validation: ten new library/repair/source/migration tests pass; frontend navigation
and upload tests pass (6). The full API run before the final source-ownership test
had 283 passes and the same three unchanged-baseline failures from Phase 2. TypeScript
passes; production build is the final phase gate. Browser checks found an older orphan,
relinked/read its source, corrected numeric zero, assigned all three taxonomy levels,
approved it, then verified that a concurrent edit returned 409 while local value 1
remained intact. An ordinary MCQ's option/answer was edited without JSON. No page
overflow at the four audited widths. Evidence: `evidence/phase5-browser-results.json`
and `phase5-conflict-preserves-edit.png`.

No production reconciliation, alias merge or source relink was performed. Apply the
schema migration in staging, qualify real stored-source behavior, inspect plans at
production scale and test concurrent PostgreSQL sessions before rollout. Reversing
an applied alias operation uses its audit link history before removing the schema
column; schema downgrade alone is not a classification-data rollback.

Review outcome: local repair and conflict checks pass. Parent hierarchy consolidation
is an explicit guarded product boundary; real-source/migration/performance rollout
gates remain. Proceed to Phase 6 after the build passes.
