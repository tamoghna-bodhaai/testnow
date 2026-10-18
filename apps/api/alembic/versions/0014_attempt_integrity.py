"""Add server-authoritative attempt cancellation and integrity flags.

Revision ID: 0014_attempt_integrity
Revises: 0013_cbt_delivery
"""
from alembic import op
import sqlalchemy as sa

revision="0014_attempt_integrity"
down_revision="0013_cbt_delivery"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("attempts",sa.Column("cancelled_at",sa.DateTime(timezone=True),nullable=True))
    op.add_column("attempts",sa.Column("cancellation_reason",sa.String(length=64),nullable=True))
    op.add_column("attempts",sa.Column("integrity_flagged",sa.Boolean(),nullable=False,server_default=sa.false()))
    op.add_column("attempts",sa.Column("cohort_classes",sa.JSON(),nullable=False,server_default=sa.text("'[]'")))
    op.create_index("ix_attempts_integrity_flagged","attempts",["integrity_flagged"])

def downgrade():
    op.drop_index("ix_attempts_integrity_flagged",table_name="attempts")
    op.drop_column("attempts","integrity_flagged")
    op.drop_column("attempts","cohort_classes")
    op.drop_column("attempts","cancellation_reason")
    op.drop_column("attempts","cancelled_at")
