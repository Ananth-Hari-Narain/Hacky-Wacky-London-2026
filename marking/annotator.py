"""
Image annotator.

Draws large, bold annotation overlays DIRECTLY ON TOP of the student's answer
image.  Each annotation gets a coloured highlight band spanning the full width
at its y-position, with a large numbered badge and feedback text.  A detailed
feedback legend panel is appended below.

Returns the result as a base64-encoded JPEG string.
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

# Semi-transparent overlay alpha
_OVERLAY_ALPHA = 60

# ── Font paths to try (macOS → Linux fallback → PIL default) ─────────────────
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


def _load_font(size: int, bold: bool = False):
    paths = _BOLD_FONT_PATHS if bold else _FONT_PATHS
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def annotate_image(image_base64: str, annotations: List[Dict]) -> str:
    """
    Annotate the student's answer image with large, bold overlays directly
    on the image and a detailed legend panel below.

    Args:
        image_base64: base64-encoded PNG / JPEG of the student's answer.
        annotations : list of dicts, each with keys:
                        label      (str)   – e.g. "1"
                        y_position (float) – 0.0 (top) … 1.0 (bottom)
                        type       (str)   – correct / error / missing / suggestion
                        feedback   (str)   – short feedback text

    Returns:
        Base64-encoded JPEG of the annotated image.
    """
    if not annotations:
        return image_base64

    # ── Decode source image ───────────────────────────────────────────────────
    img_data = base64.b64decode(image_base64)
    img = Image.open(io.BytesIO(img_data)).convert("RGBA")

    # Cap image size to avoid huge canvases (and MongoDB 16 MB BSON limit)
    MAX_DIM = 1800
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)

    img_w, img_h = img.size

    # ── Layout constants ──────────────────────────────────────────────────────
    band_h       = 52           # height of each highlight band
    badge_d      = 42           # badge circle diameter
    badge_margin = 12           # left margin for badge
    text_left    = badge_margin + badge_d + 12   # where feedback text starts
    text_wrap    = max(20, (img_w - text_left - 10) // 8)  # chars per line on overlay

    # Legend panel sizing
    legend_pad     = 20
    legend_line_h  = 24
    legend_title_h = 32
    legend_wrap    = max(40, (img_w - 60) // 7)

    # ── Fonts ─────────────────────────────────────────────────────────────────
    font_badge  = _load_font(20, bold=True)
    font_text   = _load_font(15, bold=True)
    font_legend_title = _load_font(18, bold=True)
    font_legend       = _load_font(14)
    font_legend_bold  = _load_font(14, bold=True)

    # ── Pre-calculate legend height ──────────────────────────────────────────
    legend_entries: List[tuple] = []   # (colour, label_str, type_str, wrapped_lines)
    for ann in annotations:
        colour  = _COLOURS.get(ann.get("type", ""), _DEFAULT_COLOUR)
        label   = str(ann.get("label", "?"))
        atype   = ann.get("type", "")
        lines   = textwrap.wrap(ann.get("feedback", ""), width=legend_wrap) or [""]
        legend_entries.append((colour, label, atype, lines))

    total_legend_lines = sum(len(e[3]) + 1 for e in legend_entries)  # +1 gap per entry
    legend_h = (
        legend_pad
        + legend_title_h
        + total_legend_lines * legend_line_h
        + legend_pad * 2
    )

    canvas_h = img_h + legend_h

    # ── Create canvas (RGBA for semi-transparent overlays) ───────────────────
    canvas = Image.new("RGBA", (img_w, canvas_h), (255, 255, 255, 255))
    canvas.paste(img, (0, 0))

    # ── Draw annotation overlays on top of the image ─────────────────────────
    # Sort annotations by y_position to avoid overlap issues
    sorted_anns = sorted(annotations, key=lambda a: float(a.get("y_position", 0.5)))

    # Track used y-ranges to avoid overlapping bands
    used_ranges: List[tuple] = []

    for ann in sorted_anns:
        y_frac  = float(ann.get("y_position", 0.5))
        y_center = int(y_frac * img_h)

        # Clamp to image bounds
        y_top = max(0, y_center - band_h // 2)
        y_bot = y_top + band_h
        if y_bot > img_h:
            y_bot = img_h
            y_top = max(0, y_bot - band_h)

        # Shift down if overlapping with a previous band
        for (used_top, used_bot) in used_ranges:
            if y_top < used_bot and y_bot > used_top:
                y_top = used_bot + 2
                y_bot = y_top + band_h
                if y_bot > img_h:
                    y_bot = img_h
                    y_top = max(0, y_bot - band_h)

        used_ranges.append((y_top, y_bot))

        label   = str(ann.get("label", "?"))
        colour  = _COLOURS.get(ann.get("type", ""), _DEFAULT_COLOUR)

        # Draw semi-transparent coloured band across full width
        overlay = Image.new("RGBA", (img_w, band_h), (*colour, _OVERLAY_ALPHA))
        canvas.paste(overlay, (0, y_top), overlay)

        # Draw solid coloured left edge strip (accent bar)
        accent = Image.new("RGBA", (6, band_h), (*colour, 220))
        canvas.paste(accent, (0, y_top), accent)

        draw = ImageDraw.Draw(canvas)

        # ── Badge circle ─────────────────────────────────────────────────────
        badge_x = badge_margin
        badge_y = y_top + (band_h - badge_d) // 2
        draw.ellipse(
            [(badge_x, badge_y), (badge_x + badge_d, badge_y + badge_d)],
            fill=(*colour, 240),
        )

        # Centred label number
        bbox = draw.textbbox((0, 0), label, font=font_badge)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (badge_x + (badge_d - tw) // 2, badge_y + (badge_d - th) // 2 - 1),
            label,
            fill=(255, 255, 255, 255),
            font=font_badge,
        )

        # ── Annotation type tag ──────────────────────────────────────────────
        atype = ann.get("type", "").upper()
        type_x = text_left
        type_y = y_top + 5
        draw.text(
            (type_x, type_y),
            atype,
            fill=(*colour, 255),
            font=font_text,
        )

        # ── Feedback text (first 2 lines, on the overlay) ────────────────────
        feedback = ann.get("feedback", "")
        wrapped = textwrap.wrap(feedback, width=text_wrap) or [""]
        text_y = type_y + 18
        for line_text in wrapped[:2]:
            # Draw text with a subtle dark shadow for readability
            draw.text((type_x + 1, text_y + 1), line_text, fill=(0, 0, 0, 120), font=font_legend)
            draw.text((type_x, text_y), line_text, fill=(20, 20, 20, 255), font=font_legend)
            text_y += 16

    # ── Legend panel ──────────────────────────────────────────────────────────
    leg_y = img_h

    # Solid background
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        [(0, leg_y), (img_w, canvas_h)],
        fill=(30, 30, 40, 255),
    )

    # Top accent line
    draw.rectangle([(0, leg_y), (img_w, leg_y + 3)], fill=(100, 100, 255, 255))

    # Title
    draw.text(
        (legend_pad, leg_y + legend_pad // 2 + 6),
        "DETAILED FEEDBACK",
        fill=(255, 255, 255, 255),
        font=font_legend_title,
    )

    # Entries
    cur_y = leg_y + legend_pad + legend_title_h + 4

    for colour, label, atype, lines in legend_entries:
        # Coloured dot
        dot_size = 12
        draw.ellipse(
            [(legend_pad, cur_y + 5),
             (legend_pad + dot_size, cur_y + 5 + dot_size)],
            fill=(*colour, 255),
        )

        # Label + type prefix
        prefix = f"[{label}] {atype.upper()}: "
        draw.text(
            (legend_pad + dot_size + 8, cur_y),
            prefix,
            fill=(*colour, 255),
            font=font_legend_bold,
        )

        # First line of feedback next to prefix
        prefix_w = draw.textbbox((0, 0), prefix, font=font_legend_bold)[2]
        if lines:
            draw.text(
                (legend_pad + dot_size + 8 + prefix_w, cur_y),
                lines[0],
                fill=(220, 220, 230, 255),
                font=font_legend,
            )
        cur_y += legend_line_h

        # Continuation lines
        for cont_line in lines[1:]:
            draw.text(
                (legend_pad + dot_size + 8 + 20, cur_y),
                cont_line,
                fill=(180, 180, 195, 255),
                font=font_legend,
            )
            cur_y += legend_line_h

        cur_y += 4  # gap between entries

    # ── Convert RGBA → RGB and encode to JPEG ────────────────────────────────
    rgb_canvas = Image.new("RGB", canvas.size, (255, 255, 255))
    rgb_canvas.paste(canvas, mask=canvas.split()[3])

    buf = io.BytesIO()
    rgb_canvas.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")
