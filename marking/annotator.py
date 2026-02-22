"""
Image annotator.

Takes a base64 image and a list of annotation dicts from the vision grader,
draws numbered callout markers at their approximate vertical positions on the
right edge of the image, and appends a colour-coded feedback legend panel
below the image.

Returns the result as a base64-encoded PNG string.
"""
import base64
import io
import textwrap
from typing import List, Dict

from PIL import Image, ImageDraw, ImageFont

# ── Colour palette by annotation type ────────────────────────────────────────
_COLOURS: Dict[str, tuple] = {
    "correct":    (40, 167, 69),    # green
    "error":      (220, 53, 69),    # red
    "missing":    (255, 152, 0),    # orange
    "suggestion": (0, 123, 255),    # blue
}
_DEFAULT_COLOUR = (108, 117, 125)   # grey

# ── Font paths to try (macOS → Linux fallback → PIL default) ─────────────────
_FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _load_font(size: int):
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def annotate_image(image_base64: str, annotations: List[Dict]) -> str:
    """
    Annotate the student's answer image with numbered callouts and a
    feedback legend.

    Args:
        image_base64: base64-encoded PNG / JPEG of the student's answer.
        annotations : list of dicts, each with keys:
                        label      (str)   – e.g. "1"
                        y_position (float) – 0.0 (top) … 1.0 (bottom)
                        type       (str)   – correct / error / missing / suggestion
                        feedback   (str)   – short feedback text

    Returns:
        Base64-encoded PNG of the annotated image.
    """
    if not annotations:
        return image_base64

    # ── Decode source image ───────────────────────────────────────────────────
    img_data = base64.b64decode(image_base64)
    img = Image.open(io.BytesIO(img_data)).convert("RGB")
    img_w, img_h = img.size

    # ── Layout constants ──────────────────────────────────────────────────────
    margin_w   = 260          # right annotation margin width
    badge_d    = 26           # badge diameter
    badge_x    = img_w + 14   # left edge of badge in the margin
    legend_pad = 18
    legend_line_h = 20
    legend_title_h = 28
    wrap_width = 38           # chars per line in legend

    # Pre-calculate legend height
    legend_entries: List[tuple] = []   # (colour, label_str, wrapped_lines)
    for ann in annotations:
        colour  = _COLOURS.get(ann.get("type", ""), _DEFAULT_COLOUR)
        label   = str(ann.get("label", "?"))
        lines   = textwrap.wrap(ann.get("feedback", ""), width=wrap_width) or [""]
        legend_entries.append((colour, label, lines))

    total_legend_lines = sum(len(e[2]) for e in legend_entries)
    legend_h = (
        legend_pad
        + legend_title_h
        + total_legend_lines * legend_line_h
        + legend_pad
    )

    canvas_w = img_w + margin_w
    canvas_h = img_h + legend_h

    # ── Create canvas ─────────────────────────────────────────────────────────
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    canvas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canvas)

    # Thin separator between image and right margin
    draw.line([(img_w, 0), (img_w, img_h)], fill=(200, 200, 200), width=1)

    # ── Fonts ─────────────────────────────────────────────────────────────────
    font_badge        = _load_font(13)
    font_legend_title = _load_font(14)
    font_legend       = _load_font(12)
    font_snippet      = _load_font(11)

    # ── Draw callouts ─────────────────────────────────────────────────────────
    for ann in annotations:
        y_frac  = float(ann.get("y_position", 0.5))
        y_px    = int(y_frac * img_h)
        y_px    = max(badge_d // 2 + 2, min(img_h - badge_d // 2 - 2, y_px))

        label   = str(ann.get("label", "?"))
        colour  = _COLOURS.get(ann.get("type", ""), _DEFAULT_COLOUR)

        # Dot on the image edge
        dot_r = 5
        draw.ellipse(
            [(img_w - dot_r * 2, y_px - dot_r), (img_w - 2, y_px + dot_r)],
            fill=colour,
        )

        # Horizontal connector line
        draw.line(
            [(img_w - dot_r * 2, y_px), (badge_x, y_px)],
            fill=colour,
            width=2,
        )

        # Filled circle badge
        bx1 = badge_x
        by1 = y_px - badge_d // 2
        bx2 = badge_x + badge_d
        by2 = y_px + badge_d // 2
        draw.ellipse([(bx1, by1), (bx2, by2)], fill=colour)

        # Centred label number inside badge
        bbox = draw.textbbox((0, 0), label, font=font_badge)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (bx1 + (badge_d - tw) // 2, by1 + (badge_d - th) // 2 - 1),
            label,
            fill=(255, 255, 255),
            font=font_badge,
        )

        # Short snippet text beside badge (first 35 chars)
        snippet = ann.get("feedback", "")[:35]
        if len(ann.get("feedback", "")) > 35:
            snippet += "…"
        draw.text(
            (badge_x + badge_d + 6, y_px - 7),
            snippet,
            fill=(50, 50, 50),
            font=font_snippet,
        )

    # ── Legend panel ──────────────────────────────────────────────────────────
    leg_y = img_h

    # Background
    draw.rectangle([(0, leg_y), (canvas_w, canvas_h)], fill=(248, 249, 250))
    draw.line([(0, leg_y), (canvas_w, leg_y)], fill=(200, 200, 200), width=1)

    # Title
    draw.text(
        (legend_pad, leg_y + legend_pad // 2),
        "Feedback",
        fill=(33, 37, 41),
        font=font_legend_title,
    )

    # Entries
    cur_y = leg_y + legend_pad + legend_title_h
    dot_size = 9
    for colour, label, lines in legend_entries:
        # Coloured dot
        draw.ellipse(
            [(legend_pad, cur_y + 4), (legend_pad + dot_size, cur_y + 4 + dot_size)],
            fill=colour,
        )
        # Label prefix on first line
        first_line = f"[{label}]  {lines[0]}" if lines else f"[{label}]"
        draw.text(
            (legend_pad + dot_size + 6, cur_y),
            first_line,
            fill=(33, 37, 41),
            font=font_legend,
        )
        cur_y += legend_line_h
        # Continuation lines (wrapped)
        for cont_line in lines[1:]:
            draw.text(
                (legend_pad + dot_size + 6 + 24, cur_y),
                cont_line,
                fill=(80, 80, 80),
                font=font_legend,
            )
            cur_y += legend_line_h

    # ── Encode to base64 PNG ──────────────────────────────────────────────────
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")
