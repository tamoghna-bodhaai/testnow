"""make resumable global seeding idempotent

Revision ID: 0008_seed_question_identity
Revises: 0007_scope_enum
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_seed_question_identity"
down_revision = "0007_scope_enum"
branch_labels = None
depends_on = None


def upgrade():
    inspector=sa.inspect(op.get_bind())
    columns={column["name"] for column in inspector.get_columns("questions")}
    if "source_key" not in columns:
        op.add_column("questions", sa.Column("source_key", sa.String(length=255), nullable=True))
        op.create_index("ix_questions_source_key", "questions", ["source_key"])
    constraints={constraint.get("name") for constraint in inspector.get_unique_constraints("questions")}
    if "uq_question_bank_source_key" not in constraints:
        op.create_unique_constraint("uq_question_bank_source_key", "questions", ["bank_id", "source_key"])


def downgrade():
    op.drop_constraint("uq_question_bank_source_key", "questions", type_="unique")
    op.drop_index("ix_questions_source_key", table_name="questions")
    op.drop_column("questions", "source_key")
