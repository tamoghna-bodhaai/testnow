# TestNow — phased super admin remediation plan

**Basis:** [Super admin audit: 25 findings](SUPER_ADMIN_UI_AUDIT.md), including the follow-up on ingestion responsiveness and usability.  
**Status:** Implementation plan; no application changes or production data changes performed.  
**Planning assumption:** Existing platform and ingestion pipeline are retained. Exact components, endpoints, migrations and effort must be confirmed against the code in Phase 0.

## Intended outcome

An admin must be able to identify the correct bank, understand ingestion progress, find any question, verify it against its source, repair it, and publish only eligible content—without misleading statuses, disruptive layout changes or excessive scrolling.

Use two coordinated delivery tracks: **content integrity and reliable state**, and **responsive ingestion workflow**. Ship the small, demonstrated layout fixes early while the deeper validation work proceeds. Avoid rebuilding screens against ambiguous status definitions.

## Delivery sequence

| Phase | Deliverable | Depends on | Primary ownership |
|---|---|---|---|
| 0 | Reproduction fixtures, baseline, domain/API contract | None | Engineering + content operations + QA |
| 1 | Immediate responsive and interaction stabilization | Reproduction fixtures from 0 | Frontend |
| 2 | Consistent readiness, publication checks and existing-data reconciliation | Domain contract from 0 | Backend + content operations |
| 3 | Reliable routes, bank selection and ingestion job lifecycle | 0; integrate readiness from 2 | Full stack |
| 4 | Dedicated ingestion workspace and bounded review queue | 1–3 | Frontend + backend |
| 5 | Source-based repair tools and full-library management | 2–4 | Full stack + content operations |
| 6 | Administrative support workflows and accessibility completion | 3; extend patterns from 4–5 | Full stack + product + QA |
| 7 | Regression, performance qualification and staged rollout | All applicable phases | QA + engineering + content operations |

Phases 1 and 2 can progress concurrently after their Phase 0 prerequisites. Phase 7 validation starts with the first delivery and ends with the release gate. Every phase should land as reviewable changes with its own validation; do not defer all verification to the end.

## Phase 0 — establish a reproducible foundation

**Work packages**

- **0.1 Trace the existing implementation.** Locate bank selection/request state, question validation, approval/publication handlers, aggregate counts, source storage, job controls, review rendering and responsive styles. Reproduce the intermittent bank-switch failure before proposing its technical cause.
- **0.2 Build disposable staging fixtures.** Include malformed MCQs from Properties of Matter, reviewed Q51 with a null answer, OP Tandon's missing figure and generation conflict, unclassified legacy questions, long filenames, 50+ failures and pause/cancellation states. Use stable record IDs; source question numbers repeat across pages.
- **0.3 Establish the state contract.** Keep extraction job, generation attempt, human review, content completeness, taxonomy and publication as distinct concepts. Define required fields by question type. Define which missing solutions or taxonomy fields block publication and which are advisory. A generation failure must never silently change a trusted answer key.
- **0.4 Record the baseline.** Capture the audited viewport cases and browser traces for opening a document, changing filters, opening an editor and processing live job updates in staging. Measure mounted cards, request latency, long tasks, memory and visible feedback—not just screenshot appearance.
- **0.5 Define compatibility.** Identify how published question versions are referenced by tests and attempts. Plan additive schema changes and a dry-run validator before changing historical readiness flags.

**Artifacts:** State-transition table, validation-rule matrix, API/count contract, fixture set, baseline traces and migration outline.

**Exit gate:** Every P1 finding is reproducible or explicitly documented as intermittent; content operations and engineering use the same definition of publishable content. Unknown causes remain marked as unknown.

## Phase 1 — remove the immediate ingestion layout failures

**Findings:** SA-08, SA-09, SA-18, SA-20, SA-21, SA-22, SA-25.

**Work packages**

- **1.1 Repair document-row sizing.** Put identity/status and actions in separate layout areas. Switch to stacked rows before metadata becomes unreadable; base the transition on actual available space. Prevent action groups from shrinking status into single-character columns. Preserve full progress denominators.
- **1.2 Bound diagnostics.** Replace 50-message inline expansion with a compact summary and bounded detail panel. Keep Review in the document header. Top-align actions and preserve the scroll anchor when opening/closing details.
- **1.3 Simplify action hierarchy.** One state-appropriate primary action per row; recovery actions in a secondary menu; destructive actions in a clearly separated group. Retain existing behavior while making scope and labels explicit. Publication preview is completed in Phase 2.
- **1.4 Reflow upload and review headers.** Stack required/optional upload panels on phones, show selected-file names, place the upload CTA below helper text, and wrap long filenames/header actions. Fix the five-metric layout and dialog overflow.
- **1.5 Fix known accessible labels immediately.** Associate teacher form labels and inputs, connect field errors, preserve visible focus, and provide comfortable touch targets using a 44 px target where practical.

**Acceptance gate**

- At 390, 768, 1024 and 1280 px, plus widths around each transition, status wraps normally and all actions are readable.
- No page-level horizontal overflow from upload controls or review headers; wide mathematical/source content may scroll only within its designated viewer.
- Opening diagnostics does not turn a 249 px row into a 3,254 px obstacle or move the primary action several screens away.
- Teacher inputs expose their visible names to assistive technology.
- Capture before/after evidence for the exact audited cases and verify at 200% zoom.

**Release:** A small UI stabilization release can ship independently after validation. This phase does not resolve unreliable readiness data.

## Phase 2 — make readiness and publication trustworthy

**Findings:** SA-01, SA-02, SA-03, SA-05, SA-13.

**Work packages**

- **2.1 Implement shared validation.** Produce structured, versioned results with blocking/advisory reasons. Validate stems, meaningful options, valid answer mappings, numeric answers, type-specific structures and required figures. Use heuristics for suspect solution fragments to route human review; do not automatically rewrite content based on heuristics.
- **2.2 Apply the rules at every boundary.** Import completion, draft save, approval, generation result application and publication must use consistent rules. The backend enforces publishability; frontend badges explain it. Human-reviewed and publishable remain separate fields/concepts.
- **2.3 Rebuild aggregate semantics.** Count over the full bank, not the loaded 100. Distinguish documents needing attention from questions needing attention. Allow overlapping statuses only when clearly labelled; make empty-state copy accurate for the active scope.
- **2.4 Add publication preview.** Show exact eligible, blocked and already-published counts and blocking reasons before publication. Revalidate on submission to handle concurrent edits. Make retries idempotent and report partial failures explicitly.
- **2.5 Reconcile existing content.** Run a nonmutating scan across all 21,027 questions and export affected stable IDs/reasons. Review samples with content operations, then apply a reversible migration with old/new values and validator version recorded. Invalid content should be excluded from future publication and new test selection under the agreed policy. Plan withdrawal/version replacement separately for existing published material.

**Acceptance gate**

- Q51's null answer places it in a repair queue despite its historic Reviewed flag.
- Placeholder-option MCQs and required missing figures cannot be newly approved/published as ready.
- List badges, editor warnings, counters, filters and server decisions agree for the same records.
- “Unclassified” is never described as classified; optional policy exemptions are explicit.
- Existing tests/attempts retain their intended version references; migrations have a verified reversal procedure.

**Release:** Gate new publication on this phase. Do not silently rewrite answer keys, delete records or alter historical attempts during cleanup.

## Phase 3 — stabilize navigation and ingestion job behavior

**Findings:** SA-04, SA-07, SA-15, SA-16, SA-17.

**Work packages**

- **3.1 Correct bank selection.** Key requests and mutations to the bank ID, reject stale responses, and show the requested bank's pending/error state. Do not leave the previous bank's mutation controls usable while a new selection is unresolved. Verify the mutation target on the server.
- **3.2 Introduce addressable context.** Add routes for bank/document/question identity and URL-backed filters. Restore queue, pagination and scroll context on return. Handle unauthorized/missing records and unsaved edits explicitly.
- **3.3 Formalize job transitions.** Model queued, running, pause requested, paused, cancellation requested, cancelled, completed and failed according to actual worker behavior. Keep publication status separate. Confirm pause/resume/cancel acknowledgement, idempotent requests and recovery after worker interruption in staging.
- **3.4 Improve feedback.** Separate loading, empty, filtered-empty and failed states. Display last activity/heartbeat and last successful update. Keep current data visible only with an explicit stale indicator if refresh fails.
- **3.5 Clarify diagnostics.** Label stage-specific denominators; disclose “showing 50 of 58” when logs are capped and provide the rest. Link issues with stable question IDs/source/page.

**Acceptance gate:** Rapid A→B→C selection with out-of-order, slow and failing requests never mixes bank identity/content/actions. Refresh/back/deep links restore the intended context. Job labels and available controls match the worker's acknowledged state; polling does not reorder rows under the pointer.

## Phase 4 — rebuild ingestion around focused tasks

**Findings:** SA-10, SA-23, SA-24; completes SA-18, SA-21, SA-25.

**Work packages**

- **4.1 Separate Add sources, Monitor imports and Review content.** Make Monitor the default for an existing bank. Keep bank identity and navigation visible; open Add sources deliberately rather than permanently placing its form above operational work.
- **4.2 Build the monitor.** Add filename search, subject/status filters, last-activity/issue-count sorting and actionable summary counters. Show extraction, review and publication in distinct fields. Avoid automatic reordering during updates; let the user refresh ordering deliberately.
- **4.3 Provide a job detail view.** Include stage progress, issue groups, attempts, timestamps and recovery actions. Use bounded logs with explicit pagination; keep dangerous operations scoped and clearly labelled.
- **4.4 Bound the review queue.** Start with server-paginated summaries and one selected question detail. Render math/full content on demand. Introduce virtualization only where traces justify it, preserving keyboard navigation. Do not mount all 179 full cards and hundreds of buttons on initial entry.
- **4.5 Keep navigation stable.** Place filters before diagnostics; keep next/previous and queue location accessible. Group run issues into counts and links to affected questions. Use a single-column list/detail flow on phones and a queue/detail layout when space permits.
- **4.6 Make upload reviewable.** Show supported formats/limits from actual server configuration, required/optional roles, file pairing results, duplicates and per-file validation before starting. Show per-file outcomes after submission without restarting successful files when retrying failures.

**Acceptance gate:** An admin finds a named failed source without scanning 17 rows, reaches a conflicting question directly from its issue, returns to the same filtered queue, and reviews a large import without an approximately 98,000 px content surface. Partial upload/job failures remain recoverable and understandable.

## Phase 5 — enable accurate repair across the entire library

**Findings:** SA-06, SA-11, SA-12, SA-19; completes remediation paths for SA-01, SA-02, SA-13.

**Work packages**

- **5.1 Remove the latest-100 dead end.** Add server-side question retrieval, stable pagination and search by stable ID, source/page and content. Filter by subject/chapter/topic/type, readiness and issue. Include legacy questions without linked sources.
- **5.2 Add type-specific editors.** Option rows and answer selectors for choice questions; numerical/integer controls with the platform's grading/tolerance rules; structured matrix/assertion-reason controls; subjective answer/rubric policy. Preserve advanced JSON access with schema validation.
- **5.3 Add source verification.** Display the original page with the question where supported. Preserve page/region/figure references; enable figure reassignment or correction with provenance. Explicitly label missing/unrecoverable legacy sources and provide a relinking workflow where evidence exists.
- **5.4 Repair taxonomy.** Add subject/chapter/topic selectors, canonical aliases and a missing-classification queue. Preview impact before merging aliases; preserve identifiers and references.
- **5.5 Protect edits.** Detect stale/concurrent versions, show server errors beside fields, keep unsaved work recoverable, and record who changed content and why. Material edits invalidate or recompute prior approval under Phase 2 policy.
- **5.6 Improve issue language.** Map raw codes to plain-language reasons and affected option letters, with a recommended repair action. Keep raw diagnostics available to engineering.

**Acceptance gate:** A reviewer can find an older orphan-source question, fix a normal MCQ without JSON, compare a figure with its source, reclassify it, and save/approve it only after all required checks pass. Generation conflicts require an explicit resolution rather than overwriting the trusted key.

## Phase 6 — complete platform management and accessibility

**Findings:** SA-14; cross-cutting completion of SA-08 and SA-09.

**Work packages**

- **6.1 Define the super admin support boundary.** Implement teacher detail with links to batches, students, tests and attempts the role is authorized to inspect. Add profile editing, suspension/reactivation and an access-recovery process consistent with existing authentication. Verify which capabilities already exist before adding duplicates.
- **6.2 Make consequential actions understandable.** Show exact affected teacher/bank/document scope, consequences and recovery options. Apply the existing product's confirmation policy to deletion/re-extraction/publication. Log administrative changes without exposing credentials.
- **6.3 Complete accessibility across new and existing screens.** Verify labels, field errors, focus entry/restoration, keyboard menus, pending-state announcements, contrast, text zoom and touch controls. Prefer routed detail pages for extended work; if dialogs remain, test focus containment and background interaction explicitly.

**Acceptance gate:** An admin can investigate a teacher issue and recover access through an authorized, audited workflow. All core tasks can be completed by keyboard with understandable names, focus and feedback. No WCAG-conformance claim is made until the relevant audit is complete.

## Phase 7 — qualify and release incrementally

**Verification suites**

| Suite | Required cases |
|---|---|
| Content integrity | All supported question types; null keys; placeholder options; missing figures; incomplete stems; generated/source answer conflict; legacy Reviewed records |
| State/navigation | Rapid switching; stale responses; timeout/retry; missing/unauthorized IDs; deep links; back/forward; unsaved edits |
| Job lifecycle | Real staging upload; partial failure; retry; pause/resume/cancel; worker restart; repeated requests; completion with errors |
| Responsive | 390/768/1024/1280 px plus boundary sweeps; long filenames; all actions; expanded diagnostics; 200% zoom; math/source overflow |
| Scale/performance | Current bank size and larger seeded imports; query plans; bounded DOM; repeated open/close/filter cycles; no unbounded memory growth |
| Administration | Create/edit teacher; suspend/reactivate; recovery; permissions; audit events; destructive recovery behavior |

**Proposed responsiveness targets:** Immediate local feedback within 100–200 ms; explicit pending feedback for slower work. Agree a reference device/network in Phase 0, then measure interaction latency and rendering traces on that setup. Treat these as targets to validate, not performance already demonstrated by the current app.

**Rollout steps**

1. Deploy additive contracts/migrations in staging, run fixtures and inspect migration dry-run output.
2. Release Phase 1 stabilization independently; monitor for viewport regressions.
3. Deploy readiness validation in comparison/reporting mode, resolve policy discrepancies, then enforce it for new approvals/publications and new test selection under the agreed policy.
4. Release new navigation and ingestion/review screens behind reversible feature flags where supported. Keep a compatible fallback until the new flows pass end-to-end checks.
5. Apply approved existing-content reconciliation in batches, retaining versions and audit records. Verify aggregate counts after each batch.
6. Expand rollout after content operations successfully complete upload → inspect → repair → approve → publish using representative sources.

**Rollback triggers:** Wrong-bank data/action target, invalid content becoming newly eligible, data loss, broken historical test references, unrecoverable job transitions, or serious accessibility/responsive regressions. UI rollback must not disable backend content-integrity checks. Schema compatibility and migrated-data reversal require their own procedures.

## Finding coverage matrix

Every audit finding has a primary implementation phase; cross-cutting validation remains mandatory.

| Findings | Primary phase | Additional dependency |
|---|---|---|
| SA-01, SA-02, SA-03, SA-05, SA-13 | 2 · Readiness integrity | Repair tools/data cleanup in 5 |
| SA-04, SA-07, SA-15, SA-16, SA-17 | 3 · Navigation/job state | Contracts from 0 and 2 |
| SA-08, SA-09, SA-18, SA-20, SA-21, SA-22, SA-25 | 1 · Immediate stabilization | Workflow completion in 4; accessibility in 6 |
| SA-10, SA-23, SA-24 | 4 · Ingestion workspace | Reliable APIs/state from 2–3 |
| SA-06, SA-11, SA-12, SA-19 | 5 · Library repair | Full-bank queries/readiness contract |
| SA-14 | 6 · Admin support | Role/authorization confirmation |

## Completion criteria

- All 25 findings have a validated resolution or an explicit product decision with rationale; none is silently dropped as cosmetic.
- No unresolved P1 remains in bank selection, content readiness or intermediate-width ingestion layout.
- All questions are retrievable beyond the latest 100; all blocking issues are represented consistently in counts and repair queues.
- Add-source, monitor and review workflows are usable at the audited widths, with bounded diagnostics and review rendering.
- Server-side integrity rules remain effective regardless of UI state or rollout flags.
- Existing-content reconciliation is accounted for separately from code completion, with preserved assessment history.
- The release includes migration/rollback instructions, before/after screenshots, meaningful regression results and performance traces.

**Scheduling:** Estimate each work package after Phase 0 code tracing. Prioritize the Phase 1 stabilization release and Phase 2 publication gate first; avoid committing to calendar dates for the source viewer, ingestion correctness work or legacy cleanup before storage, worker behavior and affected record counts are known.
