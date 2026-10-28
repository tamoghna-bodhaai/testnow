"""Assessment-scoped, teacher-reviewed video explainers."""
from datetime import datetime, timezone
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from .db import get_db
from .models import Attempt, ClassMembership, Enrollment, ExplainerRevision, Role, Test, TestClassAssignment, TestVersion, User
from .security import require, uid
from .services import audit
from .storage import client as storage_client
from .config import settings
from .tasks import celery_app
from .durable_jobs import enqueue

router = APIRouter()
MAX_EXPLAINER_DRAFTS = 3
ACTIVE_EXPLAINER_STATUSES = ("queued", "planning", "narrating", "rendering", "finalizing")

class GenerateExplainerIn(BaseModel):
    feedback: str = Field(default="", max_length=2000)
    override_suggestion: bool = False

def snapshot_for(test: Test, question_id: str, db: Session):
    version = db.get(TestVersion, test.published_version_id) if test.published_version_id else None
    if not version:
        raise HTTPException(409, "Publish the assessment before generating explainers")
    question = next((q for q in version.content.get("questions", []) if q.get("id") == question_id), None)
    if not question:
        raise HTTPException(404, "Question not found in the published assessment")
    return version, question

def suggested(question: dict) -> bool:
    solution = str(question.get("solution_markdown") or "")
    if not solution.strip():
        return False
    text = f'{question.get("stem_markdown", "")} {solution}'.lower()
    cues = ("graph", "plot", "curve", "geometry", "triangle", "circle", "angle", "vector",
            "motion", "force", "field", "circuit", "ray", "function", "derivative", "integral",
            "matrix", "probability", "diagram", "visuali", "step 1", "step 2")
    return bool(question.get("diagrams")) or any(cue in text for cue in cues) or len(solution.split()) >= 90

def teacher_test(test_id: str, user: User, db: Session) -> Test:
    test = db.get(Test, test_id)
    if not test or (test.owner_id != user.id and user.role != Role.ADMIN):
        raise HTTPException(404, "Assessment not found")
    return test

def revision_view(row: ExplainerRevision) -> dict:
    return {"id": row.id, "question_id": row.question_id, "status": row.status,
            "progress_message": row.progress_message,
            "feedback": row.feedback, "error": row.error, "created_at": row.created_at,
            "published_at": row.published_at,
            "preview_url": f"/api/tests/{row.test_id}/explainers/{row.id}/video" if row.video_object_key and row.status == "ready" else None}

def stream_video(key: str, request: Request):
    requested_range = request.headers.get("range")
    if requested_range and not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", requested_range):
        raise HTTPException(416, "Unsupported video range")
    params = {"Bucket": settings().s3_bucket, "Key": key}
    if requested_range:
        params["Range"] = requested_range
    try:
        result = storage_client().get_object(**params)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        raise HTTPException(416 if code == "InvalidRange" else 502, "Video could not be loaded") from exc
    body = result["Body"]
    def chunks():
        try:
            yield from body.iter_chunks(chunk_size=64 * 1024)
        finally:
            body.close()
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(result["ContentLength"]),
               "Cache-Control": "private, no-store"}
    if result.get("ContentRange"):
        headers["Content-Range"] = result["ContentRange"]
    return StreamingResponse(chunks(), status_code=206 if requested_range else 200,
                             media_type="video/mp4", headers=headers)

@router.get("/tests/{test_id}/explainers/{revision_id}/video")
def teacher_video(test_id: str, revision_id: str, request: Request,
                  user: User = Depends(require(Role.TEACHER, Role.ADMIN)), db: Session = Depends(get_db)):
    test = teacher_test(test_id, user, db)
    row = db.get(ExplainerRevision, revision_id)
    if not row or row.test_id != test.id or row.version_id != test.published_version_id or row.status != "ready" or not row.video_object_key:
        raise HTTPException(404, "Video not found")
    return stream_video(row.video_object_key, request)

@router.get("/tests/{test_id}/explainers")
def teacher_explainers(test_id: str, user: User = Depends(require(Role.TEACHER, Role.ADMIN)), db: Session = Depends(get_db)):
    test = teacher_test(test_id, user, db)
    version = db.get(TestVersion, test.published_version_id) if test.published_version_id else None
    if not version:
        return {"questions": [], "revisions": []}
    rows = db.query(ExplainerRevision).filter_by(test_id=test.id, version_id=version.id).order_by(ExplainerRevision.created_at.desc()).all()
    return {"questions": [{key: q[key] for key in ("id", "source_number", "kind", "stem_markdown", "options", "answer", "solution_markdown", "diagrams", "scoring", "interaction") if key in q}
                            | {"has_solution": bool(str(q.get("solution_markdown") or "").strip()),
                               "suggested": suggested(q)} for q in version.content.get("questions", [])],
            "revisions": [revision_view(row) for row in rows]}

@router.post("/tests/{test_id}/explainers/{question_id}/generate", status_code=202)
def generate_explainer(test_id: str, question_id: str, data: GenerateExplainerIn,
                       user: User = Depends(require(Role.TEACHER, Role.ADMIN)), db: Session = Depends(get_db)):
    test = teacher_test(test_id, user, db)
    # Serialize generation and publication for this assessment.
    db.query(Test).filter_by(id=test.id).with_for_update().first()
    version, question = snapshot_for(test, question_id, db)
    if not str(question.get("solution_markdown") or "").strip():
        raise HTTPException(422, "Add a worked solution before generating an explainer")
    if not suggested(question) and not data.override_suggestion:
        raise HTTPException(422, "This question is not suggested for animation; choose override to generate anyway")
    revisions = db.query(ExplainerRevision).filter_by(test_id=test.id, version_id=version.id, question_id=question_id)
    if revisions.filter(ExplainerRevision.published_at.is_not(None)).first():
        raise HTTPException(409, "An explainer has already been published for this question")
    if revisions.count() >= MAX_EXPLAINER_DRAFTS:
        raise HTTPException(409, "The limit of 3 explainer drafts for this question has been reached")
    active = revisions.filter(ExplainerRevision.status.in_(ACTIVE_EXPLAINER_STATUSES)).first()
    if active:
        raise HTTPException(409, "An explainer is already generating for this question")
    row = ExplainerRevision(id=uid(), test_id=test.id, version_id=version.id, question_id=question_id,
                            requested_by=user.id, feedback=data.feedback.strip() or None, status="queued",
                            progress_message="Waiting for the render worker")
    db.add(row)
    audit(db, user.id, "explainer.generation_requested", "explainer", row.id, {"test_id": test.id, "question_id": question_id})
    if settings().durable_jobs_enabled:
        enqueue(db, "app.explainers.render_explainer", [row.id], queue="explainers")
        db.commit()
        return revision_view(row)
    db.commit()
    try:
        celery_app.send_task("app.explainers.render_explainer", args=[row.id], queue="explainers")
    except Exception as exc:
        row.status = "failed"
        row.error = "Could not queue render job"
        db.commit()
        raise HTTPException(503, "Could not queue the explainer; try again") from exc
    return revision_view(row)

@router.post("/tests/{test_id}/explainers/{revision_id}/cancel")
def cancel_explainer(test_id: str, revision_id: str,
                     user: User = Depends(require(Role.TEACHER, Role.ADMIN)), db: Session = Depends(get_db)):
    test = teacher_test(test_id, user, db)
    row = db.get(ExplainerRevision, revision_id)
    if not row or row.test_id != test.id or row.version_id != test.published_version_id:
        raise HTTPException(404, "Explainer not found")
    changed = db.execute(update(ExplainerRevision).where(
        ExplainerRevision.id == row.id, ExplainerRevision.status.in_(ACTIVE_EXPLAINER_STATUSES)
    ).values(status="cancelled", progress_message="Generation cancelled").execution_options(synchronize_session=False)).rowcount
    if not changed:
        db.rollback()
        raise HTTPException(409, "This explainer is no longer generating")
    audit(db, user.id, "explainer.generation_cancelled", "explainer", row.id, {"test_id": test.id, "question_id": row.question_id})
    db.commit()
    db.refresh(row)
    return revision_view(row)

@router.post("/tests/{test_id}/explainers/{revision_id}/publish")
def publish_explainer(test_id: str, revision_id: str,
                      user: User = Depends(require(Role.TEACHER, Role.ADMIN)), db: Session = Depends(get_db)):
    test = teacher_test(test_id, user, db)
    row = db.get(ExplainerRevision, revision_id)
    if not row or row.test_id != test.id or row.version_id != test.published_version_id:
        raise HTTPException(404, "Explainer not found")
    if row.status != "ready" or not row.video_object_key:
        raise HTTPException(409, "Only a completed explainer can be published")
    db.query(Test).filter_by(id=test.id).with_for_update().first()
    for previous in db.query(ExplainerRevision).filter_by(test_id=test.id, version_id=row.version_id,
                                                          question_id=row.question_id).filter(ExplainerRevision.published_at.is_not(None)):
        previous.published_at = None
    row.published_at = datetime.now(timezone.utc)
    audit(db, user.id, "explainer.published", "explainer", row.id, {"test_id": test.id, "question_id": row.question_id})
    db.commit()
    return revision_view(row)

def result_is_released(test: Test, now: datetime) -> bool:
    policy = test.results_policy or {}
    if policy.get("mode") == "immediate":
        return True
    release = policy.get("release_at")
    if not release:
        return False
    at = datetime.fromisoformat(str(release).replace("Z", "+00:00"))
    return now >= (at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc))

@router.get("/results/{attempt_id}/explainers")
def student_explainers(attempt_id: str, user: User = Depends(require(Role.STUDENT)), db: Session = Depends(get_db)):
    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.student_id != user.id or not attempt.submitted_at:
        raise HTTPException(404, "Result not found")
    if attempt.cancelled_at:
        raise HTTPException(403, "Cancelled attempts do not have a student result")
    test = db.get(Test, attempt.test_id)
    assigned = db.query(Enrollment).filter_by(test_id=test.id, student_id=user.id).first() if test else None
    if test and not assigned:
        class_ids = [row.class_id for row in db.query(TestClassAssignment).filter_by(test_id=test.id)]
        assigned = db.query(ClassMembership).filter(ClassMembership.student_id == user.id, ClassMembership.class_id.in_(class_ids)).first() if class_ids else None
    if not assigned:
        raise HTTPException(403, "You are not assigned to this assessment")
    if not test or not result_is_released(test, datetime.now(timezone.utc)):
        raise HTTPException(403, "Results have not been released")
    rows = db.query(ExplainerRevision).filter_by(test_id=test.id, version_id=attempt.version_id).filter(ExplainerRevision.published_at.is_not(None), ExplainerRevision.status == "ready").all()
    return {row.question_id: {"url": f"/api/results/{attempt.id}/explainers/{row.question_id}/video", "published_at": row.published_at}
            for row in rows if row.video_object_key}

@router.get("/results/{attempt_id}/explainers/{question_id}/video")
def student_video(attempt_id: str, question_id: str, request: Request,
                  user: User = Depends(require(Role.STUDENT)), db: Session = Depends(get_db)):
    available = student_explainers(attempt_id, user, db)
    if question_id not in available:
        raise HTTPException(404, "Video not found")
    attempt = db.get(Attempt, attempt_id)
    row = db.query(ExplainerRevision).filter_by(test_id=attempt.test_id, version_id=attempt.version_id,
                                                question_id=question_id).filter(ExplainerRevision.published_at.is_not(None),
                                                                               ExplainerRevision.status == "ready").first()
    if not row or not row.video_object_key:
        raise HTTPException(404, "Video not found")
    return stream_video(row.video_object_key, request)
