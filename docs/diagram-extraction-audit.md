# PDF and DOCX diagram extraction audit

Initial inspection: 15 October 2026. The findings below describe the implementation before repair. The subsequent fix, verification evidence, and rollout steps are documented in [diagram-extraction-fix.md](diagram-extraction-fix.md). Existing uncommitted application changes were preserved.

## Result

The current diagram extraction layer is unreliable. The observed symptoms can be reproduced without an LLM request, production database, or object-store access. PNG rendering itself works on the checked examples. Detection, coordinate validation, ownership, and publication checks need repair. The findings do not establish a problem with Celery, Redis, S3, or the database infrastructure.

## Checks performed

| Check | Observed result |
| --- | --- |
| Run current native detection on `uploads/1791527695_cengage_optics_test.pdf` | 15 candidates: 3 embedded raster images and 12 page-sized vector groups, one on every page. |
| Visually inspect page 1 vector crop | Contains the heading, instructions, two complete questions, their options, and whitespace. It is not a bounded diagram. |
| Inspect vector paths behind page 1 result | Page-sized white fills are admitted as graphics. Some paths extend far beyond the visible page. The merged candidate is `[39.75, 0, 5898.15, 842.25]` on a roughly 595-by-842-point page. Clamping at render time still produces a page-sized text crop. |
| Visually inspect page 2 embedded image crop | Mirror diagram and labels are intact. |
| Construct an in-memory 100-by-100-point square from four separately drawn connected lines | Zero detected vector regions. Each line is rejected by the individual-path area threshold before grouping. |
| Give two disjoint question spans one page-sized native candidate | The same candidate is assigned to both questions. |
| Pass a blank crop and a text-only crop through `_save_questions` with an in-memory SQLite database and mocked object uploads | Both are saved with diagrams, automatically approved, and receive no ingestion issues. |
| Pass diagram page `0` through the same save path | Accepted; Python indexes the last page. Page bounds are not validated at this boundary. |
| Convert a controlled DOCX with prose and an inline mirror diagram using the real LibreOffice `_pdf_bytes` path | One-page PDF with one raster region; visually inspected crop preserves the diagram and labels. |
| Run `PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests/test_extraction.py -q` | 17 passed. Existing tests do not cover the reproduced visual failures. |

The PDF checks used a local sample, not production question records. The DOCX check was a controlled inline-image fixture, not the user's anomalous corpus. Floating drawings, grouped shapes, charts, equations, legacy graphics, font substitution, and production DOCX conversion fidelity remain unverified.

## Defects and relevant code

1. **Page backgrounds and decoration become diagrams.** `apps/api/app/tasks.py:270`, `native_vector_regions`, accepts drawing paths based on area alone. White backgrounds, instruction boxes, borders, and off-page paths enter grouping. Large paths merge unrelated content into one region.
2. **Small components disappear before grouping.** The same function drops every path below 900 square points before assembling figures. Horizontal and vertical strokes have zero bounding-box area, so circuits, axes, rays, and polygon edges can disappear. The one-pass grouping also does not merge all existing groups when a later path bridges them.
3. **Any intersection assigns ownership.** `apps/api/app/tasks.py:297`, `diagrams_for_item`, appends an entire native region when it intersects a question span at all. There is no containment requirement, ambiguity handling, or semantic duplicate suppression. Exact tuple matching only removes identical boxes.
4. **Model geometry lacks reliable inputs.** `apps/api/app/tasks.py:201` enables vision based only on text length. `page_unit_content` supplies plain text, without word boxes or page dimensions. Text-rich pages therefore require the model to output exact PDF coordinates without seeing layout. The bank importer renders vision at 1x, the teacher importer at 1.25x, and the seed importer at 1.45x. Explicit transforms are missing. Scale confusion is also documented in the existing Physics Bank 2 repair script.
5. **Successful rendering is treated as validation.** `apps/api/app/bank_imports.py:154` and `apps/api/app/tasks.py:376` largely accept any nonempty clipped rectangle. They do not verify nonblank content, graphics evidence, question ownership, missing labels, or excessive prose. Negative/zero page numbers are not explicitly rejected. Stored boxes describe requested rather than necessarily rendered geometry. Broad exception handlers silently suppress failed candidates.
6. **Diagram quality does not gate approval.** `apps/api/app/bank_imports.py:62`, `_issues`, flags an unavailable diagram only if the model requested diagrams and none were saved. A white or text-only PNG satisfies this check. A partially failed diagram set is also not identified by that check, and native-only failures can be invisible.
7. **DOCX inherits these failures.** `apps/api/app/bank_imports.py:22`, `_pdf_bytes`, converts DOCX to a temporary PDF and uses the same detector. It verifies a PDF exists, not that all Word figures survived conversion. The converted PDF is not persisted, so later reconstruction may depend on a different LibreOffice/font environment.
8. **Historical paths have inconsistent safeguards.** The seed importer has area checks absent from normal imports, but no complete content validation. `repair_physics_bank2_diagrams.py` is restricted to a bank-specific source layout, assumes a left question margin and one-page regions, and does not generally recover nearby text labels. Ordinary diagram labels are often text objects, not drawing paths. It is not sufficient as a universal repair. The legacy `seed_jee.py` deliberately stores full-question crops; those records must be distinguished from actual figure crops during migration.

The web renderer uses `object-fit: contain`. That does not explain the reproduced text and whitespace crops; they already exist before display.

## Recommended implementation

Keep the existing worker, storage, and database architecture. Replace the duplicated figure-selection and saving logic with one shared, versioned extraction service used by teacher imports, bank imports, seeds, and repairs.

1. **Establish canonical source geometry.** Persist the exact converted PDF for DOCX imports alongside the original. Record conversion version and source hash. Use one declared coordinate space: PDF points with defined origin, rotation, and crop-box handling. Validate finite ordered boxes and `1 <= page <= page_count`. Convert any pixel or normalized model coordinates explicitly and store the final rendered box.
2. **Detect question and figure regions with layout evidence.** Build word/line boxes and column-aware question regions from the source. If native layout is insufficient, use rendered-page layout/vision. Avoid asking a text-only model to invent geometry. Supply candidate IDs and coordinates so the model can select or classify regions instead of estimating precise boxes.
3. **Separate meaningful graphics from page furniture.** Account for clipping, page backgrounds, repeated headers/footers, and decorative rules before grouping. Retain thin visible strokes; cluster connected paths transitively, then apply minimum figure-size checks. Include raster images but treat full-page scans as page content requiring figure segmentation. Do not use a blanket white-color or width exclusion that could remove meaningful figure parts or wide graphs.
4. **Recover complete figures.** Expand graphics clusters to include nearby labels, legends, and captions with layout evidence. Bound expansion by neighboring prose, options, columns, and questions. Classify tables separately where useful. Detect when a proposed crop cuts through connected graphics or text labels. Use selective vision for ambiguous groups and scanned sources; geometry alone is insufficient across all files.
5. **Assign ownership and remove duplicates.** Require strong, unambiguous spatial ownership within the correct question/column, rather than any overlap. Keep option figures tied to their option, and distinguish question and solution figures. Handle legitimate shared or multipart figures explicitly. Merge or suppress overlapping model/native detections using geometry and content identity.
6. **Validate before publishing.** Render lossless PNG in RGB at a documented resolution, then check visible ink, graphic evidence, excessive prose, whitespace, clipped components, and duplicate content. Use thresholds as review signals, not as proof that a figure is valid: sparse ray diagrams can legitimately contain substantial whitespace. Route uncertainty and missing/partially failed diagrams to review with reason codes. Keep a full source preview available separately from a figure.
7. **Make failures auditable.** Record candidate origin, detector version, canonical coordinates, validation results, and failures. Replace silent catches with per-candidate diagnostics. Diagram uncertainty must prevent automatic approval until resolved.

PyMuPDF documents both native drawing extraction and proximity clustering, which can support this implementation; neither identifies educational figures semantically by itself. See the official [drawing extraction](https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_drawings), [drawing clustering](https://pymupdf.readthedocs.io/en/latest/page.html#Page.cluster_drawings), and [rendering](https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_pixmap) APIs. Check the installed pinned version before relying on newer parameters.

## Repair existing questions

First ship the shared service and verify it on labeled examples. Then run a dry-run audit across existing records, producing source-versus-crop previews and proposed replacements. Rebuild only diagram metadata and assets from the retained original or canonical converted PDF. Preserve question IDs, question text, options, answers, test references, and solution ownership. Do not reuse suspect model source spans without locating the question again.

Write replacements under new versioned keys and retain old references for rollback. Publish verified replacements atomically per question. Quarantine ambiguous records for review rather than deleting the diagram or fabricating a crop. Existing published questions with uncertain figures need explicit review state; fixing future imports will not repair stored images.

## Acceptance checks before rollout

Use reviewed PDF/DOCX examples spanning text-only pages, page backgrounds, thin-line circuits, labeled graphs, multiple figures, two columns, option figures, shared figures, page continuations, rotated/cropped pages, scanned pages, floating DOCX shapes, and DOCX conversion failures. Include the reproduced blank/text/page-zero cases and cases with only partial figure-extraction success.

Measure false figure detections, missed figures, correct question/option ownership, and label completeness against hand-reviewed expected regions. Require no false diagrams on the known text-only fixture; require intact expected figures and labels on the reference corpus. Verify visual previews as well as automated assertions. A passing JSON/schema or PNG-decoding test alone does not establish extraction quality.
