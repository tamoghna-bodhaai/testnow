# qbFac — Question Bank Factory

qbFac converts **reviewed structured candidates** into portable, immutable
question-bank releases. It deliberately does not write to TestNow's database,
run an exam, or promise universal PDF parsing.

Its role is to be the quality boundary between messy legacy material and a CBT
application:

```text
sources → source-family extraction recipes → candidate items → validation/review
        → qbFac release ZIP → TestNow bank importer → CBT delivery
```

## What is implemented

- Canonical, versioned item schema (`1.0`), including content, interaction,
  answer, solution, scoring, taxonomy, provenance and quality status.
- JEE-oriented first-class response kinds: single choice, multiple select,
  numerical, integer, matching list, assertion-reason, and parent passages.
- Portable ZIP release builder with canonical JSON, SHA-256 content manifest,
  stable item IDs, and verified binary assets.
- Release guardrails: only approved items, valid response contracts, official
  JEE profile compatibility, mapped solutions, audit provenance, complete media,
  and package-wide integrity hashing may leave qbFac.
- A CLI for source inspection and building releases from reviewed canonical items.
- Raw source ZIP inspection for PDF and DOCX files, including deterministic
  document-role detection, scan/digital classification, text-density evidence,
  and embedded-media counts. This is the first stage of the end-to-end factory.

See [JEE profiles](docs/jee-profiles.md) and the [example item](examples/jee-main-single-choice.json).

## Local use

```bash
cd qbFac
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m qbfac.cli release examples \
  --release-id jee-main-demo-2026.1 \
  --title "JEE Main demo release" \
  --output /tmp/jee-main-demo-2026.1.zip
```

Inspect a raw corpus before any extraction or LLM work:

```bash
PYTHONPATH=src python3 -m qbfac.cli inspect /imports/physics-corpus.zip
```

## Release contract

Each package contains:

```text
manifest.json                 # schema/release ID, counts, SHA-256
items/<stable-item-id>.json   # canonical approved item revisions
assets/<content-addressed-or-stable-name>  # referenced binary media
```

`provenance` is mandatory: source ID, printed number when available, and at
least one page/region span are retained. An extractor can be wrong; the release
must make every published item traceable to evidence.

## Deliberately not included yet

The next project increments are source-family registration, PDF/DOCX/OCR
adapter interfaces, deterministic answer/solution mappers, candidate storage,
and batch validation metrics. Those components create candidates; they do not
weaken this release contract.
