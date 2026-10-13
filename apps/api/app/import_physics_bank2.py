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
from tempfile import TemporaryDirectory

from .config import settings
from .db import SessionLocal
from .models import QuestionBank, User
from .security import uid
from .seed_jee_openrouter import main as seed_main
from .storage import client, get

LEGACY_BANK_SLUG = "jee-advanced"
PHYSICS_BANK_SLUG = "physics-bank-2"
PHYSICS_BANK_NAME = "Physics Bank 2"
EXPECTED_SOURCE_COUNT = 23


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


def pdf_keys(prefix: str) -> list[str]:
    """List exactly the PDF source objects under an explicitly supplied prefix."""
    normalized = prefix.strip("/") + "/"
    keys: list[str] = []
    token: str | None = None
    while True:
        params = {"Bucket": settings().s3_bucket, "Prefix": normalized}
        if token:
            params["ContinuationToken"] = token
        page = client().list_objects_v2(**params)
        keys.extend(item["Key"] for item in page.get("Contents", []) if item["Key"].lower().endswith(".pdf"))
        if not page.get("IsTruncated"):
            return sorted(keys)
        token = page.get("NextContinuationToken")


def download_sources(prefix: str, directory: Path) -> list[Path]:
    keys = pdf_keys(prefix)
    if len(keys) != EXPECTED_SOURCE_COUNT:
        raise SystemExit(f"Expected {EXPECTED_SOURCE_COUNT} PDF(s) under s3://{prefix.strip('/')}/, found {len(keys)}")
    paths: list[Path] = []
    for key in keys:
        path = directory / Path(key).name
        if path.exists():
            raise SystemExit(f"Duplicate PDF filename under s3 prefix: {path.name}")
        path.write_bytes(get(key))
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Import all chapter-wise Physics practice PDFs into Physics Bank 2")
    parser.add_argument("pdfs", nargs="*", type=Path)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--s3-prefix", help="Read the 23 source PDFs directly from this object-storage prefix")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pages-per-request", type=int, default=2)
    parser.add_argument("--publish-threshold", type=float, default=0.0)
    args = parser.parse_args()
    if bool(args.pdfs) == bool(args.s3_prefix):
        raise SystemExit("Specify either local PDF paths or --s3-prefix, not both")

    def run(paths: list[Path]) -> None:
        if len(paths) != EXPECTED_SOURCE_COUNT:
            raise SystemExit(f"Expected the {EXPECTED_SOURCE_COUNT} supplied Physics PDFs, received {len(paths)} readable PDF(s)")
        if not args.dry_run:
            ensure_banks(args.owner_email)
        sys.argv = [
            "seed_jee_openrouter", "--owner-email", args.owner_email,
            "--bank-slug", PHYSICS_BANK_SLUG, "--bank-name", PHYSICS_BANK_NAME,
            "--exam-code", "JEE", "--subject", "Physics", "--publish-without-solution",
            "--pages-per-request", str(args.pages_per_request),
            "--publish-threshold", str(args.publish_threshold),
        ] + (["--dry-run"] if args.dry_run else []) + [str(path) for path in paths]
        seed_main()

    if args.s3_prefix:
        with TemporaryDirectory(prefix="physics-bank-2-") as temporary_directory:
            run(download_sources(args.s3_prefix, Path(temporary_directory)))
    else:
        run(sorted(path for path in args.pdfs if path.suffix.lower() == ".pdf"))


if __name__ == "__main__":
    main()
