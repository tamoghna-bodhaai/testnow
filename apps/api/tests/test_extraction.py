import subprocess

import pytest
from app.bank_imports import _node, _pdf_bytes
from app.models import QuestionBank, QuestionKind, SourceDocument, TaxonomyNode
from app.seed_jee_openrouter import normalize_choice_kind
from app.tasks import normalize_item, validate
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
