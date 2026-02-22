"""
render_questions.py

For each problem in question_bank.questions, renders the question region
from the PDF as a cropped PNG image (not the full page). Handles multi-page
questions by stitching cropped regions. Optionally matches solution PDFs to
render solution images. Uses GPT-4o-mini to generate hints and key takeaways.
Writes solutions to a dedicated question_bank.solutions collection.

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
import re
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import certifi
import fitz  # pymupdf
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image
from pymongo import MongoClient, UpdateOne
from pymongo.server_api import ServerApi

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "questions")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
CHUNKING_MODEL = "gpt-4o-mini"
HINTS_MODEL = "gpt-4o-mini"

# Margin (in PDF points) around cropped question regions
CROP_MARGIN_TOP = 15.0
CROP_MARGIN_BOTTOM = 20.0

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _extract_question_number(label: str) -> str | None:
    """Extract the numeric/alphanumeric question identifier from a label.
    'Question 1' -> '1', 'Solution 2a' -> '2a', 'Q3' -> '3', '1.' -> '1'
    """
    cleaned = label.strip().lower()
    for pat in [
        r'(?:question|problem|solution|answer|q|mark\s*scheme)\s*(\d+[a-z]*)',
        r'^(\d+[a-z]*)\b',
    ]:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _extract_lines_with_bboxes(page) -> list[dict]:
    """
    Extract text lines AND their bounding boxes from a single get_text("dict") call.
    Guarantees 1:1 correspondence between text and bboxes.
    """
    lines = []
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line.get("spans", []))
            lines.append({
                "text": text,
                "bbox": tuple(line["bbox"]),
            })
    return lines


def _extract_all_block_bboxes(page) -> list[tuple]:
    """
    Extract bounding boxes for ALL blocks (text + image) on a page.
    Used to find the full extent of content including vector diagrams.
    """
    bboxes = []
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in data.get("blocks", []):
        bboxes.append(tuple(block["bbox"]))
    return bboxes


def _strip_page_number_lines(lines: list[dict], page_num: int) -> list[dict]:
    """Remove trailing lines that are just the page number."""
    result = list(lines)
    while result and result[-1]["text"].strip() in (str(page_num), str(page_num - 1)):
        result.pop()
    return result


def extract_full_text_aligned(pdf_path: Path) -> str:
    """Extract text using get_text('dict') so line counts match build_page_line_map exactly."""
    parts = []
    with fitz.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc):
            lines = _extract_lines_with_bboxes(page)
            if not lines:
                continue
            lines = _strip_page_number_lines(lines, page_idx + 1)
            if lines:
                page_text = "\n".join(entry["text"] for entry in lines)
                parts.append(page_text)
    return "\n\n".join(parts)


CHUNKING_SYSTEM_PROMPT = """\
You are a document segmentation assistant. You will receive the full text of \
a PDF with line numbers like [1], [2], etc. Your job is to identify where \
each distinct question, problem, or exercise begins.

Return ONLY a JSON array of objects, each with:
- "line": the line number where this section starts
- "label": use the format "Question N" where N is the ACTUAL number used in \
  the document (e.g. if the text says "5. A graph is simple..." the label must \
  be "Question 5", NOT a sequential counter).

Rules:
- The first entry should cover any preamble/header text (use label "Preamble").
- Each entry marks the START of a new section.
- Keep sub-parts of the same question together (e.g. parts a, b, c of Q1 are one chunk).
- IMPORTANT: The label number MUST match the question number in the document text. \
  Do not renumber sequentially.
- If the document ends with non-question content, create a final entry with label "Appendix".
- Return valid JSON only, no markdown fences, no commentary.\
"""


def identify_breaks(client: OpenAI, text: str, model: str,
                    system_prompt: str | None = None) -> tuple[list[dict], list[str]]:
    raw_lines = text.splitlines()
    numbered = [f"[{i + 1}] {line}" for i, line in enumerate(raw_lines)]
    numbered_text = "\n".join(numbered)

    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt or CHUNKING_SYSTEM_PROMPT},
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
# PDF page/line mapping with bounding boxes (unified extraction)
# ---------------------------------------------------------------------------

def build_page_line_map(pdf_path: Path) -> list[dict]:
    """
    For each page, record the line range, per-line bounding boxes, and
    all block bounding boxes (for capturing diagrams between text lines).
    """
    pages = []
    current_line = 1
    with fitz.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc):
            lines = _extract_lines_with_bboxes(page)
            if not lines:
                continue

            lines = _strip_page_number_lines(lines, page_idx + 1)
            if not lines:
                continue

            n_lines = len(lines)
            line_bboxes = [
                (current_line + j, entry["bbox"])
                for j, entry in enumerate(lines)
            ]

            pages.append({
                "page_idx": page_idx,
                "start_line": current_line,
                "end_line": current_line + n_lines - 1,
                "page_width": page.rect.width,
                "page_height": page.rect.height,
                "line_bboxes": line_bboxes,
                "block_bboxes": _extract_all_block_bboxes(page),
            })
            # +1 for the blank line between pages (from \n\n join)
            current_line += n_lines + 1
    return pages


# ---------------------------------------------------------------------------
# Cropped image rendering
# ---------------------------------------------------------------------------

def get_question_clip_rects(page_line_map: list[dict],
                            start_line: int, end_line: int) -> list[dict]:
    """
    For a question spanning start_line to end_line, return a clip rect
    per page it appears on. Each entry: {"page_idx": int, "clip_rect": fitz.Rect}.

    The top boundary is the first matched text line's y-top.
    The bottom boundary is the NEXT line after end_line's y-top (i.e. the start
    of the next question), or the page bottom. This ensures diagrams and vector
    graphics between text lines are fully captured.
    """
    clips = []
    for pm in page_line_map:
        # Does this page overlap with the question's line range?
        if pm["end_line"] < start_line or pm["start_line"] > end_line:
            continue

        first_q_line = max(start_line, pm["start_line"])
        last_q_line = min(end_line, pm["end_line"])

        # Find y-top from the first question line on this page
        first_bbox = None
        for line_num, bbox in pm["line_bboxes"]:
            if line_num == first_q_line:
                first_bbox = bbox
                break

        if first_bbox is None:
            clips.append({"page_idx": pm["page_idx"], "clip_rect": None})
            continue

        y_top = first_bbox[1]

        # Find y-bottom: use the start of the NEXT line after this question
        # on the same page (= start of next question). This captures all
        # content including diagrams between the text labels.
        next_line_y = None
        if last_q_line < pm["end_line"]:
            # There are more lines on this page after our question
            for line_num, bbox in pm["line_bboxes"]:
                if line_num == last_q_line + 1:
                    next_line_y = bbox[1]
                    break

        if next_line_y is not None:
            # Clip just above the next question's first line
            y_bottom = next_line_y
        else:
            # Last question on this page — use the bottom of the last
            # matched line, extended to include any overlapping blocks
            y_bottom = max(b[3] for ln, b in pm["line_bboxes"]
                          if first_q_line <= ln <= last_q_line)
            # Extend to capture blocks that overlap our y-range
            for block_bbox in pm.get("block_bboxes", []):
                by_top, by_bottom = block_bbox[1], block_bbox[3]
                if by_bottom > y_top and by_top < y_bottom:
                    y_bottom = max(y_bottom, by_bottom)

        # Apply margins
        y_top = max(0, y_top - CROP_MARGIN_TOP)
        y_bottom = min(pm["page_height"], y_bottom + CROP_MARGIN_BOTTOM)

        clip = fitz.Rect(0, y_top, pm["page_width"], y_bottom)
        clips.append({"page_idx": pm["page_idx"], "clip_rect": clip})

    return clips


def stitch_pixmaps(pixmaps: list) -> bytes:
    """Vertically stitch multiple PyMuPDF Pixmaps into a single PNG using Pillow."""
    if len(pixmaps) == 1:
        return pixmaps[0].tobytes("png")

    images = [Image.open(BytesIO(pix.tobytes("png"))) for pix in pixmaps]
    total_w = max(img.width for img in images)
    total_h = sum(img.height for img in images)

    result = Image.new("RGB", (total_w, total_h), (255, 255, 255))
    y_offset = 0
    for img in images:
        result.paste(img, (0, y_offset))
        y_offset += img.height

    buf = BytesIO()
    result.save(buf, format="PNG")
    return buf.getvalue()


def render_question_image(pdf_path: Path, page_line_map: list[dict],
                          start_line: int, end_line: int, dpi: int = 200) -> bytes:
    """
    Render the PDF region covering start_line to end_line as a cropped PNG.
    Handles multi-page questions by stitching cropped regions.
    """
    clips = get_question_clip_rects(page_line_map, start_line, end_line)

    if not clips:
        # Fallback: render first page fully
        with fitz.open(pdf_path) as doc:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            return doc[0].get_pixmap(matrix=mat).tobytes("png")

    pixmaps = []
    with fitz.open(pdf_path) as doc:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for cr in clips:
            clip_rect = cr["clip_rect"]
            # Validate clip rect has positive dimensions
            if clip_rect is not None and (clip_rect.width <= 0 or clip_rect.height <= 0):
                # Skip invalid clips — fall back to full page for this segment
                clip_rect = None
            pix = doc[cr["page_idx"]].get_pixmap(matrix=mat, clip=clip_rect)
            if pix.width > 0 and pix.height > 0:
                pixmaps.append(pix)

    if not pixmaps:
        # All clips were invalid — render first page of the document
        with fitz.open(pdf_path) as doc:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            return doc[0].get_pixmap(matrix=mat).tobytes("png")

    # Single page: return directly. Multi-page: stitch.
    if len(pixmaps) == 1:
        return pixmaps[0].tobytes("png")
    return stitch_pixmaps(pixmaps)


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

SOLUTION_CHUNKING_SYSTEM_PROMPT = """\
You are a document segmentation assistant. You will receive the full text of \
a PDF containing solutions, answers, or mark schemes. Your job is to identify \
where EACH individual numbered answer/solution begins.

Return ONLY a JSON array of objects, each with:
- "line": the line number where this solution starts
- "label": use ONLY the format "Question N" (e.g. "Question 1", "Question 2") \
  matching the answer number. Extract the answer number from whatever format \
  the document uses (e.g. "1.", "Answer 1", "Solution 1", "Q1").

Rules:
- The first entry should cover any preamble/header text (use label "Preamble").
- Each entry marks the START of a new answer section.
- IMPORTANT: Each numbered answer MUST be its own section. If the document has \
  answers numbered 1 through 10, you must produce 10 entries (plus preamble). \
  Do NOT group multiple answers into one section.
- Keep sub-parts (a, b, c) of the same numbered answer together.
- Return valid JSON only, no markdown fences, no commentary.\
"""

def find_solution_pdf(problem_pdf: Path, solutions_dir: Path) -> Path | None:
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

def _match_question_doc(questions_coll, source: str, label: str):
    """Find the matching question document, with fallback to number-based matching."""
    # Try exact match first
    doc = questions_coll.find_one({"source": source, "question": label})
    if doc:
        return doc

    # Fallback: match by extracted question number
    q_num = _extract_question_number(label)
    if not q_num:
        return None
    for cand in questions_coll.find({"source": source}):
        cand_num = _extract_question_number(cand.get("question", ""))
        if cand_num == q_num:
            return cand
    return None


def process(pdf_dir: Path, solutions_dir: Path):
    oai = OpenAI(api_key=OPENAI_API_KEY)
    db = get_db(MONGODB_URI, MONGODB_DB)
    questions_coll = db[MONGODB_COLLECTION]

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in {pdf_dir}")
        sys.exit(0)

    print(f"Found {len(pdf_files)} PDF(s) in {pdf_dir}\n")

    for pdf_path in pdf_files:
        print(f"=== {pdf_path.name} ===")

        # 1. Chunk the problem PDF (using aligned extraction)
        full_text = extract_full_text_aligned(pdf_path)
        breaks, raw_lines = identify_breaks(oai, full_text, CHUNKING_MODEL)
        page_line_map = build_page_line_map(pdf_path)

        # 2. Find matching solution PDF
        sol_pdf = find_solution_pdf(pdf_path, solutions_dir)
        sol_breaks = None
        sol_raw_lines = None
        sol_page_map = None
        if sol_pdf:
            print(f"  Solution PDF: {sol_pdf.name}")
            sol_text = extract_full_text_aligned(sol_pdf)
            sol_breaks, sol_raw_lines = identify_breaks(
                oai, sol_text, CHUNKING_MODEL,
                system_prompt=SOLUTION_CHUNKING_SYSTEM_PROMPT,
            )
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

            # Render cropped question image
            q_image = render_question_image(pdf_path, page_line_map, start_line, end_line)

            # Render solution image + get solution text (number-based matching)
            sol_image = None
            sol_text_chunk = None
            if sol_breaks and sol_raw_lines:
                q_num = _extract_question_number(label)
                matched_sol = None
                if q_num:
                    for j, sb in enumerate(sol_breaks):
                        s_num = _extract_question_number(sb["label"])
                        if s_num and s_num == q_num:
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
            MIN_TEXT_FOR_HINTS = 150
            hints = []
            takeaways = []
            if len(question_text) >= MIN_TEXT_FOR_HINTS:
                hints_data = generate_hints(oai, question_text, sol_text_chunk)
                hints = hints_data.get("hints", [])
                takeaways = hints_data.get("key_takeaways", [])
            else:
                print(f"[skipped hints: text too short ({len(question_text)} chars)] ", end="")

            # Update question document (with fallback matching)
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

            q_doc = _match_question_doc(questions_coll, pdf_path.name, label)
            if q_doc:
                questions_coll.update_one(
                    {"_id": q_doc["_id"]},
                    {"$set": update_fields},
                )
                q_status = "updated"
            else:
                q_status = "not found"

            sol_status = " +solution" if (sol_text_chunk or sol_image) else ""
            print(f"{q_status}{sol_status} ({len(hints)} hints, {len(q_image)} bytes)")

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
