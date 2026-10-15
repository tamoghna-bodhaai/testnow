"""Regression coverage for the entire visual-question contract, without a provider."""
from copy import deepcopy
import json
from pathlib import Path

import fitz
import pytest

from app.figures import (
    DIAGRAM_SCHEMA, OPTION_SCHEMA, canonical_visual_response, extract_figures,
    image_regions, model_visual_request, save_figures, vector_regions, visual_context,
    ground_question_spans,
    docx_conversion_issues,
    page_words, mark_scope_conflicts,
)
from app.tasks import normalize_item, prepare_item_for_ingestion


def document():
    doc = fitz.open()
    page=doc.new_page(width=400, height=500)
    page.insert_text((25,35),"Source question with readable native text and its choices.",fontsize=9)
    return doc


def square(page, x=100, y=100, size=80):
    for first, second in [((x,y),(x+size,y)), ((x+size,y),(x+size,y+size)),
                          ((x+size,y+size),(x,y+size)), ((x,y+size),(x,y))]:
        page.draw_line(first, second)


def item(diagrams=None, options=None, spans=None):
    return {"number": "1", "kind": "single_choice" if options else "numerical",
            "stem_markdown": "Study the figure and choose the result.",
            "options": options or [], "answer": {"value": "1"},
            "source_spans": spans if spans is not None else [{"page": 1, "bbox": [20,20,380,480]}],
            "diagrams": diagrams or [], "confidence": .99, "chapter": "Physics"}


def diagram(box=(96,96,184,184), page=1, role="question", index=None):
    return {"page": page, "bbox": list(box), "alt": "Figure", "role": role, "option_index": index}


def regions(doc):
    return image_regions(doc) + vector_regions(doc)


def test_thin_connected_strokes_are_grouped_and_labels_retained():
    doc = document(); page = doc[0]; square(page)
    page.insert_text((184,140), "P")
    detected = vector_regions(doc)
    assert len(detected) == 1
    label=next(w for w in page.get_text("words") if w[4]=="P")
    assert fitz.Rect(detected[0]["bbox"]).contains(fitz.Rect(label[:4]))
    figures, issues = extract_figures(item(), doc, detected)
    assert len(figures) == 1 and issues == []


def test_white_page_background_does_not_merge_unrelated_figures():
    doc = document(); page = doc[0]
    page.draw_rect(page.rect, fill=(1,1,1), color=None)
    square(page,50,100,45); square(page,250,300,45)
    assert len(vector_regions(doc)) == 2


def test_bridging_path_merges_all_components():
    doc = document(); page = doc[0]
    page.draw_rect(fitz.Rect(50,100,70,130)); page.draw_rect(fitz.Rect(90,100,110,130))
    page.draw_line((70,115),(90,115))
    assert len(vector_regions(doc)) == 1


@pytest.mark.parametrize("page", [0, -1, 2, True, 1.5, "1"])
def test_invalid_pages_never_index_last_page(page):
    doc = document(); square(doc[0])
    figures, issues = extract_figures(item([diagram(page=page)]), doc, [])
    assert figures == [] and "diagram_invalid_geometry" in issues


@pytest.mark.parametrize("box", [[0,0,0,30], [100,100,90,180], [0,0,float('nan'),100],
                                  [0,0,float('inf'),100], [1,2,3], None])
def test_invalid_geometry_is_reviewed_without_crashing(box):
    doc = document(); value = diagram(); value["bbox"] = box
    figures, issues = extract_figures(item([value]), doc, [])
    assert not figures and "diagram_invalid_geometry" in issues


def test_blank_and_text_crops_cannot_pass_as_vector_figures():
    doc = document(); page = doc[0]
    page.insert_text((100,120), "Ordinary prose, not a figure")
    for box in [(250,350,350,450), (90,100,300,130)]:
        figures, issues = extract_figures(item([diagram(box)]), doc, [])
        assert not figures and "diagram_text_or_no_graphics" in issues


def test_page_sized_region_never_attaches_to_two_questions():
    doc = document()
    candidate = {"page":1, "bbox":[0,0,400,500], "origin":"raster"}
    for span in [[20,20,380,100], [20,300,380,480]]:
        figures, _ = extract_figures(item(spans=[{"page":1,"bbox":span}]),doc,[candidate])
        assert not figures


def test_model_box_must_belong_to_its_question():
    doc = document(); square(doc[0])
    figures, issues = extract_figures(item([diagram()],spans=[{"page":1,"bbox":[20,300,380,480]}]),doc,[])
    assert not figures and "diagram_outside_question" in issues


def test_model_native_duplicates_do_not_create_second_image():
    doc = document(); square(doc[0])
    figures, issues = extract_figures(item([diagram()]), doc, regions(doc))
    assert len(figures) == 1 and not issues


def test_conflicting_option_ownership_is_not_silently_accepted():
    doc = document(); square(doc[0])
    opts = [{"value":"a","markdown":"one"},{"value":"b","markdown":"two"}]
    figures, issues = extract_figures(item([diagram(role="option",index=0),diagram(role="option",index=1)],opts),doc,[])
    assert len(figures) == 1 and "diagram_conflicting_owner" in issues


def test_diagram_only_choices_keep_mcq_type_and_own_images():
    doc = document(); page = doc[0]; square(page,80,120,60); square(page,260,120,60)
    opts = [{"value":v,"markdown":"","requires_diagram":True} for v in ["a","b"]]
    data = item([diagram((76,116,144,184),role="option",index=0),
                 diagram((256,116,324,184),role="option",index=1)], opts)
    normalized, issues = prepare_item_for_ingestion(data)
    assert normalized["kind"] == "single_choice" and not issues
    figures, issues = extract_figures(normalized,doc,regions(doc))
    assert [(f["role"],f["option_index"]) for f in figures] == [("option",0),("option",1)]
    assert not issues


def test_missing_one_visual_option_is_flagged_even_if_other_figures_succeed():
    doc = document(); square(doc[0])
    opts = [{"value":v,"markdown":"","requires_diagram":True} for v in ["a","b"]]
    figures, issues = extract_figures(item([diagram(role="option",index=0)],opts),doc,[])
    assert len(figures) == 1 and "option_diagram_missing:1" in issues


def test_printed_label_recovers_legacy_option_ownership():
    doc = document(); page = doc[0]; square(page,80,120,60)
    page.insert_text((50,130),"(A)")
    opts = [{"value":"a","markdown":"","requires_diagram":True},{"value":"b","markdown":"Other"}]
    figures, issues = extract_figures(item(options=opts),doc,regions(doc))
    assert figures[0]["role"] == "option" and figures[0]["option_index"] == 0
    assert not issues


def test_solution_figure_keeps_its_role():
    doc = document(); square(doc[0])
    figures, issues = extract_figures(item([diagram(role="solution")]),doc,[])
    assert figures[0]["role"] == "solution" and not issues


def test_rotated_pages_use_unrotated_geometry_and_restore_rotation():
    doc = document(); page = doc[0]; square(page); page.set_rotation(90)
    figures, issues = extract_figures(item([diagram()]),doc,[])
    assert len(figures) == 1 and not issues and doc[0].rotation == 90
    png = fitz.Pixmap(figures[0]["png"])
    assert png.width == 176 and png.height == 176


def test_text_rich_visual_page_and_sparse_continuation_receive_images():
    doc = document(); square(doc[0]); doc.new_page(width=400,height=500)
    parts = visual_context(doc,0,1,["readable prose "*40,"scanned continuation"],regions(doc),10_000_000)
    assert sum(p["type"] == "image_url" for p in parts) == 2
    assert sum(p.get("text", "").startswith("SOURCE LAYOUT ") for p in parts) == 2


def test_text_only_page_does_not_require_vision():
    doc = document(); page = doc[0]; page.insert_text((30,50),"Question prose")
    parts = visual_context(doc,0,1,["readable prose "*40],[],10_000_000)
    assert not any(p["type"] == "image_url" for p in parts)


def test_outline_only_or_scanned_source_requires_visual_review():
    doc=fitz.open();doc.new_page(width=400,height=500);square(doc[0])
    figures,issues=extract_figures(item([diagram()]),doc,[])
    assert len(figures)==1 and "diagram_scanned_source_review" in issues


def test_white_raster_cannot_pass_as_a_diagram():
    doc=document();pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,80,80),False);pix.clear_with(255)
    doc[0].insert_image(fitz.Rect(100,100,180,180),stream=pix.tobytes("png"))
    figures,issues=extract_figures(item([diagram((100,100,180,180))]),doc,image_regions(doc))
    assert figures==[] and "diagram_blank" in issues


def test_visual_request_size_limit_remains_enforced():
    doc = document(); square(doc[0])
    with pytest.raises(ValueError,match="size limit"):
        visual_context(doc,0,1,[""],regions(doc),1)


def test_storage_failure_is_a_review_issue_and_never_drops_the_question():
    doc = document(); square(doc[0])
    def fail(*args):
        raise OSError("storage unavailable")
    saved, issues = save_figures(item([diagram()]),doc,[],"prefix",fail)
    assert not saved and "diagram_storage_failed:0" in issues


def test_saved_png_is_lossless_rgb_and_metadata_has_no_bytes():
    doc = document(); square(doc[0]); uploads = []
    saved, issues = save_figures(item([diagram()]),doc,[],"prefix",lambda *args:uploads.append(args))
    assert not issues and "png" not in saved[0]
    assert uploads[0][1].startswith(b"\x89PNG") and fitz.Pixmap(uploads[0][1]).n == 3


def test_gemini_schema_uses_explicit_yx_normalized_coordinates_without_mutation():
    payload = {"model":"other","messages":[{"role":"system","content":"Extract"}],
               "response_format":{"json_schema":{"schema":{"properties":{"diagrams":{"items":deepcopy(DIAGRAM_SCHEMA)}}}}}}
    before = deepcopy(payload)
    req = model_visual_request(payload,"google/gemini-3.1-flash-lite")
    props = req["response_format"]["json_schema"]["schema"]["properties"]["diagrams"]["items"]["properties"]
    assert "box_2d" in props and "bbox" not in props and payload == before
    assert model_visual_request(payload,"other")["response_format"] == before["response_format"]


def test_gemini_coordinates_convert_to_canonical_points_once():
    payload = {"messages":[{"content":[{"type":"text","text":"SOURCE LAYOUT "+json.dumps({"page":1,"width_points":400,"height_points":500})}]}]}
    data = {"questions":[{"source_spans":[{"page":1,"box_2d":[100,200,900,800]}],
                           "diagrams":[{"page":1,"box_2d":[200,250,400,450]}]}]}
    assert canonical_visual_response(data,payload)["questions"][0]["diagrams"][0]["bbox"] == [100,100,180,200]
    assert canonical_visual_response(data,payload)["questions"][0]["source_spans"][0]["bbox"] == [80,50,320,450]


def test_gemini_does_not_silently_accept_a_different_coordinate_contract():
    data={"questions":[{"diagrams":[diagram()],"source_spans":[]}]}
    adapted=canonical_visual_response(data,{"model":"google/gemini-3.1-flash-lite"})
    assert adapted["questions"][0]["diagrams"][0]["bbox"] is None


def test_complex_or_missing_word_graphics_cannot_silently_auto_approve():
    assert docx_conversion_issues({"drawings":1,"complex":1},[{}])==["diagram_docx_complex_visual_review"]
    assert docx_conversion_issues({"drawings":1,"complex":0},[])==["diagram_docx_visuals_missing_after_conversion"]
    assert docx_conversion_issues({"drawings":1,"complex":0},[{}])==[]


def test_image_requirement_survives_normalization():
    data = normalize_item({"options":[{"value":"a","markdown":"","requires_diagram":True}],"answer":None})
    assert data["options"][0]["requires_diagram"] is True


def test_missing_option_records_can_be_recovered_from_explicit_figure_owners():
    data=item([diagram(role="option",index=0),diagram((200,200,280,280),role="option",index=1)])
    data["kind"]="single_choice";data["options"]=[]
    normalized,issues=prepare_item_for_ingestion(data)
    assert normalized["kind"]=="single_choice" and len(normalized["options"])==2
    assert all(o["requires_diagram"] for o in normalized["options"])
    assert "diagram_options_recovered" in issues


def test_unclaimed_graphic_is_not_silently_omitted_or_auto_attached():
    doc=document();square(doc[0]);data=item();data["stem_markdown"]="Select the correct representation."
    figures,issues=extract_figures(data,doc,regions(doc))
    assert not figures and "diagram_unassigned_native_candidate" in issues


def test_real_gemini_response_recovers_all_four_options_without_duplicates():
    doc=fitz.open();page=doc.new_page(width=560,height=650)
    page.insert_text((35,45),"1. Which diagram is a circle? Select exactly one answer.",fontsize=14)
    for text,pos in [("(A)",(40,120)),("(B)",(300,120)),("(C)",(40,310)),("(D)",(300,310))]:
        page.insert_text(pos,text,fontsize=13)
    page.draw_circle((140,170),45);page.draw_rect(fitz.Rect(350,125,440,215))
    page.draw_polyline([(95,405),(140,315),(185,405),(95,405)])
    page.draw_rect(fitz.Rect(350,330,465,385))
    # Captured from a real Gemini 3.1 Flash-Lite/OpenRouter response. The
    # original source span omits the right column and lower choice row.
    data=json.loads((Path(__file__).parent/"fixtures/gemini_visual_options.json").read_text())
    grounded=ground_question_spans(data["questions"],doc)[0]
    figures,issues=extract_figures(grounded,doc,regions(doc))
    assert [(f["role"],f["option_index"]) for f in figures]==[("option",0),("option",1),("option",2),("option",3)]
    assert not issues


def test_question_grounding_respects_columns_and_numeric_option_labels():
    doc=document();page=doc[0]
    page.insert_text((25,40),"1. Which graph has constant speed?")
    page.insert_text((25,100),"1) speed")
    page.insert_text((25,120),"2) distance")
    page.insert_text((220,40),"2. Determine the correct unit here.")
    first=item();first.update(number="1",stem_markdown="Which graph has constant speed?")
    second=item();second.update(number="2",stem_markdown="Determine the correct unit here.")
    grounded=ground_question_spans([first,second],doc)
    assert grounded[0]["source_spans"][0]["bbox"][2]==212
    assert grounded[0]["source_spans"][0]["bbox"][3]==488


@pytest.mark.parametrize("stem", [
    "Draw a ray diagram for a convex mirror.",
    "Sketch the graph of y = x squared.",
    "Find the resistance of a circuit with two resistors in series.",
    "Which graph represents uniform motion?",
    "Prove that the result shown by the calculation is correct.",
    "Calculate the area of a plane figure with sides 3 and 4.",
    "Determine the number of significant figures in 0.00340.",
])
def test_subject_keywords_do_not_require_a_source_diagram(stem):
    doc=document();data=item();data["stem_markdown"]=stem
    figures,issues=extract_figures(data,doc,regions(doc))
    assert figures==[] and issues==[]


@pytest.mark.parametrize("stem", [
    "Study the figure and find the angle.",
    "Calculate the resistance in the circuit shown below.",
    "In the following diagram, determine the angle.",
    "Find the current shown in the circuit.",
    "Figure 2 shows the apparatus. Find its reading.",
])
def test_explicit_source_references_still_flag_missing_figures(stem):
    doc=document();data=item();data["stem_markdown"]=stem
    figures,issues=extract_figures(data,doc,[])
    assert figures==[] and "diagram_expected_missing" in issues


@pytest.mark.parametrize("box", [(20,20,310,50),(250,350,350,450),(10,5,390,18)])
def test_model_invented_text_and_blank_stem_crops_are_optional(box):
    doc=document();data=item([diagram(box)])
    data["stem_markdown"]="Draw a circuit with two resistors in series."
    figures,issues=extract_figures(data,doc,regions(doc))
    assert figures==[] and issues==[]


def test_model_crop_of_another_questions_graphic_is_still_rejected():
    doc=document();square(doc[0],y=300)
    data=item([diagram((96,296,184,384))],spans=[{"page":1,"bbox":[20,20,380,100]}])
    data["stem_markdown"]="Calculate the resistance of two resistors."
    figures,issues=extract_figures(data,doc,regions(doc))
    assert not figures and "diagram_outside_question" in issues


def test_false_crop_filter_never_suppresses_missing_visual_options():
    doc=document();data=item([diagram((250,350,350,450))],options=[
        {"value":"a","markdown":"","requires_diagram":True},
        {"value":"b","markdown":"a textual choice","requires_diagram":False}])
    data["stem_markdown"]="Select the correct answer."
    figures,issues=extract_figures(data,doc,regions(doc))
    assert not figures and "option_diagram_missing:0" in issues


def scanned_document():
    source=fitz.open();page=source.new_page(width=500,height=650)
    page.insert_text((35,50),"1. Calculate the speed of the moving train.",fontsize=14)
    page.insert_text((35,80),"Use a distance of 120 metres and a time of 10 seconds.",fontsize=12)
    page.insert_text((35,330),"2. Determine the resistance of the given wire.",fontsize=14)
    page.insert_text((35,360),"Use its length and cross section to find the answer.",fontsize=12)
    scan=fitz.open();target=scan.new_page(width=500,height=650)
    target.insert_image(target.rect,stream=page.get_pixmap(matrix=fitz.Matrix(2,2)).tobytes("png"))
    return scan


def test_scanned_text_only_questions_do_not_require_diagrams_or_attach_page_image():
    doc=scanned_document();data=item(spans=[{"page":1,"bbox":[0,0,500,650]}])
    data["stem_markdown"]="Calculate the speed of the moving train."
    figures,issues=extract_figures(data,doc,regions(doc))
    assert figures==[] and issues==[]


def test_real_scan_ocr_grounds_question_scopes_without_overlap():
    import shutil
    if not shutil.which("tesseract"):pytest.skip("Tesseract is required by the API Docker image")
    doc=scanned_document()
    assert not doc[0].get_text("words") and len(page_words(doc[0]))>20
    first=item(spans=[{"page":1,"bbox":[0,0,500,650]}])
    first.update(number="1",stem_markdown="Calculate the speed of the moving train.")
    second=item(spans=[{"page":1,"bbox":None}])
    second.update(number="2",stem_markdown="Determine the resistance of the given wire.")
    grounded=mark_scope_conflicts(ground_question_spans([first,second],doc))
    assert grounded[0]["source_spans"][0]["bbox"][3]<330
    assert grounded[1]["source_spans"][0]["bbox"][1]>300
    assert all(i.get("_source_geometry")=="ocr_question_anchor" for i in grounded)
    assert all(not extract_figures(i,doc,regions(doc))[1] for i in grounded)


def test_scan_layout_supplies_ocr_words_without_whole_page_figure_candidate():
    doc=scanned_document()
    parts=visual_context(doc,0,1,[""],regions(doc),10_000_000)
    layout=json.loads(next(p["text"][len("SOURCE LAYOUT "):] for p in parts if p.get("text", "").startswith("SOURCE LAYOUT ")))
    assert layout["figure_candidates"]==[]
    assert "ocr_words" in layout
    assert any(p["type"]=="image_url" for p in parts)


def test_scan_with_missing_image_options_keeps_real_option_failures():
    doc=scanned_document();data=item(options=[
        {"value":"a","markdown":"","requires_diagram":True},
        {"value":"b","markdown":"","requires_diagram":True}],
        spans=[{"page":1,"bbox":[0,0,500,650]}])
    data["stem_markdown"]="Select the correct answer."
    figures,issues=extract_figures(data,doc,regions(doc))
    assert figures==[] and issues==["option_diagram_missing:0","option_diagram_missing:1"]


def test_ocr_unavailable_does_not_create_a_diagram_requirement(monkeypatch):
    doc=scanned_document();data=item(spans=[{"page":1,"bbox":[0,0,500,650]}])
    data["stem_markdown"]="Calculate the speed of the moving train."
    def unavailable(*args,**kwargs):raise RuntimeError("OCR not installed")
    monkeypatch.setattr(fitz.Page,"get_textpage_ocr",unavailable)
    assert page_words(doc[0])==[]
    assert extract_figures(data,doc,regions(doc))==([],[])


@pytest.mark.parametrize("flag",[False,"false","False"," false ",None,0])
def test_false_visual_option_flags_are_not_coerced_to_true(flag):
    data=normalize_item({"options":[{"value":"a","markdown":"Text answer","requires_diagram":flag}]})
    assert data["options"][0]["requires_diagram"] is False


def test_bounded_visual_options_on_scan_are_kept_with_correct_owners():
    source=fitz.open();page=source.new_page(width=500,height=650)
    page.insert_text((35,50),"1. Select the correct answer from the two choices below.",fontsize=14)
    page.insert_text((45,150),"(A)",fontsize=14);page.insert_text((275,150),"(B)",fontsize=14)
    page.draw_circle((140,185),40);page.draw_rect(fitz.Rect(370,145,450,225))
    doc=fitz.open();target=doc.new_page(width=500,height=650)
    target.insert_image(target.rect,stream=page.get_pixmap(matrix=fitz.Matrix(2,2)).tobytes("png"))
    data=item(options=[{"value":"a","markdown":"","requires_diagram":True},
                       {"value":"b","markdown":"","requires_diagram":True}],
              spans=[{"page":1,"bbox":[20,20,480,300]}],
              diagrams=[diagram((95,140,185,230),role="option",index=0),
                        diagram((365,140,455,230),role="option",index=1)])
    figures,issues=extract_figures(data,doc,regions(doc))
    assert [(f["role"],f["option_index"]) for f in figures]==[("option",0),("option",1)]
    assert issues==["diagram_scanned_source_review"]
