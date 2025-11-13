"""
ScreenGuide's blue buddy cursor — faithful port of the original macOS overlay.

Matches ScreenGuide-MacOS/ScreenGuide-MacOS/OverlayWindow.swift:
  - Flat solid blue equilateral triangle (#3380FF), 16×16, rotated -35°
  - Sits at (+35, +25) relative to the real cursor
  - Soft blue drop shadow (radius ~8)
  - States cross-fade in place:
      idle / speaking → triangle
      listening       → 5-bar waveform
      thinking        → rotating arc spinner
      pointing        → triangle + speech bubble ("found it!" etc.)
"""

import math
import random
import time
from typing import Optional

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath, QCursor, QFont,
)


MODE_IDLE      = "idle"
MODE_LISTENING = "listening"
MODE_THINKING  = "thinking"
MODE_SPEAKING  = "speaking"


# Exactly matches Swift source: buddyX = swiftUIPosition.x + 35; buddyY = +25
OFFSET_X = 35
OFFSET_Y = 25

# Triangle bounding box edge (Swift: frame(width: 16, height: 16))
TRI_SIZE = 16
# Swift: .rotationEffect(.degrees(-35))
TRI_ROTATION_DEG = -35.0

# #3380FF
CURSOR_BLUE = QColor(0x33, 0x80, 0xFF)

# Teaching-annotation palette. The LLM picks colors by name; default is the
# ScreenGuide blue. Chosen for contrast on both light and dark content.
ANNOT_COLORS = {
    "blue":   QColor(0x33, 0x80, 0xFF),
    "red":    QColor(0xFF, 0x45, 0x55),
    "green":  QColor(0x2E, 0xCC, 0x71),
    "yellow": QColor(0xFF, 0xD5, 0x4F),
    "orange": QColor(0xFF, 0x8C, 0x2E),
    "purple": QColor(0xB5, 0x6C, 0xFF),
    "white":  QColor(0xF5, 0xF7, 0xFA),
    "cyan":   QColor(0x4F, 0xD9, 0xFF),
}

# Progressive stroke animation — shapes draw at "hand speed" (px/sec), so a
# long line takes visibly longer than a short tick mark. Queued sequentially;
# the buddy cursor rides the pen tip while each stroke draws.
STROKE_SPEED_PX_S   = 420.0
SHAPE_DRAW_MIN_S    = 0.6
SHAPE_DRAW_MAX_S    = 2.2
SHAPE_GAP_SECONDS   = 0.28

_TEXT_SIZES = {"s": 12, "m": 17, "l": 26}


def _shape_path_pts(shape: dict):
    """(pts, closed) polyline for stroke-able shapes; None for circle/text."""
    kind = shape.get("kind")
    if kind in ("line", "arrow"):
        return list(shape["pts"]), False
    if kind == "poly":
        return list(shape["pts"]), True
    if kind == "rect":
        x1, y1, x2, y2 = shape["x1"], shape["y1"], shape["x2"], shape["y2"]
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True
    if kind == "underline":
        return [(shape["x"], shape["y"]),
                (shape["x"] + shape["w"], shape["y"])], False
    if kind == "angle":
        s = shape.get("s", 22)
        rot = math.radians(shape.get("rot", 0))
        x, y = shape["x"], shape["y"]
        d1 = (math.cos(rot), math.sin(rot))
        d2 = (math.cos(rot + math.pi / 2), math.sin(rot + math.pi / 2))
        return [(x + d1[0] * s, y + d1[1] * s),
                (x + (d1[0] + d2[0]) * s, y + (d1[1] + d2[1]) * s),
                (x + d2[0] * s, y + d2[1] * s)], False
    return None, False


def _shape_length(shape: dict) -> float:
    """Approximate stroke length in logical px — drives draw duration."""
    kind = shape.get("kind")
    if kind == "circle":
        return 2 * math.pi * shape.get("r", 30)
    if kind == "text":
        return 90.0   # fixed short reveal
    pts, closed = _shape_path_pts(shape)
    if not pts or len(pts) < 2:
        return 60.0
    if closed:
        pts = pts + [pts[0]]
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(pts, pts[1:]))


def _partial_pts(pts, closed, u):
    """First u-fraction of a polyline. Returns the drawn point list."""
    if closed and len(pts) >= 3:
        pts = list(pts) + [pts[0]]
    if len(pts) < 2:
        return list(pts)
    seg_lens = [math.hypot(b[0] - a[0], b[1] - a[1])
                for a, b in zip(pts, pts[1:])]
    total = sum(seg_lens)
    if total <= 0:
        return [pts[0]]
    budget = total * min(1.0, max(0.0, u))
    drawn = [pts[0]]
    for (a, b), L in zip(zip(pts, pts[1:]), seg_lens):
        if budget <= 0:
            break
        if L <= budget:
            drawn.append(b)
            budget -= L
        else:
            t = budget / L
            drawn.append((a[0] + (b[0] - a[0]) * t,
                          a[1] + (b[1] - a[1]) * t))
            budget = 0
    return drawn


def _stroke_tip(shape: dict, u: float):
    """Current pen-tip position of a shape at progress u — where the buddy
    hovers while 'drawing'. Returns (x, y) in logical screen coords."""
    kind = shape.get("kind")
    if kind == "circle":
        a = math.radians(90 - 360 * u)   # clockwise from 12 o'clock
        return (shape["x"] + shape["r"] * math.cos(a),
                shape["y"] - shape["r"] * math.sin(a))
    if kind == "text":
        return (shape["x"], shape["y"])
    pts, closed = _shape_path_pts(shape)
    if not pts:
        return None
    drawn = _partial_pts(pts, closed, u)
    return drawn[-1] if drawn else None

# Pointing phrases from the original
POINTER_PHRASES = (
    "right here!", "this one!", "over here!",
    "click this!", "here it is!", "found it!",
)

