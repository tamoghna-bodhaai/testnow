from types import SimpleNamespace

from app.main import question_view
from app.models import QuestionKind, QuestionScope


def test_question_view_tolerates_legacy_non_numeric_confidence():
    question=SimpleNamespace(
        id="question-1",
        source_number="1",
        kind=QuestionKind.SINGLE,
        stem_markdown="Question",
        options=["One", "Two"],
        answer={"option": 0},
        solution_markdown=None,
        scoring={"correct": 4, "incorrect": -1, "unanswered": 0},
        diagrams=[],
        source_spans=[],
        interaction={},
        confidence="not recorded",
        approved_at=None,
        bank_id=None,
        import_job_id=None,
        scope=QuestionScope.PRIVATE,
    )

    assert question_view(question)["confidence"] is None


def test_question_view_does_not_requery_preloaded_empty_taxonomy():
    question=SimpleNamespace(
        id="question-2", source_number="2", kind=QuestionKind.SINGLE,
        stem_markdown="Question", options=["One", "Two"], answer={"option":0},
        solution_markdown=None, scoring={}, diagrams=[], source_spans=[],
        interaction={}, confidence=1, approved_at=None, bank_id="bank-1",
        import_job_id=None, scope=QuestionScope.GLOBAL,
    )

    assert question_view(question, object(), taxonomy=[])["taxonomy"] == []
