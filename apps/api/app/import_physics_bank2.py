"""Import the supplied chapter-wise Physics PDFs into Physics Bank 2.

The command is safe to retry: the underlying seeder uses the source checksum,
page, and printed question number as a stable identity. It never writes to the
legacy Question Bank 1.

Example:
  python -m app.import_physics_bank2 --owner-email utils@bodhaai.tech /imports/*.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .db import SessionLocal
from .models import QuestionBank, User
from .security import uid
from .seed_jee_openrouter import main as seed_main

LEGACY_BANK_SLUG = "jee-advanced"
PHYSICS_BANK_SLUG = "physics-bank-2"
PHYSICS_BANK_NAME = "Physics Bank 2"


def ensure_banks(owner_email: str) -> None:
    """Name the existing bank and create/update the new bank exactly once."""
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(email=owner_email.lower()).first():
            raise SystemExit("Seed owner does not exist")
        legacy = db.query(QuestionBank).filter_by(slug=LEGACY_BANK_SLUG).one_or_none()
        if legacy:
            legacy.name = "Question Bank 1"
            legacy.exam_code = "JEE"
            legacy.is_global = True
        bank = db.query(QuestionBank).filter_by(slug=PHYSICS_BANK_SLUG).one_or_none()
        if not bank:
            db.add(QuestionBank(id=uid(), slug=PHYSICS_BANK_SLUG, name=PHYSICS_BANK_NAME, exam_code="JEE", is_global=True))
        else:
            bank.name = PHYSICS_BANK_NAME
            bank.exam_code = "JEE"
            bank.is_global = True
        db.commit()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import all chapter-wise Physics practice PDFs into Physics Bank 2")
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pages-per-request", type=int, default=2)
    parser.add_argument("--publish-threshold", type=float, default=0.0)
    args = parser.parse_args()
    paths = sorted(path for path in args.pdfs if path.suffix.lower() == ".pdf")
    if len(paths) != 23:
        raise SystemExit(f"Expected the 23 supplied Physics PDFs, received {len(paths)} readable PDF(s)")
    if not args.dry_run:
        ensure_banks(args.owner_email)
    sys.argv = [
        "seed_jee_openrouter", "--owner-email", args.owner_email,
        "--bank-slug", PHYSICS_BANK_SLUG, "--bank-name", PHYSICS_BANK_NAME,
        "--exam-code", "JEE", "--publish-without-solution",
        "--pages-per-request", str(args.pages_per_request),
        "--publish-threshold", str(args.publish_threshold),
    ] + (["--dry-run"] if args.dry_run else []) + [str(path) for path in paths]
    seed_main()


if __name__ == "__main__":
    main()
