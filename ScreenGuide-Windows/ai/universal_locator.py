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

    # Cell number labels — pick a font size proportional to cell size
    font_size = max(12, min(28, int(min(cell_w, cell_h) / 3.5)))
    font = _load_font(font_size)
    pad = 2

    n = 1
    for r in range(rows):
        for c in range(cols):
            cx = int(c * cell_w) + pad
            cy = int(r * cell_h) + pad
            label = str(n)
            # Measure text
            try:
                bbox = draw.textbbox((cx, cy), label, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except Exception:
                tw, th = font_size * len(label) // 2, font_size
            # Draw filled background pill behind the number
            draw.rectangle(
                [(cx - 1, cy - 1), (cx + tw + 4, cy + th + 4)],
                fill=label_bg,
            )
            draw.text((cx + 2, cy), label, fill=label_fg, font=font)
            n += 1

    out = Image.alpha_composite(base, overlay).convert("RGB")
    return out


def _img_to_jpeg_b64(img: Image.Image, quality: int = 85) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ─── LLM call ─────────────────────────────────────────────────────────────────

_PARSE_RE = re.compile(r"\b(\d{1,3})\b")


def _parse_cell_number(text: str, max_n: int) -> Optional[int]:
    """Extract a cell number 1..max_n from a free-form LLM reply.

    Strategy: prefer JSON-shaped answers, otherwise take the first integer
    in range that appears in the reply. Tolerates the model rambling.
    """
    # Try JSON first
    m = re.search(r"\{[^{}]*\}", text, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            for key in ("cell", "number", "n", "answer"):
                if key in obj:
                    n = int(obj[key])
                    if 1 <= n <= max_n:
                        return n
        except Exception:
            pass

    # Fallback: scan integers in order
    for tok in _PARSE_RE.findall(text):
        try:
            n = int(tok)
            if 1 <= n <= max_n:
                return n
        except ValueError:
            continue

    return None


async def _ask_grid_pick(
    llm: BaseLLMProvider,
    img_b64: str,
    target: str,
    max_n: int,
    model: str | None = None,
) -> Optional[int]:
    """Send an annotated image + question to the LLM and parse a cell number."""
    prompt = (
        f"You are looking at a screenshot with a red numbered grid overlay. "
        f"Cells are numbered 1 to {max_n}, left-to-right, top-to-bottom.\n\n"
        f'The user asked: "{target}"\n\n'
        f"Identify the SINGLE numbered cell that most precisely contains the "
        f"UI element the user is asking about (button, link, menu item, icon, "
        f"text field, etc.).\n\n"
        f'Respond with ONLY this JSON, nothing else:  {{"cell": <number>}}\n\n'
        f"If there's no specific UI element to point at (purely conceptual "
        f'question), respond exactly:  {{"cell": 0}}'
    )

    chunks: list[str] = []
    try:
        async for chunk in llm.stream_response(
            user_text=prompt,
            screenshots_b64=[img_b64],
            history=[],
            system_prompt=(
                "You are a precise UI element locator. You ALWAYS answer with "
                'a single JSON object of the form {"cell": <integer>}.'
            ),
            model=model,
        ):
            chunks.append(chunk)
            if len("".join(chunks)) > 400:
                break   # Keep the call short; we only need a small reply
    except Exception:
        return None

