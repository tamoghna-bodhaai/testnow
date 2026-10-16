"""Canonical item validation for portable qbFac releases.

The release schema intentionally separates a question's presentation, response
contract, scoring, provenance, and quality decision.  Extractors may keep
partial candidates in their own stores, but only approved canonical items can
cross this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = "1.0"
SUPPORTED_KINDS = {
    "single_choice",
    "multiple_select",
    "numerical",
    "integer",
    "matching_list",
    "assertion_reason",
    "passage",
}
PROFILE_KINDS = {
    "jee-main-paper-1": {"single_choice", "numerical", "integer"},
    "jee-advanced": {"single_choice", "multiple_select", "numerical", "integer", "matching_list"},
    # Allows archival candidates whose target exam has not yet been assigned.
    "qbfac-generic": SUPPORTED_KINDS,
}


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


class ItemValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{issue.path}: {issue.message}" for issue in issues))


def _is_content(value: Any) -> bool:
    return isinstance(value, dict) and value.get("format") in {"markdown", "html", "asset"} and isinstance(value.get("value"), str) and bool(value["value"].strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_item(item: dict[str, Any]) -> None:
    """Raise ``ItemValidationError`` unless an item is safe to release."""
    issues: list[ValidationIssue] = []
    for field in ("id", "revision", "exam_profiles", "taxonomy", "interaction", "content", "answer", "solution", "scoring", "provenance", "quality"):
        if field not in item:
            issues.append(ValidationIssue(field, "is required"))
    if issues:
        raise ItemValidationError(issues)

    if not isinstance(item["id"], str) or not item["id"].strip():
        issues.append(ValidationIssue("id", "must be a non-empty stable string"))
    if not isinstance(item["revision"], int) or item["revision"] < 1:
        issues.append(ValidationIssue("revision", "must be a positive integer"))
    if not isinstance(item["exam_profiles"], list) or not item["exam_profiles"] or not all(isinstance(value, str) and value for value in item["exam_profiles"]):
        issues.append(ValidationIssue("exam_profiles", "must list at least one target profile"))
    elif unknown_profiles := sorted(set(item["exam_profiles"]) - set(PROFILE_KINDS)):
        issues.append(ValidationIssue("exam_profiles", f"contains unsupported profile(s): {', '.join(unknown_profiles)}"))
    if not isinstance(item["taxonomy"], dict) or not all(isinstance(item["taxonomy"].get(key), str) and item["taxonomy"][key].strip() for key in ("subject", "chapter", "topic")):
        issues.append(ValidationIssue("taxonomy", "must contain subject, chapter, and topic"))

    content = item["content"]
    if not isinstance(content, dict) or not _is_content(content.get("stem")):
        issues.append(ValidationIssue("content.stem", "must be a non-empty markdown, html, or asset content block"))
    if not _is_content(item["solution"]):
        issues.append(ValidationIssue("solution", "must be a non-empty markdown, html, or asset content block"))

    interaction = item["interaction"]
    kind = interaction.get("kind") if isinstance(interaction, dict) else None
    if kind not in SUPPORTED_KINDS:
        issues.append(ValidationIssue("interaction.kind", f"must be one of {sorted(SUPPORTED_KINDS)}"))
    else:
        _validate_response_contract(kind, interaction, item["answer"], issues)
        for profile in item["exam_profiles"]:
            if profile in PROFILE_KINDS and kind not in PROFILE_KINDS[profile]:
                issues.append(ValidationIssue("interaction.kind", f"{kind} is not supported by {profile}"))

    scoring = item["scoring"]
    if not isinstance(scoring, dict) or scoring.get("type") not in {"exact", "partial", "manual"}:
        issues.append(ValidationIssue("scoring.type", "must be exact, partial, or manual"))
    elif scoring["type"] != "manual":
        for name in ("correct", "incorrect", "unanswered"):
            if not _is_number(scoring.get(name)):
                issues.append(ValidationIssue(f"scoring.{name}", "must be numeric"))
        if scoring["type"] == "partial":
            rules = scoring.get("rules")
            if not isinstance(rules, list) or not rules or not all(isinstance(rule, dict) and isinstance(rule.get("selected"), list) and _is_number(rule.get("score")) for rule in rules):
                issues.append(ValidationIssue("scoring.rules", "partial scoring requires selected-option rules with numeric scores"))
            elif kind != "multiple_select":
                issues.append(ValidationIssue("scoring.type", "partial scoring is currently supported only for multiple_select"))
            else:
                option_ids = {option.get("id") for option in interaction.get("options", []) if isinstance(option, dict)}
                patterns = [tuple(sorted(rule["selected"])) for rule in rules]
                if any(not pattern or not set(pattern) <= option_ids for pattern in patterns) or len(set(patterns)) != len(patterns):
                    issues.append(ValidationIssue("scoring.rules", "must use unique, declared non-empty option selections"))

    provenance = item["provenance"]
    if not isinstance(provenance, dict) or not isinstance(provenance.get("source_id"), str) or not provenance["source_id"]:
        issues.append(ValidationIssue("provenance.source_id", "is required for auditability"))
    if not isinstance(provenance.get("spans"), list) or not provenance["spans"]:
        issues.append(ValidationIssue("provenance.spans", "must retain at least one source span"))

    quality = item["quality"]
    if not isinstance(quality, dict) or quality.get("status") != "approved":
        issues.append(ValidationIssue("quality.status", "must be approved before release"))
    if issues:
        raise ItemValidationError(issues)


def _validate_response_contract(kind: str, interaction: dict[str, Any], answer: Any, issues: list[ValidationIssue]) -> None:
    options = interaction.get("options")
    if kind in {"single_choice", "multiple_select", "assertion_reason"}:
        if not isinstance(options, list) or len(options) < 2 or not all(isinstance(option, dict) and isinstance(option.get("id"), str) and _is_content(option.get("content")) for option in options):
            issues.append(ValidationIssue("interaction.options", "must contain at least two identified content options"))
            return
        option_ids = [option["id"] for option in options]
        if any(not option_id.strip() for option_id in option_ids) or len(set(option_ids)) != len(option_ids):
            issues.append(ValidationIssue("interaction.options", "option IDs must be non-empty and unique"))
        selected = answer.get("selected") if isinstance(answer, dict) else None
        ids = {option["id"] for option in options}
        if not isinstance(selected, list) or not selected or not set(selected) <= ids:
            issues.append(ValidationIssue("answer.selected", "must reference declared options"))
        elif kind in {"single_choice", "assertion_reason"} and len(selected) != 1:
            issues.append(ValidationIssue("answer.selected", "must contain exactly one option for this interaction"))
    elif kind in {"numerical", "integer"}:
        if not isinstance(answer, dict) or not _is_number(answer.get("value")):
            issues.append(ValidationIssue("answer.value", "must be a numeric canonical answer"))
        if kind == "integer" and isinstance(answer, dict) and _is_number(answer.get("value")) and int(answer["value"]) != answer["value"]:
            issues.append(ValidationIssue("answer.value", "must be an integer"))
    elif kind == "matching_list":
        rows, columns = interaction.get("rows"), interaction.get("columns")
        valid_parts = lambda parts: isinstance(parts, list) and parts and all(isinstance(part, dict) and isinstance(part.get("id"), str) and part["id"].strip() and _is_content(part.get("content")) for part in parts)
        if not valid_parts(rows) or not valid_parts(columns):
            issues.append(ValidationIssue("interaction", "must contain non-empty identified rows and columns with content"))
        elif len({part["id"] for part in rows}) != len(rows) or len({part["id"] for part in columns}) != len(columns):
            issues.append(ValidationIssue("interaction", "row and column IDs must be unique"))
        if not isinstance(answer, dict) or not isinstance(answer.get("matches"), dict):
            issues.append(ValidationIssue("answer.matches", "must define matching answers"))
        elif valid_parts(rows) and valid_parts(columns):
            row_ids, column_ids = {row["id"] for row in rows}, {column["id"] for column in columns}
            if set(answer["matches"]) != row_ids or any(not isinstance(selected, list) or not selected or not set(selected) <= column_ids for selected in answer["matches"].values()):
                issues.append(ValidationIssue("answer.matches", "must map every declared row to declared columns"))
    elif kind == "passage":
        children = interaction.get("children")
        if not isinstance(children, list) or not children:
            issues.append(ValidationIssue("interaction.children", "must contain child item IDs"))

