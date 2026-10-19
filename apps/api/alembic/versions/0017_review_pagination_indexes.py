"""Index stable bank and document review ordering without rewriting content.

Revision ID: 0017_review_pagination_indexes
Revises: 0016_taxonomy_aliases
"""
from alembic import op
revision='0017_review_pagination_indexes'
down_revision='0016_taxonomy_aliases'
branch_labels=None
depends_on=None

def upgrade():
    op.create_index('ix_questions_bank_review','questions',['bank_id','created_at','id'])
    op.create_index('ix_questions_import_review','questions',['import_job_id','created_at','id'])

def downgrade():
    op.drop_index('ix_questions_import_review',table_name='questions')
    op.drop_index('ix_questions_bank_review',table_name='questions')
