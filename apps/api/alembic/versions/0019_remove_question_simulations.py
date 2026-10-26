"""Remove persisted question simulations from existing databases.

Revision ID: 0019_remove_question_simulations
Revises: 0018_test_question_simulations
"""
from alembic import op
from sqlalchemy import inspect

revision = '0019_remove_question_simulations'
down_revision = '0018_test_question_simulations'
branch_labels = None
depends_on = None


def upgrade():
    if inspect(op.get_bind()).has_table('test_question_simulations'):
        op.drop_table('test_question_simulations')


def downgrade():
    pass
