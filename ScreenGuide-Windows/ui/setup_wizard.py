"""
First-run setup wizard.

Shown once on the first launch (or whenever the user clicks
"Tray → Run setup again…"). Walks the user through:

  1. Detect Ollama       → install if missing
  2. Detect text model   → pull if missing
  3. Detect vision model → pull if missing  (optional, larger)

Everything is optional — the user can Skip at any step and use API keys
instead. The wizard never blocks the main app from starting; the user can
close it and ScreenGuide's panel banner will keep nagging until Ollama is set up.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame, QWidget, QStackedWidget, QSizePolicy
)

from ai import ollama_bootstrap as ob
from config import cfg


# Marker file: the wizard skips itself if this exists.
def _flag_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / "ScreenGuide"
    d.mkdir(parents=True, exist_ok=True)
    return d / "setup_complete.flag"


def setup_already_ran() -> bool:
    return _flag_path().exists()


def mark_setup_complete() -> None:
    try:
        _flag_path().write_text("ok")
    except Exception:
        pass


# ─── Wizard ───────────────────────────────────────────────────────────────────

class SetupWizard(QDialog):
    """One-window wizard with three pages: Ollama install → text model → vision model."""

    progress_signal = pyqtSignal(str, float)
    finished_signal = pyqtSignal(bool, str)   # ok, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ScreenGuide Setup")
        self.setModal(False)
        self.setMinimumSize(560, 380)
        self.setStyleSheet("""
            QDialog { background: #0e1014; color: #e8eaed; }
            QLabel  { color: #e8eaed; }
            QLabel#title { font-size: 22px; font-weight: 700; }
            QLabel#subtitle { color: #a0a3a8; font-size: 13px; }
            QLabel#status { color: #c8cbd0; font-size: 13px; }
            QPushButton {
                background: #1f6feb; color: white; border: none;
                padding: 10px 18px; border-radius: 8px;
                font-weight: 600; font-size: 13px;
            }
            QPushButton:hover  { background: #2f7fff; }
            QPushButton:disabled { background: #333; color: #888; }
            QPushButton#secondary {
                background: transparent; color: #a0a3a8;
                border: 1px solid #2a2d33;
            }
            QPushButton#secondary:hover { color: #e8eaed; border-color: #444; }
            QProgressBar {
                background: #1a1d22; border: 1px solid #2a2d33;
                border-radius: 6px; height: 12px; text-align: center;
                color: #e8eaed; font-size: 11px;
            }
            QProgressBar::chunk { background: #1f6feb; border-radius: 6px; }
        """)

        self._build_ui()
        self.progress_signal.connect(self._on_progress)
        self.finished_signal.connect(self._on_finished)
        self._worker: threading.Thread | None = None

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(14)

