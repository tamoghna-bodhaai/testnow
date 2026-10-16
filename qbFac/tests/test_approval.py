import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from qbfac.approval import candidate_sha256, prepare_decisions, promote_candidates


class ApprovalTests(unittest.TestCase):
    def test_only_evidence_bound_approved_items_enter_release(self):
        candidate = {
            "candidate_id": "source-000001", "printed_number": "7",
            "provenance": {"source_file": "source.pdf"}, "source_text": "7 What is 2 + 2?",
        }
        item = {
            "id": "source-000001", "revision": 1, "exam_profiles": ["jee-main-paper-1"],
            "taxonomy": {"subject": "Mathematics", "chapter": "Arithmetic", "topic": "Addition"},
            "interaction": {"kind": "single_choice", "options": [
                {"id": "A", "content": {"format": "markdown", "value": "3"}},
                {"id": "B", "content": {"format": "markdown", "value": "4"}},
            ]},
            "content": {"stem": {"format": "markdown", "value": "What is 2 + 2?"}},
            "answer": {"selected": ["B"]},
            "solution": {"format": "markdown", "value": "Two plus two equals four."},
            "scoring": {"type": "exact", "correct": 4, "incorrect": -1, "unanswered": 0},
            "provenance": {"source_id": "source.pdf", "candidate_id": "source-000001", "printed_number": "7", "spans": [
                {"role": "question", "page": 1, "bbox": [0, 0, 100, 100]},
                {"role": "answer", "page": 2, "bbox": [0, 0, 100, 100]},
                {"role": "solution", "page": 3, "bbox": [0, 0, 100, 100]},
            ]},
            "quality": {"status": "approved", "approved_by": "reviewer-1", "checks": ["structure", "answer", "solution", "taxonomy", "visual"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.jsonl"
            decisions = root / "decisions.jsonl"
            output = root / "release.zip"
            report_path = root / "report.json"
            candidates.write_text(json.dumps(candidate) + "\n")
            self.assertEqual(prepare_decisions(candidates, decisions), 1)
            self.assertEqual(json.loads(decisions.read_text())["status"], "pending")
            queued = promote_candidates(candidates_path=candidates, decisions_path=decisions, output=output, report_path=report_path, release_id="sample", title="Sample")
            self.assertEqual((queued["pending"], queued["rejected"]), (1, 0))
            self.assertFalse(output.exists())
            decision = {"candidate_id": candidate["candidate_id"], "candidate_sha256": candidate_sha256(candidate), "status": "approved", "item": item}
            decisions.write_text(json.dumps(decision) + "\n")
            report = promote_candidates(candidates_path=candidates, decisions_path=decisions, output=output, report_path=report_path, release_id="sample", title="Sample")
            self.assertEqual(report["approved"], 1)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(json.loads(archive.read("manifest.json"))["item_count"], 1)

            decision["candidate_sha256"] = "wrong"
            decisions.write_text(json.dumps(decision) + "\n")
            other = root / "rejected.zip"
            report = promote_candidates(candidates_path=candidates, decisions_path=decisions, output=other, report_path=report_path, release_id="sample", title="Sample")
            self.assertEqual(report["rejected"], 1)
            self.assertFalse(other.exists())

    def test_missing_review_remains_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "candidates.jsonl").write_text(json.dumps({"candidate_id": "one", "printed_number": "1", "provenance": {"source_file": "source.pdf"}}) + "\n")
            (root / "decisions.jsonl").write_text("")
            report = promote_candidates(candidates_path=root / "candidates.jsonl", decisions_path=root / "decisions.jsonl", output=root / "release.zip", report_path=root / "report.json", release_id="sample", title="Sample")
            self.assertEqual(report["pending"], 1)
            self.assertFalse((root / "release.zip").exists())
