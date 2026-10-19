# Phase 4 — focused ingestion tasks and bounded review

Implemented and reviewed locally, 2026-10-19.

Monitor imports is now the default existing-bank task. Add sources and Review
library open deliberately. Bank identity remains above the task controls; source
review identifies its bank. The monitor supports filename, subject and lifecycle
filters; attention counters include question defects, extraction errors and generation
failures. Sorting captures an explicit order and provides Refresh ordering so job
updates do not reshuffle rows under the pointer. Filename/subject/state context is
stored in the URL.

Source review uses server-paginated 25-item summaries and one selected detail. The
source metadata endpoint no longer delivers every full question. Summary pages
exclude options and worked solutions. Filters precede bounded diagnostics; previous/
next controls and queue pagination stay available. Question details load on demand,
including direct stable-ID links. The queue scrolls within a bounded pane; phones
use a single-column flow. No virtualization was added without a demonstrated need.

Upload configuration comes from the server. Preflight shows names, required versus
optional roles, formats, size limits, pairing, ambiguous matches and possible file
metadata duplicates. Server SHA matching remains authoritative. Submission returns
per-file queued/already-imported/failed outcomes. Retry retains successful imports
and does not restart them. Storage/validation errors are isolated per file; ambiguous
answer pairs do not start extraction. Queue-dispatch failure leaves a recoverable
failed source with an explicit outcome. Concurrent source deduplication is serialized
at the bank write boundary.

Review checked bounded payload/DOM behavior, filter semantics, ordering stability,
partial upload recovery and source membership. Fixed findings: header height inherited
from global styles clipped long source headings; upload controls could receive files
before configuration arrived; changing review pages now disables stale queue actions.

Validation: 5 frontend navigation/preflight tests and 5 new ingestion API regressions
pass. Full API suite has 274 passes plus the same three unchanged-baseline failures
recorded in Phase 2. TypeScript passes; production build is part of this phase's gate.
Actual Chrome + API fixtures verified a 179-question source with 25 mounted summaries,
0 initial full editors, 1 selected editor, page-2 navigation, source search and
invalid-file blocking. No horizontal source overflow at 390/768/1024/1280. See
`evidence/phase4-browser-results.json` and `phase4-bounded-review-1280.png`.

Review outcome: local workflow checks pass. Real storage/provider uploads, partial
network interruption, worker integration, 200% zoom and production performance stay
in the Phase 7 staging gates. Proceed to Phase 5 after the production build passes.
