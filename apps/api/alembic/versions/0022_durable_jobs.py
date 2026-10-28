"""Transactional job dispatch and execution generations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0022_durable_jobs"
down_revision = "0021_explainer_progress"
branch_labels = None
depends_on = None


def upgrade():
    if not inspect(op.get_bind()).has_table("durable_jobs"):
        op.create_table("durable_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("task", sa.String(120), nullable=False),
            sa.Column("queue", sa.String(32), nullable=False),
            sa.Column("resource", sa.String(160), nullable=False),
            sa.Column("args", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("error", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes("durable_jobs")}
    if "ix_durable_jobs_queue_status_available" not in indexes:
        op.create_index("ix_durable_jobs_queue_status_available", "durable_jobs", ["queue", "status", "available_at"])


def downgrade():
    op.drop_table("durable_jobs")
