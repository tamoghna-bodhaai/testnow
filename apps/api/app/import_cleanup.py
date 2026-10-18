"""Shared destructive cleanup for one ingested bank material."""
from sqlalchemy.orm import Session

from .models import ImportJob, Question, QuestionTaxonomy, SourceDocument
from .services import audit
from .storage import delete_prefix


def delete_import_records(db:Session,job:ImportJob,actor_id:str|None=None)->int:
    question_ids=[row[0] for row in db.query(Question.id).filter_by(import_job_id=job.id).all()]
    if question_ids:
        db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id.in_(question_ids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.id.in_(question_ids)).delete(synchronize_session=False)
    if job.bank_id:delete_prefix(f"bank-diagrams/{job.bank_id}/{job.id}/")
    meta=dict(job.extraction_meta or {})
    if meta.get("canonical_pdf_key"):delete_prefix(meta["canonical_pdf_key"])
    source_ids=[value for value in {job.question_document_id,job.answer_document_id} if value]
    job_id=job.id;bank_id=job.bank_id
    audit(db,actor_id,"question_bank.import_deleted","import",job_id,{"bank_id":bank_id,"question_count":len(question_ids)})
    db.delete(job);db.flush()
    for source_id in source_ids:
        referenced=db.query(ImportJob).filter((ImportJob.question_document_id==source_id)|(ImportJob.answer_document_id==source_id)).first()
        source=db.get(SourceDocument,source_id)
        if source and not referenced:
            delete_prefix(source.object_key);db.delete(source)
    return len(question_ids)
