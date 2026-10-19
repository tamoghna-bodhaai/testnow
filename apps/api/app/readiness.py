"""Versioned, side-effect-free content validation shared by all write/release seams.

No stored approval is changed by reading this module. Drafts may be incomplete;
approval/publication and new assessment selection require the same result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
import math
from typing import Any

from fastapi import HTTPException
from .figures import is_visual_issue, option_media_issues, references_source_figure

VALIDATOR_VERSION = "readiness-v1"
CHOICE_KINDS = {"single_choice", "multiple_choice", "assertion_reason"}
KINDS = CHOICE_KINDS | {"numerical", "integer", "matrix_match", "subjective"}
RECOMPUTED_ISSUES = {"missing_answer", "missing_solution", "choice_options_missing", "taxonomy_uncertain", "low_confidence", "invalid_latex", "solution_generation_failed"}


def field(record: Any, name: str, default=None):
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def kind_value(kind) -> str:
    return str(getattr(kind, "value", kind))


def finite_number(value):
    if value is None or isinstance(value, (bool, dict, list)):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and math.isfinite(float(number)) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def has_usable_answer(kind, answer, options) -> bool:
    """Validate the canonical answer shape actually used by the grader."""
    kind = kind_value(kind)
    if not isinstance(answer, dict):
        return False
    if kind in {"single_choice", "assertion_reason"}:
        value = answer.get("option")
        return set(answer) == {"option"} and type(value) is int and 0 <= value < len(options)
    if kind == "multiple_choice":
        values = answer.get("options")
        return (set(answer) == {"options"} and isinstance(values, list) and bool(values)
                and all(type(value) is int and 0 <= value < len(options) for value in values)
                and len(values) == len(set(values)))
    if kind in {"numerical", "integer"}:
        number = finite_number(answer.get("value"))
        return set(answer) == {"value"} and number is not None and (kind != "integer" or number == number.to_integral_value())
    if kind == "matrix_match":
        matches = answer.get("matches")
        return (set(answer) == {"matches"} and isinstance(matches, dict) and bool(matches)
                and all(isinstance(row, str) and bool(row.strip()) and isinstance(choices, list) and bool(choices)
                        and all(isinstance(choice, str) and bool(choice.strip()) for choice in choices)
                        and len(choices) == len(set(choices)) for row, choices in matches.items()))
    return False


def placeholder(text) -> bool:
    value = re.sub(r"[*_`#]", "", str(text or "")).strip().lower().strip(" .:;!?()[]")
    return (not value or value in {"n/a", "na", "none", "null", "unknown", "tbd", "todo", "placeholder", "not provided", "not available", "correct", "incorrect", "answer", "solution", "?", "...", "…", "{}", "[]"}
            or bool(re.fullmatch(r"(?:option\s*)?[a-h]", value)))


@dataclass(frozen=True)
class Reason:
    code: str
    field: str
    message: str


@dataclass
class Readiness:
    validator_version: str
    content_valid: bool
    reviewed: bool
    publishable: bool
    blocking: list[Reason]
    advisory: list[Reason]

    def to_dict(self):
        return asdict(self)


def validate_question(question, *, taxonomy=None, reviewed=None) -> Readiness:
    """Use stored type/content, never a display fallback or an approval flag, as evidence."""
    blocking: list[Reason] = []
    advisory: list[Reason] = []
    def block(code, name, message):
        reason = Reason(code, name, message)
        if reason not in blocking:
            blocking.append(reason)
    def advise(code, name, message):
        advisory.append(Reason(code, name, message))

    kind = kind_value(field(question, "kind"))
    stem = str(field(question, "stem_markdown") or "").strip()
    options = field(question, "options") or []
    answer = field(question, "answer")
    interaction = field(question, "interaction") or {}
    diagrams = field(question, "diagrams") or []
    if not isinstance(interaction, dict):
        interaction = {}
    if not isinstance(options, list):
        options = []
        block("invalid_options", "options", "Options must be a list.")
    usable_diagrams = [d for d in diagrams if isinstance(d, dict) and str(d.get("object_key") or "").strip()] if isinstance(diagrams, list) else []
    if kind not in KINDS:
        block("unsupported_kind", "kind", "Choose a supported question type.")
    if placeholder(stem) or not re.search(r"[\w]", stem):
        block("missing_stem", "stem_markdown", "Enter the complete question text.")
    if re.search(r"(?:^|\n)\s*(?:here[,.:]|substituting (?:the )?values|therefore[,.:])", stem, re.I):
        advise("suspect_stem", "stem_markdown", "This may be a solution fragment. Compare the question text with its source.")
    if "$$$" in stem:
        block("invalid_latex", "stem_markdown", "Repair the malformed math delimiters in the question.")
    if kind in CHOICE_KINDS:
        if len(options) < 2:
            block("choice_options_missing", "options", "Choice questions need at least two meaningful options.")
        seen = set()
        for index, option in enumerate(options):
            text = option.get("markdown", option.get("text", "")) if isinstance(option, dict) else option if isinstance(option, str) else ""
            has_image = any(d.get("role") == "option" and type(d.get("option_index")) is int and d["option_index"] == index for d in usable_diagrams)
            if placeholder(text) and not has_image:
                block("placeholder_option", f"options.{index}", f"Replace placeholder option {chr(65 + index)} with its content or figure.")
            normalized = re.sub(r"\s+", " ", str(text)).strip().casefold()
            if normalized and not has_image:
                if normalized in seen:
                    block("duplicate_option", f"options.{index}", f"Option {chr(65 + index)} duplicates another option.")
                seen.add(normalized)
    if kind in KINDS - {"subjective"} and not has_usable_answer(kind, answer, options):
        block("invalid_answer", "answer", "Save a valid answer matching this question type and its available options.")
    if kind in {"numerical", "integer"}:
        tolerance = finite_number(interaction.get("tolerance", 0 if kind == "integer" else "0.000001"))
        if tolerance is None or tolerance < 0:
            block("invalid_tolerance", "interaction.tolerance", "Numerical tolerance must be a finite, nonnegative number.")
    if kind == "matrix_match":
        rows, choices = interaction.get("matrix_items"), interaction.get("matrix_choices")
        valid_contract = all(isinstance(values, list) and bool(values)
                             and all(isinstance(v, str) and bool(v.strip()) for v in values)
                             and len(values) == len(set(values)) for values in (rows, choices))
        matches = answer.get("matches") if isinstance(answer, dict) else None
        valid_matches = valid_contract and has_usable_answer(kind, answer, options)
        if valid_matches:
            valid_matches = set(matches) == set(rows) and all(set(values).issubset(set(choices)) for values in matches.values())
        if not valid_matches:
            block("invalid_matrix", "interaction", "Define every matrix row and column and map every row to valid columns.")
    option_visual_prompt = bool(re.search(r"\bwhich\s+(?:of\s+the\s+(?:following|given)\s+)?(?:diagram|figure|graph)s?\b", stem, re.I))
    if ((references_source_figure(stem) and not option_visual_prompt and not any(d.get("role", "question") == "question" for d in usable_diagrams))
            or (interaction.get("visual_intent") == "required" and not usable_diagrams)):
        block("required_figure_missing", "diagrams", "Attach and verify the required question figure against its source.")
    for issue in option_media_issues(options, usable_diagrams):
        block(str(issue), "diagrams", "Attach and verify the required option figures and their ownership.")
    for issue in interaction.get("ingestion_issues") or []:
        issue = str(issue)
        if is_visual_issue(issue):
            block(issue, "diagrams", "Review and save the flagged figure against its source.")
        elif issue not in RECOMPUTED_ISSUES:
            block(issue, "interaction.ingestion_issues", "Review this extraction finding: " + issue.replace("_", " ") + ".")
    solution = str(field(question, "solution_markdown") or "").strip()
    if not solution or placeholder(solution):
        if kind == "subjective":
            block("missing_model_solution", "solution_markdown", "Subjective questions require a model solution for manual evaluation.")
        else:
            advise("missing_solution", "solution_markdown", "A worked solution is recommended; a valid objective answer is sufficient.")
    elif len(solution) < 8 or "$$$" in solution or re.match(r"^(?:here[,.:]|and\b|substituting (?:the )?values)", solution, re.I):
        advise("suspect_solution", "solution_markdown", "Check this short or malformed solution against the source.")
    generation = interaction.get("solution_generation") or {}
    if isinstance(generation, dict) and generation.get("status") == "failed":
        advise("generation_failed", "interaction.solution_generation", "Generation failed. Verify existing content; the saved answer key is retained.")
    if taxonomy is not None:
        for classification in ("subject", "chapter", "topic"):
            if not any(node.get("kind") == classification and str(node.get("name") or "").strip()
                       and not str(node["name"]).lower().startswith(("unmapped", "unclassified")) for node in taxonomy):
                advise("missing_" + classification, "taxonomy", f"Assign a {classification}; classification is advisory in {VALIDATOR_VERSION}.")
    reviewed = bool(field(question, "approved_at")) if reviewed is None else bool(reviewed)
    return Readiness(VALIDATOR_VERSION, not blocking, reviewed, reviewed and not blocking, blocking, advisory)


def require_content_valid(question) -> Readiness:
    result = validate_question(question)
    if not result.content_valid:
        raise HTTPException(422, {"message": "Question is not ready: " + " ".join(r.message for r in result.blocking), "readiness": result.to_dict()})
    return result


def record_readiness(question):
    """Audit metadata only; never trust this cached value for an enforcement decision."""
    result = validate_question(question)
    question.interaction = {**(question.interaction or {}), "readiness": result.to_dict()}
    return result


def ready_query(query):
    """Read-through enforcement for legacy rows until reconciliation is approved.

    Stream validation, retaining only IDs of blocked rows; SQL pagination and
    counts still operate on the same eligible set. No historical flags are mutated.
    """
    blocked = [question.id for question in query.yield_per(250) if not validate_question(question).content_valid]
    # Avoid backend parameter limits for a large legacy bank.
    from .models import Question
    for start in range(0, len(blocked), 500):
        query = query.filter(Question.id.notin_(blocked[start:start + 500]))
    return query
