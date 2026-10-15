"""Non-destructive figure audit/repair. Dry run unless --apply is explicit.

python -m app.figure_repairs --bank-id BANK --report /tmp/figure-audit.json
python -m app.figure_repairs --bank-id BANK --question-id QUESTION --apply --report /tmp/figure-repair.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import SessionLocal
from .figure_review import source_pdf
from .figures import extract_figures, ground_question_spans, image_regions, is_visual_issue, vector_regions
from .models import ImportJob, ImportStatus, Question
from .security import uid
from .services import audit
from .storage import put


def propose_repair(question, document, regions=None):
    if regions is None:
        regions=image_regions(document)+vector_regions(document)
    data={"number":question.source_number,"stem_markdown":question.stem_markdown,
          "options":question.options or [],"source_spans":question.source_spans or [],
          "diagrams":[d for d in question.diagrams or [] if d.get("role")!="solution"]}
    ground_question_spans([data],document)
    return extract_figures(data,document,regions)


def stage_repair(db, question, figures, issues, actor_id=None):
    """Write new assets and require review; never rewrite answers or old assets."""
    saved=[d for d in question.diagrams or [] if d.get("role")=="solution"]
    revision=uid()
    for index, figure in enumerate(figures):
        metadata={k:v for k,v in figure.items() if k!="png"}
        key=f"repaired-diagrams/{question.id}/{revision}-{index}.png"
        put(key,figure["png"],"image/png")
        saved.append({**metadata,"object_key":key})
    previous=question.diagrams
    question.diagrams=saved
    question.approved_at=None
    question.interaction={**(question.interaction or {}),"figure_revision":revision,
        "ingestion_issues":list(dict.fromkeys([i for i in (question.interaction or {}).get("ingestion_issues",[]) if not is_visual_issue(i)]
                                             +issues+["diagram_repair_review"]))}
    job=db.get(ImportJob,question.import_job_id) if question.import_job_id else None
    if job:
        job.status=ImportStatus.REVIEW
        job.extraction_meta={**(job.extraction_meta or {}),"phase":"Figure repair awaiting review"}
    audit(db,actor_id,"question.figures_repair_staged","question",question.id,{"before":previous,"after":saved})


def repair_needed(question, figures, issues):
    def signatures(items):
        return [(d.get("page"),tuple(round(v,2) for v in d.get("bbox",[])),d.get("role","question"),d.get("option_index"))
                for d in items if d.get("role")!="solution"]
    return bool(issues or signatures(question.diagrams or [])!=signatures(figures))


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
