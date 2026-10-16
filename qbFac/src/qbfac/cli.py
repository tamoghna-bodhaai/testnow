"""Small dependency-free CLI for creating a release from reviewed JSON items."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .release import build_release


def main() -> None:
    parser = argparse.ArgumentParser(prog="qbfac", description="Build a validated qbFac release")
    parser.add_argument("items", type=Path, help="Directory containing reviewed item JSON files")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--assets", type=Path, help="Directory copied under assets/ in the release")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

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
