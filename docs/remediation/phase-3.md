# Phase 3 — navigation and ingestion state

Implemented and reviewed locally, 2026-10-19.

- Added addressable bank/document/stable-question routes, preserving legacy hashes.
- Bank/document requests use identity guards and abort superseded requests. Pending
  and failed new selections remove the previous bank's mutation controls. Polling
  checks current bank/document identity and displays an explicit stale-data warning.
- URL-backed document filters, history restoration, per-document scroll persistence,
  pending feedback and unsaved-edit / unload guards now cover the review workspace.
- Publication, extraction lifecycle and generation status are independent fields.
  `lifecycle.state` reports queued, running, pause_requested, paused,
  cancellation_requested, cancelled, completed or failed without an enum migration.
- Worker checkpoints record activity and pause/cancel acknowledgement. A queued
  control can be acknowledged without pretending a worker heartbeat occurred.
  Resume waits for acknowledgement; repeats do not enqueue duplicate work. Retry
  clears durable stop flags. Fresh worker controls prioritize cancel/delete over
  an earlier pause observation. Worker startup locks its queued-state transition.
- Diagnostics are server-paginated by extraction/generation stage. New generation
  runs retain full logs while standard responses stay bounded. Legacy capped logs
  disclose the number of older messages that were not retained.

Review covered selection races, stale callbacks, route restoration, unsaved edits,
control transitions, wrong-bank endpoints and bounded diagnostics. Review fixes:
initial deep links had to start document requests after bank selection; queued
worker startup/control acknowledgement required fresh metadata and row locks.

Validation: eight lifecycle/diagnostic tests, three navigation utility tests,
TypeScript and production build pass. Related readiness/figure pipeline regression
checks passed (92 cases before the final acknowledgement-race case was added).
Chrome tested delayed A→B→C responses, a failing D request, absence of old mutation
controls while pending/failed, a direct legacy-null question URL with attention
filter, cancelling an unsaved-edit navigation, accepting discard, and restoring the
same question/filter through browser Back. See `evidence/phase3-browser-results.json`.

No production operations occurred. Real Celery restart/interruption, PostgreSQL
concurrent workers, provider timing and heartbeat thresholds remain staging gates;
a missing heartbeat is not asserted to prove worker death or trigger destructive
recovery. Phase 7 must qualify these operational cases.

Review outcome: no open blocking issue in the exercised local contract. Proceed
to Phase 4; retain the operational gates above for release qualification.
