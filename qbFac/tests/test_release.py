import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from qbfac.release import build_release


class ReleaseBuilderTests(unittest.TestCase):
    def test_builds_a_portable_validated_release(self):
        item = {
            "id": "jee-main-physics-kinematics-000001",
            "revision": 1,
            "exam_profiles": ["jee-main-paper-1"],
            "taxonomy": {"subject": "Physics", "chapter": "Kinematics", "topic": "Motion in one dimension"},
            "interaction": {"kind": "single_choice", "options": [
                {"id": "A", "content": {"format": "markdown", "value": "1 m/s"}},
                {"id": "B", "content": {"format": "markdown", "value": "2 m/s"}},
            ]},
            "content": {"stem": {"format": "markdown", "value": "A body moves with constant velocity."}},
            "answer": {"selected": ["B"]},
            "solution": {"format": "markdown", "value": "Read the stated velocity."},
            "scoring": {"type": "exact", "correct": 4, "incorrect": -1, "unanswered": 0},
            "provenance": {"source_id": "source-001", "printed_number": "1", "spans": [{"page": 1, "bbox": [10, 20, 300, 180]}]},
            "quality": {"status": "approved", "checks": ["structure", "answer"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "main-physics-2026.1.zip"
            report = build_release(
                release_id="main-physics-2026.1",
                title="JEE Main Physics release 2026.1",
                items=[item],
                output=output,
            )

            self.assertEqual(report["approved_items"], 1)
            self.assertTrue(output.exists())
            with zipfile.ZipFile(output) as package:
                manifest = json.loads(package.read("manifest.json"))
                self.assertEqual(manifest["schema_version"], "1.0")
                self.assertEqual(manifest["item_count"], 1)
                self.assertIn("items/jee-main-physics-kinematics-000001.json", package.namelist())

    def test_includes_verified_media_referenced_by_an_item(self):
        item = {
            "id": "jee-advanced-physics-optics-000001", "revision": 1,
            "exam_profiles": ["jee-advanced"],
            "taxonomy": {"subject": "Physics", "chapter": "Ray optics", "topic": "Reflection"},
            "interaction": {"kind": "single_choice", "options": [
                {"id": "A", "content": {"format": "markdown", "value": "Concave"}},
                {"id": "B", "content": {"format": "markdown", "value": "Convex"}},
            ]},
            "content": {"stem": {"format": "asset", "value": "assets/mirror.png"}},
            "answer": {"selected": ["A"]},
            "solution": {"format": "markdown", "value": "The diagram shows a concave mirror."},
            "scoring": {"type": "exact", "correct": 3, "incorrect": -1, "unanswered": 0},
            "provenance": {"source_id": "source-002", "printed_number": "2", "spans": [{"page": 2, "bbox": [10, 20, 300, 180]}]},
            "quality": {"status": "approved", "checks": ["structure", "visual", "answer"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "advanced-optics.zip"
            build_release(release_id="advanced-optics", title="Optics", items=[item], assets={"assets/mirror.png": b"png-bytes"}, output=output)
            with zipfile.ZipFile(output) as package:
                self.assertEqual(package.read("assets/mirror.png"), b"png-bytes")

    def test_rejects_an_item_that_references_missing_media(self):
        item = {
            "id": "broken-asset", "revision": 1, "exam_profiles": ["jee-main-paper-1"],
            "taxonomy": {"subject": "Physics", "chapter": "Optics", "topic": "Mirrors"},
            "interaction": {"kind": "numerical"},
            "content": {"stem": {"format": "asset", "value": "assets/missing.png"}},
            "answer": {"value": 4},
            "solution": {"format": "markdown", "value": "The value is four."},
            "scoring": {"type": "exact", "correct": 4, "incorrect": 0, "unanswered": 0},
            "provenance": {"source_id": "source-003", "spans": [{"page": 1, "bbox": [0, 0, 1, 1]}]},
            "quality": {"status": "approved", "checks": ["structure"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "missing assets"):
                build_release(release_id="broken", title="Broken", items=[item], assets={}, output=Path(directory) / "broken.zip")

    def test_hash_changes_when_a_referenced_asset_changes(self):
        item = {
            "id": "asset-hash", "revision": 1, "exam_profiles": ["jee-main-paper-1"],
            "taxonomy": {"subject": "Physics", "chapter": "Optics", "topic": "Mirrors"},
            "interaction": {"kind": "numerical"}, "content": {"stem": {"format": "asset", "value": "assets/a.png"}},
            "answer": {"value": 4}, "scoring": {"type": "exact", "correct": 4, "incorrect": 0, "unanswered": 0},
            "solution": {"format": "markdown", "value": "The value is four."},
            "provenance": {"source_id": "source-004", "spans": [{"page": 1, "bbox": [0, 0, 1, 1]}]}, "quality": {"status": "approved", "checks": ["structure"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            first = build_release(release_id="one", title="One", items=[item], assets={"assets/a.png": b"first"}, output=Path(directory) / "one.zip")
            second = build_release(release_id="two", title="Two", items=[item], assets={"assets/a.png": b"second"}, output=Path(directory) / "two.zip")
            self.assertNotEqual(first["content_sha256"], second["content_sha256"])

    def test_rejects_ambiguous_options_empty_profiles_and_missing_passage_children(self):
        base = {
            "id": "bad", "revision": 1, "exam_profiles": ["jee-main-paper-1"],
            "taxonomy": {"subject": "Physics", "chapter": "Optics", "topic": "Mirrors"},
            "interaction": {"kind": "single_choice", "options": [
                {"id": "A", "content": {"format": "markdown", "value": "one"}},
                {"id": "A", "content": {"format": "markdown", "value": "two"}},
            ]}, "content": {"stem": {"format": "markdown", "value": "Question"}}, "answer": {"selected": ["A"]},
            "solution": {"format": "markdown", "value": "An explanation."},
            "scoring": {"type": "exact", "correct": 4, "incorrect": -1, "unanswered": 0},
            "provenance": {"source_id": "source-005", "spans": [{"page": 1, "bbox": [0, 0, 1, 1]}]}, "quality": {"status": "approved", "checks": ["structure"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bad.zip"
            with self.assertRaisesRegex(Exception, "option"):
                build_release(release_id="bad", title="Bad", items=[base], output=output)
            base["exam_profiles"] = []
            base["interaction"] = {"kind": "numerical"}; base["answer"] = {"value": 1}
            with self.assertRaisesRegex(Exception, "exam_profiles"):
                build_release(release_id="bad", title="Bad", items=[base], output=output)
            base["exam_profiles"] = ["qbfac-generic"]; base["interaction"] = {"kind": "passage", "children": ["missing-child"]}; base["answer"] = {}
            with self.assertRaisesRegex(ValueError, "missing passage children"):
                build_release(release_id="bad", title="Bad", items=[base], output=output)

