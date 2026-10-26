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
    inspector = sa.inspect(op.get_bind())
    bank_columns = {column["name"] for column in inspector.get_columns("question_banks")}
    job_columns = {column["name"] for column in inspector.get_columns("import_jobs")}
    if "status" not in bank_columns:
        op.add_column("question_banks", sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"))
    if "revision" not in bank_columns:
        op.add_column("question_banks", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    if "bank_id" not in job_columns:
        op.add_column("import_jobs", sa.Column("bank_id", sa.String(length=36), nullable=True))
    foreign_keys = {foreign_key.get("name") for foreign_key in inspector.get_foreign_keys("import_jobs")}
    if "fk_import_jobs_bank" not in foreign_keys:
        op.create_foreign_key("fk_import_jobs_bank", "import_jobs", "question_banks", ["bank_id"], ["id"], ondelete="CASCADE")
    indexes = {index["name"] for index in inspector.get_indexes("import_jobs")}
    if "ix_import_jobs_bank_id" not in indexes:
        op.create_index("ix_import_jobs_bank_id", "import_jobs", ["bank_id"])
    op.execute("UPDATE question_banks SET status = CASE WHEN is_global THEN 'published' ELSE 'draft' END")


def downgrade():
    op.drop_index("ix_import_jobs_bank_id", table_name="import_jobs")
    op.drop_constraint("fk_import_jobs_bank", "import_jobs", type_="foreignkey")
    op.drop_column("import_jobs", "bank_id")
    op.drop_column("question_banks", "revision")
    op.drop_column("question_banks", "status")
