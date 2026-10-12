"""purge legacy teacher and student records before access-code launch

Revision ID: 0004_purge_legacy
Revises: 0003_access_code_auth

This is intentionally irreversible. It clears data owned by historical teacher
and student accounts so the access-code launch starts with a clean roster while
the configured super-admin account is preserved.
"""
from alembic import op

revision="0004_purge_legacy"
down_revision="0003_access_code_auth"
branch_labels=None
depends_on=None

def upgrade():
    # Delete in dependency order. Each statement is limited to records owned by
    # legacy teacher/student accounts, preserving any administrator data.
    op.execute("DELETE FROM responses WHERE attempt_id IN (SELECT id FROM attempts WHERE student_id IN (SELECT id FROM users WHERE role = 'STUDENT'))")
    op.execute("DELETE FROM attempts WHERE student_id IN (SELECT id FROM users WHERE role = 'STUDENT')")
    op.execute("DELETE FROM test_class_assignments WHERE test_id IN (SELECT id FROM tests WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER'))")
    op.execute("DELETE FROM enrollments WHERE test_id IN (SELECT id FROM tests WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER')) OR student_id IN (SELECT id FROM users WHERE role = 'STUDENT')")
    op.execute("DELETE FROM test_versions WHERE test_id IN (SELECT id FROM tests WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER'))")
    op.execute("DELETE FROM tests WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER')")
    op.execute("DELETE FROM class_memberships WHERE class_id IN (SELECT id FROM classrooms WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER')) OR student_id IN (SELECT id FROM users WHERE role = 'STUDENT')")
    op.execute("DELETE FROM classrooms WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER')")
    op.execute("DELETE FROM questions WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER')")
    op.execute("DELETE FROM import_jobs WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER')")
    op.execute("DELETE FROM source_documents WHERE owner_id IN (SELECT id FROM users WHERE role = 'TEACHER')")
    op.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE role IN ('TEACHER', 'STUDENT'))")
    op.execute("DELETE FROM audit_events WHERE actor_id IN (SELECT id FROM users WHERE role IN ('TEACHER', 'STUDENT'))")
    op.execute("DELETE FROM users WHERE role IN ('TEACHER', 'STUDENT')")

def downgrade():
    # Historical assessment and account data cannot be reconstructed.
    pass
