"""
Universal pixel-perfect pointing — works with ANY vision-capable LLM
(GitHub Copilot GPT-4o, OpenAI, Gemini, Ollama llava/llama3.2-vision).

Why this exists:
    The original `element_locator.py` only works when ANTHROPIC_API_KEY is
    set, because it uses Claude's special Computer-Use tool which returns
    exact pixel coordinates. Users on Copilot / Ollama got NO pointing at
    all — that's the bug.

How it works (two-stage grid annotation, a.k.a. Set-of-Mark prompting):

    Stage 1 (coarse, 12×8 = 96 cells):
        - Draw a numbered grid overlay on the screenshot.
        - Ask the LLM: "Which numbered cell contains [target]?"
        - LLM picks 1–96.

    Stage 2 (fine, 6×6 = 36 sub-cells inside a 3×3 region around the pick):
        - Crop a 3×3-cell region around the chosen cell.
        - Draw a 6×6 sub-grid on the crop.
        - Ask again: "Which sub-cell contains [target]?"
        - LLM picks 1–36 within the crop.

    Final: Map the centre of the chosen sub-cell back to original screen
    pixels, then to logical Qt coordinates.

Accuracy: ~25-50px on a 1080p screen. Plenty for buttons, menu items,
form fields, links, icons. Inferior to Claude Computer Use (~5px) but
usable on any vision LLM.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from ai.base_provider import BaseLLMProvider


# ─── Tunables ─────────────────────────────────────────────────────────────────

STAGE1_COLS = 12
STAGE1_ROWS = 8
STAGE2_COLS = 6
STAGE2_ROWS = 6

# Stage-2 zoom region size in Stage-1 cells (3 means 3×3 cells around the pick)
ZOOM_RADIUS_CELLS = 1   # → 3×3 region

# Max width to send to the LLM (smaller = faster + fewer tokens, less accurate)
MAX_INFERENCE_WIDTH = 1280


@dataclass
class Detected:
    x: int             # logical Qt screen px (already DPI/origin-adjusted)
    y: int
    screen_index: int


# ─── Grid drawing ─────────────────────────────────────────────────────────────

def _load_font(size: int) -> ImageFont.ImageFont:
    """Best-effort font loader — falls back to PIL default if missing."""
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_grid(
    img: Image.Image,
    cols: int,
    rows: int,
    *,
    line_color=(255, 0, 0, 200),
    label_bg=(255, 0, 0, 220),
    label_fg=(255, 255, 255, 255),
) -> Image.Image:
    """Overlay a numbered grid on top of `img`. Returns a new RGB image."""
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    w, h = base.size
    cell_w = w / cols
    cell_h = h / rows

    # Grid lines
    for c in range(1, cols):
        x = int(c * cell_w)
        draw.line([(x, 0), (x, h)], fill=line_color, width=1)
    for r in range(1, rows):
        y = int(r * cell_h)
        draw.line([(0, y), (w, y)], fill=line_color, width=1)

