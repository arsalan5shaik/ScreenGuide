"""
Local geometric-figure detector — the "eyes" behind ScreenGuide's drawing accuracy.

Vision LLMs are unreliable at pixel localization (especially small local
models), so ScreenGuide doesn't trust them with coordinates. This module finds
geometric figures (triangles, rectangles, circles, polygons) on the screen
with classic OpenCV contour analysis and hands the LLM their EXACT vertices,
pre-normalized to the 0-1000 tag coordinate space. The LLM only has to echo
numbers — which every model can do — and the manager additionally snaps any
sloppy stroke endpoints to the nearest detected vertex.

Runs entirely on CPU in ~50-150 ms on a 1280-wide screenshot. If OpenCV is
not installed, detection silently returns nothing and ScreenGuide falls back to
pure LLM localization.
"""

from __future__ import annotations

import base64
import io
import logging
import math
from dataclasses import dataclass
from typing import List, Tuple

log = logging.getLogger("screenguide.figures")


@dataclass
class Figure:
    kind: str                            # "triangle" | "quad" | "circle" | "poly"
    vertices: List[Tuple[int, int]]      # normalized 0-1000 (empty for circle)
    center: Tuple[int, int]              # normalized 0-1000
    radius: int                          # normalized x-units (circles only)
    bbox: Tuple[int, int, int, int]      # normalized (l, t, r, b)


def detect_figures(base64_jpeg: str, max_figures: int = 4) -> List[Figure]:
    """Find prominent geometric figures in a screenshot JPEG.

    Coordinates are returned normalized 0-1000 relative to the image, ready
    for prompt injection and for _denorm() on the manager side.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError:
        log.debug("opencv not installed — figure detection disabled")
        return []

