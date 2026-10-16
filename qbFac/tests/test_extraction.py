import unittest

from qbfac.extraction import classify_question_kind, parse_page_text


class NativePdfExtractionTests(unittest.TestCase):
    def test_tracks_topic_and_question_type_from_editorial_headings(self):
        records, state = parse_page_text(
            """Topic 1 Complex Number in Iota Form
Objective Questions I (Only one correct option)
1 Let z be a complex number. (a) 1 (b) 2 (c) 3 (d) 4
2 Let w be a complex number. (a) 1 (b) 2 (c) 3 (d) 4
""",
            page_number=10,
            chapter="Complex Numbers",
            state={},
        )
        self.assertEqual([record["printed_number"] for record in records], ["1", "2"])
        self.assertTrue(all(record["chapter"] == "Complex Numbers" for record in records))
        self.assertTrue(all(record["topic"] == "Complex Number in Iota Form" for record in records))
        self.assertTrue(all(record["question_type"] == "single_choice" for record in records))
        self.assertEqual(state["question_type"], "single_choice")

    def test_understands_jee_editorial_question_type_headings(self):
        self.assertEqual(classify_question_kind("Objective Questions II (One or more than one correct option)"), "multiple_select")
        self.assertEqual(classify_question_kind("True/False"), "true_false")
        self.assertEqual(classify_question_kind("Passage Based Problems"), "passage")
        self.assertEqual(classify_question_kind("Assertion Reason"), "assertion_reason")
        self.assertEqual(classify_question_kind("Integer Type Questions"), "integer")

