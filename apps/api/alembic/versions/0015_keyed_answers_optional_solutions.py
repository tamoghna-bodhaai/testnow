"""Clear false missing-solution warnings for authoritative answer keys.

Revision ID: 0015_keyed_answers_optional
Revises: 0014_attempt_integrity
"""
from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


# Alembic's default `alembic_version.version_num` column is VARCHAR(32).
# Keep this identifier below that limit so an upgrade from 0014 can record it.
revision = "0015_keyed_answers_optional"
down_revision = "0014_attempt_integrity"
branch_labels = None
depends_on = None


def _usable(kind: str, answer: object, options: object) -> bool:
    if not isinstance(answer, dict):
        return False
    kind = {
        "SINGLE": "single_choice", "MULTIPLE": "multiple_choice", "NUMERICAL": "numerical",
        "INTEGER": "integer", "MATRIX": "matrix_match", "ASSERTION": "assertion_reason",
    }.get(kind, kind)
    option_count = len(options) if isinstance(options, list) else 0
    if kind in {"single_choice", "assertion_reason"}:
        value = answer.get("option")
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < option_count
    if kind == "multiple_choice":
        values = answer.get("options")
        return isinstance(values, list) and bool(values) and len(values) == len(set(values)) and all(
            isinstance(value, int) and not isinstance(value, bool) and 0 <= value < option_count for value in values
        )
    if kind in {"numerical", "integer"}:
        return answer.get("value") is not None and str(answer.get("value")).strip() != ""
    if kind == "matrix_match":
        return isinstance(answer.get("matches"), dict) and bool(answer["matches"])
    return False


def upgrade():
    """Repair only rows whose import has a separately stored answer-key file."""
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT q.id, q.kind, q.answer, q.options, q.interaction
        FROM questions q
        JOIN import_jobs j ON j.id = q.import_job_id
        WHERE j.answer_document_id IS NOT NULL
          AND q.answer IS NOT NULL
          AND (q.solution_markdown IS NULL OR trim(q.solution_markdown) = '')
    """)).mappings()
    questions = sa.table("questions", sa.column("id", sa.String()), sa.column("interaction", sa.JSON()))
    for row in rows:
        kind = getattr(row["kind"], "value", row["kind"])
        if not _usable(str(kind), row["answer"], row["options"]):
            continue
        interaction = dict(row["interaction"] or {})
        interaction["answer_key_authoritative"] = True
        interaction["ingestion_issues"] = [
            issue for issue in interaction.get("ingestion_issues", []) if issue != "missing_solution"
        ]
        generation = dict(interaction.get("solution_generation") or {})
        if re.search(r"solution|multi[- ]step", str(generation.get("error") or ""), re.I):
            generation.pop("error", None)
            if generation.get("status") == "failed":
                generation["status"] = "not_required"
            interaction["solution_generation"] = generation
        bind.execute(questions.update().where(questions.c.id == row["id"]).values(interaction=interaction))


def downgrade():
    # This is a metadata correction based on source provenance. Reintroducing
    # false review warnings would be destructive, so the downgrade is a no-op.
    pass
