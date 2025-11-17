"""
Central state machine for ScreenGuide Windows.

Orchestrates:
  hotkey / wake-word → ambient listener capture → STT → screen capture
  → web search → (optional Claude Computer Use pointing) → LLM → TTS
"""

import asyncio
import logging
import math
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from config import cfg
from ai.base_provider import BaseLLMProvider, Message
from audio.ambient_listener import AmbientListener
from screen.capture import capture_all_screens
from ui.panel import AppState
from tutor import (
    active_window_title, app_key,
    is_locate, is_multistep, is_next, is_stop, is_sensitive_window,
    is_repeat, is_journal_today, is_journal_week, is_quiz_review,
    is_identity_question,
)
from tutor_features import (
    journal, pdf_context, ocr, code_mode, lesson_recorder,
    multilang, workflow_capture, collab,
)
import skills as skills_pkg

_log = logging.getLogger("screenguide.manager")


def _ensure_ollama_running():
    """Start Ollama if it isn't already running. Waits up to 8 s for it to be ready."""
    import subprocess
    import urllib.request

    url = "http://localhost:11434/api/tags"
    for _ in range(2):
        try:
            urllib.request.urlopen(url, timeout=2)
            return  # already up
        except Exception:
            pass

