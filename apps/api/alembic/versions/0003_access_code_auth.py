"""access-code authentication and account lifecycle

Revision ID: 0003_access_code_auth
Revises: 0002_classrooms
"""
from alembic import op
import sqlalchemy as sa

revision="0003_access_code_auth"
down_revision="0002_classrooms"
branch_labels=None
depends_on=None

def upgrade():
    columns={column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    status_type=sa.Enum("PENDING_CODE","ACTIVE","SUSPENDED", name="accountstatus")
    status_type.create(op.get_bind(), checkfirst=True)
    # PostgreSQL cannot use a newly added enum value in the same transaction.
    # Commit the type change before the data migration below uses PENDING_CODE.
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE accountstatus ADD VALUE IF NOT EXISTS 'PENDING_CODE'")
    if "access_code_hash" not in columns:op.add_column("users", sa.Column("access_code_hash", sa.String(length=255), nullable=True))
    if "status" not in columns:op.add_column("users", sa.Column("status", status_type, nullable=True))
    if "access_code_issued_at" not in columns:op.add_column("users", sa.Column("access_code_issued_at", sa.DateTime(timezone=True), nullable=True))
    if "last_login_at" not in columns:op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET status = 'PENDING_CODE' WHERE status IS NULL")
    # Fresh databases receive this column via 0001 and it is already non-null.
    if "status" not in columns:op.alter_column("users", "status", nullable=False)

def downgrade():
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "access_code_issued_at")
    op.drop_column("users", "status")
    op.drop_column("users", "access_code_hash")
    sa.Enum("PENDING_CODE","ACTIVE","SUSPENDED", name="accountstatus").drop(op.get_bind(), checkfirst=True)
