"""
Curated registry of Ollama models recommended for ScreenGuide, plus heuristics
for classifying installed models as vision-capable or text-only.

Why curated:
    Ollama's library is huge. Most students don't know which models work
    well for screen-aware AI tutoring. This file is a quality-tested
    shortlist that gets surfaced in the tray menu under "Pull recommended".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class OllamaRec:
    name: str           # exact pull tag, e.g. "qwen2-vl:7b"
    label: str          # human-friendly display name
    size: str           # rough download size, for the tooltip
    use_for: str        # "vision" | "text"
    blurb: str          # one-line description shown in the menu


# ─── Vision models (best for screen-aware queries + grid-pointing) ────────────

RECOMMENDED_VISION: list[OllamaRec] = [
    OllamaRec(
        name="qwen2-vl:7b",
        label="Qwen2-VL 7B",
        size="4.5 GB",
        use_for="vision",
        blurb="Best UI/OCR accuracy — recommended for pointing",
    ),
    OllamaRec(
        name="llama3.2-vision:11b",
        label="Llama 3.2 Vision 11B",
        size="7.9 GB",
        use_for="vision",
        blurb="Meta's flagship vision model — solid all-rounder",
    ),
    OllamaRec(
        name="llava:7b",
        label="LLaVA 7B",
        size="4.7 GB",
        use_for="vision",
        blurb="Lightweight vision fallback — runs on weaker GPUs",
    ),
    OllamaRec(
        name="minicpm-v",
        label="MiniCPM-V 8B",
        size="5.5 GB",
        use_for="vision",
        blurb="Tiny + sharp — best for low-RAM laptops",
    ),
    OllamaRec(
        name="bakllava",
        label="BakLLaVA 7B",
        size="4.4 GB",
        use_for="vision",
        blurb="LLaVA fine-tune on Mistral — fast inference",
    ),
]

