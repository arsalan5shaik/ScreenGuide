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


class WaveformWidget(QWidget):
    """Animated waveform bars shown while listening."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self._levels = [0.0] * 12
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._decay)

    def set_level(self, rms: float):
        import random
        peak = min(1.0, rms * 3)
        self._levels = [min(1.0, peak * (0.4 + random.random() * 0.6)) for _ in self._levels]
        self.update()

    def _decay(self):
        self._levels = [max(0.0, v * 0.85) for v in self._levels]
        self.update()

    def start(self):
        self._timer.start(50)

    def stop(self):
        self._timer.stop()
        self._levels = [0.0] * 12
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bar_w = max(3, (w - 4 * len(self._levels)) // len(self._levels))
        spacing = (w - bar_w * len(self._levels)) // (len(self._levels) + 1)
        for i, level in enumerate(self._levels):
            x = spacing + i * (bar_w + spacing)
            bar_h = max(4, int(level * (h - 8)))
            y = (h - bar_h) // 2
            alpha = max(80, int(level * 255))
            color = QColor(0, 120, 255, alpha)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(x, y, bar_w, bar_h, bar_w // 2, bar_w // 2)
        painter.end()


PROVIDER_LABELS = {
    "claude":  "Claude",
    "openai":  "GPT-4o",
    "gemini":  "Gemini",
    "copilot": "Copilot",
    "ollama":  f"Ollama ({cfg.ollama_model})",
}

