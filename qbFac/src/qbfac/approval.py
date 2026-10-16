"""Promote reviewed extraction candidates into an auditable CBT release.

Decisions can be produced by a reviewer UI or a source-specific automation.  A
candidate is never made publishable merely because an extractor guessed a type.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .release import build_release, canonical_json
from .schema import ItemValidationError, validate_item


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(value)
    return records


def candidate_sha256(candidate: dict[str, Any]) -> str:
    """Bind a decision to the exact extracted evidence it reviewed."""
    return hashlib.sha256(canonical_json(candidate)).hexdigest()


def prepare_decisions(candidates_path: Path, output: Path) -> int:
    """Make a machine-fillable, evidence-bound queue for every candidate."""
    candidates = _read_jsonl(candidates_path)
    if len({row.get("candidate_id") for row in candidates}) != len(candidates):
        raise ValueError("candidate IDs must be unique")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps({
                "candidate_id": candidate["candidate_id"],
                "candidate_sha256": candidate_sha256(candidate),
                "status": "pending",
                "item": None,
            }, ensure_ascii=False) + "\n")
    return len(candidates)


def promote_candidates(
    *, candidates_path: Path, decisions_path: Path, output: Path,
    report_path: Path, release_id: str, title: str,
    assets_dir: Path | None = None,
) -> dict[str, Any]:
    """Release only complete decisions; report every rejected or untouched row."""
    candidates = _read_jsonl(candidates_path)
    decisions = _read_jsonl(decisions_path)
    by_id: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        identifier = candidate.get("candidate_id")
        if not isinstance(identifier, str) or not identifier or identifier in by_id:
            raise ValueError(f"missing or duplicate candidate_id: {identifier!r}")
        by_id[identifier] = candidate

    seen: set[str] = set()
    approved: list[dict[str, Any]] = []
    outcomes: list[dict[str, str]] = []
    for decision in decisions:
        identifier = decision.get("candidate_id")
        if not isinstance(identifier, str) or identifier not in by_id:
            raise ValueError(f"decision references unknown candidate_id: {identifier!r}")
        if identifier in seen:
            raise ValueError(f"duplicate decision for candidate_id: {identifier}")
        seen.add(identifier)
        candidate = by_id[identifier]
        if decision.get("candidate_sha256") != candidate_sha256(candidate):
            outcomes.append({"candidate_id": identifier, "status": "rejected", "reason": "source evidence changed"})
            continue
        if decision.get("status") != "approved":
            outcomes.append({"candidate_id": identifier, "status": "rejected", "reason": "decision is not approved"})
            continue
        item = decision.get("item")
        if not isinstance(item, dict):
            outcomes.append({"candidate_id": identifier, "status": "rejected", "reason": "complete canonical item is required"})
            continue
        provenance = item.get("provenance")
        quality = item.get("quality")
        spans = provenance.get("spans") if isinstance(provenance, dict) else None
        span_roles = {span.get("role") for span in spans if isinstance(span, dict)} if isinstance(spans, list) else set()
        if not isinstance(provenance, dict) or provenance.get("candidate_id") != identifier:
            reason = "item provenance must reference candidate_id"
        elif provenance.get("source_id") != candidate.get("provenance", {}).get("source_file"):
            reason = "item source_id differs from candidate source"
        elif provenance.get("printed_number") != candidate.get("printed_number"):
            reason = "printed number differs from candidate evidence"
        elif not {"question", "answer", "solution"} <= span_roles:
            reason = "provenance requires question, answer and solution source spans"
        elif not isinstance(quality, dict) or not isinstance(quality.get("approved_by"), str) or not quality["approved_by"].strip():
            reason = "approval requires approved_by"
        elif not isinstance(quality.get("checks"), list) or not all(isinstance(check, str) for check in quality["checks"]) or not {"structure", "answer", "solution", "taxonomy", "visual"} <= set(quality["checks"]):
            reason = "approval requires structure, answer, solution, taxonomy and visual checks"
        else:
            try:
                validate_item(item)
                reason = ""
            except (ItemValidationError, TypeError, ValueError) as exc:
                reason = str(exc)
        if reason:
            outcomes.append({"candidate_id": identifier, "status": "rejected", "reason": reason})
            continue
        approved.append(item)
        outcomes.append({"candidate_id": identifier, "status": "approved", "item_id": item["id"]})

    for identifier in by_id.keys() - seen:
        outcomes.append({"candidate_id": identifier, "status": "pending", "reason": "no decision"})
    counts = Counter(row["status"] for row in outcomes)
    report: dict[str, Any] = {
        "source_candidates": len(candidates), "approved": counts["approved"],
        "rejected": counts["rejected"], "pending": counts["pending"],
        "outcomes": sorted(outcomes, key=lambda row: row["candidate_id"]),
    }
    assets = {}
    if assets_dir:
        for path in assets_dir.rglob("*"):
            if path.is_file():
                assets[f"assets/{path.relative_to(assets_dir).as_posix()}"] = path.read_bytes()
    if approved:
        report["release"] = build_release(
            release_id=release_id, title=title, items=approved, output=output, assets=assets
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
