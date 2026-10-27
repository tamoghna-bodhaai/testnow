"""Trusted Manim renderer with a fixed graph and annotation layout."""
import json
import os
from pathlib import Path

from manim import *
from app.explainer_captions import caption_cues, caption_markup
from app.explainer_math import sample_function, sample_region
from app.explainer_text import split_math, narration_text

BLACK = "#000000"
WHITE_INK = "#FFFFFF"
BLUE_INK = "#71E5FF"
PALETTE = {"white": WHITE_INK, "cyan": BLUE_INK, "yellow": "#FFE66D",
           "green": "#A8F58B", "coral": "#FF9D8A"}
COLORS = (PALETTE["cyan"], PALETTE["yellow"], PALETTE["green"], PALETTE["coral"])
SERIF = "Latin Modern Roman"


def fit(mob, width, height):
    if mob.width > width:
        mob.scale_to_fit_width(width)
    if mob.height > height:
        mob.scale_to_fit_height(height)
    return mob


def bounds(marks):
    xs, ys = [0.0], [0.0]
    for item in marks:
        kind = item["type"]
        if kind in ("dot", "circle", "line", "arrow"):
            x, y = float(item.get("x", 0)), float(item.get("y", 0))
            xs.append(x); ys.append(y)
            if kind == "circle":
                r = float(item.get("radius", 1))
                xs.extend((x-r, x+r)); ys.extend((y-r, y+r))
        if kind in ("line", "arrow"):
            xs.append(float(item["x2"])); ys.append(float(item["y2"]))
        if kind == "polyline":
            for x, y in item["points"]:
                xs.append(float(x)); ys.append(float(y))
        if kind == "function":
            for run in sample_function(item["expression"], item["variable"],
                                       item["domain_start"], item["domain_end"]):
                for x, y in run:
                    xs.append(x); ys.append(y)
        if kind == "region":
            for x, y in region_points(item):
                xs.append(x); ys.append(y)
    def padded(values):
        lo, hi = min(values), max(values)
        span = max(2.0, hi-lo)
        pad = max(0.4, span * 0.12)
        return [lo-pad, hi+pad, max(0.5, round(span/5, 1))]
    return padded(xs), padded(ys)


def region_points(item):
    if item.get("points"):
        return [(float(x), float(y)) for x, y in item["points"]]
    return sample_region(item["upper_expression"], item["lower_expression"],
                         item["variable"], item["domain_start"], item["domain_end"])


def ink(item, fallback):
    return PALETTE.get(item.get("color", "auto"), fallback)


def graph_mark(item, axes, color):
    xy = lambda x, y: axes.c2p(float(x), float(y))
    kind = item["type"]
    x, y = item.get("x", 0), item.get("y", 0)
    if kind == "dot":
        return Dot(xy(x, y), color=color, radius=0.075)
    if kind == "circle":
        radius = float(item.get("radius", 1)) * min(axes.x_axis.unit_size, axes.y_axis.unit_size)
        return Circle(radius=radius, color=color, stroke_width=4).move_to(xy(x, y))
    if kind == "line":
        if (float(x), float(y)) == (float(item["x2"]), float(item["y2"])):
            return Dot(xy(x, y), color=color, radius=0.075)
        return Line(xy(x, y), xy(item["x2"], item["y2"]), color=color, stroke_width=5)
    if kind == "arrow":
        if (float(x), float(y)) == (float(item["x2"]), float(item["y2"])):
            return Dot(xy(x, y), color=color, radius=0.075)
        return Arrow(xy(x, y), xy(item["x2"], item["y2"]), color=color, buff=0, stroke_width=5)
    if kind == "polyline":
        points = []
        for x, y in item["points"]:
            candidate = (float(x), float(y))
            if not points or candidate != points[-1]:
                points.append(candidate)
        if len(points) == 1:
            return Dot(xy(*points[0]), color=color, radius=0.075)
        return VMobject(color=color, stroke_width=5).set_points_as_corners(
            [xy(x, y) for x, y in points])
    if kind == "function":
        runs = sample_function(item["expression"], item["variable"],
                               item["domain_start"], item["domain_end"])
        return VGroup(*[
            VMobject(color=color, stroke_width=5).set_points_smoothly(
                [xy(x, y) for x, y in run])
            for run in runs
        ])
    raise ValueError("Unsupported graph mark")


def graph_region(item, axes):
    return Polygon(*[axes.c2p(x, y) for x, y in region_points(item)],
                   stroke_opacity=0, fill_color=ink(item, PALETTE["green"]), fill_opacity=0.48).set_z_index(-2)


def mixed_text(content, color, font_size=34):
    """Render prose and inline math together, preserving their visual order."""
    segments = split_math(content)
    if not any(is_math for is_math, _ in segments):
        return Text(narration_text(content), font=SERIF, font_size=font_size, color=color)
    parts = []
    for is_math, value in segments:
        if is_math:
            parts.append(MathTex(value, font_size=font_size, color=color))
        elif value.strip():
            parts.append(Text(value.strip(), font=SERIF, font_size=font_size, color=color))
    return VGroup(*parts).arrange(RIGHT, buff=0.12, aligned_edge=DOWN)


def scene_body(items):
    marks = [item for item in items if item["type"] in ("dot", "circle", "line", "arrow", "polyline", "function")]
    regions = [item for item in items if item["type"] == "region"]
    notes = [item for item in items if item["type"] in ("text", "math")]
    show_axes = any(item["type"] in ("axes", "function") for item in items)
    has_graph = show_axes or bool(marks) or bool(regions)
    body = [None] * len(items)
    if has_graph:
        # All marks use one data-to-screen transform, including legacy storyboards.
        x_range, y_range = bounds(marks + regions)
        axes = Axes(x_range=x_range, y_range=y_range,
                    x_length=8.6 if notes else 11.8, y_length=4.2, tips=False,
                    axis_config={"color": "#888888", "stroke_width": 2,
                                 "include_ticks": True, "tick_size": 0.06})
        axes.move_to(LEFT * 1.9 + DOWN * 0.15 if notes else DOWN * 0.15)
        axes.set_z_index(-1)
        mark_index = 0
        for index, item in enumerate(items):
            if item["type"] == "region":
                body[index] = graph_region(item, axes)
            elif item["type"] == "axes":
                body[index] = axes
            elif item in marks:
                body[index] = graph_mark(item, axes, ink(item, COLORS[mark_index % len(COLORS)]))
                mark_index += 1
    if notes:
        width = 3.4 if has_graph else 10.8
        annotations = []
        for i, item in enumerate(notes):
            color = ink(item, WHITE_INK)
            content = item["content"]
            segments = split_math(content)
            if item["type"] == "math":
                if len(segments) == 1 and segments[0][0]:
                    content = segments[0][1]
                annotation = MathTex(content, font_size=44, color=color)
            else:
                annotation = mixed_text(content, color)
            annotations.append(fit(annotation, width, 1.15))
        stack = VGroup(*annotations).arrange(DOWN, buff=0.38, aligned_edge=LEFT)
        fit(stack, width, 4.5 if has_graph else 4.6)
        stack.move_to(RIGHT * 4.65 + DOWN * 0.1 if has_graph else DOWN * 0.1)
        note_index = 0
        for index, item in enumerate(items):
            if item["type"] in ("text", "math"):
                body[index] = annotations[note_index]
                note_index += 1
    return body


def visual_animation(item, mob):
    if item["type"] in ("text", "math"):
        return Write(mob)
    if item["type"] == "dot":
        return GrowFromCenter(mob)
    if item["type"] == "function":
        return AnimationGroup(*(Create(part) for part in mob), lag_ratio=0)
    if item["type"] in ("axes", "line", "arrow", "polyline", "circle"):
        return Create(mob)
    return FadeIn(mob)


def caption_sequence(narration, duration, hold_last=False):
    animations = []
    cues = caption_cues(narration_text(narration), duration)
    captions = []
    for content, _ in cues:
        label = fit(MarkupText(caption_markup(content), font="DejaVu Sans", font_size=26,
                               color=WHITE_INK), 11.4, 0.48)
        backing = RoundedRectangle(corner_radius=0.12, width=min(12.0, label.width + 0.55),
                                   height=0.68,
                                   fill_color="#10202E", fill_opacity=0.93,
                                   stroke_opacity=0)
        captions.append(VGroup(backing, label).move_to(DOWN * 2.92).set_z_index(10))
    caption = captions[0]
    for index, (_, length) in enumerate(cues):
        transition = min(0.12, length / 5)
        final_fade = min(0.12, length / 5) if index == len(cues) - 1 and not hold_last else 0
        if index == 0:
            animations.append(FadeIn(caption, run_time=transition))
        else:
            animations.append(Transform(caption, captions[index], run_time=transition))
        animations.append(Wait(run_time=length - transition - final_fade))
        if final_fade:
            animations.append(FadeOut(caption, run_time=final_fade))
    return Succession(*animations), caption


class Explainer(Scene):
    def construct(self):
        spec = json.loads(Path(os.environ["EXPLAINER_SPEC_FILE"]).read_text())
        durations = json.loads(Path(os.environ["EXPLAINER_DURATIONS_FILE"]).read_text())
        self.camera.background_color = BLACK
        for scene, timing in zip(spec["scenes"], durations):
            beats = scene.get("beats") or [{"narration": scene["narration"], "objects": scene["objects"]}]
            if isinstance(timing, (int, float)):
                timing = [timing]
            items = [item for beat in beats for item in beat["objects"]]
            heading = fit(mixed_text(scene["heading"], WHITE_INK, font_size=46), 12.2, 0.7).move_to(UP * 3.28)
            rule = Line(LEFT * 6.1 + UP * 2.72, RIGHT * 6.1 + UP * 2.72,
                        color=BLUE_INK, stroke_width=2)
            body = scene_body(items)
            offset = 0
            for beat_index, (beat, duration) in enumerate(zip(beats, timing)):
                count = len(beat["objects"])
                fresh = body[offset:offset + count]
                offset += count
                intro = min(0.45, duration * 0.12) if beat_index == 0 else 0
                outro = min(0.4, duration * 0.1) if beat_index == len(beats) - 1 else 0
                active_duration = duration - outro
                reveal_time = min(2.0, max(0.3, duration * 0.3), active_duration - intro)
                animations = [visual_animation(item, mob) for item, mob in zip(beat["objects"], fresh)]
                reveal = LaggedStart(*animations, lag_ratio=0.18, run_time=reveal_time)
                visual_timeline = Succession(*([
                    AnimationGroup(FadeIn(heading), Create(rule), run_time=intro)
                ] if intro else []), reveal,
                    Wait(run_time=active_duration - intro - reveal_time))
                captions, final_caption = caption_sequence(beat["narration"], active_duration,
                                                            hold_last=bool(outro))
                self.play(AnimationGroup(visual_timeline, captions, lag_ratio=0), run_time=active_duration)
                if outro:
                    self.play(FadeOut(VGroup(heading, rule, *body, final_caption)), run_time=outro)
