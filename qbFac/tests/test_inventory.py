import unittest
import zipfile
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qbfac.inventory import EvidenceLine, EvidencePage, _docx_pages, _reading_order, inventory_pages, inventory_source, write_inventory


def page(number, *lines, document="questions.pdf", role="questions", images=0):
    return EvidencePage(document, number, role, tuple(EvidenceLine(line) for line in lines), images)


def tiny_docx(*paragraphs):
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + ''.join(f'<w:p><w:r><w:t>{value}</w:t></w:r></w:p>' for value in paragraphs)
           + '</w:body></w:document>')
    raw = BytesIO()
    with zipfile.ZipFile(raw, "w") as bundle:
        bundle.writestr("word/document.xml", xml)
    return raw.getvalue()


class CoverageInventoryTests(unittest.TestCase):
    def test_docx_adapter_keeps_paragraphs_and_embedded_media_evidence(self):
        raw = BytesIO()
        with zipfile.ZipFile(raw, "w") as bundle:
            bundle.writestr("word/document.xml", (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>Chapter 1 Algebra</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>1. Diagram question</w:t></w:r></w:p></w:body></w:document>'
            ))
            bundle.writestr("word/media/image1.png", b"fake-media-evidence")
        pages = _docx_pages(raw.getvalue(), "worksheet.docx", "questions")
        self.assertEqual(pages[0].image_count, 1)
        self.assertEqual([line.text for line in pages[0].lines], ["Chapter 1 Algebra", "1. Diagram question"])
        self.assertEqual(inventory_pages(pages)["summary"]["question_candidates"], 1)

    def test_output_is_partitioned_and_never_overwrites_existing_ledger(self):
        ledger = inventory_pages([page(1, "Chapter 1 Algebra", "1. Question")])
        with TemporaryDirectory() as directory:
            output = Path(directory) / "ledger"
            with patch("qbfac.inventory.inventory_source", return_value=ledger):
                write_inventory(Path("worksheet.pdf"), output)
                summary = json.loads((output / "summary.json").read_text())
                self.assertEqual(summary["schema_version"], "inventory-1")
                self.assertEqual(len((output / "questions.jsonl").read_text().splitlines()), 1)
                with self.assertRaisesRegex(ValueError, "must be empty"):
                    write_inventory(Path("worksheet.pdf"), output)

    def test_zip_adapter_links_separate_question_key_and_solution_documents(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "sources.zip"
            with zipfile.ZipFile(source, "w") as bundle:
                bundle.writestr("questions.docx", tiny_docx("Chapter 1 Algebra", "Topic 1 Equations", "1. Find x"))
                bundle.writestr("answer_key.docx", tiny_docx("Chapter 1 Algebra", "Topic 1", "1. (b)"))
                bundle.writestr("solutions.docx", tiny_docx("Chapter 1 Algebra", "Topic 1 Equations", "1. Solve for x"))
            inventory = inventory_source(source)
        self.assertEqual(inventory["summary"]["linked_questions"], 1)
        self.assertEqual(inventory["questions"][0]["answer"]["value"], ["B"])

    def test_geometry_orders_answer_heading_before_two_column_key(self):
        lines = [
            EvidenceLine("Topic 2", (340, 230, 380, 240)),
            EvidenceLine("1. (a)", (345, 250, 400, 260)),
            EvidenceLine("Topic 1", (65, 230, 105, 240)),
            EvidenceLine("1. (b)", (70, 250, 125, 260)),
            EvidenceLine("Answers", (290, 200, 360, 220)),
            EvidenceLine("Hints & Solutions", (230, 500, 425, 520)),
        ]
        self.assertEqual([line.text for line in _reading_order(lines, 650)],
                         ["Answers", "Topic 1", "1. (b)", "Topic 2", "1. (a)", "Hints & Solutions"])

    def test_inline_key_and_solutions_link_without_dropping_other_questions(self):
        inventory = inventory_pages([
            page(1, "Chapter 1 Algebra", "Topic 1 Quadratics", "Objective Questions I",
                 "1. First question (a) 1 (b) 2 (c) 3 (d) 4",
                 "2. Second question (a) 1 (b) 2 (c) 3 (d) 4",
                 "Answers", "Topic 1", "1. (b)", "Hints & Solutions",
                 "Topic 1 Quadratics", "1. First worked solution"),
        ])
        self.assertEqual(len(inventory["questions"]), 2)
        self.assertEqual(inventory["questions"][0]["question_type"], "single_choice")
        self.assertEqual(inventory["questions"][0]["status"], "linked")
        self.assertEqual(inventory["questions"][1]["status"], "missing_answer_and_solution")
        self.assertEqual(inventory["summary"]["question_candidates"], 2)
        self.assertEqual(inventory["summary"]["unresolved_questions"], 1)

    def test_end_of_book_key_and_separate_solution_document(self):
        inventory = inventory_pages([
            page(1, "Chapter 1 Algebra", "Topic 1 Quadratics", "1. First question"),
            page(2, "Chapter 2 Calculus", "Topic 1 Limits", "1. Second question"),
            page(3, "Answers", "Chapter 1 Algebra", "Topic 1", "1. (c)",
                 "Chapter 2 Calculus", "Topic 1", "1. (a)", role="answer_key"),
            page(1, "Solutions", "Chapter 1 Algebra", "Topic 1 Quadratics",
                 "1. First worked solution", "Chapter 2 Calculus", "Topic 1 Limits",
                 "1. Second worked solution", document="solutions.pdf", role="solutions"),
        ])
        self.assertEqual([q["status"] for q in inventory["questions"]], ["linked", "linked"])
        self.assertEqual([q["answer"]["value"] for q in inventory["questions"]], [["C"], ["A"]])
        self.assertEqual(len(inventory["unmatched_answers"]), 0)
        self.assertEqual(len(inventory["unmatched_solutions"]), 0)

    def test_duplicate_number_is_ambiguous_not_arbitrarily_linked(self):
        inventory = inventory_pages([
            page(1, "Chapter 1 Algebra", "Topic 1 Quadratics", "1. First question",
                 "1. Another question", "Answers", "Topic 1", "1. (a)",
                 "Solutions", "Topic 1 Quadratics", "1. Worked solution"),
        ])
        self.assertEqual(len(inventory["questions"]), 2)
        self.assertTrue(all(q["status"] == "ambiguous_question_key" for q in inventory["questions"]))

    def test_integer_and_multi_select_answers_remain_typed_observations(self):
        inventory = inventory_pages([
            page(1, "Chapter 1 Algebra", "Topic 1 Equations", "Objective Questions II",
                 "1. Select every valid choice", "Integer Answer Type Questions",
                 "2. Enter the value", "Answers", "Topic 1", "1. (a, d)", "2. (-3)"),
        ])
        self.assertEqual([q["question_type"] for q in inventory["questions"]],
                         ["multiple_select", "integer"])
        self.assertEqual([answer["value"] for answer in inventory["answers"]], [["A", "D"], -3])

    def test_scan_is_explicit_coverage_gap_not_zero_questions(self):
        inventory = inventory_pages([page(1, images=1)])
        self.assertEqual(inventory["summary"]["pages_needing_ocr"], 1)
        self.assertEqual(inventory["pages"][0]["status"], "needs_ocr")
        self.assertEqual(inventory["summary"]["question_candidates"], 0)
        self.assertFalse(inventory["summary"]["all_pages_text_extracted"])
        self.assertFalse(inventory["summary"]["question_recall_verified"])

    def test_front_matter_numbering_is_preserved_but_not_called_questions(self):
        inventory = inventory_pages([
            page(1, "1. Algebra 2-10", role="front_matter"),
            page(2, "Chapter 1 Algebra", "1. Real question"),
        ])
        self.assertEqual(inventory["summary"]["pages"], 2)
        self.assertEqual(inventory["summary"]["question_candidates"], 1)
        self.assertIn("1. Algebra 2-10", inventory["pages"][0]["lines"][0]["text"])

    def test_toc_chapter_hint_resets_phase_after_prior_solutions(self):
        inventory = inventory_pages([
            EvidencePage("book.pdf", 1, "questions", (EvidenceLine("1. First question"),
                EvidenceLine("Answers"), EvidenceLine("1. (a)"),
                EvidenceLine("Solutions"), EvidenceLine("1. First solution")), chapter_hint="Algebra"),
            EvidencePage("book.pdf", 2, "questions", (EvidenceLine("1. Next chapter question"),),
                         chapter_hint="Calculus"),
        ])
        self.assertEqual([q["chapter"] for q in inventory["questions"]], ["Algebra", "Calculus"])


if __name__ == "__main__":
    unittest.main()
