import pytest
from app.models import QuestionKind
from app.seed_jee_openrouter import normalize_choice_kind
from app.tasks import normalize_item, validate

def test_choice_requires_multiple_options():
    with pytest.raises(ValueError):validate({"kind":"single_choice","stem_markdown":"$x$","options":["a"]})
def test_rejects_malformed_latex_delimiter():
    with pytest.raises(ValueError):validate({"kind":"numerical","stem_markdown":"$$$ x","options":[]})

def test_answer_key_option_is_normalized_before_review():
    item=normalize_item({"options":[{"value":"a","markdown":"first"},{"value":"b","markdown":"second"}],"answer":{"value":"B"},"confidence":1})
    assert item["answer"]=={"option":1}

def test_optionless_choice_record_becomes_subjective():
    assert normalize_choice_kind(QuestionKind.SINGLE, []) == QuestionKind.SUBJECTIVE

def test_choice_record_with_choices_keeps_its_type():
    options=[{"value":"a","markdown":"first"},{"value":"b","markdown":"second"}]
    assert normalize_choice_kind(QuestionKind.MULTIPLE, options) == QuestionKind.MULTIPLE
