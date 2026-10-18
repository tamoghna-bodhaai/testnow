import subprocess

import pytest
from app.bank_imports import _node, _pdf_bytes
from app.main import clear_bank_import_output
from app.models import ImportJob, ImportStatus, Question, QuestionBank, QuestionKind, QuestionScope, QuestionTaxonomy, SourceDocument, TaxonomyNode
from app.seed_jee_openrouter import normalize_choice_kind
from app.tasks import answer_from_key, best_page_text, embedded_answer_key_text, expected_questions, extraction_units, normalize_item, page_unit_content, prepare_item_for_ingestion, solution_from_key, unit_requires_vision, validate
from io import BytesIO
import fitz
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base

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

def test_optionless_choice_is_recovered_and_flagged_instead_of_failing_import():
    item,issues=prepare_item_for_ingestion({"kind":"single_choice","stem_markdown":"Solve $x$","options":[],"answer":{"option":0}})
    assert item["kind"] == "subjective"
    assert item["answer"] is None
    assert issues == ["choice_options_missing"]

def test_invalid_model_record_is_skipped_without_raising():
    item,issues=prepare_item_for_ingestion({"kind":"numerical","stem_markdown":"$$$ x","options":[]})
    assert item is None
    assert issues == ["invalid_record:Malformed LaTex delimiters"]

def test_flattened_labeled_options_are_recovered_as_a_choice_question():
    item=normalize_item({"kind":"subjective","stem_markdown":"What is x?\n(A) 1\n(B) 2\n(C) 3\n(D) 4","options":[],"answer":{"value":"B"},"confidence":1})
    assert item["kind"] == "single_choice"
    assert item["stem_markdown"] == "What is x?"
    assert [option["markdown"] for option in item["options"]] == ["1","2","3","4"]
    assert item["answer"] == {"option":1}

def test_page_unit_owns_only_the_primary_page_and_has_continuation_context():
    content=page_unit_content(["Question 7", "options continue here", "Question 8"],0,"answer key")
    assert "BEGINS on primary source pages 1-1" in content
    assert "CONTINUATION CONTEXT PAGE 2" in content
    assert "Question 8" not in content

def test_native_answer_key_lookup_maps_only_explicit_numbered_choice():
    options=[{"value":"a","markdown":"one"},{"value":"b","markdown":"two"}]
    assert answer_from_key("42",options,"41. A\n42. (B)\n43. A") == {"option":1}
    assert answer_from_key("44",[],"44. -2.5") == {"value":"-2.5"}
    assert answer_from_key("42",options,"The answer to 42 may be B in some versions.") is None

def test_layout_sorted_pdf_text_keeps_late_inline_numerals_in_their_visual_position():
    document=fitz.open();page=document.new_page(width=600,height=800)
    prefix="Q10. An elevator cab of mass";first="800 kg";middle="carries a load of";second="200 kg"
    x1=40+fitz.get_text_length(prefix,fontsize=11)+6
    xm=x1+fitz.get_text_length(first,fontsize=11)+6
    x2=xm+fitz.get_text_length(middle,fontsize=11)+6
    page.insert_text((40,80),prefix,fontsize=11)
    page.insert_text((xm,80),middle,fontsize=11)
    page.insert_text((x2+fitz.get_text_length(second,fontsize=11)+3,80),".",fontsize=11)
    page.insert_text((x1,80),first,fontsize=11)
    page.insert_text((x2,80),second,fontsize=11)
    page.insert_text((40,100),"(a) 30 kW   (b) 42 kW",fontsize=11)
    raw=document.tobytes();document.close()
    rendered=fitz.open(stream=raw,filetype="pdf")
    text=best_page_text(raw,PdfReader(BytesIO(raw)),rendered)[0]
    rendered.close()
    assert "mass 800 kg" in text
    assert "load of 200 kg" in text
    assert text.index("800 kg") < text.index("(a) 30 kW")

def test_answer_and_solution_are_recovered_from_numbered_solution_block():
    key="""Q9. Pump question\n// ANSWER\nWorking: first result.\nFinal Answer: (A) 3 kW\nQ10. Elevator question\n// ANSWER\nGiven: 800 kg and 200 kg.\nWorking:\n1. F = Mg + f.\n2. P = Fv = 42 kW.\nFinal Answer: (B) 42 kW\nQ11. Next question"""
    options=[{"value":"a","markdown":"30 kW"},{"value":"b","markdown":"42 kW"}]
    assert answer_from_key("10",options,key) == {"option":1}
    solution=solution_from_key("10",key)
    assert "800 kg and 200 kg" in solution
    assert "P = Fv = 42 kW" in solution
    assert "Next question" not in solution

def test_worked_solution_final_answer_wins_over_question_starting_with_a():
    key="Q3. A force acts on a particle.\n// ANSWER\nWorking: integrate it.\nFinal Answer: (B) 18 J"
    options=[{"value":"a","markdown":"16 J"},{"value":"b","markdown":"18 J"}]
    assert answer_from_key("3",options,key) == {"option":1}

def test_numerical_worked_solution_uses_final_answer_not_first_stem_value():
    key="Q4. 2 kg is accelerated.\n// ANSWER\nWorking: calculate it.\nFinal Answer: 18.5"
    assert answer_from_key("4",[],key) == {"value":"18.5"}

def test_source_question_label_is_removed_from_stored_stem():
    item=normalize_item({"number":"20","kind":"subjective","stem_markdown":"Q20. Calculate the work done.","options":[],"answer":None,"confidence":1})
    assert item["stem_markdown"] == "Calculate the work done."

def test_sparse_pages_are_batched_but_dense_pages_are_bounded():
    pages=["cover", "index", "1 Let x be real\n2. If y is positive\n3. Find z\n4. Given a\n5. Which value\n6. The result\n7. In a set\n8. Prove that\n9. Evaluate x", "10. Let j be real"]
    assert extraction_units(pages) == [(0,3),(3,4)]


def test_scan_pages_without_text_are_independent_extraction_units():
    assert extraction_units(["", "", "", ""]) == [(0,1),(1,2),(2,3),(3,4)]
    assert extraction_units(["Readable cover", "", "Native text", ""]) == [(0,1),(1,2),(2,3),(3,4)]

def test_coaching_book_question_numbers_without_a_dot_are_detected():
    # This is the printing convention on Arihant PDF page 10, where the old
    # `number.` detector returned zero and allowed an empty model response.
    text="""1 Let z be a complex number. Determine its modulus from the stated conditions.\n(a) one (b) two\n2 All the points in S satisfying the given relation lie on a curve in the complex plane.\n3 Let x be real and evaluate the following expression using the supplied identity.\n4 If y is positive, determine the possible values after applying the relation above."""
    assert expected_questions(text) == 4
    assert unit_requires_vision([text],0,1) is False

def test_combined_document_answer_key_pages_are_detected():
    pages=["1. Question text", "ANSWER KEY\n1. A\n2. B\n3. C\n4. D\n5. A\n6. B"]
    assert "1. A" in embedded_answer_key_text(pages)

def test_docx_conversion_uses_generated_pdf_despite_javaldx_warning(monkeypatch):
    source=SourceDocument(id="source",owner_id="owner",filename="paper.docx",object_key="source.docx",sha256="x",media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",size_bytes=4)
    monkeypatch.setattr("app.bank_imports.get",lambda _:b"PK\x03\x04")
    def convert(command, **_):
        from pathlib import Path
        Path(next(part for part in command if str(part).endswith(".docx"))).with_suffix(".pdf").write_bytes(b"%PDF-1.7 converted")
        return subprocess.CompletedProcess(command, 1, "", "Warning: failed to launch javaldx")
    monkeypatch.setattr("app.bank_imports.subprocess.run",convert)
    assert _pdf_bytes(source)==b"%PDF-1.7 converted"

def test_duplicate_root_taxonomy_does_not_abort_an_import():
    engine=create_engine("sqlite+pysqlite:///:memory:"); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
    db.add(QuestionBank(id="bank",slug="bank",name="Bank",exam_code="test")); db.commit()
    first=TaxonomyNode(id="first",bank_id="bank",parent_id=None,kind="subject",name="Physics")
    second=TaxonomyNode(id="second",bank_id="bank",parent_id=None,kind="subject",name="Physics")
    db.add_all([first,second]); db.commit()
    assert _node(db,"bank",None,"subject","Physics").id in {"first","second"}

def test_taxonomy_normalization_reuses_cosmetic_chapter_variants():
    engine=create_engine("sqlite+pysqlite:///:memory:"); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
    db.add(QuestionBank(id="bank",slug="bank",name="Bank",exam_code="test")); db.commit()
    subject=_node(db,"bank",None,"subject","Physics")
    original=_node(db,"bank",subject.id,"chapter","Wave Optics")
    assert _node(db,"bank",subject.id,"chapter","wave-optics").id == original.id

def test_clean_reextraction_removes_only_the_materials_derived_questions(monkeypatch):
    engine=create_engine("sqlite+pysqlite:///:memory:"); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
    db.add(QuestionBank(id="bank",slug="bank",name="Bank",exam_code="test"))
    job=ImportJob(id="job",owner_id="owner",bank_id="bank",question_document_id="source",status=ImportStatus.FAILED)
    db.add(job)
    node=TaxonomyNode(id="node",bank_id="bank",parent_id=None,kind="subject",name="Physics")
    extracted=Question(id="extracted",owner_id="owner",bank_id="bank",scope=QuestionScope.GLOBAL,import_job_id="job",source_key="source:1",kind=QuestionKind.SUBJECTIVE,stem_markdown="Extracted",options=[],answer=None)
    untouched=Question(id="untouched",owner_id="owner",bank_id="bank",scope=QuestionScope.GLOBAL,source_key="manual:1",kind=QuestionKind.SUBJECTIVE,stem_markdown="Manual",options=[],answer=None)
    db.add_all([node,extracted,untouched]); db.flush(); db.add(QuestionTaxonomy(question_id="extracted",taxonomy_node_id="node")); db.commit()
    prefixes=[];monkeypatch.setattr("app.main.delete_prefix",lambda prefix:prefixes.append(prefix))
    assert clear_bank_import_output(db,job) == 1
    db.commit()
    assert db.get(Question,"extracted") is None
    assert db.get(Question,"untouched") is not None
    assert db.query(QuestionTaxonomy).filter_by(question_id="extracted").count() == 0
    assert prefixes == ["bank-diagrams/bank/job/"]
