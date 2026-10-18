"""Add non-destructive CBT delivery state and audit events.

Revision ID: 0013_cbt_delivery
Revises: 0012_material_publication
"""
from alembic import op
import sqlalchemy as sa

revision="0013_cbt_delivery"
down_revision="0012_material_publication"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("responses",sa.Column("visited_at",sa.DateTime(timezone=True),nullable=True))
    op.create_table("attempt_events",
        sa.Column("id",sa.String(length=36),primary_key=True),
        sa.Column("attempt_id",sa.String(length=36),sa.ForeignKey("attempts.id",ondelete="CASCADE"),nullable=False),
        sa.Column("event_type",sa.String(length=64),nullable=False),
        sa.Column("data",sa.JSON(),nullable=False,server_default=sa.text("'{}'")),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
    )
    op.create_index("ix_attempt_events_attempt_id","attempt_events",["attempt_id"])
    op.create_index("ix_attempt_events_event_type","attempt_events",["event_type"])

def downgrade():
    op.drop_index("ix_attempt_events_event_type",table_name="attempt_events")
    op.drop_index("ix_attempt_events_attempt_id",table_name="attempt_events")
    op.drop_table("attempt_events")
    op.drop_column("responses","visited_at")
