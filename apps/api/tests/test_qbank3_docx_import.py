from pathlib import Path

import fitz
import pytest

from app.import_qbank3_docx import (chemistry_pairs, clean_box, kind_for_heading,
                                    normalized_chapter, subject_for_path,
                                    validate_item)
from app.models import QuestionKind


def test_question_type_headings_map_to_existing_platform_kinds():
    assert kind_for_heading("Objective Questions I Only one correct option") == QuestionKind.SINGLE
    assert kind_for_heading("Objective Question II One or more than one correct option") == QuestionKind.MULTIPLE
    assert kind_for_heading("Assertion and Reason") == QuestionKind.ASSERTION
    assert kind_for_heading("Fill in the Blanks") == QuestionKind.SUBJECTIVE
    assert kind_for_heading("Matrix Match") == QuestionKind.MATRIX


def test_chemistry_pairing_deduplicates_identical_solution_file(tmp_path: Path):
    question = tmp_path / "Chapter_06_Equilibrium_Questions.docx"
    solution = tmp_path / "Chapter_06_Equilibrium_Answer_Key_and_Solutions.docx"
    duplicate = tmp_path / "Chapter_06_Equilibrium_Answer_Key_and_Solutions (1).docx"
    question.write_bytes(b"question")
    solution.write_bytes(b"solution")
    duplicate.write_bytes(b"solution")

    pairs, duplicates = chemistry_pairs([question, solution, duplicate])

    assert pairs == [(question, solution)]
    assert duplicates == [duplicate]
    assert normalized_chapter(question.name) == normalized_chapter(solution.name)


def test_chapter_without_solution_is_kept_for_unscored_import(tmp_path: Path):
    question = tmp_path / "Chapter_04_Chemical_Bonding_Questions.docx"
    question.write_bytes(b"question")
    assert chemistry_pairs([question])[0] == [(question, None)]


def test_invalid_choice_and_latex_are_rejected_before_publication():
    with pytest.raises(ValueError, match="fewer"):
        validate_item({"kind": "single_choice", "options": [{"value": "a", "markdown": "one"}], "stem_markdown": "A question stem"})
    with pytest.raises(ValueError, match="malformed"):
        validate_item({"kind": "numerical", "options": [], "stem_markdown": "$$$ x = 1"})


def test_diagram_box_rejects_whole_page_crops():
    document = fitz.open()
    page = document.new_page(width=200, height=100)
    try:
        assert clean_box([10, 10, 80, 80], page) == [10.0, 10.0, 80.0, 80.0]
        assert clean_box([0, 0, 200, 100], page) is None
    finally:
        document.close()


def test_subject_is_derived_only_from_the_source_directory():
    assert subject_for_path(Path("/imports/physics/anything.docx")) == "Physics"
    assert subject_for_path(Path("/imports/maths/anything.docx")) == "Mathematics"
