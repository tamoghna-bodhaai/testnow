"""Small dependency-free CLI for creating a release from reviewed JSON items."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .release import build_release
from .intake import inspect_source_archive
from .extraction import extract_jee_pdf
from .approval import prepare_decisions, promote_candidates
from .arihant import build_arihant_release
from .inventory import write_inventory


def main() -> None:
    parser = argparse.ArgumentParser(prog="qbfac", description="Create or inspect structured question-bank material")
    subcommands = parser.add_subparsers(dest="command", required=True)
    release = subcommands.add_parser("release", help="build a release from reviewed canonical items")
    release.add_argument("items", type=Path, help="Directory containing reviewed item JSON files")
    release.add_argument("--release-id", required=True)
    release.add_argument("--title", required=True)
    release.add_argument("--assets", type=Path, help="Directory copied under assets/ in the release")
    release.add_argument("--output", required=True, type=Path)
    inspect = subcommands.add_parser("inspect", help="inspect a raw source ZIP before extraction")
    inspect.add_argument("archive", type=Path)
    extract = subcommands.add_parser("extract-jee-pdf", help="extract chapter/topic/type candidates from a structured JEE PDF")
    extract.add_argument("source", type=Path)
    extract.add_argument("--output", required=True, type=Path)
    inventory = subcommands.add_parser("inventory", help="account for question, answer, solution, and OCR gaps without publishing")
    inventory.add_argument("source", type=Path, help="PDF, DOCX, or source ZIP")
    inventory.add_argument("--output", required=True, type=Path, help="coverage ledger directory")
    prepare = subcommands.add_parser("prepare-decisions", help="create an evidence-bound review queue from extracted candidates")
    prepare.add_argument("candidates", type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    promote = subcommands.add_parser("promote", help="validate reviewed candidates and build an approved CBT release")
    promote.add_argument("candidates", type=Path)
    promote.add_argument("decisions", type=Path)
    promote.add_argument("--release-id", required=True)
    promote.add_argument("--title", required=True)
    promote.add_argument("--assets", type=Path)
    promote.add_argument("--output", required=True, type=Path)
    promote.add_argument("--report", required=True, type=Path)
    arihant = subcommands.add_parser("build-arihant", help="build visual CBT items from the Arihant JEE mathematics PDF")
    arihant.add_argument("source", type=Path)
    arihant.add_argument("--chapter")
    arihant.add_argument("--max-items", type=int)
    arihant.add_argument("--output", required=True, type=Path)
    arihant.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    if args.command == "inspect":
        print(json.dumps(inspect_source_archive(args.archive), indent=2))
        return
    if args.command == "extract-jee-pdf":
        print(json.dumps(extract_jee_pdf(args.source, args.output), indent=2))
        return
    if args.command == "inventory":
        print(json.dumps(write_inventory(args.source, args.output), indent=2))
        return
    if args.command == "prepare-decisions":
        print(json.dumps({"queued": prepare_decisions(args.candidates, args.output), "output": str(args.output)}, indent=2))
        return
    if args.command == "promote":
        print(json.dumps(promote_candidates(
            candidates_path=args.candidates, decisions_path=args.decisions,
            output=args.output, report_path=args.report, release_id=args.release_id,
            title=args.title, assets_dir=args.assets,
        ), indent=2))
        return
    if args.command == "build-arihant":
        report = build_arihant_release(args.source, args.output, args.report, chapter_filter=args.chapter, max_items=args.max_items)
        print(json.dumps({key: value for key, value in report.items() if key != "examples"}, indent=2))
        return

    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.items.glob("*.json"))]
    assets = {}
    if args.assets:
        for path in args.assets.rglob("*"):
            if path.is_file():
                assets[f"assets/{path.relative_to(args.assets).as_posix()}"] = path.read_bytes()
    report = build_release(release_id=args.release_id, title=args.title, items=records, assets=assets, output=args.output)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
