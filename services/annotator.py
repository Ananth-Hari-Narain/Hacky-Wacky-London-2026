"""
Image annotator.

Draws annotation overlays on the student's answer image with coloured
highlight bands, numbered badges, and a detailed feedback legend panel.
"""
import base64
import io
import textwrap
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFont

_COLOURS: Dict[str, tuple] = {
    "correct":    (40, 167, 69),
    "error":      (220, 53, 69),
    "missing":    (255, 152, 0),
    "suggestion": (0, 123, 255),
}
_DEFAULT_COLOUR = (108, 117, 125)
_OVERLAY_ALPHA = 60

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
    if not annotations:
        return image_base64

    img_data = base64.b64decode(image_base64)
    img = Image.open(io.BytesIO(img_data)).convert("RGBA")

    MAX_DIM = 1800
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)

    img_w, img_h = img.size

    band_h = 52
    badge_d = 42
    badge_margin = 12
    text_left = badge_margin + badge_d + 12
    text_wrap = max(20, (img_w - text_left - 10) // 8)

    legend_pad = 20
    legend_line_h = 24
    legend_title_h = 32
    legend_wrap = max(40, (img_w - 60) // 7)

    font_badge = _load_font(20, bold=True)
    font_text = _load_font(15, bold=True)
    font_legend_title = _load_font(18, bold=True)
    font_legend = _load_font(14)
    font_legend_bold = _load_font(14, bold=True)

    legend_entries: List[tuple] = []
    for ann in annotations:
        colour = _COLOURS.get(ann.get("type", ""), _DEFAULT_COLOUR)
        label = str(ann.get("label", "?"))
        atype = ann.get("type", "")
        lines = textwrap.wrap(ann.get("feedback", ""), width=legend_wrap) or [""]
        legend_entries.append((colour, label, atype, lines))

    total_legend_lines = sum(len(e[3]) + 1 for e in legend_entries)
    legend_h = legend_pad + legend_title_h + total_legend_lines * legend_line_h + legend_pad * 2
    canvas_h = img_h + legend_h

    canvas = Image.new("RGBA", (img_w, canvas_h), (255, 255, 255, 255))
    canvas.paste(img, (0, 0))

    sorted_anns = sorted(annotations, key=lambda a: float(a.get("y_position", 0.5)))
    used_ranges: List[tuple] = []

    for ann in sorted_anns:
        y_frac = float(ann.get("y_position", 0.5))
        y_center = int(y_frac * img_h)
        y_top = max(0, y_center - band_h // 2)
        y_bot = y_top + band_h
        if y_bot > img_h:
            y_bot = img_h
            y_top = max(0, y_bot - band_h)

        for (used_top, used_bot) in used_ranges:
            if y_top < used_bot and y_bot > used_top:
                y_top = used_bot + 2
                y_bot = y_top + band_h
                if y_bot > img_h:
                    y_bot = img_h
                    y_top = max(0, y_bot - band_h)

        used_ranges.append((y_top, y_bot))

        label = str(ann.get("label", "?"))
        colour = _COLOURS.get(ann.get("type", ""), _DEFAULT_COLOUR)

        overlay = Image.new("RGBA", (img_w, band_h), (*colour, _OVERLAY_ALPHA))
        canvas.paste(overlay, (0, y_top), overlay)

        accent = Image.new("RGBA", (6, band_h), (*colour, 220))
        canvas.paste(accent, (0, y_top), accent)

        draw = ImageDraw.Draw(canvas)

        badge_x = badge_margin
        badge_y = y_top + (band_h - badge_d) // 2
        draw.ellipse(
            [(badge_x, badge_y), (badge_x + badge_d, badge_y + badge_d)],
            fill=(*colour, 240),
        )

        bbox = draw.textbbox((0, 0), label, font=font_badge)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (badge_x + (badge_d - tw) // 2, badge_y + (badge_d - th) // 2 - 1),
            label,
            fill=(255, 255, 255, 255),
            font=font_badge,
        )

        atype = ann.get("type", "").upper()
        type_x = text_left
        type_y = y_top + 5
        draw.text((type_x, type_y), atype, fill=(*colour, 255), font=font_text)

        feedback = ann.get("feedback", "")
        wrapped = textwrap.wrap(feedback, width=text_wrap) or [""]
        text_y = type_y + 18
        for line_text in wrapped[:2]:
            draw.text((type_x + 1, text_y + 1), line_text, fill=(0, 0, 0, 120), font=font_legend)
            draw.text((type_x, text_y), line_text, fill=(20, 20, 20, 255), font=font_legend)
            text_y += 16

    # Legend panel
    leg_y = img_h
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([(0, leg_y), (img_w, canvas_h)], fill=(30, 30, 40, 255))
    draw.rectangle([(0, leg_y), (img_w, leg_y + 3)], fill=(100, 100, 255, 255))
    draw.text(
        (legend_pad, leg_y + legend_pad // 2 + 6),
        "DETAILED FEEDBACK",
        fill=(255, 255, 255, 255),
        font=font_legend_title,
    )

    cur_y = leg_y + legend_pad + legend_title_h + 4
    for colour, label, atype, lines in legend_entries:
        dot_size = 12
        draw.ellipse(
            [(legend_pad, cur_y + 5), (legend_pad + dot_size, cur_y + 5 + dot_size)],
            fill=(*colour, 255),
        )
        prefix = f"[{label}] {atype.upper()}: "
        draw.text((legend_pad + dot_size + 8, cur_y), prefix, fill=(*colour, 255), font=font_legend_bold)
        prefix_w = draw.textbbox((0, 0), prefix, font=font_legend_bold)[2]
        if lines:
            draw.text(
                (legend_pad + dot_size + 8 + prefix_w, cur_y),
                lines[0],
                fill=(220, 220, 230, 255),
                font=font_legend,
            )
        cur_y += legend_line_h
        for cont_line in lines[1:]:
            draw.text(
                (legend_pad + dot_size + 8 + 20, cur_y),
                cont_line,
                fill=(180, 180, 195, 255),
                font=font_legend,
            )
            cur_y += legend_line_h
        cur_y += 4

    rgb_canvas = Image.new("RGB", canvas.size, (255, 255, 255))
    rgb_canvas.paste(canvas, mask=canvas.split()[3])

    buf = io.BytesIO()
    rgb_canvas.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")
