"""Shared destructive cleanup for one ingested bank material."""
from sqlalchemy.orm import Session

from .models import ImportJob, Question, QuestionTaxonomy, SourceDocument, TestVersion
from fastapi import HTTPException
from .services import audit
from .storage import delete_prefix


def ensure_unreferenced_questions(db,question_ids,asset_prefixes=()):
    ids=set(question_ids)
    if not ids and not asset_prefixes:return
    if ids:db.query(Question).filter(Question.id.in_(ids)).populate_existing().with_for_update().all()
    def uses_assets(question):
        return any(isinstance(diagram,dict) and any(str(diagram.get("object_key") or "").startswith(prefix) for prefix in asset_prefixes) for diagram in ((question or {}).get("diagrams") or []))
    if asset_prefixes:
        for question in db.query(Question).filter(Question.id.notin_(ids)).yield_per(100):
            if uses_assets({"diagrams":question.diagrams}):raise HTTPException(409,"Other questions reuse this material's figure assets. Preserve the source and create a bank revision instead")
    for version in db.query(TestVersion).yield_per(100):
        referenced={question.get("id") for question in (version.content or {}).get("questions",[]) if isinstance(question,dict)}
        if ids&referenced or any(uses_assets(question) for question in (version.content or {}).get("questions",[]) if isinstance(question,dict)):raise HTTPException(409,"This content is referenced by an assessment version. Preserve its records/assets and create a bank revision instead")


def delete_import_records(db:Session,job:ImportJob,actor_id:str|None=None)->int:
    if job.bank_id:
        from .models import QuestionBank
        db.query(QuestionBank).filter_by(id=job.bank_id).with_for_update().one()
    question_ids=[row[0] for row in db.query(Question.id).filter_by(import_job_id=job.id).all()]
    ensure_unreferenced_questions(db,question_ids,[f"bank-diagrams/{job.bank_id}/{job.id}/"] if job.bank_id else [])
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
        linked_question=db.query(Question.id).filter(Question.interaction["source_document_id"].as_string()==source_id).first()
        if source and not referenced and not linked_question:
            delete_prefix(source.object_key);db.delete(source)
    return len(question_ids)
