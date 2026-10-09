import pytest
from app.tasks import validate

def test_choice_requires_multiple_options():
    with pytest.raises(ValueError):validate({"kind":"single_choice","stem_markdown":"$x$","options":["a"]})
def test_rejects_malformed_latex_delimiter():
    with pytest.raises(ValueError):validate({"kind":"numerical","stem_markdown":"$$$ x","options":[]})
