"""class-based assessment assignment

Revision ID: 0002_classrooms
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision="0002_classrooms"
down_revision="0001_initial"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("classrooms",
        sa.Column("id",sa.String(length=36),primary_key=True),
        sa.Column("owner_id",sa.String(length=36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False,index=True),
        sa.Column("name",sa.String(length=160),nullable=False),
        sa.Column("description",sa.String(length=500)),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint("owner_id","name"),
    )
    op.create_table("class_memberships",
        sa.Column("class_id",sa.String(length=36),sa.ForeignKey("classrooms.id",ondelete="CASCADE"),primary_key=True),
        sa.Column("student_id",sa.String(length=36),sa.ForeignKey("users.id",ondelete="CASCADE"),primary_key=True),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
    )
    op.create_table("test_class_assignments",
        sa.Column("test_id",sa.String(length=36),sa.ForeignKey("tests.id",ondelete="CASCADE"),primary_key=True),
        sa.Column("class_id",sa.String(length=36),sa.ForeignKey("classrooms.id",ondelete="CASCADE"),primary_key=True),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
    )

def downgrade():
    op.drop_table("test_class_assignments")
    op.drop_table("class_memberships")
    op.drop_table("classrooms")
