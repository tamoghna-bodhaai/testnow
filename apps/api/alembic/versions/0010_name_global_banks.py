"""Name the legacy JEE bank and reserve the generic JEE classification.

Revision ID: 0010_name_global_banks
Revises: 0009_math_taxonomy
"""
from alembic import op

revision = "0010_name_global_banks"
down_revision = "0009_math_taxonomy"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE question_banks SET name = 'Question Bank 1', exam_code = 'JEE', is_global = true WHERE slug = 'jee-advanced'")


def downgrade():
    op.execute("UPDATE question_banks SET name = 'JEE Advanced Question Bank', exam_code = 'JEE_ADVANCED' WHERE slug = 'jee-advanced'")
