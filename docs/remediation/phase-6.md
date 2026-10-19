# Phase 6 — teacher support, consequences and accessibility

Implemented and reviewed locally, 2026-10-19.

Teacher names now open routed administrator support pages. Profile editing,
suspension/reactivation and code recovery reuse the existing authentication APIs.
Recovery revokes sessions and preserves suspension unless reactivation is explicitly
requested. Support context is scoped to the selected teacher's batches, students,
tests and attempts, with server pagination and saved assessment-version context.
Cross-teacher resource IDs are rejected. Credential hashes/codes are excluded from
responses and audits; recovery input is masked and reset after success. Profile and
recovery forms track unsaved work independently.

Consequential controls identify the exact teacher/bank/source and describe scope,
consequences and recovery. Soft-deleted banks are restorable using audited prior
visibility (legacy unknown state defaults private). Reference checks prevent deleting
or re-extracting assessment content/assets, including crops reused by a revision.
Question/source locks serialize reference checks against new assessment snapshots.
Relinked-question source documents are retained when the original import is removed.
No product claim is made that a permanently deleted unreferenced upload is recoverable
without its original file.

Document and nested figure dialogs trap Tab/Shift+Tab, make background content inert,
handle Escape through unsaved-change guards, restore focus to stable semantic
triggers, and restore background state/scroll locks when closed. Keyboard coordinate
controls provide an alternative to dragging crop bounds. Visible focus, practical
44 px actions, reduced motion, labels, live pending/error feedback and bounded source/
math viewers cover the changed workflows. Native recovery/publication confirmations
remain consistent with existing product controls.

Review fixed: reset-code unintentionally reactivated suspension; profile versus
recovery dirty state could erase another form's pending guard; raw support data
needed structured presentation; dialogs needed stable focus restoration through
routing/remounts; destructive cleanup needed version/reused-asset protections.

Validation: six support/recovery/history regressions pass, along with related
repair/source suites. The full suite before the final reused-asset case had 289
passes and the same three baseline failures identified in Phase 2. TypeScript,
frontend utility tests and the production build are the phase gate.

Chrome verified profile editing, masked code reset without reactivation, explicit
reactivation, attempt/saved-response inspection, accessible teacher labels and no
page overflow at 390/768/1024/1280. It verified forward/reverse Tab containment,
inert background, a crop created with coordinate controls, nested/outer focus
restoration and complete background restoration. See `evidence/phase6-browser-results.json`
and `phase6-teacher-support.png`.

Review outcome: local support and keyboard checks pass. No WCAG-conformance claim
is made; assistive-technology/contrast/zoom qualification and real account/worker
operations remain Phase 7 release gates. Proceed to Phase 7 after the build passes.
