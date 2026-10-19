# Super admin remediation: first delivery

Scope: Phase 0 code trace and Phase 1 UI stabilization. No production data changes.

## Observed implementation and state contract

| Concept | Current source | Required separation |
| --- | --- | --- |
| Extraction | `ImportJob.status`: uploaded, processing, review, failed, approved | Pause/cancel request flags are not worker acknowledgements. |
| Generation | `extraction_meta.solution_generation` | Generation success is not human review or publication. |
| Human review | `Question.approved_at` | Historical approval does not prove present content completeness. |
| Completeness | `bank_imports._issues`, figure validation, endpoint checks | Phase 2 must consolidate into versioned blocking/advisory results. |
| Taxonomy | QuestionTaxonomy / TaxonomyNode | Unclassified and unmapped values must remain explicit. |
| Publication | `ImportJob.published_at`, bank status | Must eventually revalidate eligible questions on submission. |

Code locations: `apps/web/app/portal.tsx` contains BankImportStatus, FolderInput,
DocumentImportPage and teacher administration. `globals.css` contains the competing
upload media rules and unconstrained document action column. `apps/api/app/main.py`
contains bank selection APIs, question editing/approval, counts and publication;
`bank_imports.py` handles extraction and initial approval; `solution_generation.py`
applies generated results. Figures/source storage live in `figures.py` / `storage.py`.

Bank detail currently loads the latest 100 questions. Bank aggregates query the full
bank, but their definitions differ from content validity. Source identity uses source
hash + question number + page; use stable question IDs for repair fixtures.

## Compatibility and unresolved policy

Do not reinterpret historical approvals or alter existing test/attempt references in
this UI release. Phase 2 needs an additive validator result/version and a nonmutating
scan before any reversible reconciliation. Publication currently uses approval flags.
Solution and taxonomy requirements by question type need content-operations agreement.
A failed generation must preserve trusted source keys. Bank-switch root cause remains
unconfirmed; no reproduction of its intermittent production failure is claimed.

## Phase 1 implementation

- Container-based row stacking protects metadata even beside the bank sidebar.
- Review/Inspect remains the primary visible action; recovery/publication/destructive
  controls retain their handlers inside a keyboard-accessible More actions disclosure.
- Extraction/generation diagnostics scroll within a bounded list; review run issues
  have a bounded region. Actions align to the row top.
- Mobile upload panels stack; helper text precedes the full-width grid CTA row.
  Selected file and folder names are shown in a bounded list.
- Teacher labels are associated with inputs; visible focus and touch targets added.
- Review headers, filters and five-metric grid wrap without forcing page width.

## Qualification

Run TypeScript and production build. Check 390, 768, 1024, 1280 widths and container
boundaries with long filenames, 50+ errors, full generation denominators and all
available actions, including expanded menus and diagnostics. Repeat at 200% zoom.
Existing `audit-evidence/` files are the production baseline, not new validation.

Live staging worker lifecycle, assistive technology, focus containment, browser traces,
real-record fixtures and authenticated end-to-end checks remain release gates.
This delivery does not claim Phase 0 or Phase 1 acceptance gates fully passed.
Next: shared validation matrix, real-record fixtures and publication comparison scan.

Local results: TypeScript passed; Next production build passed (existing multiple-
lockfile workspace-root warning). `git diff --check` passed. A disposable static
fixture using application CSS, long source names, expanded secondary actions and
50 extraction messages passed no-horizontal-overflow checks at actual Chrome widths
500, 768, 1024 and 1280. The diagnostic list measured 220 px throughout. Chrome's
headless window minimum clamped the requested 390 width to 500; 390 and 200% zoom
are explicitly unverified. This fixture checks CSS behavior, not authenticated React
flows, and does not replace staging acceptance. No production browser was modified.
