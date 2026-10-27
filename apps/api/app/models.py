import enum
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class Role(str, enum.Enum): ADMIN="admin"; TEACHER="teacher"; STUDENT="student"
class AccountStatus(str, enum.Enum): PENDING_CODE="pending_code"; ACTIVE="active"; SUSPENDED="suspended"
class ImportStatus(str, enum.Enum): UPLOADED="uploaded"; PROCESSING="processing"; REVIEW="review"; FAILED="failed"; APPROVED="approved"
class TestStatus(str, enum.Enum): DRAFT="draft"; PUBLISHED="published"; ARCHIVED="archived"
class QuestionKind(str, enum.Enum): SINGLE="single_choice"; MULTIPLE="multiple_choice"; NUMERICAL="numerical"; INTEGER="integer"; MATRIX="matrix_match"; ASSERTION="assertion_reason"; SUBJECTIVE="subjective"
class QuestionScope(str, enum.Enum): PRIVATE="private"; GLOBAL="global"

class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class DurableJob(Timestamped, Base):
    __tablename__ = "durable_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task: Mapped[str] = mapped_column(String(120))
    queue: Mapped[str] = mapped_column(String(32))
    resource: Mapped[str] = mapped_column(String(160))
    args: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_durable_jobs_queue_status_available", "queue", "status", "available_at"),)

class User(Timestamped, Base):
    __tablename__="users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    # Retained temporarily for compatibility with the initial migration. New logins
    # never read this column; access codes are Argon2 hashes just like passwords.
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    access_code_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role))
    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), default=AccountStatus.PENDING_CODE, nullable=False)
    access_code_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
class Session(Timestamped, Base):
    __tablename__="sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
class SourceDocument(Timestamped, Base):
    __tablename__="source_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255)); object_key: Mapped[str] = mapped_column(String(512), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True); media_type: Mapped[str] = mapped_column(String(100)); size_bytes: Mapped[int] = mapped_column(Integer)
class ImportJob(Timestamped, Base):
    __tablename__="import_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    question_document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"))
    answer_document_id: Mapped[str | None] = mapped_column(ForeignKey("source_documents.id"))
    bank_id: Mapped[str | None] = mapped_column(ForeignKey("question_banks.id", ondelete="CASCADE"), index=True)
    status: Mapped[ImportStatus] = mapped_column(Enum(ImportStatus), default=ImportStatus.UPLOADED)
    error: Mapped[str | None] = mapped_column(Text); extraction_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    # A source document is a separately reviewable and releasable material.
    # It may be fully reviewed without becoming visible in a live bank until an
    # administrator explicitly confirms publication for that material.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
class QuestionBank(Timestamped, Base):
    __tablename__="question_banks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    exam_code: Mapped[str] = mapped_column(String(80), index=True)
    is_global: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Draft banks and revisions are deliberately private until a super admin
    # publishes a fully reviewed release.
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
class TaxonomyNode(Timestamped, Base):
    __tablename__="taxonomy_nodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bank_id: Mapped[str] = mapped_column(ForeignKey("question_banks.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("taxonomy_nodes.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(120))
    alias_of_id: Mapped[str | None] = mapped_column(ForeignKey("taxonomy_nodes.id"), index=True)
    __table_args__=(UniqueConstraint("bank_id", "parent_id", "kind", "name", name="uq_taxonomy_node_path"),)
class Question(Timestamped, Base):
    __tablename__="questions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    bank_id: Mapped[str | None] = mapped_column(ForeignKey("question_banks.id"), index=True)
    scope: Mapped[QuestionScope] = mapped_column(Enum(QuestionScope), default=QuestionScope.PRIVATE, nullable=False)
    import_job_id: Mapped[str | None] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    source_number: Mapped[str | None] = mapped_column(String(32))
    # Stable identity for an externally seeded record.  Manual and teacher-imported
    # questions deliberately leave this null.
    source_key: Mapped[str | None] = mapped_column(String(255), index=True)
    kind: Mapped[QuestionKind] = mapped_column(Enum(QuestionKind))
    stem_markdown: Mapped[str] = mapped_column(Text); options: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[dict | None] = mapped_column(JSON); solution_markdown: Mapped[str | None] = mapped_column(Text)
    scoring: Mapped[dict] = mapped_column(JSON, default=lambda:{"correct":4,"incorrect":-1,"unanswered":0})
    diagrams: Mapped[list] = mapped_column(JSON, default=list); source_spans: Mapped[list] = mapped_column(JSON, default=list)
    interaction: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(); approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__=(UniqueConstraint("bank_id", "source_key", name="uq_question_bank_source_key"),
                    Index("ix_questions_bank_review","bank_id","created_at","id"),
                    Index("ix_questions_import_review","import_job_id","created_at","id"))
class QuestionTaxonomy(Base):
    __tablename__="question_taxonomies"
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    taxonomy_node_id: Mapped[str] = mapped_column(ForeignKey("taxonomy_nodes.id", ondelete="CASCADE"), primary_key=True)
class Test(Timestamped, Base):
    __tablename__="tests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True); owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255)); subject: Mapped[str] = mapped_column(String(120)); status: Mapped[TestStatus] = mapped_column(Enum(TestStatus), default=TestStatus.DRAFT)
    duration_seconds: Mapped[int] = mapped_column(Integer); opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_attempts: Mapped[int] = mapped_column(Integer, default=1); results_policy: Mapped[dict] = mapped_column(JSON, default=lambda:{"mode":"immediate"})
    published_version_id: Mapped[str | None] = mapped_column(String(36))
class TestVersion(Timestamped, Base):
    __tablename__="test_versions"; id: Mapped[str] = mapped_column(String(36), primary_key=True); test_id: Mapped[str] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer); content: Mapped[dict] = mapped_column(JSON); published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__=(UniqueConstraint("test_id","ordinal"),)
class ExplainerRevision(Timestamped, Base):
    __tablename__="explainer_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    test_id: Mapped[str] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("test_versions.id", ondelete="CASCADE"))
    question_id: Mapped[str] = mapped_column(String(36))
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    feedback: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    progress_message: Mapped[str | None] = mapped_column(String(160))
    scene_spec: Mapped[dict | None] = mapped_column(JSON)
    video_object_key: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__=(Index("ix_explainer_test_question", "test_id", "version_id", "question_id"),
                    Index("uq_explainer_published", "test_id", "version_id", "question_id", unique=True,
                          sqlite_where=published_at.is_not(None), postgresql_where=published_at.is_not(None)))
class Enrollment(Timestamped, Base):
    __tablename__="enrollments"; test_id: Mapped[str] = mapped_column(ForeignKey("tests.id",ondelete="CASCADE"),primary_key=True); student_id: Mapped[str] = mapped_column(ForeignKey("users.id",ondelete="CASCADE"),primary_key=True)
class Classroom(Timestamped, Base):
    __tablename__="classrooms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(String(500))
    __table_args__=(UniqueConstraint("owner_id", "name"),)
class ClassMembership(Timestamped, Base):
    __tablename__="class_memberships"
    class_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id", ondelete="CASCADE"), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
class TestClassAssignment(Timestamped, Base):
    __tablename__="test_class_assignments"
    test_id: Mapped[str] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), primary_key=True)
    class_id: Mapped[str] = mapped_column(ForeignKey("classrooms.id", ondelete="CASCADE"), primary_key=True)
class Attempt(Timestamped, Base):
    __tablename__="attempts"; id: Mapped[str] = mapped_column(String(36),primary_key=True); test_id: Mapped[str] = mapped_column(ForeignKey("tests.id"),index=True); version_id: Mapped[str] = mapped_column(ForeignKey("test_versions.id")); student_id: Mapped[str] = mapped_column(ForeignKey("users.id"),index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); score: Mapped[float | None] = mapped_column(); revision: Mapped[int] = mapped_column(Integer,default=0)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(64))
    integrity_flagged: Mapped[bool] = mapped_column(Boolean,default=False,nullable=False,index=True)
    cohort_classes: Mapped[list] = mapped_column(JSON,default=list,nullable=False)
class Response(Timestamped, Base):
    __tablename__="responses"; attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id",ondelete="CASCADE"),primary_key=True); question_id: Mapped[str] = mapped_column(String(36),primary_key=True); answer: Mapped[dict | None] = mapped_column(JSON); marked_for_review: Mapped[bool] = mapped_column(Boolean,default=False)
    # Nullable for records created before CBT palette state was tracked.
    visited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
class AuditEvent(Base):
    __tablename__="audit_events"; id: Mapped[str] = mapped_column(String(36),primary_key=True); actor_id: Mapped[str | None] = mapped_column(String(36),index=True); action: Mapped[str] = mapped_column(String(100)); entity_type: Mapped[str] = mapped_column(String(80)); entity_id: Mapped[str] = mapped_column(String(36)); data: Mapped[dict] = mapped_column(JSON,default=dict); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())
class AttemptEvent(Base):
    """Append-only candidate delivery trail; never used to reconstruct answers."""
    __tablename__="attempt_events"
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id",ondelete="CASCADE"),index=True)
    event_type: Mapped[str] = mapped_column(String(64),index=True)
    data: Mapped[dict] = mapped_column(JSON,default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
