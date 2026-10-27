"""Transactional job dispatch and execution generations."""
from alembic import op
import sqlalchemy as sa

revision = "0022_durable_jobs"
down_revision = "0021_explainer_progress"
branch_labels = None
depends_on = None


def upgrade():
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
    op.create_index("ix_durable_jobs_queue_status_available", "durable_jobs", ["queue", "status", "available_at"])


def downgrade():
    op.drop_table("durable_jobs")
