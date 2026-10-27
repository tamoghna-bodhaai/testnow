from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from starlette.requests import Request
import pytest

from app.db import Base
from app.explainers import GenerateExplainerIn, cancel_explainer, generate_explainer, publish_explainer, result_is_released, stream_video, student_explainers, suggested
from app.explainer_generation import ExplainerPlan, GenerationCancelled, Visual, plan_storyboard, raise_for_openrouter_status, run, scene_beats, set_stage, validate_storyboard
from app.explainer_captions import caption_cues, caption_markup
from app.explainer_math import sample_function, sample_region
from app.main import delete_test
from app.models import AccountStatus, Attempt, Enrollment, ExplainerRevision, Role, Test, TestStatus, TestVersion, User

NOW = datetime.now(timezone.utc)

@pytest.fixture(autouse=True)
def test_api_key(monkeypatch):
    from app.explainer_generation import settings
    monkeypatch.setattr(settings(), "openrouter_api_key", "test-key")

@pytest.fixture
def records(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    teacher = User(id="teacher", name="Teacher", email="teacher@example.com", password_hash="", role=Role.TEACHER, status=AccountStatus.ACTIVE)
    student = User(id="student", name="Student", email="student@example.com", password_hash="", role=Role.STUDENT, status=AccountStatus.ACTIVE)
    other = User(id="other", name="Other", email="other@example.com", password_hash="", role=Role.STUDENT, status=AccountStatus.ACTIVE)
    test = Test(id="test", owner_id=teacher.id, title="Physics", subject="Physics", status=TestStatus.PUBLISHED,
                duration_seconds=3600, max_attempts=1, results_policy={"mode": "immediate"}, published_version_id="version")
    version = TestVersion(id="version", test_id=test.id, ordinal=2, published_at=NOW,
        content={"questions": [{"id": "q1", "stem_markdown": "Plot the motion graph", "solution_markdown": "Draw the graph and compare the slopes.", "answer": {"option": 1}},
                               {"id": "q2", "stem_markdown": "What is the capital?", "solution_markdown": "Paris.", "answer": {"option": 0}}]})
    attempt = Attempt(id="attempt", test_id=test.id, version_id=version.id, student_id=student.id,
                      started_at=NOW - timedelta(hours=1), ends_at=NOW, submitted_at=NOW, score=4)
    db.add_all([teacher, student, other, test, version, attempt, Enrollment(test_id=test.id, student_id=student.id)])
    db.commit()
    monkeypatch.setattr("app.explainers.celery_app.send_task", lambda *args, **kwargs: None)
    yield db, teacher, student, other, test, attempt
    db.close(); engine.dispose()

def test_suggestion_and_teacher_override(records):
    db, teacher, _, _, test, _ = records
    assert suggested({"stem_markdown": "Plot a graph", "solution_markdown": "Slope"})
    assert not suggested({"stem_markdown": "Name the capital", "solution_markdown": "Paris"})
    with pytest.raises(HTTPException) as exc:
        generate_explainer(test.id, "q2", GenerateExplainerIn(), teacher, db)
    assert exc.value.status_code == 422
    draft = generate_explainer(test.id, "q2", GenerateExplainerIn(override_suggestion=True), teacher, db)
    assert draft["status"] == "queued"
    with pytest.raises(HTTPException) as exc:
        generate_explainer(test.id, "q2", GenerateExplainerIn(override_suggestion=True), teacher, db)
    assert exc.value.status_code == 409

def test_publication_requires_ready_revision_and_replaces_previous(records):
    db, teacher, _, _, test, _ = records
    first = ExplainerRevision(id="first", test_id=test.id, version_id="version", question_id="q1", requested_by=teacher.id,
                              status="ready", video_object_key="one.mp4", published_at=NOW)
    second = ExplainerRevision(id="second", test_id=test.id, version_id="version", question_id="q1", requested_by=teacher.id,
                               status="ready", video_object_key="two.mp4")
    db.add_all([first, second]); db.commit()
    publish_explainer(test.id, second.id, teacher, db)
    db.refresh(first); db.refresh(second)
    assert first.published_at is None and second.published_at is not None
    second.status = "failed"; db.commit()
    with pytest.raises(HTTPException) as exc:
        publish_explainer(test.id, second.id, teacher, db)
    assert exc.value.status_code == 409

def test_teacher_can_cancel_active_generation_and_start_a_new_draft(records):
    db, teacher, _, other, test, _ = records
    draft = generate_explainer(test.id, "q1", GenerateExplainerIn(), teacher, db)
    cancelled = cancel_explainer(test.id, draft["id"], teacher, db)
    assert cancelled["status"] == "cancelled"
    assert cancelled["progress_message"] == "Generation cancelled"
    with pytest.raises(GenerationCancelled):
        set_stage(db, draft["id"], "ready", "Ready to preview")
    assert db.get(ExplainerRevision, draft["id"]).status == "cancelled"
    assert generate_explainer(test.id, "q1", GenerateExplainerIn(), teacher, db)["status"] == "queued"
    with pytest.raises(HTTPException) as exc:
        cancel_explainer(test.id, draft["id"], teacher, db)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        cancel_explainer(test.id, draft["id"], other, db)
    assert exc.value.status_code == 404

def test_cancellation_stops_a_running_renderer_command():
    import sys
    import time
    started = time.monotonic()
    def cancel():
        raise GenerationCancelled()
    with pytest.raises(GenerationCancelled):
        run([sys.executable, "-c", "import time; time.sleep(10)"], cancel_check=cancel)
    assert time.monotonic() - started < 4

def test_student_playback_respects_release_assignment_and_publication(records):
    db, teacher, student, other, test, attempt = records
    draft = ExplainerRevision(id="draft", test_id=test.id, version_id="version", question_id="q1", requested_by=teacher.id,
                              status="ready", video_object_key="draft.mp4")
    live = ExplainerRevision(id="live", test_id=test.id, version_id="version", question_id="q2", requested_by=teacher.id,
                             status="ready", video_object_key="live.mp4", published_at=NOW)
    db.add_all([draft, live]); db.commit()
    assert student_explainers(attempt.id, student, db) == {"q2": {"url": "/api/results/attempt/explainers/q2/video", "published_at": live.published_at}}
    with pytest.raises(HTTPException) as exc:
        student_explainers(attempt.id, other, db)
    assert exc.value.status_code == 404
    test.results_policy = {"mode": "scheduled", "release_at": (NOW + timedelta(days=1)).isoformat()}; db.commit()
    with pytest.raises(HTTPException) as exc:
        student_explainers(attempt.id, student, db)
    assert exc.value.status_code == 403
    assert not result_is_released(test, NOW)
    test.results_policy = {"mode": "immediate"}; db.query(Enrollment).delete(); db.commit()
    with pytest.raises(HTTPException) as exc:
        student_explainers(attempt.id, student, db)
    assert exc.value.status_code == 403

def test_scene_spec_rejects_executable_tex_and_out_of_bounds_geometry():
    template = {"scenes": [{"heading": "See the slope", "narration": "The slope changes as the point moves along the curve.",
                            "objects": [{"type": "math", "content": "y=x^2", "x": 0, "y": 0}]}] * 3}
    assert len(ExplainerPlan.model_validate(template).scenes) == 3
    template["scenes"][0]["objects"][0]["content"] = r"\input{/etc/passwd}"
    with pytest.raises(ValueError):
        ExplainerPlan.model_validate(template)
    template["scenes"][0]["objects"][0] = {"type": "dot", "x": 1001, "y": 0}
    with pytest.raises(ValueError):
        ExplainerPlan.model_validate(template)

def test_storyboard_accepts_scene_list_and_visual_aliases():
    import json
    scenes = [{"heading": "See the graph", "narration": "We trace the curve and compare the area on each side.",
               "visuals": [{"type": "math", "label": "y=x^2"}]} for _ in range(3)]
    spec = validate_storyboard(json.dumps(scenes))
    assert spec["scenes"][0]["objects"][0]["content"] == "y=x^2"


def test_equations_cannot_be_mistaken_for_plain_text():
    import json
    beat = {"narration": "We calculate the power using the kinetic energy and time.", "objects": [
        {"type": "text", "content": "P_KE = (0.5 * m * v^2) / t"},
        {"type": "text", "content": "Intersection x=2", "math": "x=2"}]}
    scene = {"heading": "Power", "beats": [beat]}
    objects = validate_storyboard(json.dumps({"scenes": [scene, scene]}))["scenes"][0]["beats"][0]["objects"]
    assert objects[0]["type"] == "math" and r"_{\mathrm{KE}}" in objects[0]["content"]
    assert objects[1]["type"] == "math" and objects[1]["content"] == "x=2"
    assert Visual(type="math", content=r"P_{\mathrm{KE}}=\frac{1}{2}mv^{2}/t").type == "math"


def test_two_scene_storyboard_can_explain_a_short_problem(monkeypatch):
    import json
    narration = "We first identify the rate at which water rises and the height it travels. This gives the power needed against gravity. Next we account for the speed of the water as it leaves the pump. Adding both contributions gives the total mechanical power required for this process."
    words = narration.split()
    scene = {"heading": "Pump power", "beats": [
        {"narration": " ".join(words[:23]), "objects": [{"type": "text", "content": "Lift the water"}]},
        {"narration": " ".join(words[23:]), "objects": [{"type": "math", "content": "P=4\\,kW"}]}]}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": json.dumps({"scenes": [scene] * 2})}}]}
    # Expand the narration to the supported overall duration without changing beat structure.
    scene["beats"][0]["narration"] *= 2
    scene["beats"][1]["narration"] *= 2
    monkeypatch.setattr("app.explainer_generation.httpx.post", lambda *args, **kwargs: Response())
    assert len(plan_storyboard({"stem_markdown": "Find the pump power"}, None, None)["scenes"]) == 2


def test_short_single_scene_plan_is_accepted(monkeypatch):
    import json
    scene = {"heading": "One step", "beats": [{"narration": "Add the two values to find the result.",
             "objects": [{"type": "math", "content": "1+1=2"}]}]}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": json.dumps({"scenes": [scene]})}}]}
    monkeypatch.setattr("app.explainer_generation.httpx.post", lambda *args, **kwargs: Response())
    assert len(plan_storyboard({"stem_markdown": "Add 1 and 1"}, None, None)["scenes"]) == 1


def test_openrouter_rejection_keeps_the_provider_explanation():
    import httpx
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(400, request=request, json={"error": {"message": "Invalid image data"}})
    with pytest.raises(RuntimeError, match=r"storyboard request \(400\): Invalid image data"):
        raise_for_openrouter_status(response, "storyboard")


def test_graph_functions_plot_equations_in_data_coordinates():
    parabola = sample_function("y**2", "y", -2, 2, count=5)[0]
    assert parabola == [(4.0, -2.0), (1.0, -1.0), (0.0, 0.0),
                        (1.0, 1.0), (4.0, 2.0)]
    line = sample_function("2*y+3", "y", -1, 1, count=3)[0]
    assert line == [(1.0, -1.0), (3.0, 0.0), (5.0, 1.0)]
    assert len(sample_function("1/x", "x", -1, 1, count=5)) == 2
    spec = {"scenes": [{"heading": "Graph the functions",
                        "narration": "Plot both equations on the same mathematical axes.",
                        "objects": [{"type": "axes"},
                                    {"type": "function", "expression": "y**2", "variable": "y",
                                     "domain_start": -2, "domain_end": 2}]}] * 3}
    assert ExplainerPlan.model_validate(spec).scenes[0].objects[1].expression == "y**2"
    spec["scenes"][0]["objects"][1]["expression"] = "__import__('os').system('true')"
    with pytest.raises(ValueError):
        ExplainerPlan.model_validate(spec)


def test_physics_motion_path_is_validated():
    block = Visual(type="moving_block", points=[[-3, 2], [-1, 0.5], [1, 0.2], [3, 0.2]])
    assert len(block.points) == 4
    with pytest.raises(ValueError):
        Visual(type="moving_block", points=[[0, 0]])


def test_shaded_regions_follow_function_boundaries_and_reject_crossings():
    points = sample_region("x**2", "0", "x", 0, 2, count=5)
    assert points[:5] == [(0.0, 0.0), (0.5, 0.25), (1.0, 1.0),
                          (1.5, 2.25), (2.0, 4.0)]
    assert points[-1] == (0.0, 0.0)
    with pytest.raises(ValueError, match="cross"):
        sample_region("x", "1-x", "x", 0, 1)
    scene = {"heading": "Area between curves", "narration": "Shade the area between the two exact functions.",
             "objects": [{"type": "axes"},
                         {"type": "function", "expression": "sqrt(8*x-x**2)", "domain_start": 0, "domain_end": 4},
                         {"type": "function", "expression": "sqrt(4*x)", "domain_start": 0, "domain_end": 4},
                         {"type": "region", "upper_expression": "sqrt(8*x-x**2)",
                          "lower_expression": "sqrt(4*x)", "domain_start": 0, "domain_end": 4,
                          "color": "green"}]}
    assert ExplainerPlan.model_validate({"scenes": [scene] * 3}).scenes[0].objects[-1].color == "green"


def test_area_storyboard_repairs_missing_region(monkeypatch):
    import json
    scene = {"heading": "Area under the curve", "beats": [
        {"narration": "First, we draw axes and trace the exact upper curve across its given interval.",
         "objects": [{"type": "axes"}, {"type": "function", "expression": "x**2", "domain_start": 0, "domain_end": 2}]},
        {"narration": "Next, we identify the lower x axis and the bounds from the intersections. The shaded part between them is the area we need, so we integrate the upper function minus zero. This gives the requested value and makes the geometry clear for students.",
         "objects": [{"type": "math", "content": "A=\\int_0^2 x^2 dx"}]}]}
    missing = {"scenes": [scene] * 3}
    shaded = {"scenes": [dict(scene, beats=[scene["beats"][0], dict(scene["beats"][1], objects=[
        {"type": "region", "upper_expression": "x**2", "lower_expression": "0",
         "domain_start": 0, "domain_end": 2}, *scene["beats"][1]["objects"]])])] * 3}
    replies = [missing, shaded]
    class Response:
        def __init__(self, value): self.value = value
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": json.dumps(self.value)}}]}
    monkeypatch.setattr("app.explainer_generation.httpx.post", lambda *args, **kwargs: Response(replies.pop(0)))
    result = plan_storyboard({"stem_markdown": "Find the area under y=x²"}, None, None)
    assert result["scenes"][0]["beats"][1]["objects"][0]["type"] == "region"


def test_future_storyboards_repair_segmented_function_graphs(monkeypatch):
    import json
    first = {"narration": "We identify the equation and choose y as the independent variable before drawing axes.",
             "objects": [{"type": "axes"}]}
    second = {"narration": "Now we evaluate the function across its domain. The continuous shape matters because guessed straight segments can change the intersection and enclosed region. We compare the curve on shared axes and then read the correct result.",
              "objects": [{"type": "polyline", "points": [[0, 0], [1, 1], [4, 2]]},
                          {"type": "math", "content": "x=y^2"}]}
    bad = {"scenes": [{"heading": "Graph the parabola", "beats": [first, second]} for _ in range(3)]}
    good_step = dict(second, objects=[
        {"type": "function", "expression": "y**2", "variable": "y", "domain_start": -2, "domain_end": 2},
        {"type": "math", "content": "x=y^2"}])
    good = {"scenes": [{"heading": "Graph the parabola", "beats": [first, good_step]} for _ in range(3)]}
    with pytest.raises(ValueError):
        ExplainerPlan.model_validate(bad)
    replies = [bad, good]
    calls = []
    class Response:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": json.dumps(self.payload)}}]}
    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"]["messages"])
        return Response(replies.pop(0))
    monkeypatch.setattr("app.explainer_generation.httpx.post", fake_post)
    result = plan_storyboard({"stem_markdown": "Graph x=y²"}, None, None)
    assert len(calls) == 2
    assert result["scenes"][0]["beats"][1]["objects"][0]["type"] == "function"


def test_narrated_beats_keep_visuals_in_spoken_order():
    scene = {"heading": "Build the graph", "beats": [
        {"narration": "First we draw the axes so the coordinates have meaning.", "objects": [{"type": "axes"}]},
        {"narration": "Now we trace the parabola as a continuous mathematical curve.",
         "objects": [{"type": "function", "expression": "x**2", "domain_start": 0, "domain_end": 2}]},
        {"narration": "Finally we shade the area under the curve between the bounds.",
         "objects": [{"type": "region", "upper_expression": "x**2", "lower_expression": "0",
                      "domain_start": 0, "domain_end": 2}]}]}
    validated = ExplainerPlan.model_validate({"scenes": [scene] * 3}).model_dump()
    assert [[obj["type"] for obj in beat["objects"]] for beat in scene_beats(validated["scenes"][0])] == [
        ["axes"], ["function"], ["region"]]
    premature = dict(scene, beats=[
        dict(scene["beats"][0], objects=scene["beats"][0]["objects"] + scene["beats"][2]["objects"]),
        *scene["beats"][1:]])
    with pytest.raises(ValueError, match="Introduce the boundaries"):
        ExplainerPlan.model_validate({"scenes": [premature] * 3})


def test_caption_cues_cover_all_words_and_match_beat_duration():
    narration = ("First draw the axes and trace the upper curve. Then draw the lower curve "
                 "and shade exactly the region between them. Finally set up the integral.")
    cues = caption_cues(narration, 9.4)
    assert len(cues) >= 2
    assert " ".join(text for text, _ in cues) == narration
    assert all("\n" not in text and duration > 0 for text, duration in cues)
    assert sum(duration for _, duration in cues) == pytest.approx(9.4)


def test_caption_math_markup_preserves_prose_and_escapes_text():
    assert caption_markup("10 x_m squared minus x_m") == "10 x<sub>m</sub> squared minus x<sub>m</sub>"
    assert caption_markup("v^{2} and P_{KE} < 4") == "v<sup>2</sup> and P<sub>KE</sub> &lt; 4"

def test_video_stream_forwards_byte_range(monkeypatch):
    class Body:
        def iter_chunks(self, chunk_size):
            yield b"video"
        def close(self):
            pass
    calls = []
    class Store:
        def get_object(self, **params):
            calls.append(params)
            return {"Body": Body(), "ContentLength": 5, "ContentRange": "bytes 0-4/100"}
    monkeypatch.setattr("app.explainers.storage_client", lambda: Store())
    request = Request({"type": "http", "method": "GET", "headers": [(b"range", b"bytes=0-4")]})
    response = stream_video("clip.mp4", request)
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-4/100"
    assert calls[0]["Range"] == "bytes=0-4"


def test_deleting_unattempted_test_cleans_explainer_records(records, monkeypatch):
    db, teacher, _, _, test, _ = records
    db.query(Attempt).delete()
    db.add(ExplainerRevision(id="to-delete", test_id=test.id, version_id="version", question_id="q1",
                             requested_by=teacher.id, status="ready", video_object_key="explainers/test/version/q1/to-delete.mp4"))
    db.commit()
    removed = []
    monkeypatch.setattr("app.main.delete_prefix", lambda prefix: removed.append(prefix))
    delete_test(test.id, teacher, db)
    assert db.query(ExplainerRevision).count() == 0
    assert removed == ["explainers/test/"]

def test_generation_limit_counts_all_attempts_per_question(records):
    db, teacher, _, _, test, _ = records
    for _ in range(3):
        draft = generate_explainer(test.id, "q1", GenerateExplainerIn(), teacher, db)
        cancel_explainer(test.id, draft["id"], teacher, db)
    with pytest.raises(HTTPException) as exc:
        generate_explainer(test.id, "q1", GenerateExplainerIn(), teacher, db)
    assert exc.value.status_code == 409
    assert "limit of 3" in exc.value.detail
    assert generate_explainer(test.id, "q2", GenerateExplainerIn(override_suggestion=True), teacher, db)["status"] == "queued"


def test_generation_rejected_after_publication(records):
    db, teacher, _, _, test, _ = records
    row = ExplainerRevision(id="published", test_id=test.id, version_id="version", question_id="q1",
                            requested_by=teacher.id, status="ready", video_object_key="video.mp4")
    db.add(row); db.commit()
    publish_explainer(test.id, row.id, teacher, db)
    with pytest.raises(HTTPException) as exc:
        generate_explainer(test.id, "q1", GenerateExplainerIn(), teacher, db)
    assert exc.value.status_code == 409
    assert "already been published" in exc.value.detail


def test_mixed_latex_visuals_and_narration():
    from app.explainer_text import split_math, narration_text
    label = r"Total mass $M = 800\text{ kg} + 200\text{ kg} = 1000\text{ kg}$"
    assert split_math(label) == [(False, "Total mass "), (True, r"M = 800\text{ kg} + 200\text{ kg} = 1000\text{ kg}")]
    assert narration_text(label) == "Total mass M = 800 kg + 200 kg = 1000 kg"
    assert narration_text(r"force $F$ equal to \(P=42\text{ kW}\)") == "force F equal to P=42 kW"
    assert narration_text(r"$\frac{1}{2} \times x^{2}$") == "(1)/(2) × x^2"
    assert split_math(r"$$x^2$$") == [(True, "x^2")]
    with pytest.raises(ValueError):
        Visual(type="text", content=r"Label $\input{/etc/passwd}$")
