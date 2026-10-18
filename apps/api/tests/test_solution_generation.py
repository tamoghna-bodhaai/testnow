import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
import pytest

from app.db import Base
from app.main import _generation_job, import_view, question_view
from app.models import (
    AuditEvent,
    ImportJob,
    ImportStatus,
    Question,
    QuestionBank,
    QuestionKind,
    QuestionScope,
    Role,
    SourceDocument,
    QuestionTaxonomy,
    TaxonomyNode,
    User,
)
from app.solution_generation import approve_generation, generate_question, missing_generation_fields, queue_generation, run_generation


def setup_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    admin = User(id="admin", name="Admin", email="admin@example.com", role=Role.ADMIN)
    bank = QuestionBank(id="bank", name="Bank", slug="bank", exam_code="JEE")
    source = SourceDocument(
        id="source",
        owner_id="admin",
        filename="source.pdf",
        object_key="source.pdf",
        sha256="source-hash",
        media_type="application/pdf",
        size_bytes=100,
    )
    job = ImportJob(
        id="job",
        owner_id="admin",
        bank_id="bank",
        question_document_id="source",
        status=ImportStatus.REVIEW,
    )
    db.add_all([admin, bank, source, job])
    db.commit()
    return db, admin, bank, job


def question_record(db, *, kind=QuestionKind.SINGLE, answer=None, solution=None):
    question = Question(
        id="question",
        owner_id="admin",
        bank_id="bank",
        scope=QuestionScope.GLOBAL,
        import_job_id="job",
        source_number="1",
        kind=kind,
        stem_markdown="What is two plus two?",
        options=[
            {"value": "a", "markdown": "4"},
            {"value": "b", "markdown": "5"},
        ],
        answer=answer,
        solution_markdown=solution,
        interaction={"ingestion_issues": []},
    )
    db.add(question)
    db.commit()
    return question


def test_question_view_reports_missing_fields_for_generation():
    db, _, _, _ = setup_db()
    question = question_record(db)

    view = question_view(question, db)

    assert view["generation_eligible"] is True
    assert view["generation_missing_fields"] == ["answer", "solution"]


def test_subjective_questions_generate_only_a_solution():
    db, _, _, _ = setup_db()
    question = question_record(db, kind=QuestionKind.SUBJECTIVE)

    view = question_view(question, db)

    assert view["generation_missing_fields"] == ["solution"]


@pytest.mark.parametrize("kind", [
    QuestionKind.SINGLE,
    QuestionKind.MULTIPLE,
    QuestionKind.NUMERICAL,
    QuestionKind.INTEGER,
    QuestionKind.MATRIX,
    QuestionKind.ASSERTION,
])
def test_legacy_objective_questions_are_eligible_without_issue_flags(kind):
    db, _, _, _ = setup_db()
    question = question_record(db, kind=kind, solution="   \n")
    question.interaction = {}

    assert missing_generation_fields(question) == ["answer", "solution"]


def test_populated_fields_are_not_generation_eligible():
    db, _, _, _ = setup_db()
    question = question_record(db, answer={"option": 0}, solution="Already solved")

    assert missing_generation_fields(question) == []


@pytest.mark.parametrize(("kind", "answer"), [
    (QuestionKind.SINGLE, {"option": None}),
    (QuestionKind.MULTIPLE, {"options": []}),
    (QuestionKind.MULTIPLE, {"options": [{}]}),
    (QuestionKind.NUMERICAL, {"value": "  \n"}),
    (QuestionKind.NUMERICAL, {"value": "not-a-number"}),
    (QuestionKind.NUMERICAL, {"value": "NaN"}),
    (QuestionKind.INTEGER, {"value": "2.5"}),
    (QuestionKind.MATRIX, {"matches": {}}),
    (QuestionKind.ASSERTION, {"option": " "}),
])
def test_malformed_legacy_answer_shapes_are_generation_eligible(kind, answer):
    db, _, _, _ = setup_db()
    question = question_record(db, kind=kind, answer=answer, solution="Existing solution")
    question.interaction = {}

    assert missing_generation_fields(question) == ["answer"]


def test_generation_replaces_a_semantically_blank_legacy_answer():
    db, admin, _, job = setup_db()
    question = question_record(
        db,
        kind=QuestionKind.NUMERICAL,
        answer={"value": "   "},
        solution="1. Evaluate the expression.\n\n2. The resulting value is $4$.",
    )
    question.options = []
    queue_generation(db, job, admin.id, question)

    generate_question(db, question, job, provider=lambda _: {
        "can_solve": True,
        "answer": {"value": 4},
        "matrix_items": [],
        "matrix_choices": [],
        "solution_markdown": "1. Evaluate the expression.\n\n2. The resulting value is $4$.",
        "confidence": 0.95,
        "notes": "Recovered a blank legacy key.",
    })

    assert question.answer == {"value": 4}
    assert question.solution_markdown.startswith("1.")


def test_import_view_reports_generation_eligible_count():
    db, _, _, job = setup_db()
    question_record(db)

    view = import_view(job, db)

    assert view["generation_eligible_count"] == 1


def test_generation_fills_only_missing_fields_and_stages_review():
    db, admin, _, job = setup_db()
    question = question_record(db, answer={"option": 0})
    queue_generation(db, job, admin.id, question)
    db.commit()

    result = generate_question(db, question, job, provider=lambda _: {
        "can_solve": True,
        "answer": {"option": 0},
        "solution_markdown": "1. Add the two quantities.\n\n2. Therefore, $2+2=4$, which is option A.",
        "confidence": 0.99,
        "notes": "Direct arithmetic.",
    })

    assert question.answer == {"option": 0}
    assert question.solution_markdown.endswith("option A.")
    assert result["generated_fields"] == ["solution"]
    assert question.approved_at is None
    assert "llm_generated_unreviewed" in question.interaction["ingestion_issues"]


def test_generation_rejects_solution_that_conflicts_with_existing_answer():
    db, admin, _, job = setup_db()
    question = question_record(db, answer={"option": 0})
    queue_generation(db, job, admin.id, question)

    try:
        generate_question(db, question, job, provider=lambda _: {
            "can_solve": True,
            "answer": {"option": 1},
            "solution_markdown": "1. Evaluate the expression carefully.\n\n2. The result is five, corresponding to option B.",
            "confidence": 0.7,
            "notes": "Conflict.",
        })
    except ValueError as exc:
        assert "conflicts" in str(exc)
    else:
        raise AssertionError("A conflicting generated solution must fail")

    assert question.solution_markdown is None
    assert question.answer == {"option": 0}


def test_batch_approval_clears_all_warnings_for_successful_generated_questions():
    db, admin, _, job = setup_db()
    question = question_record(db, answer={"option": 0})
    question.interaction = {"ingestion_issues": ["taxonomy_uncertain", "missing_solution"]}
    state = queue_generation(db, job, admin.id, question)
    generate_question(db, question, job, provider=lambda _: {
        "can_solve": True,
        "answer": {"option": 0},
        "solution_markdown": "1. Add two and two.\n\n2. The value is $4$, so the correct choice is option A.",
        "confidence": 0.99,
        "notes": "Solved.",
    })
    job.extraction_meta = {**job.extraction_meta, "solution_generation": {
        **job.extraction_meta["solution_generation"], "status": "completed",
    }}

    approved = approve_generation(db, job, state["run_id"], admin.id)

    assert approved == 1
    assert question.approved_at is not None
    assert question.interaction["ingestion_issues"] == []
    assert "taxonomy_uncertain" in question.interaction["ignored_ingestion_issues"]
    event = db.query(AuditEvent).filter_by(action="question_solution_generation.approved").one()
    assert event.data["warnings_overridden"] == [{"question_id": question.id, "warnings": ["taxonomy_uncertain", "llm_generated_unreviewed"]}]


def test_only_one_generation_run_can_be_active_per_source_document():
    db, admin, _, job = setup_db()
    question_record(db)
    queue_generation(db, job, admin.id)

    with pytest.raises(ValueError, match="already active"):
        queue_generation(db, job, admin.id)


def test_historical_run_cannot_be_approved_while_a_retry_is_active():
    db, admin, _, job = setup_db()
    question = question_record(db)
    first = queue_generation(db, job, admin.id, question)
    current = {**job.extraction_meta["solution_generation"], "status": "failed"}
    job.extraction_meta = {**job.extraction_meta, "solution_generation": current}
    db.flush()
    queue_generation(db, job, admin.id, question)

    with pytest.raises(ValueError, match="active solution-generation run"):
        approve_generation(db, job, first["run_id"], admin.id)


def test_published_source_requires_a_bank_revision_before_generation():
    db, _, bank, job = setup_db()
    question_record(db)
    job.published_at = bank.created_at
    db.commit()

    with pytest.raises(HTTPException) as caught:
        _generation_job(bank.id, job.id, db)

    assert caught.value.status_code == 409
    assert "revision" in caught.value.detail.lower()
    with pytest.raises(ValueError, match="revision"):
        queue_generation(db, job, "admin")


def test_document_generation_checkpoints_success_and_continues_after_failure(monkeypatch):
    db, admin, _, job = setup_db()
    first = question_record(db)
    second = Question(
        id="question-2", owner_id="admin", bank_id="bank", scope=QuestionScope.GLOBAL,
        import_job_id="job", source_number="2", kind=QuestionKind.SUBJECTIVE,
        stem_markdown="Explain why the sky appears blue.", options=[], answer=None,
        solution_markdown=None, interaction={"ingestion_issues": ["missing_solution"]},
    )
    db.add(second); db.commit()
    state = queue_generation(db, job, admin.id); db.commit()
    run_id, job_id = state["run_id"], job.id
    calls = 0

    def provider(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("provider unavailable")
        return {
            "can_solve": True,
            "answer": {"option": 0},
            "solution_markdown": "1. Add the two values.\n\n2. The result is $4$, which corresponds to option A.",
            "confidence": 0.98,
            "notes": "Solved.",
        }

    worker_sessions = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr("app.solution_generation.SessionLocal", worker_sessions)
    monkeypatch.setattr("app.solution_generation._provider_request", provider)
    run_generation(job_id, run_id, admin.id)

    db.expire_all()
    saved = db.get(ImportJob, job_id).extraction_meta["solution_generation"]
    assert saved["status"] == "completed_with_errors"
    assert saved["processed"] == 2
    assert saved["succeeded"] == 1
    assert saved["failed"] == 1
    rows = db.query(Question).filter_by(import_job_id=job_id).all()
    assert sum(bool(row.solution_markdown) for row in rows) == 1
    assert sum((row.interaction or {}).get("solution_generation", {}).get("status") == "failed" for row in rows) == 1
    retry = queue_generation(db, db.get(ImportJob, job_id), admin.id)
    assert retry["total"] == 1
    assert db.get(ImportJob, job_id).extraction_meta["solution_generation_history"][0]["run_id"] == run_id


def test_solver_receives_taxonomy_and_records_the_actual_fallback_model():
    db, admin, _, job = setup_db()
    question = question_record(db, answer={"option": 0})
    subject = TaxonomyNode(id="subject", bank_id="bank", parent_id=None, kind="subject", name="Physics")
    chapter = TaxonomyNode(id="chapter", bank_id="bank", parent_id="subject", kind="chapter", name="Mechanics")
    db.add_all([subject, chapter]); db.flush()
    db.add_all([QuestionTaxonomy(question_id=question.id, taxonomy_node_id=subject.id), QuestionTaxonomy(question_id=question.id, taxonomy_node_id=chapter.id)])
    queue_generation(db, job, admin.id, question)
    captured = {}

    def provider(payload):
        captured.update(payload)
        return ({
            "can_solve": True, "answer": {"option": 0},
            "solution_markdown": "1. Add the given values.\n\n2. The result is $4$, corresponding to option A.",
            "confidence": 0.95, "notes": "Solved.",
        }, "fallback/model")

    generate_question(db, question, job, provider=provider)

    prompt = json.loads(captured["messages"][1]["content"][0]["text"])
    assert {item["name"] for item in prompt["taxonomy"]} == {"Physics", "Mechanics"}
    assert question.interaction["solution_generation"]["model"] == "fallback/model"


def test_solver_rejects_out_of_range_multiple_choice_output():
    db, admin, _, job = setup_db()
    question = question_record(db, kind=QuestionKind.MULTIPLE)
    queue_generation(db, job, admin.id, question)

    with pytest.raises(ValueError, match="valid answer"):
        generate_question(db, question, job, provider=lambda _: {
            "can_solve": True, "answer": {"options": [0, 99]},
            "solution_markdown": "1. Check every supplied choice.\n\n2. The selected choices are A and an invalid option.",
            "confidence": 0.5, "notes": "Invalid.",
        })
    assert question.answer is None and question.solution_markdown is None


@pytest.mark.parametrize(("kind", "value"), [
    (QuestionKind.NUMERICAL, "not-a-number"),
    (QuestionKind.NUMERICAL, "NaN"),
    (QuestionKind.NUMERICAL, "Infinity"),
    (QuestionKind.INTEGER, "2.5"),
])
def test_solver_rejects_unscorable_generated_numeric_answers(kind, value):
    db, admin, _, job = setup_db()
    question = question_record(db, kind=kind)
    question.options = []
    queue_generation(db, job, admin.id, question)

    with pytest.raises(ValueError, match="valid answer"):
        generate_question(db, question, job, provider=lambda _: {
            "can_solve": True,
            "answer": {"value": value},
            "matrix_items": [],
            "matrix_choices": [],
            "solution_markdown": "1. Evaluate the supplied expression.\n\n2. Report the resulting value after checking it.",
            "confidence": 0.5,
            "notes": "Invalid numeric result.",
        })


def test_solver_includes_saved_question_images(monkeypatch):
    db, admin, _, job = setup_db()
    question = question_record(db, answer={"option": 0})
    question.diagrams = [{"object_key": "diagram.png", "role": "question"}]
    queue_generation(db, job, admin.id, question)
    monkeypatch.setattr("app.solution_generation.get", lambda _: b"image-bytes")
    captured = {}

    def provider(payload):
        captured.update(payload)
        return {
            "can_solve": True, "answer": {"option": 0},
            "solution_markdown": "1. Inspect the supplied diagram.\n\n2. It supports option A as the correct answer.",
            "confidence": 0.9, "notes": "Used image.",
        }

    generate_question(db, question, job, provider=provider)
    assert any(part["type"] == "image_url" for part in captured["messages"][1]["content"])


def test_generation_aborts_if_source_becomes_published_before_write():
    db, admin, _, job = setup_db()
    question = question_record(db, answer={"option": 0})
    queue_generation(db, job, admin.id, question)
    job.published_at = datetime.now(timezone.utc); db.flush()

    with pytest.raises(ValueError, match="became live"):
        generate_question(db, question, job, provider=lambda _: {
            "can_solve": True, "answer": {"option": 0},
            "solution_markdown": "1. Add the two values.\n\n2. The result is four, corresponding to option A.",
            "confidence": 0.99, "notes": "Solved.",
        })
    assert question.solution_markdown is None


def test_numerical_solution_accepts_equivalent_string_and_number_answers():
    db, admin, _, job = setup_db()
    question = question_record(db, kind=QuestionKind.NUMERICAL, answer={"value": "4"})
    question.options = []
    queue_generation(db, job, admin.id, question)

    generate_question(db, question, job, provider=lambda _: {
        "can_solve": True, "answer": {"value": 4},
        "solution_markdown": "1. Evaluate the supplied expression.\n\n2. Its numerical value is $4$, matching the existing key.",
        "confidence": 0.99, "notes": "Equivalent value.",
    })
    assert question.answer == {"value": "4"}
    assert question.solution_markdown


def test_subjective_generation_writes_a_model_solution_without_an_answer():
    db, admin, _, job = setup_db()
    question = question_record(db, kind=QuestionKind.SUBJECTIVE)
    question.options = []
    queue_generation(db, job, admin.id, question)

    generate_question(db, question, job, provider=lambda _: {
        "can_solve": True, "answer": None,
        "solution_markdown": "1. State the governing principle clearly.\n\n2. Apply it to the facts and explain the conclusion.",
        "confidence": 0.9, "notes": "Model response.",
    })
    assert question.answer is None
    assert question.solution_markdown.startswith("1.")


def test_matrix_generation_can_recover_a_legacy_contract_and_rejects_duplicates():
    db, admin, _, job = setup_db()
    question = question_record(db, kind=QuestionKind.MATRIX)
    question.options = []
    queue_generation(db, job, admin.id, question)
    result = {
        "can_solve": True, "answer": {"matches": {"A": ["P", "P"]}},
        "matrix_items": ["A"], "matrix_choices": ["P", "Q"],
        "solution_markdown": "1. Compare row A with every column.\n\n2. The proposed mapping repeats column P.",
        "confidence": 0.7, "notes": "Duplicate.",
    }
    with pytest.raises(ValueError, match="valid answer"):
        generate_question(db, question, job, provider=lambda _: result)
    with pytest.raises(ValueError, match="valid answer"):
        generate_question(db, question, job, provider=lambda _: result)

    valid = {
        **result,
        "answer": {"matches": {"A": ["P"]}},
        "solution_markdown": "1. Compare row A with columns P and Q.\n\n2. The stated relation matches A only with P.",
    }
    generate_question(db, question, job, provider=lambda _: valid)
    assert question.answer == {"matches": {"A": ["P"]}}
    assert question.interaction["matrix_items"] == ["A"]
    assert question.interaction["matrix_choices"] == ["P", "Q"]


def test_matrix_generation_requires_a_mapping_for_every_row():
    db, admin, _, job = setup_db()
    question = question_record(db, kind=QuestionKind.MATRIX)
    question.options = []
    queue_generation(db, job, admin.id, question)

    with pytest.raises(ValueError, match="valid answer"):
        generate_question(db, question, job, provider=lambda _: {
            "can_solve": True,
            "answer": {"matches": {"A": ["P"]}},
            "matrix_items": ["A", "B"],
            "matrix_choices": ["P", "Q"],
            "solution_markdown": "1. Compare both rows with the columns.\n\n2. Only row A was included, so the mapping is incomplete.",
            "confidence": 0.6,
            "notes": "Incomplete mapping.",
        })


def test_legacy_matrix_contract_allows_a_column_shared_by_multiple_rows():
    db, admin, _, job = setup_db()
    answer = {"matches": {"A": ["P"], "B": ["P", "Q"]}}
    question = question_record(db, kind=QuestionKind.MATRIX, answer=answer)
    question.options = []
    queue_generation(db, job, admin.id, question)

    generate_question(db, question, job, provider=lambda _: {
        "can_solve": True,
        "answer": answer,
        "matrix_items": [],
        "matrix_choices": [],
        "solution_markdown": "1. Compare rows A and B with both columns.\n\n2. A matches P, while B matches P and Q.",
        "confidence": 0.9,
        "notes": "Matches the stored key.",
    })

    assert question.answer == answer
    assert question.interaction["matrix_items"] == ["A", "B"]
    assert question.interaction["matrix_choices"] == ["P", "Q"]

