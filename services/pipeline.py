"""
Unified question processing pipeline.

Extracts text from problem PDFs, chunks via GPT, embeds, renders cropped
images, matches solutions, generates hints — all in a single pass.
"""
import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import fitz  # pymupdf
from PIL import Image
from pymongo import UpdateOne

from config.settings import (
    CHUNKING_MODEL,
    EMBEDDING_MODEL,
    HINTS_MODEL,
    EMBED_BATCH_SIZE,
    CROP_MARGIN_TOP,
    CROP_MARGIN_BOTTOM,
    MIN_TEXT_FOR_HINTS,
)
from config.prompts import (
    CHUNKING_SYSTEM_PROMPT,
    SOLUTION_CHUNKING_SYSTEM_PROMPT,
    HINTS_SYSTEM_PROMPT,
    HINTS_NO_SOLUTION_PROMPT,
)
from models.db import questions_coll
from services.openai_client import client as oai


# =========================================================================
# Label helpers
# =========================================================================

def _extract_question_number(label: str) -> str | None:
    cleaned = label.strip().lower()
    for pat in [
        r'(?:question|problem|solution|answer|q|mark\s*scheme)\s*(\d+[a-z]*)',
        r'^(\d+[a-z]*)\b',
    ]:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


# =========================================================================
# PDF text extraction (aligned — using get_text("dict") only)
# =========================================================================

def _extract_lines_with_bboxes(page) -> list[dict]:
    lines = []
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line.get("spans", []))
            lines.append({"text": text, "bbox": tuple(line["bbox"])})
    return lines


def _extract_all_block_bboxes(page) -> list[tuple]:
    bboxes = []
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in data.get("blocks", []):
        bboxes.append(tuple(block["bbox"]))
    return bboxes


def _strip_page_number_lines(lines: list[dict], page_num: int) -> list[dict]:
    result = list(lines)
    while result and result[-1]["text"].strip() in (str(page_num), str(page_num - 1)):
        result.pop()
    return result


def extract_full_text_aligned(pdf_path: Path) -> str:
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


# =========================================================================
# Question boundary detection
# =========================================================================

# Matches lines like "1. Give the...", "10. [From 2021 exam]", "3 (Gersting)..."
# Requires the number to be followed by a punctuation char and actual text,
# so bare vertex-label lines like "2" or "10" are ignored.
_QUESTION_START_RE = re.compile(
    r'^(\d+)\s*[\.\)\]]\s+\S'       # "N. text" or "N) text" or "N] text"
    r'|^(\d+)\s+\([A-Z]'            # "N (Author...)" e.g. "3 (Gersting)"
    r'|^(\d+)\s+\[[A-Z]'            # "N [From ..." (but not "[3]" alone)
)

# Solution documents: same strict patterns, plus "Question N" / "Solution N" headers
# Also matches bare "N." on its own line (common in solutions), validated later by
# _filter_sequential_breaks to remove false positives.
_SOLUTION_START_RE = re.compile(
    r'^(?:Question|Solution|Answer|Q)\s*(\d+)'   # "Question 1", "Solution 2"
    r'|^(\d+)\s*[\.\)\]]\s+\S'                    # "1. text..." (must have text after)
    r'|^(\d+)\s+\([A-Za-z]'                       # "4. (a),(b) Node..."
    r'|^(\d+)\.\s*$'                               # bare "5." on its own line
, re.IGNORECASE)


def _detect_breaks_regex(raw_lines: list[str],
                         pattern: re.Pattern = _QUESTION_START_RE) -> list[dict]:
    """Detect question boundaries using regex, then keep only the longest
    strictly-increasing subsequence of question numbers (first occurrence
    of each number). This filters out false positives from diagram labels."""
    candidates = []
    for i, line in enumerate(raw_lines):
        stripped = line.strip()
        if not stripped:
            continue
        m = pattern.match(stripped)
        if m:
            num = next(g for g in m.groups() if g is not None)
            candidates.append({
                "line": i + 1,
                "label": f"Question {num}",
                "_num": int(num),
            })

    if not candidates:
        return []

    # Keep only the first occurrence of each question number — that's the
    # real start, later duplicates are diagram noise or sub-part references.
    seen = set()
    breaks = []
    for c in candidates:
        if c["_num"] not in seen:
            seen.add(c["_num"])
            breaks.append(c)

    # Remove internal key
    for b in breaks:
        b.pop("_num", None)

    return breaks


def _detect_breaks_gpt(raw_lines: list[str], model: str | None = None,
                        system_prompt: str | None = None) -> list[dict]:
    """Fallback: use GPT to detect question boundaries."""
    numbered = [f"[{i + 1}] {line}" for i, line in enumerate(raw_lines)]
    numbered_text = "\n".join(numbered)

    response = oai.chat.completions.create(
        model=model or CHUNKING_MODEL,
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
    return breaks


def identify_breaks(text: str, model: str | None = None,
                    system_prompt: str | None = None) -> tuple[list[dict], list[str]]:
    raw_lines = text.splitlines()

    # Determine if this is a solutions document
    is_solution = (system_prompt == SOLUTION_CHUNKING_SYSTEM_PROMPT)
    pattern = _SOLUTION_START_RE if is_solution else _QUESTION_START_RE

    # Try regex first — deterministic, no AI hallucination
    breaks = _detect_breaks_regex(raw_lines, pattern)

    if not breaks:
        # Fallback to GPT only if regex found nothing
        breaks = _detect_breaks_gpt(raw_lines, model, system_prompt)
    else:
        # Add preamble entry if first question doesn't start at line 1
        if breaks[0]["line"] > 1:
            breaks.insert(0, {"line": 1, "label": "Preamble"})

    return breaks, raw_lines


def split_at_breaks(breaks: list[dict], raw_lines: list[str]) -> list[dict]:
    chunks = []
    for i, brk in enumerate(breaks):
        start = brk["line"] - 1
        end = breaks[i + 1]["line"] - 1 if i + 1 < len(breaks) else len(raw_lines)
        chunk_text = "\n".join(raw_lines[start:end]).strip()
        if chunk_text:
            chunks.append({"label": brk["label"], "text": chunk_text})
    return chunks


# =========================================================================
# Page/line mapping with bounding boxes
# =========================================================================

def build_page_line_map(pdf_path: Path) -> list[dict]:
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
            current_line += n_lines + 1
    return pages


# =========================================================================
# Cropped image rendering
# =========================================================================

def get_question_clip_rects(page_line_map: list[dict],
                            start_line: int, end_line: int) -> list[dict]:
    clips = []
    for pm in page_line_map:
        if pm["end_line"] < start_line or pm["start_line"] > end_line:
            continue

        first_q_line = max(start_line, pm["start_line"])
        last_q_line = min(end_line, pm["end_line"])

        first_bbox = None
        for line_num, bbox in pm["line_bboxes"]:
            if line_num == first_q_line:
                first_bbox = bbox
                break

        if first_bbox is None:
            clips.append({"page_idx": pm["page_idx"], "clip_rect": None})
            continue

        y_top = first_bbox[1]

        next_line_y = None
        if last_q_line < pm["end_line"]:
            for line_num, bbox in pm["line_bboxes"]:
                if line_num == last_q_line + 1:
                    next_line_y = bbox[1]
                    break

        if next_line_y is not None:
            y_bottom = next_line_y
        else:
            y_bottom = max(b[3] for ln, b in pm["line_bboxes"]
                          if first_q_line <= ln <= last_q_line)
            for block_bbox in pm.get("block_bboxes", []):
                by_top, by_bottom = block_bbox[1], block_bbox[3]
                if by_bottom > y_top and by_top < y_bottom:
                    y_bottom = max(y_bottom, by_bottom)

        y_top = max(0, y_top - CROP_MARGIN_TOP)
        y_bottom = min(pm["page_height"], y_bottom + CROP_MARGIN_BOTTOM)

        clip = fitz.Rect(0, y_top, pm["page_width"], y_bottom)
        clips.append({"page_idx": pm["page_idx"], "clip_rect": clip})

    return clips


def stitch_pixmaps(pixmaps: list) -> bytes:
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
    clips = get_question_clip_rects(page_line_map, start_line, end_line)

    if not clips:
        with fitz.open(pdf_path) as doc:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            return doc[0].get_pixmap(matrix=mat).tobytes("png")

    pixmaps = []
    with fitz.open(pdf_path) as doc:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for cr in clips:
            clip_rect = cr["clip_rect"]
            if clip_rect is not None and (clip_rect.width <= 0 or clip_rect.height <= 0):
                clip_rect = None
            pix = doc[cr["page_idx"]].get_pixmap(matrix=mat, clip=clip_rect)
            if pix.width > 0 and pix.height > 0:
                pixmaps.append(pix)

    if not pixmaps:
        with fitz.open(pdf_path) as doc:
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            return doc[0].get_pixmap(matrix=mat).tobytes("png")

    if len(pixmaps) == 1:
        return pixmaps[0].tobytes("png")
    return stitch_pixmaps(pixmaps)


# =========================================================================
# Hints generation
# =========================================================================

def generate_hints(question_text: str, solution_text: str | None) -> dict:
    if solution_text:
        prompt = HINTS_SYSTEM_PROMPT
        user_msg = f"Problem:\n{question_text}\n\nSolution:\n{solution_text}"
    else:
        prompt = HINTS_NO_SOLUTION_PROMPT
        user_msg = f"Problem:\n{question_text}"

    response = oai.chat.completions.create(
        model=HINTS_MODEL,
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
    )
    return json.loads(response.choices[0].message.content)


# =========================================================================
# Embedding
# =========================================================================

def embed_batch(texts: list[str]) -> list[list[float]]:
    response = oai.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


# =========================================================================
# Solution PDF matching
# =========================================================================

def find_solution_pdf(problem_pdf: Path, solutions_dir: Path) -> Path | None:
    stem = problem_pdf.stem
    for suffix in ("_solutions", "_sol", "_answers", "_ms", "_markscheme"):
        candidate = solutions_dir / f"{stem}{suffix}.pdf"
        if candidate.exists():
            return candidate
    return None


def _match_question_doc(coll, source: str, label: str):
    doc = coll.find_one({"source": source, "question": label})
    if doc:
        return doc
    q_num = _extract_question_number(label)
    if not q_num:
        return None
    for cand in coll.find({"source": source}):
        cand_num = _extract_question_number(cand.get("question", ""))
        if cand_num == q_num:
            return cand
    return None


# =========================================================================
# Unified pipeline
# =========================================================================

def process_problem_pdf(pdf_path: Path,
                        solutions_dir: Path | None = None) -> list[dict]:
    """
    Process a single problem PDF end-to-end:
    1. Extract aligned text
    2. GPT chunking
    3. Build page/line map
    4. For each question: embed + render image + match solution + generate hints
    5. Upsert to MongoDB

    Returns the list of upserted document dicts.
    """
    collection = questions_coll()

    # 1. Extract text (aligned)
    full_text = extract_full_text_aligned(pdf_path)
    breaks, raw_lines = identify_breaks(full_text)
    page_line_map = build_page_line_map(pdf_path)

    # 2. Find matching solution PDF
    sol_pdf = None
    sol_breaks = None
    sol_raw_lines = None
    sol_page_map = None
    if solutions_dir and solutions_dir.is_dir():
        sol_pdf = find_solution_pdf(pdf_path, solutions_dir)
        if sol_pdf:
            sol_text = extract_full_text_aligned(sol_pdf)
            sol_breaks, sol_raw_lines = identify_breaks(
                sol_text,
                system_prompt=SOLUTION_CHUNKING_SYSTEM_PROMPT,
            )
            sol_page_map = build_page_line_map(sol_pdf)

    # 3. Process each question chunk
    question_texts = []
    question_docs = []

    for i, brk in enumerate(breaks):
        label = brk["label"]
        if label.lower() in ("preamble", "appendix"):
            continue

        start_line = brk["line"]
        end_line = (breaks[i + 1]["line"] - 1
                    if i + 1 < len(breaks) else len(raw_lines))
        question_text = "\n".join(raw_lines[start_line - 1:end_line]).strip()

        if not question_text:
            continue

        # Render question image
        q_image = render_question_image(pdf_path, page_line_map, start_line, end_line)

        # Match solution
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
                sol_text_chunk = "\n".join(sol_raw_lines[s_start - 1:s_end]).strip()
                sol_image = render_question_image(
                    sol_pdf, sol_page_map, s_start, s_end
                )

        # Generate hints
        hints = []
        takeaways = []
        if len(question_text) >= MIN_TEXT_FOR_HINTS:
            hints_data = generate_hints(question_text, sol_text_chunk)
            hints = hints_data.get("hints", [])
            takeaways = hints_data.get("key_takeaways", [])

        question_texts.append(question_text)
        question_docs.append({
            "source": pdf_path.name,
            "chunk_index": i,
            "question": label,
            "text": question_text,
            "question_image": q_image,
            "solution_text": sol_text_chunk,
            "solution_image": sol_image,
            "hints": hints,
            "key_takeaways": takeaways,
            "embedding": None,
            "updated_at": datetime.now(timezone.utc),
        })

    # 4. Batch embed all question texts
    if question_texts:
        all_embeddings = []
        for batch_start in range(0, len(question_texts), EMBED_BATCH_SIZE):
            batch = question_texts[batch_start:batch_start + EMBED_BATCH_SIZE]
            all_embeddings.extend(embed_batch(batch))
        for doc, emb in zip(question_docs, all_embeddings):
            doc["embedding"] = emb

    # 5. Upsert to MongoDB
    if question_docs:
        ops = [
            UpdateOne(
                {"source": d["source"], "chunk_index": d["chunk_index"]},
                {"$set": d},
                upsert=True,
            )
            for d in question_docs
        ]
        collection.bulk_write(ops, ordered=False)

    return question_docs


def process_problem_dir(pdf_dir: Path, solutions_dir: Path | None = None) -> int:
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    total = 0
    for pdf_path in pdf_files:
        docs = process_problem_pdf(pdf_path, solutions_dir)
        total += len(docs)
    return total
