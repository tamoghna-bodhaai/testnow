from app.services import answers_match, safe_question_kind, score

def test_optionless_choice_is_safe_to_display_as_written_question():
    assert safe_question_kind("single_choice", []) == "subjective"
    assert safe_question_kind("single_choice", [{"markdown":"A"}, {"markdown":"B"}]) == "single_choice"
from app.map_jee_solutions import canonical_chapter


def test_math_chapter_normalization_collapses_spacing_and_aliases():
    assert canonical_chapter("Mathematics", "M atrices & Determinants") == "Matrices & Determinants"
    assert canonical_chapter("Mathematics", "Matrices and Determinants") == "Matrices & Determinants"
    assert canonical_chapter("Mathematics", "Complex Number") == "Complex Numbers"
    assert canonical_chapter("Physics", "M atrices & Determinants") == "Matrices & Determinants"

def test_scoring_uses_question_policy():
    content={"questions":[
      {"id":"a","answer":{"option":1},"scoring":{"correct":4,"incorrect":-1,"unanswered":0}},
      {"id":"b","answer":{"value":"42"},"scoring":{"correct":3,"incorrect":0,"unanswered":0}},
    ]}
    total,review=score(content,{"a":{"option":1},"b":{"value":"0"}})
    assert total==4
    assert review[0]["correct"] is True
    assert review[1]["points"]==0

def test_unanswered_is_not_wrong():
    total,review=score({"questions":[{"id":"a","answer":{"option":0},"scoring":{"correct":4,"incorrect":-1,"unanswered":0}}]}, {})
    assert total==0 and review[0]["points"]==0

def test_multi_correct_requires_the_complete_set():
    question={"kind":"multiple_choice","answer":{"options":[0,2]}}
    assert answers_match(question,{"options":[0,2]})
    assert not answers_match(question,{"options":[0]})
    assert answers_match(question,{"options":[2,0]})

def test_numerical_tolerance_and_integer_exactness():
    assert answers_match({"kind":"numerical","answer":{"value":2.0},"interaction":{"tolerance":0.01}},{"value":"2.009"})
    assert not answers_match({"kind":"integer","answer":{"value":2},"interaction":{}},{"value":"2.0001"})
