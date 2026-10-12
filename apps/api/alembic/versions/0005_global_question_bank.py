"""global JEE question bank and richer question interaction payloads

Revision ID: 0005_global_question_bank
Revises: 0004_purge_legacy
"""
from alembic import op
import sqlalchemy as sa

revision="0005_global_question_bank"
down_revision="0004_purge_legacy"
branch_labels=None
depends_on=None

def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE questionkind ADD VALUE IF NOT EXISTS 'MATRIX'")
    op.create_table("question_banks", sa.Column("id",sa.String(36),primary_key=True), sa.Column("slug",sa.String(80),nullable=False,unique=True), sa.Column("name",sa.String(160),nullable=False), sa.Column("exam_code",sa.String(80),nullable=False), sa.Column("is_global",sa.Boolean(),nullable=False,server_default=sa.true()), sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False), sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_index("ix_question_banks_exam_code","question_banks",["exam_code"])
    op.create_table("taxonomy_nodes", sa.Column("id",sa.String(36),primary_key=True), sa.Column("bank_id",sa.String(36),sa.ForeignKey("question_banks.id",ondelete="CASCADE"),nullable=False), sa.Column("parent_id",sa.String(36),sa.ForeignKey("taxonomy_nodes.id",ondelete="CASCADE")), sa.Column("kind",sa.String(32),nullable=False), sa.Column("name",sa.String(200),nullable=False), sa.Column("code",sa.String(120)), sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False), sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False), sa.UniqueConstraint("bank_id","parent_id","kind","name",name="uq_taxonomy_node_path"))
    op.create_index("ix_taxonomy_nodes_bank_id","taxonomy_nodes",["bank_id"]);op.create_index("ix_taxonomy_nodes_parent_id","taxonomy_nodes",["parent_id"])
    op.add_column("questions",sa.Column("bank_id",sa.String(36),sa.ForeignKey("question_banks.id")))
    op.add_column("questions",sa.Column("scope",sa.String(16),nullable=False,server_default="private"))
    op.add_column("questions",sa.Column("interaction",sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
    op.create_index("ix_questions_bank_id","questions",["bank_id"])
    op.create_table("question_taxonomies",sa.Column("question_id",sa.String(36),sa.ForeignKey("questions.id",ondelete="CASCADE"),primary_key=True),sa.Column("taxonomy_node_id",sa.String(36),sa.ForeignKey("taxonomy_nodes.id",ondelete="CASCADE"),primary_key=True))

def downgrade():
    op.drop_table("question_taxonomies");op.drop_index("ix_questions_bank_id",table_name="questions");op.drop_column("questions","interaction");op.drop_column("questions","scope");op.drop_column("questions","bank_id");op.drop_table("taxonomy_nodes");op.drop_table("question_banks")
