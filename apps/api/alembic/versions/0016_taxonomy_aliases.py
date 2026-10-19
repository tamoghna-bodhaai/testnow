"""Add an alias target without repurposing existing taxonomy identifiers/codes.

Revision ID: 0016_taxonomy_aliases
Revises: 0015_keyed_answers_optional_solutions
"""
from alembic import op
import sqlalchemy as sa
revision='0016_taxonomy_aliases'
down_revision='0015_keyed_answers_optional_solutions'
branch_labels=None
depends_on=None

def upgrade():
    with op.batch_alter_table('taxonomy_nodes') as batch:
        batch.add_column(sa.Column('alias_of_id',sa.String(36),nullable=True))
        batch.create_foreign_key('fk_taxonomy_alias_target','taxonomy_nodes',['alias_of_id'],['id'])
        batch.create_index('ix_taxonomy_nodes_alias_of_id',['alias_of_id'])

def downgrade():
    with op.batch_alter_table('taxonomy_nodes') as batch:
        batch.drop_index('ix_taxonomy_nodes_alias_of_id')
        batch.drop_constraint('fk_taxonomy_alias_target',type_='foreignkey')
        batch.drop_column('alias_of_id')
