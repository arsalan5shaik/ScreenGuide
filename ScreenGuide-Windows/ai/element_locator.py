"""
Port of ElementLocationDetector.swift — uses Claude's Computer Use API to find
the exact pixel coordinate of a UI element the user is asking about.

Only runs when ANTHROPIC_API_KEY is set. Returns (x, y) in the full screenshot's
original pixel space so the overlay can fly to it.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx
from PIL import Image

from config import cfg


# Anthropic-recommended Computer Use resolutions (matched by aspect ratio)
_CU_RESOLUTIONS = (
    (1024, 768,  1024 / 768),   # 4:3
    (1280, 800,  1280 / 800),   # 16:10
    (1366, 768,  1366 / 768),   # 16:9
)

_API_URL = "https://api.anthropic.com/v1/messages"
_BETA_HEADER = "computer-use-2025-11-24"


@dataclass
class Detected:
    x: int             # in original screenshot pixel space
    y: int
    screen_index: int


def _pick_resolution(w: int, h: int) -> Tuple[int, int]:
    ar = w / max(1, h)
    best = (1280, 800)
    smallest = float("inf")
    for rw, rh, rar in _CU_RESOLUTIONS:
        d = abs(ar - rar)
        if d < smallest:
            smallest = d
            best = (rw, rh)
    return best

