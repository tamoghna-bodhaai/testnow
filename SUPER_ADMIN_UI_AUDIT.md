# TestNow — Super admin interface inspection

**Implementation follow-up:** [Phased remediation plan](SUPER_ADMIN_REMEDIATION_PLAN.md) consolidates both inspection passes into concrete work packages, dependencies, release gates and coverage for all 25 findings. It supersedes the preliminary implementation-order table below.

**Inspection date:** 18 October 2026, approximately 11:32–11:38 PM IST  
**Application:** https://web-interface-production-3443.up.railway.app/  
**Method:** Live inspection of the authenticated super admin session in Chrome, using rendered screens, accessibility/DOM snapshots, and selected browser console warnings. Desktop and 390 × 844 responsive viewport inspected. No application source or backend was audited.

## Executive assessment

The interface supports teacher creation, bank management, document ingestion, and question review, but its most important trust signals are unreliable. Published material contains visibly incomplete questions marked as passed; bank health summaries under-report review work; and review/classification labels disagree with editor content. These are higher priority than cosmetic changes because admins can mistake incomplete assessment content for content ready to use.

The next phase should first establish one consistent definition of question readiness, reconcile existing content against it, and repair bank selection and review navigation. Then improve the tools for finding and repairing questions at scale. The current library contains **21,027 questions**, while the bank-level browser exposes only the latest 100 per bank.

**Responsiveness follow-up:** A second focused inspection confirmed a severe 1024 px ingestion breakpoint failure, excessive expansion of inline failure logs, mobile upload overflow, and an oversized review surface. See the additional findings SA-20–SA-25 and the ingestion redesign brief at the end. The report now contains **25 findings**.

**Recommendation:** Treat the P1 findings below as release gates for the next content publication phase. No claim is made that these questions have appeared in an actual student test; the confirmed observation is that their source/bank is labelled LIVE or published in the admin UI.

## Coverage and limits

| Surface | Inspected |
|---|---|
| Admin dashboard | Metrics, add-teacher form, teacher activity table, available account actions, desktop/mobile layout and accessible names |
| Bank library | All four banks, name search, no-result state, Draft filter, selection, header actions and inline edit-details form |
| Arihant qbank | All 17 document rows; failed-generation diagnostics; completed/error and paused review workspaces; passed filter; rendered question and edit forms |
| qbank 10 | Three document rows, cancellation/processing status, topic tree, latest-100 review scope |
| Physics question bank 1 | All 13 document rows; Properties of Matter document workspace; passed subjective/MCQ examples and rendered editor |
| Question Bank 1 | Empty source library, topic browsing, unclassified groups, numerical question rendered/edit views |
| Responsive | Dashboard/table, bank library, Arihant document-review dialog at 390 × 844 |

This was a **read-only production inspection**. No teachers/banks were created; no accounts were suspended; no documents were uploaded, stopped, retried, re-extracted or deleted; no questions were saved, approved or published. Consequently, validation after submission, confirmation dialogs, rollback behavior, actual publishing enforcement, ingestion execution and authorization enforcement remain untested. Not every one of the 21,027 questions was individually reviewed. Student and teacher portals were outside scope.

All inspected application views retained the root URL `/`. Paths below are navigation instructions, not separate routable URLs.

## Priority definitions

- **P1 — High:** Wrong readiness signals, broken access to core work, or substantial risk of invalid assessment content being treated as usable.
- **P2 — Medium:** Significant repair-workflow friction, missing operational controls, accessibility or responsive defects.
- **P3 — Low:** Presentation and clarity improvements.

Findings distinguish **observed defects** from **interface gaps**. Backend causes are hypotheses until investigated in code.

## Findings ready for the next phase

### SA-01 · P1 · Published material contains malformed questions marked passed

**Type:** Observed content/readiness defect.  
**Reproduce:** Question banks → Physics question bank 1 → `09_Properties_of_Matter.docx` → Review.

The document is labelled LIVE and reports 122 passed questions out of 130. Visible examples include:

- Q18, “As the bubble moves upwards…”: options A–C are “Not provided”; D says “Correct”; answer key is **Option A**. Its editor also marks A as the correct answer. The stem reads like an explanation rather than a complete question.
- Q20: stem consists of “Buoyancy force / Here, / and / Substituting the values, we get”; options contain “Not provided” and “Correct”, while the answer key again says Option A.
- Several passed subjective items contain explanation fragments and show “Answer key Missing”. Q22 and Q26 are immediately visible examples. A missing key can be acceptable for some subjective policies, but the UI does not explain such an exemption, and the fragments need source verification.

**Impact:** “Passed review” cannot currently be trusted as a usable-content signal. This goes beyond visual polish.

**Fix:** Validate meaningful stems, non-placeholder options, answer structure, required figures and completeness before assigning readiness. Review the question/solution segmentation of these imports. Re-audit existing published content using the same rules.

**Acceptance:** These exact malformed MCQs appear in a repair queue and cannot be considered ready. The valid answer must map to an actual option. Required content rules must be enforced on the server as well as displayed in the UI.

**Evidence:** [Live passed questions](audit-evidence/live-passed-missing-answer.png), [malformed MCQ editor](audit-evidence/live-malformed-mcq.png).

### SA-02 · P1 · Reviewed numerical question has no answer, yet bank says no issues

**Type:** Observed contradictory readiness defect.  
**Reproduce:** Question banks → Question Bank 1 → Modern Physics → Unclassified topic → Q51. → View / edit → Edit.

Q51 is labelled Reviewed. The rendered editor explicitly reports “Missing answer and solution”; the edit form contains Answer JSON `null` and warns that an answer must be added before approval. Nevertheless, the bank reports **0 items** needing attention and “No issues found. All questions are classified and reviewed.”

**Impact:** Existing reviewed records bypass the effective readiness definition shown by the editor. The warning alone does not surface the content in the work queue.

**Fix:** Derive review queues, readiness badges and publication eligibility from the same validation result. Reconcile legacy reviewed records rather than treating the reviewed flag as sufficient.

**Acceptance:** Q51 appears under missing-answer work regardless of its historical reviewed flag; bank totals match the full validated dataset.

**Evidence:** [Numerical editor](audit-evidence/numerical-reviewed-incomplete.png).

### SA-03 · P1 · Document health summary reports zero attention despite visible failures

**Type:** Observed summary defect.  
**Reproduce:** Select Arihant qbank → Document imports.

The summary shows **17 total / 0 need attention / 11 in progress**. The same screen includes OP Tandon Organic with 179 flagged questions and 58 generation failures; VK Jaiswal has 265 flagged and 52 failures. Other rows explicitly say “Needs anomaly review”. The mismatch also appears in the Physics bank: 0 documents need attention despite every listed document saying “Needs anomaly review”.

**Impact:** An admin scanning the summary receives a false clean-health signal.

**Fix:** Define and label document-level attention independently from question counts. Include blocked, failed and review-needed states in that summary, and make it a filter into the affected documents.

**Acceptance:** Summary values reconcile with row states; unit and overlap semantics are explicit; clicking attention shows the affected records.

**Evidence:** [Import overview](audit-evidence/import-overview.png).

### SA-04 · P1 · Bank switching can silently leave the previous bank active

**Type:** Observed intermittent navigation failure.  
**Reproduce observed:** Open Arihant, then qbank 10, then select Physics question bank 1 or Question Bank 1.

During this sequence, the target bank button received focus but the heading, materials and actionable controls remained for **qbank 10**, across repeated observations and a wait for the target heading. Searching for the target and selecting it through the accessible control did not resolve it. Navigating **Admin dashboard → Question banks**, then selecting the target from the fresh library, did work for both remaining banks.

**Impact:** Users cannot reliably access another bank and may act on the old bank if they assume their selection succeeded. Focus alone is not evidence that the selected-bank styling changed.

**Fix:** Investigate bank-selection state, outstanding requests and failure handling. Show a loading/error state naming the requested bank, prevent stale responses from replacing newer selections, and disable mutations while selection is unresolved.

**Acceptance:** Repeated A→B→C switching and failed/slow requests always leave the list selection, heading, document set and mutation target consistent. A visible retry appears on failure.

**Evidence:** [Previous bank retained after selection](audit-evidence/bank-switch-stale.png). Root cause was not established by this UI inspection.

### SA-05 · P1 · Passed-review filter includes failed and incomplete questions

**Type:** Observed status ambiguity and missing-content defect.  
**Reproduce:** Arihant → OP_Tandon_Organic_Chemistry.pdf → Review → Passed 2.

Q24 and Q135 are marked Passed review while their solution says unavailable and their review note says the model did not return a valid multi-step solution. The header reports 179 questions, 179 needing attention and 2 passed. Q24 asks for the weakest C–H bond but displays only α, β, γ, δ options without the molecular diagram needed to interpret them.

**Impact:** Passed, flagged and generated statuses overlap without explanation. A “Publish 2 passed” action is presented for content whose displayed completeness is questionable. Its actual server-side publication behavior was not tested.

**Fix:** Separate human review, content completeness and generation status. Show explicit blocking reasons and eligibility counts. Require missing-figure repair before readiness; decide whether a solution is mandatory by question policy and communicate that policy.

**Acceptance:** The passed filter clearly communicates any nonblocking generation failure; blocking missing content excludes a question from publishable counts.

**Evidence:** [Passed items with generation failures](audit-evidence/passed-generation-failed.png).

### SA-06 · P2 · Bank-wide question management is limited to the latest 100

**Type:** Observed interface gap.  
**Reproduce:** Open any bank → Review and edit questions.

The UI states “Showing the latest 100 of …” (4,414 / 11,326 / 1,975 / 3,312). No question-level search, pagination or load-more control is exposed in this bank section. Document review offers another route for banks with sources, but Question Bank 1 shows no sources, leaving its older questions without an evident browsing route.

**Fix:** Add server-side search and pagination across all questions, with source, subject, chapter, topic, type, readiness and anomaly filters. Clearly scope queue counts to “loaded sample” until full-bank counts exist.

**Acceptance:** An admin can locate an older question by stable ID/source/page without going through the newest 100, including records without linked sources.

### SA-07 · P2 · Publication, extraction and review states are conflated

**Type:** Observed communication defect.  
**Reproduce:** Arihant document list; compare Himanshu Pandey with OP Tandon Physical Chemistry.

Both say “Pause requested — finishing the current page batch” and offer Resume. One badge says PAUSED; the other says LIVE. qbank 10 documents say “Cancellation requested” while showing PROCESSING and offering Pause. The interface does not explain whether LIVE describes existing published content independently from the current extraction job.

**Fix:** Display separate labelled states for publication, extraction job and review. Include last activity, processed pages, job age and the next available action. Distinguish pause requested from paused, and cancellation requested from cancelled.

**Acceptance:** An admin can answer whether content is visible, whether a worker is running, and whether a job needs intervention without inferring meaning from one badge. These observations do not prove that workers are stuck.

**Evidence:** [Import overview](audit-evidence/import-overview.png).

### SA-08 · P2 · Mobile document-review dialog overflows horizontally

**Type:** Confirmed responsive layout defect.  
**Reproduce:** Viewport 390 × 844 → Arihant → OP Tandon Organic → Review.

The dialog has a measured client width of **360 px** and scroll width of **458 px**. The filename, subtitle and action row extend beyond the right edge. “Approve generated passes” is cut off; an additional horizontal scrollbar appears. The five metric tiles leave an empty tinted sixth cell.

**Fix:** Allow filename wrapping, use `min-width: 0` where appropriate, wrap/stack actions, and give long content explicit overflow rules. Use a grid that accommodates five metrics without an artificial empty tile.

**Acceptance:** At 390 px and nearby mobile widths, all header actions and labels remain visible without horizontal page/dialog scrolling. Recheck long filenames and text zoom.

**Evidence:** [Mobile review dialog](audit-evidence/mobile-review.png).

### SA-09 · P2 · Teacher creation inputs have no accessible names

**Type:** Confirmed accessibility defect.  
**Reproduce:** Admin dashboard → Add teacher; inspect accessibility tree.

Name, Email and 5-digit access code are visible labels but their three textboxes are unnamed in the accessibility snapshot. DOM inspection showed no input IDs or aria-labels. The form does have required fields; email type and the five-digit pattern are present, which is useful validation groundwork.

**Fix:** Associate each visible label with its input using `label`/`htmlFor` and unique IDs, or equivalent accessible naming. Connect validation messages using `aria-describedby` and `aria-invalid`.

**Acceptance:** Each field is exposed with its visible name to assistive technology and can be located by that label. Verify keyboard navigation and error announcements.

### SA-10 · P2 · Run issues overwhelm the review workspace

**Type:** Observed workflow/layout defect.  
**Reproduce:** Arihant → OP Tandon Organic → Review.

A long, fully expanded Run issues section sits before question filters and cards. At desktop size it occupies more than a viewport. Entries are paragraphs rather than jump links; repeated source question numbers such as Q23 make the target ambiguous without a page or stable ID. The subtitle also visually crowds the metric section beneath the header.

**Fix:** Collapse/group issues by reason, show a compact summary, and link each issue to the exact question using source/page/stable ID. Keep filters and context accessible while scrolling.

**Acceptance:** An admin can reach and repair a generation conflict in one click without scrolling through the entire diagnostic list.

### SA-11 · P2 · Editor requires JSON for ordinary content repair

**Type:** Observed usability gap.  
**Reproduce:** Any question → Edit.

Options and answers must be edited as JSON even for straightforward MCQs or numerical questions. The form exposes type, stem, options, answer, solution and confidence, but no subject/chapter/topic correction controls. That leaves “Unclassified topic” work without an evident repair action in the inspected editor.

**Fix:** Add type-specific controls: option rows, correct-answer selectors, numeric answer/tolerance inputs and appropriate matrix editing. Add taxonomy selectors and retain JSON as an advanced view. Provide schema errors before save.

**Acceptance:** A content reviewer can fix a standard MCQ and reclassify a question without writing JSON. Matrix and multi-answer structures remain valid after editing.

### SA-12 · P2 · Source provenance is insufficient for verification

**Type:** Observed interface gap.  
**Reproduce:** Question Bank 1 → Q51; compare document-scoped Q24 editor.

Question Bank 1 has 3,312 questions but zero source documents; Q51 says “Unknown source · page 166”. Other editors expose a filename and page as text but no source-page viewer or figure-repair control in the inspected panels. This makes diagram ownership and question/solution segmentation issues difficult to resolve confidently.

**Fix:** Preserve/link source provenance, support an explicit legacy/unlinked state, and add side-by-side original-page and extracted-question review with figure assignment/cropping tools.

**Acceptance:** A reviewer can compare the original question, choices, figures and answer source directly from the editor, or sees a clear unrecoverable-source warning.

### SA-13 · P2 · Unclassified content is labelled Complete and globally classified

**Type:** Observed misleading copy/state defect.  
**Reproduce:** Question Bank 1 → topic tree.

All 100 loaded questions sit in “Unclassified topic” groups, each labelled Complete, while the empty attention queue says “All questions are classified and reviewed.” Even if Complete means only reviewed, its scope is unclear and the explicit classification claim is false for the visible topics.

**Fix:** Separate taxonomy completeness from review completion. Use scoped empty-state copy and put missing taxonomy into an actionable queue where classification is required.

**Acceptance:** No unclassified topic is described as classified; counts and empty states remain correct for the full bank and the current filter.

### SA-14 · P2 · Super admin management has limited drill-down and recovery tools

**Type:** Observed product gap; requirement-dependent.  
**Reproduce:** Admin dashboard.

The dashboard exposes creation and Suspend for teachers. Teacher name, batch/student/test/attempt counts and summary cards do not expose drill-down controls. No teacher profile edit, invitation/access-code recovery workflow, audit history or platform-wide student/test inspection is present in the visible super admin navigation.

**Fix:** Add teacher detail and linked operational lists; define supported account recovery and administrative audit trails. Clarify which operations intentionally belong to the teacher portal.

**Acceptance:** Support staff can investigate a teacher's test or student issue from a teacher detail view, with logged actions and appropriate permissions. This finding does not assert those capabilities are absent from all backend APIs.

### SA-15 · P2 · Navigation has no visible deep links or restorable review location

**Type:** Observed interface gap.  
**Reproduce:** Switch dashboard/library, select banks, open document dialogs and question editors.

The URL stays `/` throughout the inspected navigation. There is no visible bank/document/question permalink. Returning to the library from the dashboard clears the selected bank. Refresh/history persistence was not fully tested, so loss on refresh is a risk to verify rather than a confirmed defect.

**Fix:** Add routable bank/document/question IDs and URL-backed filters, plus breadcrumbs/back behavior that preserves the queue and scroll position.

**Acceptance:** Opening a copied question URL restores the same authorized question and review context; back navigation restores the preceding filtered list.

### SA-16 · P2 · Loading states impersonate empty data

**Type:** Observed transient feedback defect.  
**Reproduce observed:** Dashboard → Question banks.

The first observation showed 0 total / 0 questions and “No banks match this filter”, then resolved to 4 banks / 21,027 questions. Selecting a bank initially retained “Create or select a bank to begin” before details appeared. No explicit loading feedback appeared in those observed intermediate states.

**Fix:** Distinguish loading, empty, filtered-empty and failed states. Keep retry available for load failures and announce status changes accessibly.

**Acceptance:** Slow requests never show a definitive zero/empty assertion until loading has completed successfully.

### SA-17 · P3 · Failure counts and progress lack clear denominators

**Type:** Observed clarity defect.  
**Reproduce:** Arihant → OP Tandon Organic / VK Jaiswal.

OP Tandon shows 58 failed but “Generation failures (50)”; VK Jaiswal shows 52 failed but the same 50-item disclosure. This may be a deliberate log cap, but that cap is not disclosed. A full 159/159 progress indicator coexists with only 37/1107 pages checked and 179 questions, without explicit labels distinguishing generation attempts from extraction coverage.

**Fix:** Label each stage and denominator; use “Showing 50 of 58 failures” with access to the rest. Explain processed versus successful versus total eligible questions.

**Acceptance:** Failure totals, visible entries and stage progress can be reconciled without guessing.

### SA-18 · P3 · Dense action rows compress the information being acted on

**Type:** Observed desktop presentation defect.  
**Reproduce:** Arihant → Document imports.

OP Tandon's action row reserves space for many controls, squeezing the progress value into an ellipsis (“159…”), and wrapping status/page details into a narrow column. Several actions have similarly strong visual emphasis; destructive/re-extraction controls remain adjacent to routine review work. This pass did not test their confirmation mechanisms.

**Fix:** Make Review the primary action, group operational actions separately and place infrequent destructive operations in a clearly labelled menu. Let metadata use the available row width.

**Acceptance:** Counts remain readable at desktop widths and zoom; routine review is visually distinct from retry, approval, publishing and destructive operations.

**Evidence:** [Desktop import layout](audit-evidence/import-overview.png).

### SA-19 · P3 · Raw flags and inconsistent taxonomy make triage harder

**Type:** Observed information-design gap.

The bank attention queue presents raw values such as `diagram_question_scope_overlap`, `option_diagram_missing:0`, `llm_generated_unreviewed` and `single_choice`. Topic naming varies between “Reaction mechanism”, “Reaction Mechanisms”, “Adiabatic Process” and “Adiabatic Processes”. These variants fragment browsing and make issue meaning harder to interpret.

**Fix:** Map internal codes to plain-language labels, show the affected option as A/B/C/D, provide a recommended repair action, and canonicalize/merge taxonomy aliases. Keep raw codes in an advanced diagnostics view.

**Acceptance:** A reviewer understands each flag and repair action without backend knowledge; equivalent topics resolve to one canonical taxonomy entry.

## What is working

- Clear top-level distinction between dashboard and question banks; desktop headings and card structure are easy to scan.
- Bank search and Draft no-result filtering worked in the inspected cases.
- Document-scoped review provides useful All / Needs attention / Generated / Passed filters, question previews and source-page metadata.
- The editor supports rendered, JSON and edit views, and exposes a missing-answer warning.
- Teacher creation has basic required/email/five-digit HTML constraints; the teacher table includes operational counts.
- Mobile dashboard content stacks successfully and its wide teacher table has a contained horizontal scrollbar. That pattern works better than the overflowing document dialog, though Suspend requires horizontal scrolling on a phone.

## Proposed implementation order

| Phase | Work | Exit condition |
|---|---|---|
| 1 · Readiness integrity | SA-01, 02, 03, 05, 13; reconcile current published/legacy records | Missing answers, placeholder options and required figures cannot coexist with publishable readiness; all summary/queue counts agree |
| 2 · Reliable navigation | SA-04, 15, 16 | Bank switching is deterministic; slow/failing requests are visible; document/question links preserve context |
| 3 · Review at scale | SA-06, 10, 11, 12, 19 | Full-bank question retrieval, direct issue-to-question navigation, usable repair fields and source comparison |
| 4 · Operational clarity | SA-07, 14, 17, 18 | Publication/job/review states are separate; teacher drill-down and failure diagnostics support routine operations |
| 5 · Responsive/accessibility finish | SA-08, 09 plus keyboard/focus testing | Mobile review has no clipping; all controls have usable names and accessible feedback |

**Small early fixes:** teacher label associations, explicit loading copy, disclosed log truncation, scoped empty-state copy, header wrapping and less crowded action layout. These can proceed alongside the deeper readiness work. Effort estimates should follow source-code inspection; UI evidence alone is insufficient for reliable delivery estimates.

## Next-phase validation checklist

- [ ] Use disposable staging fixtures to test create/edit/delete, teacher suspension/recovery and upload/re-extraction paths.
- [ ] Enforce the same readiness rules in import, editor, approval, publication, counts and question selection.
- [ ] Add representative fixtures from the exact malformed/legacy questions identified above.
- [ ] Verify publish preview, exact affected counts, blocking reasons and already-published content handling.
- [ ] Exercise slow requests, failed requests and rapid bank switching to expose stale responses.
- [ ] Locate older questions beyond 100 records, including questions without sources.
- [ ] Test diagram-heavy MCQs, multiple-correct, numerical, integer, matrix-match, assertion-reason and subjective content against original pages.
- [ ] Verify that generated answer conflicts cannot silently replace a trusted source key.
- [ ] Test unsaved edits, cancellation, refresh and browser back/forward.
- [ ] Check keyboard focus entry/trap/restoration for document dialogs. Background content remains present in DOM snapshots while dialogs are open; that alone does not establish a focus-trap defect.
- [ ] Test accessible form errors, status announcements, contrast, text zoom and keyboard use. This pass did not establish WCAG conformance or measure contrast ratios.
- [ ] Recheck 390 px, tablet and desktop breakpoints, including long filenames, wide mathematical notation and large diagnostic lists.
- [ ] Verify teacher/admin permission boundaries and audit logging separately; this visual inspection is not a security audit.

## Evidence index

Screenshots are local inspection artifacts. File names describe the inspected state, not proof of a backend cause.

| Artifact | Contents |
|---|---|
| `audit-evidence/dashboard.png` | Desktop teacher/platform dashboard |
| `audit-evidence/import-overview.png` | Zero attention summary, generation errors, mixed live/paused statuses, compressed actions |
| `audit-evidence/import-status-and-failures.png` | Expanded generation diagnostics |
| `audit-evidence/passed-generation-failed.png` | Passed filter showing unavailable solution and failure note |
| `audit-evidence/live-passed-missing-answer.png` | Live DOCX review with passed incomplete items |
| `audit-evidence/live-malformed-mcq.png` | Published-source MCQ with placeholder choices |
| `audit-evidence/numerical-reviewed-incomplete.png` | Reviewed numerical question with null answer |
| `audit-evidence/unclassified-complete.png` | Unclassified topic browser |
| `audit-evidence/bank-switch-stale.png` | Previous bank retained during target-bank selection attempt |
| `audit-evidence/paused-import.png` | Paused source review |
| `audit-evidence/mobile-dashboard.png` | Responsive teacher form/table |
| `audit-evidence/mobile-review.png` | Horizontally overflowing mobile document dialog |

## Follow-up: responsiveness and ingestion usability

This additional pass exercised the same authenticated production UI at **1280 × 800, 1024 × 768, 768 × 1024 and 390 × 844**, in addition to the original desktop viewport. It focused on document rows, expanding/collapsing diagnostics, upload form reflow, review-dialog density and switching review filters. No ingestion jobs or production records were changed. The temporary viewport override was reset afterward.

### Viewport assessment

| Width | Observed behavior | Assessment |
|---|---|---|
| Original wide desktop | Fixed-width content region leaves substantial surrounding space; long action rows still compress the first document's metadata/progress | Usable but inefficient and crowded |
| 1280 px laptop | First failed-generation row is 249 px high collapsed; six management actions plus filename compete for space; progress value is ellipsized | Fragile layout; diagnostics expansion makes it substantially worse |
| 1024 px landscape/tablet/small laptop | Status text collapses to one character per line; first collapsed document row becomes 1,714 px high | **Severely broken breakpoint** |
| 768 px portrait tablet | Document metadata and actions stack; progress denominator becomes readable again; upload panels remain side by side | Document list is materially better than at 1024 px; breakpoint transition is poorly chosen |
| 390 px phone | Document actions wrap over many lines; upload panels stay side by side; upload CTA extends outside viewport; review dialog also overflows | Requires dedicated compact layout |

### SA-20 · P1 · Intermediate ingestion breakpoint makes status text effectively unreadable

**Type:** Confirmed responsive defect.  
**Reproduce:** Arihant qbank → Document imports → set viewport to 1024 × 768; keep OP Tandon Organic's failure log collapsed.

The row retains the horizontal desktop arrangement even though its actions consume most of the available width. The status beginning “Figure correction awaiting question approval” breaks into a vertical column of individual characters. The document row measures **1,714 px tall** even with diagnostics collapsed. At 768 px it switches to a stacked layout and becomes readable again.

**Impact:** A common intermediate viewport is worse than both the larger desktop and smaller tablet layout. Users cannot quickly interpret status or reach the next document; this is a core ingestion usability blocker.

**Fix:** Switch document rows to a stacked layout earlier, based on the available content width. Prevent metadata from shrinking below a useful minimum. Give the action group its own wrapping row; do not allocate metadata whatever width remains after all action buttons.

**Acceptance:** At 1024 px and nearby widths, status wraps at normal word boundaries and all collapsed metadata/actions remain readable. Validate continuously from 768 to 1280 px rather than testing only named device sizes.

**Evidence:** [1024 px status collapse](audit-evidence/ingestion-1024-collapse.png), [768 px stacked comparison](audit-evidence/ingestion-768.png).

### SA-21 · P2 · Inline failure expansion creates several screens of displacement

**Type:** Confirmed interaction/layout defect; extends SA-10 and SA-18.  
**Reproduce:** At 1280 × 800, Arihant → OP Tandon Organic → expand Generation failures (50).

The row grows from **249 px to 3,254 px**. Failure messages remain constrained to a narrow column despite ample blank space to their right. The Review action moves to approximately **y = 1,798 px** relative to the viewport during that observed state, outside the visible screen. Subsequent documents are pushed several screens down. Collapsing the log reverses this displacement.

**Impact:** A diagnostic disclosure unexpectedly reorganizes the entire work surface and separates status from its actions. This provides a concrete explanation for perceived jank even without a rendering-performance trace.

**Fix:** Keep the row stable and top-align its actions. Show a short issue summary inline; move full logs into a separate bounded panel or document detail view. If logs remain inline, let them span the row width with a clearly labelled height limit and independent access to all entries.

**Acceptance:** Expanding diagnostics does not move the document's primary action outside its visible header or displace the next row by several viewports. Opening/closing details preserves an understandable scroll anchor.

**Evidence:** [Collapsed laptop row](audit-evidence/ingestion-1280.png), [expanded narrow log and blank space](audit-evidence/ingestion-expanded-1280.png).

### SA-22 · P2 · Mobile upload form fails to reflow and clips its primary action

**Type:** Confirmed responsive defect.  
**Reproduce:** At 390 × 844, Arihant → Upload source documents.

Question and optional answer/solution panels remain in two narrow columns. Labels wrap across many lines and native file controls truncate their “No file chosen” text. The explanatory text occupies a narrow left column while the upload action remains alongside it. The button's right edge measures approximately **417 px** in a **390 px viewport**; the document's scroll width is also 417 px. The button label visibly clips beyond the right screen edge.

**Impact:** Users cannot see the full primary action or easily distinguish/verify file selection. The page gains horizontal scrolling even when the document rows themselves fit.

**Fix:** Stack both file panels and place a full-width upload CTA below the helper text at narrow widths. Allow file controls to fit their container; show selected file names in an explicit list. Keep required versus optional source roles clear.

**Acceptance:** At 390 px, document scroll width does not exceed viewport width. Full button labels, file selections and required/optional distinctions are visible without horizontal scrolling.

**Evidence:** [Narrow upload panels](audit-evidence/upload-390-overflow.png), [clipped upload CTA](audit-evidence/upload-button-390.png).

### SA-23 · P2 · Document review mounts an oversized scrolling work surface

**Type:** Measured UI density; potential performance contributor, not a proven performance root cause.  
**Reproduce:** At 1280 × 800, Arihant → OP Tandon Organic → Review → All 179.

The review dialog contains **179 question article elements and 366 buttons**, with a measured scroll height of approximately **97,996 px** in an 800 px-high dialog. Question content and mathematical rendering are present throughout this long surface. The parent bank view also remains in the DOM. Browser warnings about LaTeX input were observed in the first pass, but they do not prove that those warnings cause the reported jank.

**Impact:** Navigation requires substantial scrolling, especially before reaching filters beneath Run issues. Large mounted content is a plausible rendering/memory contributor, but this inspection did not measure frame rates, long tasks, CPU usage or network timings.

**Fix:** Paginate or virtualize the queue and render full question details only for the selected item. Keep queue navigation, filters and source context stable. Profile the actual screen before attributing delays to React, polling, math rendering or backend requests.

**Acceptance:** The initial review view mounts only the visible page/window of questions plus a small buffer; all records remain searchable. Record performance traces on a representative lower-powered laptop and verify that filter/editor interactions remain responsive with large imports.

### SA-24 · P2 · Ingestion monitoring lacks tools for finding the next task

**Type:** Observed usability gap.

The 17-document import list is grouped by subject, but the inspected interface exposes no document-name search, job-status filter, sort by recent activity/failure, collapsed subject navigation or actionable attention summary. To find a specific failed or paused import, the admin scans every row and compares narrative status text. Bank search does not filter the document list.

**Impact:** Clutter increases with each imported source. Users spend effort locating work before they can resolve it. The large upload form and bank-creation controls also remain above a monitoring task they may visit repeatedly.

**Fix:** Add document search plus explicit state filters: Needs review, Failed, Running, Paused and Published. Support sorting by last activity and issue count. Make the summary counters open the corresponding filtered list. Move upload into a clear “Add sources” action or separate tab after initial setup.

**Acceptance:** An admin can locate a named source or all failed imports directly, with the active bank and filters always visible; adding more documents does not require proportionally more manual scanning.

### SA-25 · P2 · Action hierarchy does not reflect a clear ingestion workflow

**Type:** Observed usability defect; extends SA-18.

OP Tandon Organic presents Review, Retry missing generation, Approve all generated, Publish extracted questions, Re-extract material and Delete material in the same row. Three processing/approval/publication actions share strong filled styling. On a phone these wrap over several lines; on desktop they squeeze metadata. A REVIEW badge sits immediately next to a Review button. Most of these buttons measured approximately **31–33 px high** at laptop width, leaving little room for comfortable touch interaction if the same sizing persists on touch layouts.

**Impact:** Admins must understand the whole ingestion state machine before choosing the next action. Routine review, generation recovery, bulk approval and publication compete for attention; hazardous operations are placed alongside them. This observation does not establish whether those operations have adequate confirmation dialogs.

**Fix:** Show one state-appropriate primary action, normally Review issues or Inspect progress. Put recovery operations in a secondary menu and destructive operations in a separate clearly labelled group. Use explicit publication preview/counts after review. Provide comfortably sized touch controls, targeting 44 px where practical, and preserve keyboard access and focus indication.

**Acceptance:** Each row answers “What should I do next?” without reading six actions. Routine and destructive operations are visually distinct; actions do not force metadata into an unreadable column.

## Ingestion redesign brief for the next phase

### Separate the three recurring tasks

1. **Add sources:** Required question sources, optional answer sources, subject, selected-file list, visible pairing results and a clear start action. Keep this in a dedicated panel/tab rather than permanently occupying the monitoring page.
2. **Monitor imports:** A compact searchable list with filename, subject, extraction stage, review readiness, publication state, last activity and one primary action. Summary metrics filter this list. Job details/logs open without changing every row's height.
3. **Review content:** A stable queue and one focused question editor, with the source page beside it where space permits. Filters and next/previous navigation remain accessible. On phones, use a single-column queue/detail flow.

### Proposed row hierarchy

| Area | Content |
|---|---|
| Identity | Filename, subject and source metadata |
| Extraction | Explicit job stage, successful/failed/remaining counts and last update |
| Review | Number of blocking issues and next review action |
| Publication | Draft / partially published / published, separate from job status |
| Actions | One primary action plus a secondary actions menu |
| Diagnostics | Compact issue summary linking to a bounded detail view |

Keep width allocation stable across documents with different action sets. Long filenames should wrap or truncate with full-name access; status must retain word-readable width. Prefer one main vertical scroll surface per workflow. Avoid full-height nested overlays for a task users may spend extended time performing.

### Fix order for responsiveness and clutter

1. **Immediate:** Repair the 1024 px row layout and mobile upload overflow (SA-20, SA-22). These are reproducible visual defects.
2. **Next:** Bound diagnostic expansion and simplify row actions (SA-21, SA-25). This directly reduces disruptive movement and competing controls.
3. **Then:** Add import search/filtering and reduce the mounted review surface (SA-23, SA-24), supported by browser performance profiling.
4. **Finish:** Preserve queue/scroll context; test keyboard, touch and zoom; verify that status updates do not reorder or resize rows under the user's pointer.

### Validation targets

- Test 390, 768, 1024, 1280 px and the intervals between them, with both long filenames and all available actions.
- Check upload, monitor and review surfaces at 200% text/browser zoom as a separate follow-up.
- No page-level horizontal overflow; intentionally wide source/math viewers must have clearly contained scrolling.
- Expanded diagnostics must not push primary actions several screens away.
- Keep question filters and next/previous controls accessible during long review sessions.
- Measure pointer-to-visible-feedback and filter/editor responsiveness on realistic data. A useful product target is feedback within 100–200 ms, with a clear pending state for slower work; these are proposed targets, not measured current timings.
- Capture traces for dropped frames, long tasks, memory growth and repeated math rendering before selecting performance optimizations.
- Exercise real job updates in staging to verify progress stability. Production ingestion was not started or resumed during this pass, so polling-driven jumps and completion transitions remain untested.

**Scope of the conclusion:** The reported clutter and disruptive movement are reproducible through layout and disclosure interactions. A specific animation, JavaScript, rendering or backend performance cause has not been proven. The recommended next phase should address the demonstrated layout defects and profile any remaining lag.
