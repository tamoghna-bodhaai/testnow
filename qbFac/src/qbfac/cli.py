"""Small dependency-free CLI for creating a release from reviewed JSON items."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .release import build_release
from .intake import inspect_source_archive
from .extraction import extract_jee_pdf


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
    args = parser.parse_args()

    if args.command == "inspect":
        print(json.dumps(inspect_source_archive(args.archive), indent=2))
        return
    if args.command == "extract-jee-pdf":
        print(json.dumps(extract_jee_pdf(args.source, args.output), indent=2))
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
