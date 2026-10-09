import enum
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class Role(str, enum.Enum): ADMIN="admin"; TEACHER="teacher"; STUDENT="student"
class ImportStatus(str, enum.Enum): UPLOADED="uploaded"; PROCESSING="processing"; REVIEW="review"; FAILED="failed"; APPROVED="approved"
class TestStatus(str, enum.Enum): DRAFT="draft"; PUBLISHED="published"; ARCHIVED="archived"
class QuestionKind(str, enum.Enum): SINGLE="single_choice"; MULTIPLE="multiple_choice"; NUMERICAL="numerical"; INTEGER="integer"; ASSERTION="assertion_reason"; SUBJECTIVE="subjective"

class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
class User(Timestamped, Base):
    __tablename__="users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role))
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
    status: Mapped[ImportStatus] = mapped_column(Enum(ImportStatus), default=ImportStatus.UPLOADED)
    error: Mapped[str | None] = mapped_column(Text); extraction_meta: Mapped[dict] = mapped_column(JSON, default=dict)
class Question(Timestamped, Base):
    __tablename__="questions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    import_job_id: Mapped[str | None] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    source_number: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[QuestionKind] = mapped_column(Enum(QuestionKind))
    stem_markdown: Mapped[str] = mapped_column(Text); options: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[dict | None] = mapped_column(JSON); solution_markdown: Mapped[str | None] = mapped_column(Text)
    scoring: Mapped[dict] = mapped_column(JSON, default=lambda:{"correct":4,"incorrect":-1,"unanswered":0})
    diagrams: Mapped[list] = mapped_column(JSON, default=list); source_spans: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(); approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
class Enrollment(Timestamped, Base):
    __tablename__="enrollments"; test_id: Mapped[str] = mapped_column(ForeignKey("tests.id",ondelete="CASCADE"),primary_key=True); student_id: Mapped[str] = mapped_column(ForeignKey("users.id",ondelete="CASCADE"),primary_key=True)
class Attempt(Timestamped, Base):
    __tablename__="attempts"; id: Mapped[str] = mapped_column(String(36),primary_key=True); test_id: Mapped[str] = mapped_column(ForeignKey("tests.id"),index=True); version_id: Mapped[str] = mapped_column(ForeignKey("test_versions.id")); student_id: Mapped[str] = mapped_column(ForeignKey("users.id"),index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); score: Mapped[float | None] = mapped_column(); revision: Mapped[int] = mapped_column(Integer,default=0)
class Response(Timestamped, Base):
    __tablename__="responses"; attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id",ondelete="CASCADE"),primary_key=True); question_id: Mapped[str] = mapped_column(String(36),primary_key=True); answer: Mapped[dict | None] = mapped_column(JSON); marked_for_review: Mapped[bool] = mapped_column(Boolean,default=False)
class AuditEvent(Base):
    __tablename__="audit_events"; id: Mapped[str] = mapped_column(String(36),primary_key=True); actor_id: Mapped[str | None] = mapped_column(String(36),index=True); action: Mapped[str] = mapped_column(String(100)); entity_type: Mapped[str] = mapped_column(String(80)); entity_id: Mapped[str] = mapped_column(String(36)); data: Mapped[dict] = mapped_column(JSON,default=dict); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())
