from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import shutil
from zipfile import ZipFile

import fitz
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bank_imports import _pdf_bytes, _save_questions
from app.config import settings
from app.db import Base
from app.figure_review import review_figures, figure_source_page, save_question_draft
from app.figures import image_regions, vector_regions, extract_figures, mark_scope_conflicts
from app.figure_repairs import propose_repair, repair_needed, stage_repair
from app.main import question_media, resolve_bank_question
from app.models import Attempt, ImportJob, ImportStatus, Question, QuestionBank, QuestionKind, QuestionScope, Role, SourceDocument, Test as Assessment, TestVersion as AssessmentVersion, User
from app.schemas import FigureReviewIn, QuestionIn
from app.services import snapshot_questions
from app.tasks import extract_import
from app.bank_imports import run_bank_import
from app.main import import_view


def setup_db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db=sessionmaker(bind=engine)()
    admin=User(id="admin",name="Admin",email="admin@example.com",role=Role.ADMIN)
    bank=QuestionBank(id="bank",name="Bank",slug="bank",exam_code="JEE")
    source=SourceDocument(id="source",owner_id="admin",filename="source.pdf",object_key="source.pdf",sha256="source-hash",media_type="application/pdf",size_bytes=100)
    job=ImportJob(id="job",owner_id="admin",bank_id="bank",question_document_id="source",status=ImportStatus.PROCESSING)
    db.add_all([admin,bank,source,job]);db.commit()
    return db,admin,bank,source,job


def fixture_pdf():
    document=fitz.open();page=document.new_page(width=400,height=500)
    page.insert_text((30,50),"1. Which diagram shows the required shape? Select one.")
    page.draw_circle((130,150),40);page.draw_rect(fitz.Rect(260,110,340,190))
    return document


def model_item():
    return {"number":"1","kind":"single_choice","stem_markdown":"Which diagram shows a circle?",
            "options":[{"value":v,"markdown":"","requires_diagram":True} for v in ["a","b"]],
            "answer":{"option":0},"confidence":.99,"chapter":"Geometry","topic":"Shapes",
            "source_spans":[{"page":1,"bbox":[20,20,380,220]}],"diagrams":[
                {"page":1,"bbox":[85,105,175,195],"role":"option","option_index":0,"alt":"Circle"},
                {"page":1,"bbox":[255,105,345,195],"role":"option","option_index":1,"alt":"Square"},
            ],"solution_markdown":"A circle has constant radius."}


def test_bank_ingestion_preserves_visual_options_through_test_snapshot(monkeypatch):
    db,admin,bank,source,job=setup_db();doc=fixture_pdf();uploads=[]
    monkeypatch.setattr("app.bank_imports.put",lambda *args:uploads.append(args))
    counts=_save_questions(db,bank,job,source,"Physics",doc,image_regions(doc)+vector_regions(doc),[model_item()],set(),{1})
    assert counts==(1,0,0)
    q=db.query(Question).one()
    assert q.kind==QuestionKind.SINGLE and q.approved_at is not None
    snapshot=snapshot_questions(db,[q.id],admin.id)[0]
    assert [d["option_index"] for d in snapshot["diagrams"]]==[0,1]
    assert [o["markdown"] for o in snapshot["options"]]==["",""]
    assert snapshot["answer"]=={"option":0} and len(uploads)==2


def test_partial_visual_option_failure_prevents_auto_approval(monkeypatch):
    db,admin,bank,source,job=setup_db();doc=fixture_pdf();data=model_item()
    data["diagrams"][1]["page"]=0
    monkeypatch.setattr("app.bank_imports.put",lambda *args:None)
    counts=_save_questions(db,bank,job,source,"Physics",doc,[],[data],set(),{1})
    q=db.query(Question).one()
    assert counts==(1,1,0) and q.approved_at is None
    assert "option_diagram_missing:1" in q.interaction["ingestion_issues"]


def test_plain_text_question_still_imports_without_diagrams(monkeypatch):
    db,admin,bank,source,job=setup_db();doc=fixture_pdf();data=model_item()
    data.update(stem_markdown="What is two plus two?",diagrams=[],options=[{"value":"a","markdown":"4"},{"value":"b","markdown":"5"}])
    monkeypatch.setattr("app.bank_imports.put",lambda *args:None)
    assert _save_questions(db,bank,job,source,"Physics",doc,[],[data],set(),{1})==(1,0,0)
    assert db.query(Question).one().diagrams==[]


@pytest.mark.parametrize("stem", [
    "Draw a circuit with two resistors connected in series.",
    "Sketch the graph of the function y = x squared.",
    "What is two plus two?",
])
@pytest.mark.parametrize("invented_crop", [False, True])
def test_admin_bank_text_only_questions_do_not_require_diagrams(monkeypatch,stem,invented_crop):
    db,admin,bank,source,job=setup_db()
    doc=fitz.open();page=doc.new_page(width=400,height=500)
    page.insert_text((30,50),"1. "+stem,fontsize=9)
    page.insert_text((30,75),"Explain the result clearly and state the relevant units.",fontsize=9)
    data=model_item()
    data.update(kind="subjective",stem_markdown=stem,options=[],answer=None,
                diagrams=[{"page":1,"bbox":[20,20,380,100],"role":"question","option_index":None}]
                if invented_crop else [])
    uploads=[];monkeypatch.setattr("app.bank_imports.put",lambda *args:uploads.append(args))
    assert _save_questions(db,bank,job,source,"Physics",doc,image_regions(doc)+vector_regions(doc),[data],set(),{1})==(1,0,0)
    question=db.query(Question).one()
    assert question.diagrams==[] and question.interaction["ingestion_issues"]==[]
    assert question.approved_at is not None and uploads==[]


def test_teacher_worker_uses_the_same_contract_and_checkpoints(monkeypatch):
    db,admin,bank,source,job=setup_db();job.bank_id=None;db.commit()
    doc=fixture_pdf();raw=doc.tobytes();requests=[]
    monkeypatch.setattr("app.tasks.SessionLocal",lambda:db)
    monkeypatch.setattr("app.tasks.get",lambda key:raw)
    monkeypatch.setattr("app.tasks.put",lambda *args:None)
    monkeypatch.setattr("app.tasks.settings",lambda:settings().model_copy(update={"openrouter_api_key":"test-key"}))
    def extract(payload,*args):
        requests.append(payload);return {"questions":[model_item()]}
    monkeypatch.setattr("app.tasks.request_extraction",extract)
    extract_import.run(job.id)
    q=db.query(Question).one();saved_job=db.get(ImportJob,"job")
    assert len(q.diagrams)==2 and q.interaction["ingestion_issues"]==[]
    assert saved_job.status==ImportStatus.REVIEW and saved_job.extraction_meta["processed_pages"]==1
    assert any(p["type"]=="image_url" for p in requests[0]["messages"][1]["content"])


def test_admin_import_keeps_skipped_batch_visible_after_later_success(monkeypatch):
    db,admin,bank,source,job=setup_db();doc=fitz.open()
    for n in range(3):
        page=doc.new_page(width=400,height=500)
        page.insert_text((30,50),f"{n+1}. Calculate the result and state the relevant units.",fontsize=9)
    monkeypatch.setattr("app.bank_imports.SessionLocal",lambda:db)
    monkeypatch.setattr("app.bank_imports.get",lambda key:doc.tobytes())
    monkeypatch.setattr("app.bank_imports.settings",lambda:settings().model_copy(update={"openrouter_api_key":"test-key"}))
    monkeypatch.setattr("app.bank_imports.extraction_units",lambda texts:[(0,2),(2,3)])
    calls=[]
    def extract(payload,*args):
        calls.append(payload)
        if len(calls)==1:raise RuntimeError("Extraction provider failed after retries: malformed JSON")
        checkpoint=db.get(ImportJob,"job").extraction_meta
        assert checkpoint["failed_pages"][0]["end_page"]==2
        return {"questions":[{"number":"3","kind":"subjective","stem_markdown":"Calculate the result and state the relevant units.",
                               "options":[],"diagrams":[],"source_spans":[{"page":3,"bbox":[20,20,380,100]}],
                               "answer":None,"chapter":"Physics","topic":"Units","confidence":.99}]}
    monkeypatch.setattr("app.bank_imports.request_extraction",extract)
    run_bank_import(job.id)
    result=db.get(ImportJob,"job")
    assert len(calls)==2 and db.query(Question).count()==1
    assert result.status==ImportStatus.REVIEW and result.extraction_meta["processed_pages"]==3
    assert result.extraction_meta["phase"]=="Extraction incomplete: source pages skipped"
    assert result.extraction_meta["failed_pages"]==[{"page":1,"end_page":2,"error":"Extraction provider failed after retries: malformed JSON"}]
    view=import_view(result,db)
    assert view["errors"][0]["end_page"]==2 and "malformed JSON" in view["errors"][0]["message"]


def test_aes_encrypted_pdf_with_empty_user_password_imports(monkeypatch):
    from pypdf import PdfReader, PdfWriter
    db,admin,bank,source,job=setup_db();doc=fitz.open();page=doc.new_page()
    page.insert_text((30,50),"1. Calculate the result and state the relevant units.")
    writer=PdfWriter(clone_from=BytesIO(doc.tobytes()))
    writer.encrypt(user_password="",owner_password="owner-only",algorithm="AES-256")
    encrypted=BytesIO();writer.write(encrypted);raw=encrypted.getvalue()
    assert PdfReader(BytesIO(raw)).is_encrypted
    monkeypatch.setattr("app.bank_imports.SessionLocal",lambda:db)
    monkeypatch.setattr("app.bank_imports.get",lambda key:raw)
    monkeypatch.setattr("app.bank_imports.settings",lambda:settings().model_copy(update={"openrouter_api_key":"test-key"}))
    monkeypatch.setattr("app.bank_imports.request_extraction",lambda *args:{"questions":[{
        "number":"1","kind":"subjective","stem_markdown":"Calculate the result and state the relevant units.",
        "options":[],"diagrams":[],"source_spans":[{"page":1,"bbox":[20,20,550,100]}],
        "answer":None,"chapter":"Physics","topic":"Units","confidence":.99}]})
    run_bank_import(job.id)
    result=db.get(ImportJob,"job")
    assert result.status==ImportStatus.APPROVED and result.error is None
    assert result.extraction_meta["processed_pages"]==1
    assert db.query(Question).count()==1


def test_approving_present_questions_does_not_clear_skipped_source_pages():
    db,admin,bank,source,job=setup_db();q=question_record(db)
    q.kind=QuestionKind.SUBJECTIVE;q.options=[];q.diagrams=[];q.interaction={};q.approved_at=None
    job.status=ImportStatus.REVIEW;job.extraction_meta={"failed_pages":[{"page":1,"end_page":2,"error":"provider timeout"}]}
    db.commit()
    data=QuestionIn(kind=q.kind,stem_markdown=q.stem_markdown,options=[],answer=None)
    resolve_bank_question(bank.id,q.id,data,user=admin,db=db)
    assert q.approved_at is not None and job.status==ImportStatus.REVIEW
    assert job.extraction_meta["phase"]=="Extraction incomplete: source pages skipped"


def test_legacy_failed_page_error_is_returned_as_readable_message():
    db,admin,bank,source,job=setup_db()
    job.extraction_meta={"failed_pages":[{"page":2,"error":"Provider timed out"}]}
    assert import_view(job,db)["errors"][0]["message"]=="Provider timed out"


def question_record(db):
    q=Question(id="q",owner_id="admin",bank_id="bank",scope=QuestionScope.GLOBAL,import_job_id="job",source_number="1",
               kind=QuestionKind.SINGLE,stem_markdown="Which diagram is correct?",options=model_item()["options"],answer={"option":0},
               source_spans=model_item()["source_spans"],diagrams=[{"object_key":"old.png","page":1,"bbox":[20,20,380,480]}],
               interaction={"source_document_id":"source","ingestion_issues":["diagram_invalid_geometry","missing_answer"]},
               approved_at=datetime.now(timezone.utc))
    db.add(q);db.commit();return q


def test_review_crops_are_versioned_and_preserve_question_identity(monkeypatch):
    db,admin,bank,source,job=setup_db();q=question_record(db);raw=fixture_pdf().tobytes();keys=[]
    monkeypatch.setattr("app.figure_review.get",lambda key:raw)
    monkeypatch.setattr("app.figure_review.put",lambda key,*args:keys.append(key))
    answer=q.answer;stem=q.stem_markdown
    result=review_figures(q.id,FigureReviewIn(diagrams=model_item()["diagrams"],confirmed=True),admin,db)
    assert q.id=="q" and q.stem_markdown==stem and q.answer==answer
    assert q.approved_at is None and result["ingestion_issues"]==["missing_answer"]
    assert len(keys)==2 and all(k.startswith("reviewed-diagrams/q/") for k in keys)
    assert "old.png" not in keys


def test_review_requires_every_visual_choice_and_confirmation(monkeypatch):
    db,admin,bank,source,job=setup_db();q=question_record(db)
    monkeypatch.setattr("app.figure_review.get",lambda key:fixture_pdf().tobytes())
    for data in [FigureReviewIn(diagrams=[],confirmed=False),FigureReviewIn(diagrams=model_item()["diagrams"][:1],confirmed=True)]:
        with pytest.raises(HTTPException) as caught:
            review_figures(q.id,data,admin,db)
        assert caught.value.status_code==422
    assert q.diagrams[0]["object_key"]=="old.png"


def test_review_upload_failure_keeps_existing_database_references(monkeypatch):
    db,admin,bank,source,job=setup_db();q=question_record(db)
    monkeypatch.setattr("app.figure_review.get",lambda key:fixture_pdf().tobytes())
    def fail(*args):raise OSError("storage offline")
    monkeypatch.setattr("app.figure_review.put",fail)
    with pytest.raises(OSError):review_figures(q.id,FigureReviewIn(diagrams=model_item()["diagrams"],confirmed=True),admin,db)
    assert q.diagrams[0]["object_key"]=="old.png" and q.approved_at is not None


def test_visual_flags_cannot_be_cleared_by_generic_approve(monkeypatch):
    db,admin,bank,source,job=setup_db();q=question_record(db)
    data=QuestionIn(kind=q.kind,stem_markdown=q.stem_markdown,options=q.options,answer=q.answer)
    with pytest.raises(HTTPException) as caught:resolve_bank_question(bank.id,q.id,data,user=admin,db=db)
    assert caught.value.status_code==422


def test_source_preview_rejects_unauthorized_teacher_and_invalid_page(monkeypatch):
    db,admin,bank,source,job=setup_db();q=question_record(db)
    monkeypatch.setattr("app.figure_review.get",lambda key:fixture_pdf().tobytes())
    stranger=User(id="stranger",role=Role.TEACHER)
    with pytest.raises(HTTPException) as caught:figure_source_page(q.id,1,stranger,db)
    assert caught.value.status_code==404
    with pytest.raises(HTTPException) as caught:figure_source_page(q.id,0,admin,db)
    assert caught.value.status_code==404


def test_saved_test_keeps_old_asset_after_question_diagrams_change(monkeypatch):
    db,admin,bank,source,job=setup_db();q=question_record(db)
    test=Assessment(id="test",owner_id=admin.id,title="Test",subject="Physics",duration_seconds=60)
    version=AssessmentVersion(id="v1",test_id=test.id,ordinal=1,content={"questions":[{"id":q.id,"diagrams":q.diagrams}]},published_at=datetime.now(timezone.utc))
    student=User(id="student",name="Student",email="s@example.com",role=Role.STUDENT)
    attempt=Attempt(id="attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime.now(timezone.utc),ends_at=datetime.now(timezone.utc)+timedelta(minutes=5))
    db.add_all([test,version,student,attempt]);q.diagrams=[];db.commit();fetched=[]
    monkeypatch.setattr("app.main.get",lambda key:fetched.append(key) or b"png")
    response=question_media(q.id,0,student,db,asset="old.png")
    assert response.status_code==200 and fetched==["old.png"]
    with pytest.raises(HTTPException) as caught:question_media(q.id,0,student,db,asset="private/other-source.pdf")
    assert caught.value.status_code==404 and fetched==["old.png"]


def test_cross_question_scope_conflicts_are_reviewed():
    first=model_item();second=model_item();second["number"]="2"
    marked=mark_scope_conflicts([first,second])
    assert all("diagram_question_scope_overlap" in i["_visual_scope_issues"] for i in marked)


def test_invalid_source_geometry_does_not_abort_other_questions(monkeypatch):
    db,admin,bank,source,job=setup_db();doc=fixture_pdf();data=model_item()
    data["source_spans"]=[None,{"page":0,"bbox":[0,0,1,1]}]
    monkeypatch.setattr("app.bank_imports.put",lambda *args:None)
    assert _save_questions(db,bank,job,source,"Physics",doc,[],[data],set(),{1})==(1,1,0)
    assert "diagram_invalid_source_geometry" in db.query(Question).one().interaction["ingestion_issues"]


def test_repair_proposal_is_read_only_and_staging_preserves_content(monkeypatch):
    db,admin,bank,source,job=setup_db();q=question_record(db)
    q.diagrams=model_item()["diagrams"];db.commit()
    figures,issues=propose_repair(q,fixture_pdf())
    assert len(figures)==2 and not issues and q.approved_at is not None
    original=(q.id,q.stem_markdown,q.options,q.answer,q.source_spans)
    keys=[];monkeypatch.setattr("app.figure_repairs.put",lambda key,*args:keys.append(key))
    stage_repair(db,q,figures,issues,admin.id);db.commit()
    assert (q.id,q.stem_markdown,q.options,q.answer,q.source_spans)==original
    assert all(k.startswith("repaired-diagrams/q/") for k in keys)
    assert q.approved_at is None and "diagram_repair_review" in q.interaction["ingestion_issues"]


def test_repair_does_not_stage_an_unchanged_text_only_question():
    db,admin,bank,source,job=setup_db();q=question_record(db);q.diagrams=[]
    q.stem_markdown="Calculate two plus two.";q.options=[{"value":"a","markdown":"4"},{"value":"b","markdown":"5"}]
    assert not repair_needed(q,[],[])


def test_unrecognized_visual_choices_can_be_staged_before_figure_review():
    db,admin,bank,source,job=setup_db();q=question_record(db)
    q.kind=QuestionKind.SUBJECTIVE;q.options=[];q.diagrams=[];db.commit()
    data=QuestionIn(kind=QuestionKind.SINGLE,stem_markdown=q.stem_markdown,options=model_item()["options"],answer=None)
    save_question_draft(q.id,data,admin,db)
    assert q.kind==QuestionKind.SINGLE and len(q.options)==2 and q.approved_at is None
    assert "option_diagram_missing:0" in q.interaction["ingestion_issues"]
    assert "option_diagram_missing:1" in q.interaction["ingestion_issues"]


def test_draft_option_reordering_requires_image_ownership_review():
    db,admin,bank,source,job=setup_db();q=question_record(db);q.diagrams=model_item()["diagrams"];db.commit()
    data=QuestionIn(kind=q.kind,stem_markdown=q.stem_markdown,options=list(reversed(q.options)))
    save_question_draft(q.id,data,admin,db)
    assert "diagram_option_ownership_uncertain" in q.interaction["ingestion_issues"] and q.approved_at is None


def test_admin_can_ignore_a_false_positive_warning_and_approve_with_audit_trail():
    db,admin,bank,source,job=setup_db();q=question_record(db)
    q.approved_at=None
    q.interaction={"ingestion_issues":["diagram_unassigned_native_candidate"]}
    db.commit()
    data=QuestionIn(kind=q.kind,stem_markdown=q.stem_markdown,options=q.options,answer=q.answer)
    resolve_bank_question(bank.id,q.id,data,user=admin,db=db,ignore_warnings=True)
    assert q.approved_at is not None
    assert q.interaction["ingestion_issues"]==[]
    assert q.interaction["ignored_ingestion_issues"]==["diagram_unassigned_native_candidate"]
    assert q.interaction["warnings_ignored_by"]==admin.id


@pytest.mark.skipif(not shutil.which("soffice"),reason="LibreOffice is not installed")
def test_real_docx_conversion_preserves_diagram_only_options(monkeypatch):
    doc=fixture_pdf();pngs=[]
    for value in model_item()["diagrams"]:
        pngs.append(doc[0].get_pixmap(clip=fitz.Rect(value["bbox"])).tobytes("png"))
    ns="http://schemas.openxmlformats.org/"
    parts=[]
    for i in range(2):
        parts.append(f'''<w:p><w:r><w:t>({chr(65+i)})</w:t></w:r></w:p><w:p><w:r><w:drawing><wp:inline><wp:extent cx="1524000" cy="1524000"/><wp:docPr id="{i+1}" name="Option"/><a:graphic><a:graphicData uri="{ns}drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="{i}" name="option.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rId{i+1}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1524000" cy="1524000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>''')
    xml=f'''<w:document xmlns:w="{ns}wordprocessingml/2006/main" xmlns:r="{ns}officeDocument/2006/relationships" xmlns:wp="{ns}drawingml/2006/wordprocessingDrawing" xmlns:a="{ns}drawingml/2006/main" xmlns:pic="{ns}drawingml/2006/picture"><w:body><w:p><w:r><w:t>1. Which diagram is a circle? Select one answer from the two visual options below.</w:t></w:r></w:p>{''.join(parts)}</w:body></w:document>'''
    buf=BytesIO()
    with ZipFile(buf,"w") as z:
        z.writestr("[Content_Types].xml",'''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>''')
        z.writestr("_rels/.rels",f'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{ns}officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''')
        z.writestr("word/document.xml",xml)
        z.writestr("word/_rels/document.xml.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i+1}" Type="{ns}officeDocument/2006/relationships/image" Target="media/option{i}.png"/>' for i in range(2))+'</Relationships>')
        for i,png in enumerate(pngs):z.writestr(f"word/media/option{i}.png",png)
    monkeypatch.setattr("app.bank_imports.get",lambda key:buf.getvalue())
    source=SourceDocument(filename="options.docx",object_key="options.docx",media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    inventory={}
    converted=fitz.open(stream=_pdf_bytes(source,inventory),filetype="pdf")
    assert inventory=={"drawings":2,"complex":0}
    rs=image_regions(converted);assert len(rs)==2
    data=model_item();data["source_spans"]=[{"page":1,"bbox":list(converted[0].rect)}]
    data["diagrams"]=[{**r,"role":"option","option_index":i} for i,r in enumerate(rs)]
    figures,issues=extract_figures(data,converted,rs)
    assert len(figures)==2 and not issues and [f["option_index"] for f in figures]==[0,1], issues
