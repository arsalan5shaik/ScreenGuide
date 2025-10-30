import asyncio
from enum import Enum, auto
from typing import Callable, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QComboBox, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QCursor

from ui.design import (
    PANEL_QSS, PANEL_WIDTH, PANEL_HEIGHT, PANEL_RADIUS,
    STATE_IDLE, STATE_LISTENING, STATE_THINKING, STATE_SPEAKING,
    FONT_TITLE, FONT_STATUS, FONT_RESPONSE, FONT_LABEL,
    SURFACE, TEXT_SECONDARY, BORDER, ANIM_FAST_MS
)
from config import cfg


class AppState(Enum):
    IDLE      = auto()
    LISTENING = auto()
    THINKING  = auto()
    SPEAKING  = auto()


def _hotkey_label() -> str:
    """Human-readable hotkey from config, e.g. 'ctrl+win' -> 'Ctrl+Win'."""
    try:
        from config import cfg
        return "+".join(p.strip().capitalize() for p in cfg.hotkey.split("+"))
    except Exception:
        return "Ctrl+Win"


STATE_LABELS = {
    AppState.IDLE:      f"Say 'ScreenGuide' or {_hotkey_label()}",
    AppState.LISTENING: "Listening...",
    AppState.THINKING:  "Thinking...",
    AppState.SPEAKING:  "Speaking...",
}

STATE_COLORS = {
    AppState.IDLE:      STATE_IDLE,
    AppState.LISTENING: STATE_LISTENING,
    AppState.THINKING:  STATE_THINKING,
    AppState.SPEAKING:  STATE_SPEAKING,
}

