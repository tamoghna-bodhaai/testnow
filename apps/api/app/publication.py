"""Preview and revalidate a bank release without changing historical questions."""
from collections import Counter
from .models import ImportJob, Question
from .readiness import VALIDATOR_VERSION, validate_question


def publication_plan(db, bank, job_id=None, *, lock=False):
    jobs_query = db.query(ImportJob).filter_by(bank_id=bank.id).order_by(ImportJob.id)
    if job_id is not None:
        jobs_query = jobs_query.filter_by(id=job_id)
    jobs = (jobs_query.populate_existing().with_for_update() if lock else jobs_query).all()
    by_id = {job.id: job for job in jobs}
    query = db.query(Question).filter_by(bank_id=bank.id).order_by(Question.id)
    if job_id is not None:
        query = query.filter_by(import_job_id=job_id)
    rows = (query.populate_existing().with_for_update() if lock else query).all()
    counts = Counter()
    reasons = Counter()
    blocked = []
    eligible = []
    for question in rows:
        result = validate_question(question)
        job = by_id.get(question.import_job_id)
        active = bool(job and (job.status.value == "processing"
                      or (job.status.value == "uploaded" and not (job.extraction_meta or {}).get("pause_requested"))
                      or ((job.extraction_meta or {}).get("solution_generation") or {}).get("status") in {"queued", "running"}))
        codes = [reason.code for reason in result.blocking]
        if not result.reviewed:
            codes.append("human_review_required")
        if active:
            codes.append("job_active")
        if question.import_job_id and not job:
            codes.append("source_job_missing")
        if codes:
            counts["blocked"] += 1
            reasons.update(set(codes))
            blocked.append({"question_id": question.id, "source_number": question.source_number,
                            "import_job_id": question.import_job_id, "reasons": sorted(set(codes))})
        elif (job and job.published_at) or (not question.import_job_id and bank.status == "published" and bank.is_global):
            counts["already_published"] += 1
        else:
            counts["eligible"] += 1
            eligible.append(question)
    preview = {"validator_version": VALIDATOR_VERSION, "bank_id": bank.id, "import_job_id": job_id,
               "total": len(rows), "eligible": counts["eligible"], "blocked": counts["blocked"],
               "already_published": counts["already_published"], "blocking_reasons": dict(sorted(reasons.items())),
               "blocked_questions": blocked}
    return preview, eligible, jobs
