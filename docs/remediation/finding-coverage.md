# Audit finding disposition

Local implementation/review coverage, 2026-10-19. Evidence and qualifications live
in each phase report. A local resolution does not close the external release gates
in [rollout.md](rollout.md).

| Finding | Implemented resolution | Evidence / remaining gate |
| --- | --- | --- |
| SA-01 | Shared type/option/figure validator; revalidated approval/publication/selection | Phase 2 tests; historic published content needs production scan/policy |
| SA-02 | Null keys block readiness despite historic Reviewed flag | Phase 2 and 7 publication browser checks |
| SA-03 | Full-bank question/document attention counts with distinct semantics | Phase 2 count tests; actual-bank comparison pending |
| SA-04 | Abort/identity guards, pending/error controls scoped to requested bank | Phase 3 delayed/failing browser races; phase 7 repeat |
| SA-05 | Shared publishable filter excludes incomplete/failed legacy records | Phase 2 readiness/filter tests |
| SA-06 | Paginated full-library search including orphan sources | Phase 5 older orphan browser fixture |
| SA-07 | Separate worker lifecycle, human review and publication fields | Phase 3 transitions; real worker restart gate |
| SA-08 | Reflowed source/review dialogs and bounded content viewers | Phase 1/4/6 widths; native zoom/AT gate |
| SA-09 | Teacher labels associated with fields | Phase 1/6 labels/browser checks; AT gate |
| SA-10 | Bounded/paginated diagnostics with question links | Phase 3/4 issue and queue checks |
| SA-11 | Type-specific content/answer controls with strict advanced JSON | Phase 5 numeric and normal MCQ browser checks |
| SA-12 | Original-page viewer, guarded relink and figure provenance | Phase 5/6 source and keyboard crop tests; real storage gate |
| SA-13 | Explicit Unclassified and advisory policy; real taxonomy filters | Phase 2/5; no automatic historic classification |
| SA-14 | Routed/scoped teacher support, history and audited access recovery | Phase 6 tests/browser; real account gate |
| SA-15 | Bank/document/question routes, URL filters and return context | Phase 3 navigation/browser tests |
| SA-16 | Loading, empty, error and stale feedback separated | Phase 3 failing-bank browser test |
| SA-17 | Stage denominators, complete paginated issue logs and legacy disclosure | Phase 3 lifecycle/issue tests |
| SA-18 | Separate document identity/actions with state-specific primary action | Phase 1/4 widths/browser evidence |
| SA-19 | Plain-language issue explanations, classification tools and alias preview | Phase 5; parent hierarchy merges explicitly guarded |
| SA-20 | Intermediate-width stacked rows before metadata collapses | Phase 1 screenshots; phase 7 breakpoint sweep |
| SA-21 | Compact failure summary with bounded diagnostic details | Phase 1/4 browser evidence |
| SA-22 | Stacked upload roles, file names, limits and reachable CTA | Phase 1/4 responsive/preflight evidence |
| SA-23 | 25 summaries, zero initial full editors, one selected detail | Phase 4 179-question browser fixture; phase 7 cycles |
| SA-24 | Default monitor with filename/subject/state filters, counters, deliberate sorting | Phase 4 monitor browser evidence |
| SA-25 | Add sources/Monitor/Review separation and scoped recovery/destructive controls | Phase 4/6 browser evidence |

Explicit boundaries: no production answer-key rewrite, legacy-flag reconciliation,
published snapshot withdrawal, parent hierarchy merge, automatic deployment, or WCAG
conformance claim. All remain visible decisions/gates, not silently completed work.
