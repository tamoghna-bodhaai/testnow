import pytest
from app.tasks import normalize_item, validate

def test_choice_requires_multiple_options():
    with pytest.raises(ValueError):validate({"kind":"single_choice","stem_markdown":"$x$","options":["a"]})
def test_rejects_malformed_latex_delimiter():
    with pytest.raises(ValueError):validate({"kind":"numerical","stem_markdown":"$$$ x","options":[]})

def test_answer_key_option_is_normalized_before_review():
    item=normalize_item({"options":[{"value":"a","markdown":"first"},{"value":"b","markdown":"second"}],"answer":{"value":"B"},"confidence":1})
    assert item["answer"]=={"option":1}
