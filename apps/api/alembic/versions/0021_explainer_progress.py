"""Track explainer stage details for teacher progress updates."""
from alembic import op
import sqlalchemy as sa

revision = "0021_explainer_progress"
down_revision = "0020_explainer_revisions"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("explainer_revisions", sa.Column("progress_message", sa.String(160), nullable=True))

def downgrade():
    op.drop_column("explainer_revisions", "progress_message")
