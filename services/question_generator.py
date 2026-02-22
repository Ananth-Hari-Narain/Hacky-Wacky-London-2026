"""
AI question generation service.

Generates new practice questions based on existing exam questions (for style)
and lecture notes (for content), then stores them with full metadata so they
work with the existing grading pipeline.
"""
import json
import textwrap
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from pymongo import UpdateOne

from config.settings import (
    QUESTION_GEN_MODEL,
    EMBED_BATCH_SIZE,
)
from config.prompts import QUESTION_GENERATION_SYSTEM_PROMPT
from models.db import questions_coll, notes_chunks_coll
from services.openai_client import client as oai
from services.pipeline import generate_hints, embed_batch


# =========================================================================
# Font loading (same approach as services/annotator.py)
# =========================================================================

_FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_BOLD_FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_MONO_FONT_PATHS = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFMono-Regular.otf",
    "/System/Library/Fonts/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]


def _load_font(size: int, bold: bool = False, mono: bool = False):
    if mono:
        paths = _MONO_FONT_PATHS
    elif bold:
        paths = _BOLD_FONT_PATHS
    else:
        paths = _FONT_PATHS
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


# Characters that suggest a line is part of an ASCII diagram
_DIAGRAM_CHARS = set("─│┌┐└┘├┤┬┴┼╔╗╚╝║═╠╣╦╩╬--+|>/<\\()")


def _is_diagram_line(line: str) -> bool:
    """Heuristic: a line is 'diagram-like' if it has many special chars or arrows."""
    stripped = line.strip()
    if not stripped:
        return False
    if "-->" in stripped or "<--" in stripped or "---" in stripped:
        return True
    if "|" in stripped and ("-" in stripped or "+" in stripped):
        return True
    special = sum(1 for c in stripped if c in _DIAGRAM_CHARS)
    return special > len(stripped) * 0.3


# =========================================================================
# Text-to-image rendering
# =========================================================================

def _render_text_to_png(text: str, title: str | None = None,
                        width: int = 900, padding: int = 50,
                        font_size: int = 18, title_size: int = 24) -> bytes:
    """Render text onto a white image and return PNG bytes.

    Diagram lines (detected by heuristic) are rendered in a monospace font
    without word-wrapping so ASCII art stays aligned.
    """
    body_font = _load_font(font_size)
    mono_font = _load_font(font_size, mono=True)
    title_font = _load_font(title_size, bold=True)

    # Wrap text to fit within the image width
    chars_per_line = max(40, (width - 2 * padding) // (font_size * 6 // 10))

    # Build list of (text, is_diagram) tuples for each rendered line
    rendered_lines: list[tuple[str, bool]] = []
    for paragraph in text.split("\n"):
        if paragraph.strip() == "":
            rendered_lines.append(("", False))
        elif _is_diagram_line(paragraph):
            # Don't wrap diagram lines — preserve spacing
            rendered_lines.append((paragraph, True))
        else:
            for wrapped in textwrap.wrap(paragraph, width=chars_per_line):
                rendered_lines.append((wrapped, False))

    # Calculate image height
    line_height = int(font_size * 1.6)
    title_height = int(title_size * 2.0) if title else 0
    content_height = len(rendered_lines) * line_height
    img_height = padding + title_height + content_height + padding

    # Create white image
    img = Image.new("RGB", (width, img_height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    y = padding

    # Draw title
    if title:
        draw.text((padding, y), title, fill=(30, 30, 30), font=title_font)
        y += title_height

    # Draw body text (mono for diagrams, regular for prose)
    for line_text, is_diagram in rendered_lines:
        font = mono_font if is_diagram else body_font
        color = (30, 80, 120) if is_diagram else (50, 50, 50)
        draw.text((padding, y), line_text, fill=color, font=font)
        y += line_height

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# =========================================================================
# Gather context from existing data
# =========================================================================

def _fetch_example_questions(limit: int = 5) -> list[dict]:
    """Fetch a sample of existing uploaded questions for style reference."""
    return list(questions_coll().find(
        {
            "source": {"$ne": "ai-generated"},
            "question": {"$not": {"$regex": "^(Preamble|Appendix)", "$options": "i"}},
            "text": {"$exists": True, "$ne": ""},
        },
        {"text": 1, "solution_text": 1, "question": 1, "_id": 0},
    ).limit(limit))


def _fetch_notes_context(max_chunks: int = 15) -> str:
    """Fetch lecture notes chunks and concatenate them."""
    chunks = list(notes_chunks_coll().find(
        {"text": {"$exists": True, "$ne": ""}},
        {"text": 1, "source": 1, "page": 1, "_id": 0},
    ).limit(max_chunks))

    if not chunks:
        return ""

    parts = []
    for c in chunks:
        source = c.get("source", "unknown")
        page = c.get("page", "?")
        parts.append(f"[{source}, p.{page}]\n{c['text']}")
    return "\n\n---\n\n".join(parts)


# =========================================================================
# Generate questions via OpenAI
# =========================================================================

def _call_generation(example_questions: list[dict], notes_context: str,
                     count: int = 3) -> list[dict]:
    """Call OpenAI to generate new questions + solutions."""
    example_parts = []
    for i, q in enumerate(example_questions, 1):
        ex = f"--- Example {i} ---\n{q['text']}"
        if q.get("solution_text"):
            ex += f"\n\nSolution:\n{q['solution_text']}"
        example_parts.append(ex)
    examples_block = "\n\n".join(example_parts)

    user_message = (
        f"EXAMPLE QUESTIONS (match this style):\n\n{examples_block}\n\n"
        f"LECTURE NOTES (use as source material):\n\n{notes_context}"
    )

    response = oai.chat.completions.create(
        model=QUESTION_GEN_MODEL,
        temperature=0.7,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": QUESTION_GENERATION_SYSTEM_PROMPT.format(count=count)},
            {"role": "user", "content": user_message},
        ],
    )

    parsed = json.loads(response.choices[0].message.content)

    if isinstance(parsed, list):
        return parsed
    if "questions" in parsed:
        return parsed["questions"]
    return next(v for v in parsed.values() if isinstance(v, list))


# =========================================================================
# Full pipeline
# =========================================================================

def _get_next_gen_index() -> int:
    """Find the next available chunk_index for ai-generated questions."""
    last = questions_coll().find_one(
        {"source": "ai-generated"},
        sort=[("chunk_index", -1)],
    )
    if last and "chunk_index" in last:
        return last["chunk_index"] + 1
    return 0


def generate_questions(count: int = 3,
                       example_limit: int = 5,
                       notes_limit: int = 15) -> list[dict]:
    """
    Full pipeline: fetch context -> generate -> hints -> embed -> render images -> store.

    Returns list of stored question summaries.
    """
    # 1. Gather context
    examples = _fetch_example_questions(limit=example_limit)
    if not examples:
        raise ValueError("No existing questions found. Upload a problem PDF first.")

    notes_context = _fetch_notes_context(max_chunks=notes_limit)
    if not notes_context:
        raise ValueError("No lecture notes found. Upload lecture notes first.")

    # 2. Generate questions + solutions
    raw_questions = _call_generation(examples, notes_context, count=count)

    # 3. Build full documents with hints, embeddings, and images
    start_index = _get_next_gen_index()
    question_docs = []
    question_texts = []

    for i, rq in enumerate(raw_questions):
        q_text = rq.get("question_text", "").strip()
        s_text = rq.get("solution_text", "").strip()

        if not q_text:
            continue

        label = f"Practice Question {start_index + i + 1}"
        hints_data = generate_hints(q_text, s_text if s_text else None)

        # Render question and solution text as PNG images
        q_image = _render_text_to_png(q_text, title=label)
        s_image = _render_text_to_png(s_text, title=f"{label} — Solution") if s_text else None

        doc = {
            "source": "ai-generated",
            "chunk_index": start_index + i,
            "question": label,
            "text": q_text,
            "question_image": q_image,
            "solution_text": s_text,
            "solution_image": s_image,
            "hints": hints_data.get("hints", []),
            "key_takeaways": hints_data.get("key_takeaways", []),
            "embedding": None,
            "generated": True,
            "updated_at": datetime.now(timezone.utc),
        }

        question_docs.append(doc)
        question_texts.append(q_text)

    # 4. Batch embed
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
        questions_coll().bulk_write(ops, ordered=False)

    return [
        {
            "question": d["question"],
            "text": d["text"],
            "solution_text": d["solution_text"],
            "hints_count": len(d["hints"]),
            "chunk_index": d["chunk_index"],
        }
        for d in question_docs
    ]
