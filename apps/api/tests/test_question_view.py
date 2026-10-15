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
