"""persist the SQLAlchemy enum name used for matrix match questions"""
from alembic import op

revision="0006_add_matrix_question_kind"
down_revision="0005_global_question_bank"
branch_labels=None
depends_on=None

def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE questionkind ADD VALUE IF NOT EXISTS 'MATRIX'")

def downgrade():
    # PostgreSQL enum values cannot be removed safely in place.
    pass
