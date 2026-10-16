# Coverage-first ingestion contract

The inventory is qbFac's primary ingestion artifact. A CBT release is a
downstream, approved subset. Neither a large candidate count nor a unique
number match proves that a complete question has been converted.

## Interface and invariants

`inventory_source(path)` accepts a PDF, DOCX, or ZIP of these documents and
returns one evidence ledger. The same linking logic consumes normalized pages
from every format; source readers are adapters at this seam.

- Every source page has a ledger row. A page with images but no native text is
  marked `needs_ocr`, never treated as a zero-question page.
- Every recognized question, answer, and solution anchor has source provenance.
  Unrecognized numbered lines remain in `unclassified_numbered_lines`.
- A question remains in `questions` when its answer or solution is missing or
  ambiguous. Multiple possible links are never resolved by list order.
- Each source page retains its raw native text and line coordinates where
  available. The original document remains the visual authority; the ledger
  does not claim to have extracted a diagram just because native text exists.
- `linked` means only that unique answer and solution anchors were found. It
  does **not** mean full question content, mathematical notation, diagrams,
  answer correctness, or CBT rendering have passed approval.
- `question_recall_verified` remains false until evaluation against labeled
  pages from multiple source families. Page text extraction is separately
  reported by `all_pages_text_extracted`.

## Required next gates before release automation

1. Build a stratified, labeled benchmark from multiple publishers and from
   digital PDF, scan, DOCX, inline keys, appendix keys, separate solutions,
   diagrams, passages, MCQ, multiple-select, and integer questions. Keep it
   independent of the book used during development.
2. Measure anchor recall, question-boundary recall, answer-link precision,
   solution-link precision, and rendered-item correctness independently. Count
   unprocessed pages and ambiguous links as failures, not as absent items.
3. Add OCR as an evidence-producing adapter for `needs_ocr` pages. Preserve its
   text coordinates and confidence; do not silently replace native evidence.
4. Assemble complete, possibly cross-page visual question/option/solution
   regions from the ledger. Permit image-first CBT content when structured
   text or LaTeX is unreliable. Mark unresolved boundaries rather than making
   a plausible-looking but incomplete crop.
5. Only then promote validated canonical items through the existing approval
   and release contracts. The exceptional review queue is for ambiguous cases,
   not manual transcription of the corpus.

This contract deliberately separates *inventory coverage* from *CBT item
quality*. Both must be measured; optimizing one number cannot substitute for
the other.
