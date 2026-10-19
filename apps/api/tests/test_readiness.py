from datetime import datetime, timezone
from copy import deepcopy

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app, bank_readiness_report, bank_question_counts, bank_questions, import_view, publish_bank_import, publish_question_bank, preview_bank_import, question_view, resolve_bank_question, approve_question, publish_test
from app.figure_review import save_question_draft
from app.models import AuditEvent, ImportJob, ImportStatus, Question, QuestionBank, QuestionKind, QuestionScope, Role, SourceDocument, Test as Assessment, TestVersion as Version, User
from app.readiness import VALIDATOR_VERSION, validate_question
from app.schemas import QuestionIn
from app.security import current_user
from app.services import snapshot_questions


def content(**overrides):
    return {"kind":"single_choice", "stem_markdown":"What is two plus two?", "options":[{"value":"a","markdown":"4"},{"value":"b","markdown":"5"}],
            "answer":{"option":0}, "solution_markdown":None, "interaction":{}, "diagrams":[], **overrides}


def codes(result):
    return {reason.code for reason in result.blocking}


@pytest.mark.parametrize("kind,answer,interaction", [
    ("single_choice", {"option":0}, {}),
    ("multiple_choice", {"options":[0,1]}, {}),
    ("assertion_reason", {"option":1}, {}),
    ("numerical", {"value":"0"}, {"tolerance":0.01}),
    ("integer", {"value":-2}, {}),
    ("matrix_match", {"matches":{"P":["1","2"],"Q":["2"]}}, {"matrix_items":["P","Q"],"matrix_choices":["1","2"]}),
    ("subjective", None, {}),
])
def test_supported_types_have_independent_review_and_content_validity(kind, answer, interaction):
    record=content(kind=kind, answer=answer, interaction=interaction, solution_markdown="The result follows by addition.")
    draft=validate_question(record)
    assert draft.content_valid and not draft.reviewed and not draft.publishable
    approved=validate_question(record,reviewed=True)
    assert approved.publishable and approved.validator_version==VALIDATOR_VERSION


@pytest.mark.parametrize("answer", [None, {}, {"option":None}, {"option":True}, {"option":-1}, {"option":2}, {"option":"a"}, {"option":0,"extra":True}])
def test_null_and_invalid_choice_keys_block_even_legacy_reviewed_records(answer):
    result=validate_question(content(answer=answer,approved_at=datetime.now(timezone.utc)))
    assert result.reviewed and not result.publishable
    assert "invalid_answer" in codes(result)


@pytest.mark.parametrize("options", [[{"markdown":"Not provided"},{"markdown":"Correct"}], [{"markdown":"A"},{"markdown":"B"}], [{"markdown":"Option A"},{"markdown":"Option B"}], [{"markdown":""},{"markdown":"5"}], []])
def test_placeholder_options_are_blocking(options):
    assert not validate_question(content(options=options)).content_valid


@pytest.mark.parametrize("kind,answer", [
    ("multiple_choice",{"options":[0,0]}), ("multiple_choice",{"options":[True]}),
    ("numerical",{"value":"NaN"}), ("numerical",{"value":"Infinity"}),
    ("integer",{"value":"2.5"}), ("numerical",{"value":False}),
    ("matrix_match",{"matches":{"P":[None]}}),
])
def test_invalid_type_specific_answers_are_blocking(kind,answer):
    assert not validate_question(content(kind=kind,answer=answer)).content_valid


def test_matrix_requires_complete_source_contract_and_valid_columns():
    record=content(kind="matrix_match",answer={"matches":{"P":["9"]}},interaction={"matrix_items":["P","Q"],"matrix_choices":["1","2"]})
    assert "invalid_matrix" in codes(validate_question(record))


def test_missing_figure_cannot_be_satisfied_by_an_unstored_crop():
    record=content(stem_markdown="Calculate the area in the given figure.",diagrams=[{"role":"question","bbox":[0,0,10,10]}])
    assert "required_figure_missing" in codes(validate_question(record))
    record["diagrams"][0]["object_key"]="figures/verified.png"
    assert validate_question(record).content_valid


def test_diagram_only_options_are_meaningful_when_each_has_a_stored_figure():
    record=content(stem_markdown="Which diagram shows a circle?",options=[{"markdown":"","requires_diagram":True},{"markdown":"","requires_diagram":True}],
                   diagrams=[{"role":"option","option_index":i,"object_key":f"option-{i}.png"} for i in range(2)],interaction={"visual_intent":"required"})
    assert validate_question(record).content_valid
    record["diagrams"].pop()
    assert not validate_question(record).content_valid


def test_solution_and_taxonomy_policy_is_explicit_and_validation_does_not_mutate():
    record=content()
    original=deepcopy(record)
    result=validate_question(record,taxonomy=[])
    assert result.content_valid
    assert {r.code for r in result.advisory}=={"missing_solution","missing_subject","missing_chapter","missing_topic"}
    assert record==original
    assert "missing_model_solution" in codes(validate_question(content(kind="subjective",answer=None)))


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session=sessionmaker(bind=engine,autoflush=False)()
    session.add_all([User(id="admin",name="Admin",email="admin@example.com",role=Role.ADMIN),
                     QuestionBank(id="bank",name="Bank",slug="bank",exam_code="JEE",is_global=False),
                     SourceDocument(id="source",owner_id="admin",filename="source.pdf",object_key="source.pdf",sha256="hash",media_type="application/pdf",size_bytes=1),
                     ImportJob(id="job",owner_id="admin",bank_id="bank",question_document_id="source",status=ImportStatus.REVIEW)])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def add_question(db,qid="q",**overrides):
    record=content(**overrides)
    record["kind"]=QuestionKind(record["kind"])
    question=Question(id=qid,owner_id="admin",bank_id="bank",scope=QuestionScope.GLOBAL,import_job_id="job",source_number=qid,approved_at=datetime.now(timezone.utc),**record)
    db.add(question);db.commit()
    return question


def test_preview_revalidation_partial_release_and_retry_are_consistent(db):
    good=add_question(db,"good")
    broken=add_question(db,"Q51",answer=None)
    admin=db.get(User,"admin")
    preview=preview_bank_import("bank","job",admin,db)
    assert (preview["eligible"],preview["blocked"],preview["already_published"])==(1,1,0)
    assert preview["blocked_questions"][0]["question_id"]=="Q51"
    assert not db.get(ImportJob,"job").published_at
    first=publish_bank_import("bank","job",admin,db)
    assert first["publication"]["published_count"]==1 and first["publication"]["partial"]
    retry=publish_bank_import("bank","job",admin,db)
    assert retry["publication"]["published_count"]==0
    assert retry["publication"]["already_published"]==1
    assert db.query(AuditEvent).filter_by(action="question_bank.material_published").count()==1
    assert broken.approved_at is not None and broken.answer is None
    assert not question_view(broken,db)["readiness"]["publishable"]
    assert import_view(db.get(ImportJob,"job"),db)["readiness"]["blocked"]==1
    assert bank_question_counts("bank",user=admin,db=db)["total"]==1
    page=bank_questions("bank",kinds=[],page=1,page_size=25,user=admin,db=db)
    assert page["total"]==1 and page["items"][0]["id"]==good.id
    with pytest.raises(HTTPException):snapshot_questions(db,[broken.id],admin.id)
    assert snapshot_questions(db,[good.id],admin.id)[0]["answer"]=={"option":0}


def test_submit_does_not_trust_a_stale_preview_or_cached_validation(db):
    question=add_question(db)
    admin=db.get(User,"admin")
    assert preview_bank_import("bank","job",admin,db)["eligible"]==1
    question.answer=None
    question.interaction={"readiness":{"publishable":True,"content_valid":True}}
    db.commit()
    with pytest.raises(HTTPException) as exc:publish_bank_import("bank","job",admin,db)
    assert exc.value.status_code==422
    assert not db.get(ImportJob,"job").published_at


def test_wrong_bank_scope_rejected_and_active_job_cannot_publish(db):
    add_question(db)
    admin=db.get(User,"admin")
    with pytest.raises(HTTPException) as exc:publish_bank_import("wrong-bank","job",admin,db)
    assert exc.value.status_code==404
    job=db.get(ImportJob,"job");job.status=ImportStatus.PROCESSING;db.commit()
    assert preview_bank_import("bank","job",admin,db)["blocking_reasons"]["job_active"]==1
    with pytest.raises(HTTPException):publish_bank_import("bank","job",admin,db)
    assert not job.published_at


def test_approval_rejects_placeholder_and_null_answers_at_both_boundaries(db):
    question=add_question(db,answer=None)
    question.approved_at=None;db.commit()
    admin=db.get(User,"admin")
    with pytest.raises(HTTPException):approve_question("job",question.id,admin,db)
    data=QuestionIn(**content(options=[{"markdown":"A"},{"markdown":"B"}]))
    with pytest.raises(HTTPException):resolve_bank_question("bank",question.id,data,admin,db,ignore_warnings=True)
    db.rollback()
    assert db.get(Question,question.id).approved_at is None


def test_shared_validator_allows_incomplete_draft_and_invalidates_approval(db):
    question=add_question(db)
    result=save_question_draft(question.id,QuestionIn(**content(answer=None)),db.get(User,"admin"),db)
    assert not result["approved"] and not result["readiness"]["content_valid"]
    assert question.approved_at is None
    assert question.interaction["readiness"]["validator_version"]==VALIDATOR_VERSION


def test_readiness_report_scans_beyond_latest_100_without_changing_records(db):
    for index in range(105):add_question(db,str(index),answer=None if index==0 else {"option":0})
    report=bank_readiness_report("bank",db.get(User,"admin"),db)
    assert report["summary"]["total"]==105
    assert report["summary"]["blocked"]==1
    assert report["affected_questions"][0]["question_id"]=="0"
    assert not report["mutated"] and db.get(Question,"0").approved_at is not None


def test_new_snapshot_rejects_unreleased_material_and_old_snapshots_remain_unchanged(db):
    question=add_question(db)
    admin=db.get(User,"admin")
    historical={"questions":[content(id=question.id,answer={"option":1})]}
    assessment=Assessment(id="assessment",owner_id=admin.id,title="Old test",subject="Math",duration_seconds=60)
    version=Version(id="version",test_id=assessment.id,ordinal=1,content=historical,published_at=datetime.now(timezone.utc))
    db.add_all([assessment,version]);db.commit()
    with pytest.raises(HTTPException):snapshot_questions(db,[question.id],admin.id)
    publish_question_bank("bank",admin,db)
    assert db.get(Version,"version").content==historical
    assert snapshot_questions(db,[question.id],admin.id)[0]["answer"]=={"option":0}


def test_invalid_saved_test_draft_cannot_be_newly_published(db):
    assessment=Assessment(id="assessment",owner_id="admin",title="Draft test",subject="Math",duration_seconds=60)
    version=Version(id="version",test_id=assessment.id,ordinal=1,content={"questions":[content(answer=None)]})
    db.add_all([assessment,version]);db.commit()
    with pytest.raises(HTTPException):publish_test(assessment.id,db.get(User,"admin"),db)
    assert db.query(Version).count()==1 and not assessment.published_version_id


def test_preview_and_publication_http_contract_and_permissions(db):
    add_question(db)
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[current_user]=lambda:db.get(User,"admin")
    try:
        client=TestClient(app)
        preview=client.get("/admin/question-banks/bank/imports/job/publication-preview")
        assert preview.status_code==200 and preview.json()["eligible"]==1
        released=client.post("/admin/question-banks/bank/imports/job/publish")
        assert released.status_code==200 and released.json()["publication"]["published_count"]==1
        app.dependency_overrides[current_user]=lambda:User(id="teacher",role=Role.TEACHER)
        assert client.get("/admin/question-banks/bank/publication-preview").status_code==403
        assert client.post("/admin/question-banks/bank/publish").status_code==403
    finally:
        app.dependency_overrides.clear()


def test_import_cannot_auto_approve_the_audited_not_provided_options(db,monkeypatch):
    import fitz
    from app.bank_imports import _save_questions
    monkeypatch.setattr("app.bank_imports.put",lambda *args:None)
    doc=fitz.open();page=doc.new_page(width=400,height=500)
    page.insert_text((30,50),"1. As the bubble moves upwards, select the correct relation.")
    item={"number":"18","kind":"single_choice","stem_markdown":"As the bubble moves upwards, select the correct relation.",
          "options":[{"value":letter,"markdown":"Not provided" if letter!="d" else "Correct"} for letter in "abcd"],
          "answer":{"option":0},"solution_markdown":"The pressure decreases as the bubble moves upwards.","confidence":.99,
          "chapter":"Properties of Matter","source_spans":[{"page":1,"bbox":[20,20,380,150]}],"diagrams":[]}
    try:
        counts=_save_questions(db,db.get(QuestionBank,"bank"),db.get(ImportJob,"job"),db.get(SourceDocument,"source"),"Physics",doc,[],[item],set(),{1})
        assert counts==(1,1,0)
        question=db.query(Question).one()
        assert question.approved_at is None
        assert "placeholder_option" in codes(validate_question(question))
    finally:
        doc.close()


def test_bulk_generation_approval_rechecks_content_instead_of_trusting_run_success(db):
    from app.solution_generation import approve_generation
    question=add_question(db,options=[{"markdown":"Not provided"},{"markdown":"Correct"}],solution_markdown="The answer follows from the calculation.")
    question.approved_at=None
    question.interaction={"solution_generation":{"run_id":"run","status":"completed"},"ingestion_issues":["llm_generated_unreviewed"]}
    job=db.get(ImportJob,"job");job.extraction_meta={"solution_generation":{"run_id":"run","status":"completed"}}
    db.commit()
    assert approve_generation(db,job,"run","admin")==0
    assert question.approved_at is None and not validate_question(question).content_valid


def test_generation_does_not_overwrite_a_concurrent_human_answer_edit(db):
    from app.solution_generation import generate_question
    question=add_question(db,answer=None)
    question.interaction={"solution_generation":{"requested_fields":["answer","solution"]}}
    db.commit()
    def provider(_payload):
        with sessionmaker(bind=db.bind)() as other:
            current=other.get(Question,question.id)
            current.answer={"option":1}
            other.commit()
        return {"can_solve":True,"answer":{"option":0},"solution_markdown":"Two plus two equals four.","confidence":.99,"notes":"Solved"}
    with pytest.raises(ValueError,match="changed during generation"):
        generate_question(db,question,db.get(ImportJob,"job"),provider=provider)
    assert db.get(Question,question.id).answer=={"option":1}


def test_malformed_choice_keeps_its_stored_type_in_the_repair_payload(db):
    question=add_question(db,options=[])
    view=question_view(question,db)
    assert view["kind"]=="single_choice"
    assert not view["readiness"]["content_valid"]


def test_invalid_approval_http_errors_include_actionable_reasons(db):
    question=add_question(db,answer=None)
    question.approved_at=None;db.commit()
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[current_user]=lambda:db.get(User,"admin")
    try:
        response=TestClient(app).post("/teacher/imports/job/questions/q/approve")
        assert response.status_code==422
        detail=response.json()["detail"]
        assert detail["readiness"]["blocking"][0]["code"]=="invalid_answer"
        assert "valid answer" in detail["message"]
    finally:
        app.dependency_overrides.clear()


def test_malformed_trusted_key_requires_explicit_repair_instead_of_generation(db):
    from app.solution_generation import generate_question
    question=add_question(db,answer={"option":None},interaction={"answer_key_authoritative":True})
    def unexpected_provider(_payload):
        pytest.fail("Generation must not attempt to replace a trusted malformed key")
    with pytest.raises(ValueError,match="trusted source answer key is malformed"):
        generate_question(db,question,db.get(ImportJob,"job"),provider=unexpected_provider)
    assert question.answer=={"option":None}


def test_acknowledged_pause_allows_explicit_release_of_valid_partial_output(db):
    add_question(db)
    job=db.get(ImportJob,"job");job.status=ImportStatus.UPLOADED;job.extraction_meta={"pause_requested":True,"phase":"Paused by administrator"};db.commit()
    assert preview_bank_import("bank","job",db.get(User,"admin"),db)["eligible"]==1
    assert publish_bank_import("bank","job",db.get(User,"admin"),db)["publication"]["published_count"]==1
