import tempfile
import unittest
import zipfile
from pathlib import Path

import fitz

from qbfac.intake import inspect_source_archive


class SourceArchiveIntakeTests(unittest.TestCase):
    def test_classifies_question_and_answer_pdfs_without_parsing_with_an_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = fitz.open()
            page = question.new_page()
            page.insert_text((72, 72), "Physics: Kinematics\n1. A body has speed 2 m/s.\n(A) 1 m/s\n(B) 2 m/s")
            question.save(root / "physics_questions.pdf")
            answers = fitz.open()
            page = answers.new_page()
            page.insert_text((72, 72), "Answer Key\n1. B")
            answers.save(root / "physics_answer_key.pdf")
            archive = root / "physics.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.write(root / "physics_questions.pdf", "physics_questions.pdf")
                bundle.write(root / "physics_answer_key.pdf", "physics_answer_key.pdf")

            manifest = inspect_source_archive(archive)

            self.assertEqual(len(manifest["documents"]), 2)
            roles = {document["role"] for document in manifest["documents"]}
            self.assertEqual(roles, {"questions", "answer_key"})
            question_document = next(document for document in manifest["documents"] if document["role"] == "questions")
            self.assertEqual(question_document["kind"], "digital_pdf")
            self.assertEqual(question_document["page_count"], 1)
            self.assertGreater(question_document["native_text_characters"], 10)

