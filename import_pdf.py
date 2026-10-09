#!/usr/bin/env python3
"""Import a PDF question paper into TestNow as a teacher-owned draft.

Usage: python3 import_pdf.py /absolute/path/to/paper.pdf
"""
import json, re, shutil, sys, time
from pathlib import Path
from pypdf import PdfReader
import fitz
from server import UPLOADS, db

SOURCE = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else None
TEACHER_EMAIL = sys.argv[2].strip().lower() if len(sys.argv) > 2 else "teacher@testnow.local"
if not SOURCE or not SOURCE.is_file():
    raise SystemExit("Usage: python3 import_pdf.py /absolute/path/to/paper.pdf")

text = "\n".join(page.extract_text() or "" for page in PdfReader(str(SOURCE)).pages)
matches = list(re.finditer(r"(?m)^\s*Q\s*(\d+)\.\s*", text))
if not matches:
    raise SystemExit("No numbered questions could be found in this PDF.")

def clean(value):
    return re.sub(r"\s+", " ", value).strip()

def parse_question(number, chunk):
    # Common school-paper pattern: question stem followed by (a) through (d).
    option_positions = list(re.finditer(r"\(\s*([a-d])\s*\)", chunk, re.I))
    prompt = clean(chunk[:option_positions[0].start()] if option_positions else chunk)
    options = []
    for i, mark in enumerate(option_positions):
        end = option_positions[i + 1].start() if i + 1 < len(option_positions) else len(chunk)
        option = clean(chunk[mark.end():end])
        if option:
            options.append(option)
    return {
        "number": number,
        "prompt": prompt or f"Imported question {number}",
        "options": options[:4],
        "kind": "MCQ" if len(options) >= 2 else "Numerical",
    }

questions = []
for i, match in enumerate(matches):
    end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
    questions.append(parse_question(int(match.group(1)), text[match.end():end]))

stored = f"{int(time.time())}_{SOURCE.name.replace(' ', '_')}"
shutil.copy2(SOURCE, UPLOADS / stored)
con = db()
teacher = con.execute("SELECT id FROM users WHERE email=?", (TEACHER_EMAIL,)).fetchone()
if not teacher:
    raise SystemExit("Seed teacher account is missing. Start server.py once first.")
teacher_id = teacher["id"]
now = int(time.time())
test_id = con.execute(
    "INSERT INTO tests(owner_id,title,subject,duration_minutes,marks,status,paper_filename,created_at) VALUES(?,?,?,?,?,?,?,?)",
    (teacher_id, "Cengage Optics Test · answer key pending", "Physics", 120, 90, "draft", stored, now),
).lastrowid

# Preserve the paper's visual mathematical notation, diagrams, and option layout.
# PDF text extraction often loses superscripts, fraction bars, vectors, and diagrams;
# each question therefore also stores a high-resolution crop of its source region.
visual_doc = fitz.open(SOURCE)
starts = {}
for page_index, page in enumerate(visual_doc):
    for word in page.get_text("words"):
        hit = re.fullmatch(r"Q(\d+)\.", word[4])
        if hit:
            starts[int(hit.group(1))] = (page_index, word[1])
image_dir = UPLOADS / f"paper_{test_id}"
image_dir.mkdir(exist_ok=True)

def visual_path(question_number):
    if question_number not in starts:
        return []
    page_index, top = starts[question_number]
    page = visual_doc[page_index]
    next_starts = [y for n, (p, y) in starts.items() if p == page_index and y > top]
    bottom = min(next_starts) - 10 if next_starts else page.rect.height - 18
    clip = fitz.Rect(24, max(0, top - 18), page.rect.width - 24, max(top + 25, bottom))
    image_name = f"q{question_number:02d}.png"
    page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False).save(image_dir / image_name)
    return [f"paper_{test_id}/{image_name}"]

for ordinal, question in enumerate(questions, 1):
    visuals = visual_path(question["number"])
    rich_prompt = (f"[[visual:{visuals[0]}]]" if visuals else "") + question["prompt"]
    question_id = con.execute(
        "INSERT INTO questions(owner_id,source,chapter,kind,difficulty,prompt,options_json,answer,solution,visual_paths,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (teacher_id, SOURCE.name, "Optics", question["kind"], "Hard", rich_prompt, json.dumps(question["options"]), "__PENDING__", "Answer key pending teacher review.", json.dumps(visuals), now),
    ).lastrowid
    con.execute("INSERT INTO test_questions(test_id,question_id,ordinal) VALUES(?,?,?)", (test_id, question_id, ordinal))
con.commit(); con.close()
print(json.dumps({"test_id": test_id, "question_count": len(questions), "status": "draft", "stored_file": stored}))
