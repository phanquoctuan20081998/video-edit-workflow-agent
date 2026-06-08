"""Subtitle renderer — Pillow-based, no ImageMagick dependency.

Renders word_timestamps onto video frames as burnt-in subtitles.
Called from composite.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from app.models.video_spec import SubtitleStyle, WordTimestamp


def render_subtitle_frame(
    width: int,
    height: int,
    words: list[WordTimestamp],
    t: float,
    style: SubtitleStyle,
    font_path: str | None = None,
) -> Image.Image | None:
    """Return a transparent RGBA image with subtitle text at time t, or None if no active words."""
    active = [w for w in words if w.start <= t <= w.end]
    if not active:
        return None

    text = " ".join(w.word for w in active)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(font_path or _find_font(), style.size)
    except (IOError, OSError):
        font = ImageFont.load_default()

    color = _hex_to_rgba(style.color)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (width - tw) // 2
    y = int(height * 0.85)

    if style.stroke:
        stroke_fill = (0, 0, 0, 255)
        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
            draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)

    draw.text((x, y), text, font=font, fill=color)
    return img


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, alpha)


def _find_font() -> str:
    candidates = [
        "resource/fonts/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return ""
