"""Add assessment-scoped explainer drafts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0020_explainer_revisions"
down_revision = "0019_remove_question_simulations"
branch_labels = None
depends_on = None

def upgrade():
    if not inspect(op.get_bind()).has_table("explainer_revisions"):
        op.create_table("explainer_revisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("test_id", sa.String(36), sa.ForeignKey("tests.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_id", sa.String(36), sa.ForeignKey("test_versions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("question_id", sa.String(36), nullable=False),
            sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("feedback", sa.Text()), sa.Column("status", sa.String(24), nullable=False),
            sa.Column("scene_spec", sa.JSON()), sa.Column("video_object_key", sa.String(512)),
            sa.Column("error", sa.Text()), sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))

    existing_indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes("explainer_revisions")}
    if "ix_explainer_revisions_test_id" not in existing_indexes:
        op.create_index("ix_explainer_revisions_test_id", "explainer_revisions", ["test_id"])
    if "ix_explainer_test_question" not in existing_indexes:
        op.create_index("ix_explainer_test_question", "explainer_revisions", ["test_id", "version_id", "question_id"])
    if "uq_explainer_published" not in existing_indexes:
        op.create_index("uq_explainer_published", "explainer_revisions", ["test_id", "version_id", "question_id"],
                        unique=True, postgresql_where=sa.text("published_at IS NOT NULL"), sqlite_where=sa.text("published_at IS NOT NULL"))

def downgrade():
    op.drop_index("uq_explainer_published", table_name="explainer_revisions")
    op.drop_index("ix_explainer_test_question", table_name="explainer_revisions")
    op.drop_index("ix_explainer_revisions_test_id", table_name="explainer_revisions")
    op.drop_table("explainer_revisions")
