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

