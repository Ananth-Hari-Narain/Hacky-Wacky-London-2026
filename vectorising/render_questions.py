"""
render_questions.py

For each problem in question_bank.questions, renders the question region
from the PDF as a PNG image. Optionally matches solution PDFs to render
solution images. Uses GPT-4o to generate hints and key takeaways.

Usage:
    python render_questions.py [--pdf-dir ./pdfs] [--solutions-dir ./solutions]

Naming convention for solutions:
    Problem PDF:  first_PMT.pdf
    Solution PDF: first_PMT_solutions.pdf

Environment variables (set in .env or shell):
    MONGODB_URI    - MongoDB Atlas connection string
    OPENAI_API_KEY - OpenAI API key
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import certifi
import fitz  # pymupdf
from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient
from pymongo.server_api import ServerApi

load_dotenv()

MONGODB_URI = os.environ["MONGODB_URI"]
MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
CHUNKING_MODEL = "gpt-4o-mini"
HINTS_MODEL = "gpt-4o-mini"

# ---------------------------------------------------------------------------
# Shared helpers (same as embed_questions.py)
# ---------------------------------------------------------------------------

def _strip_page_numbers(page_text: str, page_num: int) -> str:
    lines = page_text.splitlines()
    while lines and lines[-1].strip() in (str(page_num), str(page_num - 1)):
        lines.pop()
    return "\n".join(lines)


def extract_full_text(pdf_path: Path) -> str:
    parts = []
    with fitz.open(pdf_path) as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                text = _strip_page_numbers(text, page_num)
                parts.append(text)
    return "\n\n".join(parts)


CHUNKING_SYSTEM_PROMPT = """\
You are a document segmentation assistant. You will receive the full text of \
a PDF with line numbers like [1], [2], etc. Your job is to identify where \
each distinct question, problem, or exercise begins.

Return ONLY a JSON array of objects, each with:
- "line": the line number where this section starts
- "label": a short label for the section (e.g. "Preamble", "Question 1", etc.)

Rules:
- The first entry should cover any preamble/header text (use label "Preamble").
- Each entry marks the START of a new section.
- Keep sub-parts of the same question together.
- If the document ends with non-question content, create a final entry with label "Appendix".
- Return valid JSON only, no markdown fences, no commentary.\
"""


def identify_breaks(client: OpenAI, text: str, model: str) -> tuple[list[dict], list[str]]:
    raw_lines = text.splitlines()
    numbered = [f"[{i + 1}] {line}" for i, line in enumerate(raw_lines)]
    numbered_text = "\n".join(numbered)

    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CHUNKING_SYSTEM_PROMPT},
            {"role": "user", "content": f"The document has {len(raw_lines)} lines.\n\n{numbered_text}"},
        ],
    )

    parsed = json.loads(response.choices[0].message.content)
    if isinstance(parsed, list):
        breaks = parsed
    else:
        breaks = next(v for v in parsed.values() if isinstance(v, list))

    breaks = sorted(breaks, key=lambda b: b["line"])
    breaks = [b for b in breaks if 1 <= b["line"] <= len(raw_lines)]
    return breaks, raw_lines


# ---------------------------------------------------------------------------
# PDF page/line mapping
# ---------------------------------------------------------------------------

def build_page_line_map(pdf_path: Path) -> list[dict]:
    """
    For each page, record the line range in the concatenated text.
    Returns [{page_num, start_line, end_line, page_obj_index}].
    """
    pages = []
    current_line = 1
    with fitz.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc):
            text = page.get_text("text").strip()
            if not text:
                continue
            text = _strip_page_numbers(text, page_idx + 1)
            n_lines = len(text.splitlines())
            pages.append({
                "page_idx": page_idx,
                "start_line": current_line,
                "end_line": current_line + n_lines - 1,
            })
            # +1 for the blank line between pages in extract_full_text
            current_line += n_lines + 1  # \n\n adds an empty line
    return pages


def render_region(pdf_path: Path, page_idx: int, clip_rect=None, dpi: int = 200) -> bytes:
    """Render a page (or clipped region) as PNG bytes."""
    with fitz.open(pdf_path) as doc:
        page = doc[page_idx]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, clip=clip_rect)
        return pix.tobytes("png")


def render_question_image(pdf_path: Path, page_line_map: list[dict],
                          start_line: int, end_line: int, dpi: int = 200) -> bytes:
    """
    Render the PDF region covering start_line to end_line as a PNG.
    If the question spans multiple pages, renders the first page it appears on.
    """
    # Find which page this question starts on
    for pm in page_line_map:
        if pm["start_line"] <= start_line <= pm["end_line"]:
            return render_region(pdf_path, pm["page_idx"], dpi=dpi)
    # Fallback: render first page
    return render_region(pdf_path, 0, dpi=dpi)


# ---------------------------------------------------------------------------
# Hints & key takeaways generation
# ---------------------------------------------------------------------------

HINTS_SYSTEM_PROMPT = """\
You are an academic tutor. You will receive the text of a problem and its \
exemplar solution. Generate:

1. "hints": an array of exactly 3 progressive hints, from vague to specific:
   - Hint 1: A gentle nudge about the approach (no specifics)
   - Hint 2: A more concrete suggestion about the method/technique
   - Hint 3: A near-giveaway that points directly at the key step
2. "key_takeaways": an array of 2-3 short sentences summarising what the \
student should learn from this problem.

Return ONLY a JSON object with keys "hints" and "key_takeaways".\
"""

HINTS_NO_SOLUTION_PROMPT = """\
You are an academic tutor. You will receive the text of a problem (no solution \
is available). Generate:

1. "hints": an array of exactly 3 progressive hints, from vague to specific.
2. "key_takeaways": an array of 2-3 short sentences summarising what the \
student should learn from this problem.

Return ONLY a JSON object with keys "hints" and "key_takeaways".\
"""


def generate_hints(client: OpenAI, question_text: str,
                   solution_text: str | None) -> dict:
    if solution_text:
        prompt = HINTS_SYSTEM_PROMPT
        user_msg = f"Problem:\n{question_text}\n\nSolution:\n{solution_text}"
    else:
        prompt = HINTS_NO_SOLUTION_PROMPT
        user_msg = f"Problem:\n{question_text}"

    response = client.chat.completions.create(
        model=HINTS_MODEL,
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
    )
    return json.loads(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Solution PDF matching
# ---------------------------------------------------------------------------

def find_solution_pdf(problem_pdf: Path, solutions_dir: Path) -> Path | None:
    """
    Try to match a solution PDF for a problem PDF.
    Conventions tried:
        first_PMT.pdf → first_PMT_solutions.pdf
        first_PMT.pdf → first_PMT_sol.pdf
        first_PMT.pdf → first_PMT_answers.pdf
    """
    stem = problem_pdf.stem
    for suffix in ("_solutions", "_sol", "_answers", "_ms", "_markscheme"):
        candidate = solutions_dir / f"{stem}{suffix}.pdf"
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------

def get_db(uri: str, db_name: str):
    client = MongoClient(uri, server_api=ServerApi("1"), tlsCAFile=certifi.where())
    return client[db_name]


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process(pdf_dir: Path, solutions_dir: Path):
    oai = OpenAI(api_key=OPENAI_API_KEY)
    db = get_db(MONGODB_URI, MONGODB_DB)
    questions_coll = db["questions"]

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in {pdf_dir}")
        sys.exit(0)

    print(f"Found {len(pdf_files)} PDF(s) in {pdf_dir}\n")

    for pdf_path in pdf_files:
        print(f"=== {pdf_path.name} ===")

        # 1. Chunk the problem PDF
        full_text = extract_full_text(pdf_path)
        breaks, raw_lines = identify_breaks(oai, full_text, CHUNKING_MODEL)
        page_line_map = build_page_line_map(pdf_path)

        # 2. Find matching solution PDF
        sol_pdf = find_solution_pdf(pdf_path, solutions_dir)
        sol_breaks = None
        sol_raw_lines = None
        sol_page_map = None
        if sol_pdf:
            print(f"  Solution PDF: {sol_pdf.name}")
            sol_text = extract_full_text(sol_pdf)
            sol_breaks, sol_raw_lines = identify_breaks(oai, sol_text, CHUNKING_MODEL)
            sol_page_map = build_page_line_map(sol_pdf)
        else:
            print("  No solution PDF found")

        # 3. Process each question
        for i, brk in enumerate(breaks):
            label = brk["label"]
            if label.lower() in ("preamble", "appendix"):
                continue

            start_line = brk["line"]
            end_line = breaks[i + 1]["line"] - 1 if i + 1 < len(breaks) else len(raw_lines)
            question_text = "\n".join(raw_lines[start_line - 1 : end_line]).strip()

            print(f"  {label} ... ", end="", flush=True)

            # Render question image
            q_image = render_question_image(pdf_path, page_line_map, start_line, end_line)

            # Render solution image + get solution text
            sol_image = None
            sol_text_chunk = None
            if sol_breaks and sol_raw_lines:
                # Try to match by label (e.g. "Question 1" in both)
                matched_sol = None
                for j, sb in enumerate(sol_breaks):
                    if sb["label"].lower() == label.lower():
                        matched_sol = j
                        break
                if matched_sol is not None:
                    s_start = sol_breaks[matched_sol]["line"]
                    s_end = (sol_breaks[matched_sol + 1]["line"] - 1
                             if matched_sol + 1 < len(sol_breaks)
                             else len(sol_raw_lines))
                    sol_text_chunk = "\n".join(sol_raw_lines[s_start - 1 : s_end]).strip()
                    sol_image = render_question_image(sol_pdf, sol_page_map, s_start, s_end)

            # Generate hints — skip if text is too short (likely diagram-heavy)
            # Short text means the question relies on a visual we can't send
            MIN_TEXT_FOR_HINTS = 150  # characters
            hints = []
            takeaways = []
            if len(question_text) >= MIN_TEXT_FOR_HINTS:
                hints_data = generate_hints(oai, question_text, sol_text_chunk)
                hints = hints_data.get("hints", [])
                takeaways = hints_data.get("key_takeaways", [])
            else:
                print(f"[skipped hints: text too short ({len(question_text)} chars, likely diagram)] ", end="")

            # Update MongoDB document
            update_fields = {
                "question_image": q_image,
                "hints": hints,
                "key_takeaways": takeaways,
                "updated_at": datetime.now(timezone.utc),
            }
            if sol_image:
                update_fields["solution_image"] = sol_image
            if sol_text_chunk:
                update_fields["solution_text"] = sol_text_chunk

            result = questions_coll.update_one(
                {"source": pdf_path.name, "question": label},
                {"$set": update_fields},
            )

            status = "updated" if result.modified_count else "matched" if result.matched_count else "not found"
            print(f"{status} ({len(hints)} hints, {len(q_image)} bytes)")

    print("\nDone.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render question/solution images and generate hints")
    parser.add_argument("--pdf-dir", default="./pdfs", help="Problem PDFs directory")
    parser.add_argument("--solutions-dir", default="./solutions", help="Solution PDFs directory")
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    solutions_dir = Path(args.solutions_dir).expanduser().resolve()

    if not pdf_dir.is_dir():
        print(f"Error: {pdf_dir} not found")
        sys.exit(1)
    if not solutions_dir.is_dir():
        print(f"Warning: {solutions_dir} not found, continuing without solutions")
        solutions_dir = None

    process(pdf_dir, solutions_dir or Path("/nonexistent"))
