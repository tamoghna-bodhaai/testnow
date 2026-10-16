import unittest

import fitz

from qbfac.arihant import _answers, _choice_regions


class ArihantRecipeTests(unittest.TestCase):
    def test_links_topic_scoped_choice_keys(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((70, 100), "Answers", fontsize=12)
        page.insert_text((70, 120), "Topic 1", fontsize=12)
        page.insert_text((70, 140), "1. (c)", fontsize=12)
        page.insert_text((70, 160), "Topic 2", fontsize=12)
        page.insert_text((70, 180), "1. (a, d)", fontsize=12)
        page.insert_text((70, 220), "Topic 1 Worked Solutions", fontsize=12)
        try:
            answer = _answers(document, [0])
            self.assertEqual(answer[("1", "1")]["selected"], ["C"])
            self.assertEqual(answer[("2", "1")]["selected"], ["A", "D"])
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
