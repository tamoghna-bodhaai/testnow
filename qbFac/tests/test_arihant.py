import unittest

import fitz

from qbfac.arihant import _answers, _choice_regions, _question_regions, _solution_regions


class ArihantRecipeTests(unittest.TestCase):
    def test_links_topic_scoped_choice_keys(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((70, 100), "Answers", fontsize=12)
        page.insert_text((70, 120), "Topic 1", fontsize=12)
        page.insert_text((70, 140), "1. (c)", fontsize=12)
        page.insert_text((70, 160), "Topic 2", fontsize=12)
        page.insert_text((70, 180), "1. (a, d)", fontsize=12)
        page.insert_text((70, 195), "2. (6)", fontsize=12)
        page.insert_text((70, 220), "Topic 1 Worked Solutions", fontsize=12)
        try:
            answer = _answers(document, [0])
            self.assertEqual(answer[("1", "1")]["selected"], ["C"])
            self.assertEqual(answer[("2", "1")]["selected"], ["A", "D"])
            self.assertEqual(answer[("2", "2")]["value"], 6)
        finally:
            document.close()

    def test_reads_key_by_visual_position_when_pdf_text_order_is_reversed(self):
        document = fitz.open()
        page = document.new_page(width=620, height=800)
        # Many source pages place the heading last in their PDF text stream.
        page.insert_text((70, 250), "Topic 1", fontsize=12)
        page.insert_text((70, 270), "1. (c)", fontsize=12)
        page.insert_text((340, 250), "Topic 2", fontsize=12)
        page.insert_text((340, 270), "1. (a, d)", fontsize=12)
        page.insert_text((70, 550), "Topic 1\nWorked Solutions", fontsize=12)
        page.insert_text((290, 205), "Answers", fontsize=12)
        page.insert_text((230, 500), "Hints & Solutions", fontsize=12)
        try:
            answer = _answers(document, [0])
            self.assertEqual(answer[("1", "1")]["selected"], ["C"])
            self.assertEqual(answer[("2", "1")]["selected"], ["A", "D"])
        finally:
            document.close()

    def test_solution_regions_do_not_reuse_answer_key_rows(self):
        document = fitz.open()
        page = document.new_page(width=620, height=800)
        page.insert_text((70, 250), "Topic 1", fontsize=12)
        page.insert_text((75, 280), "1. (c)", fontsize=12)
        page.insert_text((290, 205), "Answers", fontsize=12)
        page.insert_text((230, 500), "Hints & Solutions", fontsize=12)
        page.insert_text((70, 540), "Topic 1 Worked Solutions", fontsize=12)
        page.insert_text((75, 570), "1. First worked solution", fontsize=12)
        page.insert_text((75, 620), "2. Second worked solution", fontsize=12)
        try:
            solutions = _solution_regions(document, [0])
            self.assertEqual(len(solutions[("1", "1")]), 1)
            self.assertGreater(solutions[("1", "1")][0]["rect"].y0, 500)
        finally:
            document.close()

    def test_first_solution_can_share_a_topic_heading_block(self):
        document = fitz.open()
        page = document.new_page(width=620, height=800)
        page.insert_text((230, 100), "Hints & Solutions", fontsize=12)
        page.insert_text((70, 140), "Topic 1\nAlgebra\n1. First worked solution", fontsize=12)
        page.insert_text((75, 210), "2. Second worked solution", fontsize=12)
        try:
            solutions = _solution_regions(document, [0])
            self.assertEqual(len(solutions[("1", "1")]), 1)
        finally:
            document.close()

    def test_question_crop_starts_at_printed_number_not_previous_option_tail(self):
        document = fitz.open()
        page = document.new_page(width=650, height=800)
        page.insert_text((63, 100), "Topic 1 Algebra", fontsize=12)
        page.insert_text((342, 160), "(d) Previous option", fontsize=12)
        page.insert_text((336, 185), "13. New question", fontsize=12)
        page.insert_text((350, 210), "(a) 1 (b) 2 (c) 3 (d) 4", fontsize=12)
        page.insert_text((336, 250), "14. Following question", fontsize=12)
        try:
            questions = _question_regions(document, [0], "Algebra")
            target = next(question for question in questions if question["number"] == "13")
            crop_text = page.get_text("text", clip=target["rect"])
            self.assertIn("13. New question", crop_text)
            self.assertNotIn("Previous option", crop_text)
        finally:
            document.close()

    def test_separates_visual_options_from_stem_and_following_section(self):
        document = fitz.open()
        page = document.new_page(width=620, height=800)
        page.insert_text((74, 100), "15. Which equations are tangent?", fontsize=12)
        page.insert_text((74, 125), "(a) y = 0", fontsize=12)
        page.insert_text((200, 125), "(b) y = 4x", fontsize=12)
        page.insert_text((74, 150), "(c) y = x", fontsize=12)
        page.insert_text((200, 150), "(d) y = -x", fontsize=12)
        page.insert_text((74, 200), "Passage Based Problems", fontsize=12)
        try:
            result = _choice_regions(page, fitz.Rect(62, 85, 326, 190))
            self.assertIsNotNone(result)
            stem, options = result
            self.assertNotIn("(a)", page.get_text("text", clip=stem))
            self.assertLessEqual(options["D"].y1, 190)
            self.assertIn("y = -x", page.get_text("text", clip=options["D"]))
        finally:
            document.close()
