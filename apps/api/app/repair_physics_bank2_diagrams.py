"""Stage Physics Bank 2 repairs using the shared figure validator.

Dry-run counts proposals and review issues. Applying writes new immutable
assets and stages questions for review; text, options, answers, solution
figures, and earlier test-version assets are preserved.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

import fitz

from .db import SessionLocal
from .models import Question, QuestionBank, QuestionScope, SourceDocument, User
from .storage import get
from .figures import image_regions, vector_regions
from .figure_repairs import propose_repair, repair_needed, stage_repair


BANK_SLUG = "physics-bank-2"
def repair_source(db, bank: QuestionBank, source: SourceDocument, dry_run: bool) -> dict[str, int]:
    document = fitz.open(stream=get(source.object_key), filetype="pdf")
    try:
        questions = [
            question for question in db.query(Question).filter_by(bank_id=bank.id, scope=QuestionScope.GLOBAL)
            if (question.interaction or {}).get("source_document_id") == source.id
        ]
        stats = defaultdict(int)
        regions = image_regions(document) + vector_regions(document)
        for question in questions:
            stats["questions"] += 1
            figures, issues = propose_repair(question, document, regions)
            stats["question_diagrams_proposed"] += len(figures)
            stats["questions_needing_review"] += bool(issues)
            if not dry_run and repair_needed(question,figures,issues):
                stage_repair(db, question, figures, issues)
        if not dry_run:
            db.commit()
        return dict(stats)
    finally:
        document.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair Physics Bank 2 question diagrams from native PDF vector regions")
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--bank-slug", default=BANK_SLUG)
    parser.add_argument("--source", action="append", help="Optional exact source filename; may be repeated")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(email=args.owner_email.lower()).first():
            raise SystemExit("Repair owner does not exist")
        bank = db.query(QuestionBank).filter_by(slug=args.bank_slug, is_global=True).one_or_none()
        if not bank:
            raise SystemExit(f"Global bank {args.bank_slug!r} does not exist")
        sources = db.query(SourceDocument).filter(SourceDocument.object_key.like(f"seed-sources/{bank.id}/%")).order_by(SourceDocument.filename).all()
        if args.source:
            selected = set(args.source)
            sources = [source for source in sources if source.filename in selected]
        total: dict[str, int] = defaultdict(int)
        for source in sources:
            result = repair_source(db, bank, source, args.dry_run)
            print(f"{source.filename}: {result}", flush=True)
            for key, value in result.items():
                total[key] += value
        print(f"TOTAL: {dict(total)}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
