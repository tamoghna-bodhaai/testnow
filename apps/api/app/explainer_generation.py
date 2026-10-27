"""LLM storyboard, OpenRouter speech, and bounded video rendering."""
import json
import base64
import os
import signal
import time
from pathlib import Path
import re
import subprocess
import tempfile
import httpx
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update
from typing import Literal

from .config import settings
from .db import SessionLocal
from .explainer_math import sample_function, sample_region
from .models import ExplainerRevision, TestVersion
from .storage import client as storage_client, get, put
from .tasks import celery_app

ACTIVE_STATUSES = ("queued", "planning", "narrating", "rendering", "finalizing")

class GenerationCancelled(Exception):
    pass

def check_cancelled(db, revision_id: str):
    status = db.execute(select(ExplainerRevision.status).where(ExplainerRevision.id == revision_id)).scalar_one_or_none()
    if status is None or status == "cancelled":
        raise GenerationCancelled()

def set_stage(db, revision_id: str, status: str, message: str):
    changed = db.execute(update(ExplainerRevision).where(
        ExplainerRevision.id == revision_id, ExplainerRevision.status.in_(ACTIVE_STATUSES)
    ).values(status=status, progress_message=message).execution_options(synchronize_session=False)).rowcount
    if not changed:
        db.rollback()
        raise GenerationCancelled()
    db.commit()
    db.expire_all()

class Visual(BaseModel):
    type: Literal["text", "math", "dot", "circle", "line", "arrow", "polyline", "axes", "function", "region"]
    color: Literal["auto", "white", "cyan", "yellow", "green", "coral"] = "auto"
    content: str = Field(default="", max_length=160)
    x: float = Field(default=0, ge=-5.5, le=5.5)
    y: float = Field(default=0, ge=-2.8, le=2.8)
    x2: float = Field(default=0, ge=-5.5, le=5.5)
    y2: float = Field(default=0, ge=-2.8, le=2.8)
    radius: float = Field(default=1, gt=0, le=2.5)
    points: list[list[float]] = Field(default_factory=list, max_length=30)
    expression: str = Field(default="", max_length=120)
    upper_expression: str = Field(default="", max_length=120)
    lower_expression: str = Field(default="", max_length=120)
    variable: Literal["x", "y"] = "x"
    domain_start: float = Field(default=-3, ge=-20, le=20)
    domain_end: float = Field(default=3, ge=-20, le=20)

    @model_validator(mode="after")
    def safe_visual(self):
        if self.type in {"text", "math"} and not self.content.strip():
            raise ValueError("Visual text is required")
        if self.type == "math" and re.search(r"\\(?:input|include|write|read|open|catcode|usepackage|immediate|special|def|newcommand)\b", self.content, re.I):
            raise ValueError("Unsupported TeX command")
        if self.type == "polyline" and (len(self.points) < 2 or any(len(p) != 2 or not all(isinstance(v, (int, float)) and -5.5 <= v <= 5.5 for v in p) for p in self.points)):
            raise ValueError("Invalid polyline")
        if self.type == "function":
            sample_function(self.expression, self.variable, self.domain_start, self.domain_end, count=41)
        if self.type == "region":
            if self.points:
                if self.upper_expression or self.lower_expression or len(self.points) < 3 or any(
                    len(p) != 2 or not all(isinstance(v, (int, float)) and -20 <= v <= 20 for v in p)
                    for p in self.points
                ):
                    raise ValueError("Invalid polygon region")
            else:
                sample_region(self.upper_expression, self.lower_expression, self.variable,
                              self.domain_start, self.domain_end, count=41)
        return self

class BeatPlan(BaseModel):
    narration: str = Field(min_length=12, max_length=500)
    objects: list[Visual] = Field(min_length=1, max_length=5)


class ScenePlan(BaseModel):
    heading: str = Field(min_length=2, max_length=70)
    # Legacy drafts retain their one-piece scene. New drafts use narrated beats.
    narration: str = Field(default="", max_length=1400)
    objects: list[Visual] = Field(default_factory=list, max_length=10)
    beats: list[BeatPlan] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def function_graphs_use_equations(self):
        if self.beats:
            if self.narration or self.objects:
                raise ValueError("A scene must use either beats or legacy narration and objects")
            objects = [obj for beat in self.beats for obj in beat.objects]
            if len(objects) > 10:
                raise ValueError("Too many visuals in one scene")
            if any(obj.type == "region" for obj in self.beats[0].objects):
                raise ValueError("Introduce the boundaries before revealing the shaded region")
        else:
            if not self.narration.strip() or not self.objects:
                raise ValueError("A scene needs narrated visual steps")
            objects = self.objects
        if any(obj.type == "region" for obj in objects) and not any(obj.type == "axes" for obj in objects):
            raise ValueError("A shaded region needs mathematical axes")
        if any(obj.type == "axes" for obj in objects):
            if any(obj.type == "polyline" for obj in objects):
                raise ValueError("Axes graphs cannot approximate a curve with a polyline; use a function")
            if any(obj.type == "math" and "=" in obj.content for obj in objects) and not any(
                obj.type == "function" for obj in objects
            ) and not any(obj.type == "region" and obj.points for obj in objects):
                raise ValueError("A labelled equation on axes needs a function plot")
        return self

class ExplainerPlan(BaseModel):
    scenes: list[ScenePlan] = Field(min_length=2, max_length=6)


def scene_beats(scene: dict) -> list[dict]:
    """Read both current beat storyboards and previously saved scene specs."""
    return scene.get("beats") or [{"narration": scene["narration"], "objects": scene["objects"]}]

def validate_storyboard(value: str) -> dict:
    parsed = json.loads(value)
    if isinstance(parsed, list):
        parsed = {"scenes": parsed}
    if isinstance(parsed, dict) and isinstance(parsed.get("scenes"), list):
        for scene in parsed["scenes"]:
            if not isinstance(scene, dict):
                continue
            for step in [scene, *(scene.get("beats") or [])]:
                if "objects" not in step and ("visuals" in step or "elements" in step):
                    step["objects"] = step.get("visuals", step.get("elements"))
                if isinstance(step.get("objects"), list):
                    for obj in step["objects"]:
                        if isinstance(obj, dict) and "content" not in obj:
                            obj["content"] = obj.get("label", obj.get("text", obj.get("latex", "")))
    return ExplainerPlan.model_validate(parsed).model_dump()

def plan_storyboard(question: dict, feedback: str | None, previous: dict | None) -> dict:
    prompt = {"question": {k: question.get(k) for k in ("stem_markdown", "options", "answer", "solution_markdown", "diagrams", "taxonomy")},
              "teacher_feedback": feedback, "previous_draft": previous}
    system = ("Create an accurate 60-120 second English educational animation with a blackboard mathematical visual style. "
              "Use only the supplied question and worked solution as factual authority. Return JSON with 2-6 scenes. "
              "Each scene has a heading and 2-5 beats, with at most 10 objects total. Each beat has its own short narration and 1-5 NEW visual objects, revealed when that beat's speech starts. "
              "Objects persist for the rest of their scene. Never put all objects in the first beat or show a conclusion before its spoken explanation. "
              "Plan a teaching sequence: introduce the question, construct axes/curves or diagram, mark bounds/intersections, reveal the relevant shaded area, then show the setup and answer. "
              "Split spoken reasoning into beats at the exact moments when a visual should appear. Keep each beat focused on one idea. "
              "Visual types: text, math, dot, circle, line, arrow, polyline, axes, function, region. "
              "For every mathematical graph, include axes and function objects. A function object has expression (plain arithmetic, e.g. 'y**2' or '2*y+3'), "
              "variable ('y' means x=f(y); 'x' means y=f(x)), domain_start, and domain_end. "
              "Use functions for every smooth equation, including lines and parabolas; NEVER approximate a function with line segments or polyline points. "
              "Use polyline only for genuinely piecewise straight diagrams without axes; do not use it on mathematical axes. "
              "For area, integration, probability-density, or enclosed-region problems, reveal a region object in a beat after the relevant boundaries have been introduced. "
              "A curved region uses upper_expression and lower_expression (plain arithmetic functions of variable), variable, domain_start, and domain_end. "
              "For vertical slices variable is x and upper/lower are y values; for horizontal slices variable is y and upper/lower are x values. "
              "Use '0' for an axis boundary. Split at intersections if which function is upper changes. "
              "For an area bounded only by straight edges, region may instead use points [[x,y],...] in boundary order. "
              "Region boundaries must match the actual equations; never guess a filled shape from a few points. "
              "All graph objects share mathematical x,y coordinates; do not use screen positions to lay them out. "
              "For other marks keep coordinates within x -5.5..5.5 and y -2.8..2.8. Line/arrow use x,y,x2,y2; "
              "circle uses radius; polyline uses points [[x,y],...]. Put brief labels and equations in text/math objects; "
              "the renderer places these in a separate annotation area, ignoring their x and y. "
              "Use at most three short annotations per graph scene. Give functions mathematically correct expressions and domains from the source solution. "
              "Use bright cyan, yellow, green, or coral for highlighted math/curves and white for ordinary equations; the optional color field can match an equation to its curve. "
              "Keep text concise and legible. Narration across all beats totals 160-220 words in clear Indian English, explaining concept, worked steps, and answer. "
              "Function expressions contain arithmetic only; never include Python code, imports, or arbitrary LaTeX commands. Return only JSON.")
    user_content = [{"type": "text", "text": json.dumps(prompt)}]
    for diagram in (question.get("diagrams") or [])[:2]:
        key = diagram.get("object_key") if isinstance(diagram, dict) else None
        if not key:
            continue
        try:
            data = get(key)
        except Exception:
            continue
        media_type = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg" if data.startswith(b"\xff\xd8") else None
        if media_type and len(data) <= 2_000_000:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{base64.b64encode(data).decode()}"}})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_content}]
    for attempt in range(2):
        response = httpx.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings().openrouter_api_key}", "HTTP-Referer": "https://testnow.app", "X-Title": "TestNow Explainers"},
            json={"model": settings().explainer_model, "messages": messages,
                  "response_format": {"type": "json_object"}, "stream": False}, timeout=120)
        response.raise_for_status()
        value = response.json()["choices"][0]["message"]["content"]
        try:
            spec = validate_storyboard(value)
            if any(len(scene["beats"]) < 2 for scene in spec["scenes"]):
                raise ValueError("Each new scene needs at least two narrated visual beats")
            stem = question.get("stem_markdown") or ""
            if re.search(r"\b(area|integral|integration)\b|shaded\s+region", stem, re.I) and not any(
                obj["type"] == "region" for scene in spec["scenes"] for beat in scene["beats"] for obj in beat["objects"]
            ):
                raise ValueError("An area problem needs a shaded mathematical region")
            words = sum(len(beat["narration"].split()) for scene in spec["scenes"] for beat in scene["beats"])
            if not 130 <= words <= 250:
                raise ValueError("Narration length is outside the supported range")
            return spec
        except (ValueError, json.JSONDecodeError) as exc:
            if attempt:
                raise ValueError("The explainer plan could not be validated. Please generate a new draft.") from exc
            messages.extend([
                {"role": "assistant", "content": value},
                {"role": "user", "content": (
                    f"Correct the storyboard JSON. Validation error: {str(exc)[:500]}. "
                    "Every equation shown on axes must use a function object, not plotted line segments. "
                    "Area problems need a mathematically bounded region object in the relevant graph scene. "
                    "Every scene needs 2-5 narrated beats, each adding only the visuals discussed in that beat. "
                    "Keep the same mathematically correct lesson and return the complete corrected JSON."
                )},
            ])

def speech(text: str, path: Path):
    response = httpx.post("https://openrouter.ai/api/v1/audio/speech",
        headers={"Authorization": f"Bearer {settings().openrouter_api_key}"},
        json={"model": settings().explainer_tts_model, "voice": settings().explainer_tts_voice,
              "input": text, "response_format": "mp3"}, timeout=120)
    response.raise_for_status()
    if not response.headers.get("content-type", "").startswith("audio/") or len(response.content) < 1000:
        raise ValueError("Speech service did not return audio")
    path.write_bytes(response.content)

def run(command: list[str], *, env=None, cwd=None, timeout=300, cancel_check=None):
    process = subprocess.Popen(command, env=env, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                output, errors = process.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if cancel_check:
                    cancel_check()
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
        if cancel_check:
            cancel_check()
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command, output, errors)
        return subprocess.CompletedProcess(command, process.returncode, output, errors)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        raise

def render_video(spec: dict, work: Path, *, progress=None, cancel_check=None) -> bytes:
    audio_files = []
    durations = []
    for index, scene in enumerate(spec["scenes"]):
        beat_durations = []
        for beat_index, beat in enumerate(scene_beats(scene)):
            if cancel_check:
                cancel_check()
            if progress:
                progress("narrating", f"Recording scene {index + 1} of {len(spec['scenes'])}, step {beat_index + 1}")
            audio = work / f"scene-{index}-step-{beat_index}.mp3"
            speech(beat["narration"], audio)
            if cancel_check:
                cancel_check()
            audio_files.append(audio)
            probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio)], cancel_check=cancel_check)
            beat_durations.append(float(probe.stdout.strip()))
        durations.append(beat_durations)
    total = sum(sum(scene_durations) for scene_durations in durations)
    if not 55 <= total <= 125:
        raise ValueError("Narration duration is outside the supported range")
    (work / "spec.json").write_text(json.dumps(spec))
    (work / "durations.json").write_text(json.dumps(durations))
    manifest = work / "audio.txt"
    manifest.write_text("".join(f"file '{path.name}'\n" for path in audio_files))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c:a", "aac", str(work / "narration.m4a")], cwd=work, cancel_check=cancel_check)
    if progress:
        progress("rendering", "Building the animation scenes")
    media = work / "media"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(work), "PYTHONPATH": "/app", "EXPLAINER_SPEC_FILE": str(work / "spec.json"),
           "EXPLAINER_DURATIONS_FILE": str(work / "durations.json")}
    source = str(Path(__file__).with_name("explainer_render.py"))
    run(["runuser", "-u", "renderer", "--", "manim", "-qm", "--disable_caching", "--media_dir", str(media), source, "Explainer"], env=env, cwd=work, timeout=600, cancel_check=cancel_check)
    clips = list(media.rglob("Explainer.mp4"))
    if not clips:
        raise RuntimeError("Manim did not produce a video")
    output = work / "explainer.mp4"
    if progress:
        progress("finalizing", "Combining narration and video")
    run(["ffmpeg", "-y", "-i", str(clips[0]), "-i", str(work / "narration.m4a"), "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(output)], cwd=work, cancel_check=cancel_check)
    return output.read_bytes()

@celery_app.task(name="app.explainers.render_explainer", max_retries=0, time_limit=900)
def render_explainer(revision_id: str):
    db = SessionLocal()
    uploaded_key = None
    try:
        row = db.get(ExplainerRevision, revision_id)
        if not row or row.status != "queued":
            return
        version = db.get(TestVersion, row.version_id)
        question = next((q for q in version.content["questions"] if q["id"] == row.question_id), None)
        if not question:
            raise ValueError("Question snapshot is missing")
        previous = db.query(ExplainerRevision).filter_by(test_id=row.test_id, version_id=row.version_id,
            question_id=row.question_id, status="ready").order_by(ExplainerRevision.created_at.desc()).first()
        set_stage(db, revision_id, "planning", "Formulating the lesson and scenes")
        spec = plan_storyboard(question, row.feedback, previous.scene_spec if previous else None)
        check_cancelled(db, revision_id)
        row.scene_spec = spec
        db.commit()
        set_stage(db, revision_id, "narrating", "Preparing narration")
        with tempfile.TemporaryDirectory(prefix="explainer-") as folder:
            work = Path(folder)
            os.chmod(work, 0o777)
            data = render_video(spec, work,
                                progress=lambda status, message: set_stage(db, revision_id, status, message),
                                cancel_check=lambda: check_cancelled(db, revision_id))
        set_stage(db, revision_id, "finalizing", "Saving the finished video")
        key = f"explainers/{row.test_id}/{row.version_id}/{row.question_id}/{row.id}.mp4"
        put(key, data, "video/mp4")
        uploaded_key = key
        changed = db.execute(update(ExplainerRevision).where(
            ExplainerRevision.id == revision_id, ExplainerRevision.status == "finalizing"
        ).values(video_object_key=key, status="ready", progress_message="Ready to preview", error=None)
            .execution_options(synchronize_session=False)).rowcount
        if not changed:
            db.rollback()
            raise GenerationCancelled()
        db.commit()
    except GenerationCancelled:
        db.rollback()
        if uploaded_key:
            try:
                storage_client().delete_object(Bucket=settings().s3_bucket, Key=uploaded_key)
            except Exception:
                pass
        return
    except Exception as exc:
        db.rollback()
        db.expire_all()
        row = db.get(ExplainerRevision, revision_id)
        if row and row.status != "cancelled":
            row.status = "failed"
            row.progress_message = "Generation failed"
            row.error = str(exc)[:1000]
            db.commit()
            raise
    finally:
        db.close()
