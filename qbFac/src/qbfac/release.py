"""Build immutable qbFac content releases without touching an exam database."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema import SCHEMA_VERSION, validate_item


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _asset_references(value: Any) -> set[str]:
    """Return paths referenced by content blocks, regardless of nesting."""
    if isinstance(value, dict):
        here = {value["value"]} if value.get("format") == "asset" and isinstance(value.get("value"), str) else set()
        return here | set().union(*(_asset_references(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_asset_references(child) for child in value)) if value else set()
    return set()


def build_release(*, release_id: str, title: str, items: Iterable[dict[str, Any]], output: Path, assets: Mapping[str, bytes] | None = None) -> dict[str, Any]:
    """Validate approved items and write one atomic portable release archive."""
    materialized = list(items)
    assets = dict(assets or {})
    ids: set[str] = set()
    entries: list[tuple[str, bytes]] = []
    for item in materialized:
        validate_item(item)
        if item["id"] in ids:
            raise ValueError(f"duplicate item ID in release: {item['id']}")
        ids.add(item["id"])
        entries.append((f"items/{item['id']}.json", canonical_json(item)))
    if not materialized:
        raise ValueError("a release must contain at least one approved item")

    referenced_assets = set().union(*(_asset_references(item) for item in materialized))
    missing_assets = sorted(referenced_assets - set(assets))
    if missing_assets:
        raise ValueError(f"missing assets required by items: {', '.join(missing_assets)}")
    invalid_assets = sorted(name for name, payload in assets.items() if not name.startswith("assets/") or not isinstance(payload, bytes))
    if invalid_assets:
        raise ValueError(f"assets must be byte payloads below assets/: {', '.join(invalid_assets)}")

    passage_children = {
        item["id"]: item["interaction"].get("children", [])
        for item in materialized
        if item["interaction"].get("kind") == "passage"
    }
    absent_children = sorted({child for children in passage_children.values() for child in children if child not in ids})
    if absent_children:
        raise ValueError(f"missing passage children in release: {', '.join(absent_children)}")

    digest = hashlib.sha256()
    for name, payload in sorted([*entries, *assets.items()]):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
    content_hash = digest.hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "item_count": len(entries),
        "asset_count": len(assets),
        "content_sha256": content_hash,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("manifest.json", canonical_json(manifest))
        for name, payload in entries:
            package.writestr(name, payload)
        for name, payload in sorted(assets.items()):
            package.writestr(name, payload)
    return {"release_id": release_id, "approved_items": len(entries), "content_sha256": content_hash, "output": str(output)}
