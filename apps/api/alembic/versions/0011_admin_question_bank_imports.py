"""Add staged global-bank import lifecycle fields.

Revision ID: 0011_admin_question_bank_imports
Revises: 0010_name_global_banks
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_admin_question_bank_imports"
down_revision = "0010_name_global_banks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("question_banks", sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"))
    op.add_column("question_banks", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("import_jobs", sa.Column("bank_id", sa.String(length=36), nullable=True))
    op.create_foreign_key("fk_import_jobs_bank", "import_jobs", "question_banks", ["bank_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_import_jobs_bank_id", "import_jobs", ["bank_id"])
    op.execute("UPDATE question_banks SET status = CASE WHEN is_global THEN 'published' ELSE 'draft' END")


def downgrade():
    op.drop_index("ix_import_jobs_bank_id", table_name="import_jobs")
    op.drop_constraint("fk_import_jobs_bank", "import_jobs", type_="foreignkey")
    op.drop_column("import_jobs", "bank_id")
    op.drop_column("question_banks", "revision")
    op.drop_column("question_banks", "status")
