"""Non-destructive figure audit/repair. Dry run unless --apply is explicit.

python -m app.figure_repairs --bank-id BANK --report /tmp/figure-audit.json
python -m app.figure_repairs --bank-id BANK --question-id QUESTION --apply --report /tmp/figure-repair.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .db import SessionLocal
from .figure_review import source_pdf
from .figures import extract_figures, ground_question_spans, image_regions, is_visual_issue, vector_regions, visual_intent
from .models import ImportJob, ImportStatus, Question
from .security import uid
from .services import audit
from .storage import put
from .tasks import celery_app


def propose_repair(question, document, regions=None):
    if regions is None:
        regions=image_regions(document)+vector_regions(document)
    data={"number":question.source_number,"stem_markdown":question.stem_markdown,
          "options":question.options or [],"source_spans":question.source_spans or [],
          "diagrams":[d for d in question.diagrams or [] if d.get("role")!="solution"],
          "visual_intent":(question.interaction or {}).get("visual_intent")}
    ground_question_spans([data],document)
    return extract_figures(data,document,regions)


def stage_repair(db, question, figures, issues, actor_id=None):
    """Write repaired assets without turning disproven false positives into work.

    A revalidation that concludes a question is text-only clears visual issues
    and preserves an existing approval.  Any retained crop or unresolved issue
    still requires a deliberate reviewer decision.
    """
    saved=[d for d in question.diagrams or [] if d.get("role")=="solution"]
    revision=uid()
    for index, figure in enumerate(figures):
        metadata={k:v for k,v in figure.items() if k!="png"}
        from .job_context import asset_key
        key=asset_key(f"repaired-diagrams/{question.id}/{revision}-{index}.png")
        put(key,figure["png"],"image/png")
        saved.append({**metadata,"object_key":key})
    previous=question.diagrams
    question.diagrams=saved
    needs_review=bool(figures or issues)
    if needs_review:
        question.approved_at=None
    question.interaction={**(question.interaction or {}),"figure_revision":revision,
        "ingestion_issues":list(dict.fromkeys([i for i in (question.interaction or {}).get("ingestion_issues",[]) if not is_visual_issue(i)]
                                             +issues+(["diagram_repair_review"] if needs_review else [])))}
    job=db.get(ImportJob,question.import_job_id) if question.import_job_id else None
    if job and needs_review:
        job.status=ImportStatus.REVIEW
        job.extraction_meta={**(job.extraction_meta or {}),"phase":"Figure repair awaiting review"}
    audit(db,actor_id,"question.figures_repair_staged","question",question.id,{"before":previous,"after":saved})


def repair_needed(question, figures, issues):
    def signatures(items):
        return [(d.get("page"),tuple(round(v,2) for v in d.get("bbox",[])),d.get("role","question"),d.get("option_index"))
                for d in items if d.get("role")!="solution"]
    previous_issues={i for i in (question.interaction or {}).get("ingestion_issues",[]) if is_visual_issue(i)}
    # A text-only scan can have stale flags with no crop signature to change.
    # Include changed validation findings in the repair preview as well.
    stale_scan_warning = previous_issues == {"diagram_scanned_source_review"} and not issues
    clearable_stale = {"diagram_scanned_source_review", "diagram_docx_complex_visual_review",
                       "diagram_docx_visuals_missing_after_conversion", "diagram_unassigned_native_candidate"}
    stale_text_only_warning = bool(previous_issues & clearable_stale) and not issues and not figures and visual_intent({
        "stem_markdown": question.stem_markdown, "options": question.options or [],
        "diagrams": question.diagrams or [], "visual_intent": (question.interaction or {}).get("visual_intent"),
    }) == "none"
    return bool(issues or stale_scan_warning or stale_text_only_warning
                or signatures(question.diagrams or [])!=signatures(figures))


@celery_app.task(bind=True)
def revalidate_bank_figures(self, bank_id: str, run_id: str, actor_id: str | None = None):
    """Revalidate stored figures using the current shared policy.

    This intentionally operates only on source-bank questions.  Published test
    versions are immutable snapshots and are never queried or modified here.
    """
    db = SessionLocal()
    report = {"run_id": run_id, "bank_id": bank_id, "started_at": datetime.now(timezone.utc).isoformat(),
              "status": "running", "questions": 0, "changed": 0, "cleared": 0,
              "needs_review": 0, "errors": []}
    try:
        rows = db.query(Question).filter_by(bank_id=bank_id).order_by(Question.id).all()
        jobs = {q.import_job_id: db.get(ImportJob, q.import_job_id) for q in rows if q.import_job_id}
        for job in jobs.values():
            if job:
                job.extraction_meta = {**(job.extraction_meta or {}), "figure_revalidation": {"run_id": run_id, "status": "running", "processed": 0, "total": len(rows)}}
        db.commit()
        proposals = {}
        # Complete a read-only audit before changing a single source question.
        # A failed/expired source therefore cannot leave a bank half-repaired.
        for question in rows:
            report["questions"] += 1
            try:
                with source_pdf(question, db) as (document, _raw, _source, _job):
                    figures, issues = propose_repair(question, document)
                proposals[question.id] = {"changed": repair_needed(question, figures, issues),
                                          "cleared": not figures and not issues}
                job = jobs.get(question.import_job_id)
                if job:
                    job.extraction_meta = {**(job.extraction_meta or {}), "figure_revalidation": {"run_id": run_id, "status": "running", "phase": "Auditing", "processed": report["questions"], "total": len(rows)}}
                db.commit()
            except Exception as exc:
                db.rollback()
                report["errors"].append({"question_id": question.id, "error": str(exc)[:300]})
        # Reopen each source only after the complete audit has identified safe
        # changes.  The second validation keeps this resilient to a worker
        # restart or a concurrent source update.
        for index, question in enumerate(rows, 1):
            proposal = proposals.get(question.id)
            if not proposal or not proposal["changed"]:
                continue
            with source_pdf(question, db) as (document, _raw, _source, _job):
                figures, issues = propose_repair(question, document)
            if repair_needed(question, figures, issues):
                was_text_only = not figures and not issues
                stage_repair(db, question, figures, issues, actor_id)
                report["changed"] += 1
                report["cleared"] += int(was_text_only)
                report["needs_review"] += int(not was_text_only)
            job = jobs.get(question.import_job_id)
            if job:
                job.extraction_meta = {**(job.extraction_meta or {}), "figure_revalidation": {"run_id": run_id, "status": "running", "phase": "Applying", "processed": index, "total": len(rows)}}
            db.commit()
        report["status"] = "completed"
    except Exception as exc:
        db.rollback()
        report["status"] = "failed"
        report["errors"].append({"error": str(exc)[:300]})
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        from .job_context import asset_key
        report_key = asset_key(f"figure-revalidation/{bank_id}/{run_id}.json")
        try:
            put(report_key, json.dumps(report, indent=2).encode(), "application/json")
            for job in jobs.values() if 'jobs' in locals() else []:
                if job:
                    job.extraction_meta = {**(job.extraction_meta or {}), "figure_revalidation": {**report, "report_key": report_key}}
            db.commit()
        finally:
            db.close()
    return {**report, "report_key": report_key}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank-id",required=True)
    parser.add_argument("--question-id",action="append")
    parser.add_argument("--limit",type=int,default=100)
    parser.add_argument("--report",required=True,type=Path)
    parser.add_argument("--apply",action="store_true",help="Stage replacement figures for review; never auto-approve")
    args=parser.parse_args()
    if args.limit<1:parser.error("--limit must be positive")
    db=SessionLocal();results=[]
    try:
        rows=db.query(Question).filter_by(bank_id=args.bank_id)
        if args.question_id:rows=rows.filter(Question.id.in_(args.question_id))
        ids=[q.id for q in rows.order_by(Question.id).limit(args.limit)]
        args.report.parent.mkdir(parents=True,exist_ok=True)
        preview_dir=args.report.parent/(args.report.stem+"-previews")
        preview_dir.mkdir(exist_ok=True)
        for question_id in ids:
            question=db.get(Question,question_id)
            try:
                with source_pdf(question,db) as (document,raw,source,job):
                    figures,issues=propose_repair(question,document)
                    changed=repair_needed(question,figures,issues)
                    previews=[]
                    for index,figure in enumerate(figures):
                        preview=preview_dir/f"{question.id}-{index}.png"
                        preview.write_bytes(figure["png"])
                        previews.append({**{k:v for k,v in figure.items() if k!="png"},"preview":str(preview)})
                    if args.apply and changed:
                        if source.media_type!="application/pdf" and job and not (job.extraction_meta or {}).get("canonical_pdf_key"):
                            key=f"bank-sources/{question.bank_id}/{source.id}/canonical.pdf"
                            put(key,raw,"application/pdf")
                            job.extraction_meta={**(job.extraction_meta or {}),"canonical_pdf_key":key}
                        stage_repair(db,question,figures,issues)
                        db.commit()
                    results.append({"question_id":question_id,"issues":issues,"figures":previews,"would_change":changed,"applied":args.apply and changed})
            except Exception as exc:
                db.rollback()
                results.append({"question_id":question_id,"error":str(exc),"applied":False})
        args.report.write_text(json.dumps({"mode":"staged_for_review" if args.apply else "dry_run","results":results},indent=2))
        print(f"Audited {len(results)} question(s); report: {args.report}")
    finally:
        db.close()


if __name__=="__main__":main()
