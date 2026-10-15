"""Track publication independently for each bank import material.

Revision ID: 0012_material_publication
Revises: 0011_admin_question_bank_imports
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_material_publication"
down_revision = "0011_admin_question_bank_imports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("import_jobs", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    # Existing published banks must retain their already-live materials. New
    # imports begin unpublished and require explicit confirmation.
    op.execute(
        "UPDATE import_jobs SET published_at = CURRENT_TIMESTAMP "
        "WHERE bank_id IN (SELECT id FROM question_banks WHERE status = 'published')"
    )


def downgrade():
    op.drop_column("import_jobs", "published_at")
