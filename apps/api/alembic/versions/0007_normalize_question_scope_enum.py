"""normalize the production question scope column to its ORM enum

Revision ID: 0007_scope_enum
Revises: 0006_add_matrix_question_kind
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_scope_enum"
down_revision = "0006_add_matrix_question_kind"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return

    bind = op.get_bind()
    scope_type = bind.execute(sa.text(
        "SELECT udt_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'questions' AND column_name = 'scope'"
    )).scalar_one_or_none()
    if not scope_type or scope_type == "questionscope":
        return

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE questionscope AS ENUM ('PRIVATE', 'GLOBAL');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("ALTER TABLE questions ALTER COLUMN scope DROP DEFAULT")
    op.execute(
        "ALTER TABLE questions ALTER COLUMN scope TYPE questionscope "
        "USING upper(scope::text)::questionscope"
    )
    op.execute("ALTER TABLE questions ALTER COLUMN scope SET DEFAULT 'PRIVATE'::questionscope")


def downgrade():
    # Keep the normalized type; converting back would reintroduce the mismatch.
    pass
