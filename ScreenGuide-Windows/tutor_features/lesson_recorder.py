"""
Lesson recording.

Captures ScreenGuide lessons as:
  • MP4 video — primary monitor, 8 fps, ScreenGuide's pointer overlaid
  • Markdown transcript — Q&A + timestamps next to the video

Output:  ~/Documents/ScreenGuide Lessons/<timestamp>/

Backend: imageio-ffmpeg (auto-bundles ffmpeg, no system install needed).
"""

from __future__ import annotations

import io
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import mss
from PIL import Image


FPS = 8


class LessonRecorder:
    """Headless screen recorder. Markdown transcript built up alongside."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._writer = None
        self._md_lines: list[str] = []
        self._t0: float = 0.0
        self._out_dir: Optional[Path] = None
        self.is_recording = False

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> Optional[Path]:
        """Begin recording. Returns the output directory or None on failure."""
        if self.is_recording:
            return self._out_dir

        try:
            import imageio_ffmpeg   # noqa: F401  (required by imageio[ffmpeg])
            import imageio
        except ImportError:
            return None

