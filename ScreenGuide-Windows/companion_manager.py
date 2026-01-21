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

    # API down. If an ollama process already exists, don't spawn a second
    # `ollama serve` — duplicate instances fight over the port and wedge the
    # API entirely. Just wait for the existing one below.
    already_running = False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        already_running = "ollama.exe" in out.lower()
    except Exception:
        pass

    if not already_running:
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except FileNotFoundError:
            return  # ollama not installed, provider will fail gracefully

    # Wait up to 8 s for the server to come up
    for _ in range(16):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            pass


def _build_system_prompt(
    window_title: str = "",
    lesson_step: int = 0,
    total_steps: int = 0,
    quiz_mode: bool = False,
    detected_coord: Optional[tuple] = None,
    code_active: bool = False,
    language_code: str = "en",
    extra: str = "",
) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    ctx_lines = [f"TODAY'S DATE: {today}."]
    if window_title:
        ctx_lines.append(f'ACTIVE WINDOW: "{window_title}"')
    if detected_coord:
        x, y, label = detected_coord
        ctx_lines.append(
            f"DETECTED ELEMENT (pre-computed by the pointing engine — use "
            f"this coordinate verbatim in your [POINT] tag): x={x}, y={y}, "
            f"label='{label}'. (Already normalized 0-1000.)"
        )
    if total_steps > 1:
        ctx_lines.append(
            f"LESSON PROGRESS: step {lesson_step + 1} of {total_steps}. "
            "Explain ONLY this step, then end with \"Say 'next' when ready.\""
        )

    # ── Quiz mode: dominant prompt that completely replaces normal behaviour ──
    if quiz_mode:
        return f"""You are ScreenGuide, an interactive QUIZ TUTOR. The user has
turned on Quiz Mode and wants to be tested, NOT explained to.

{chr(10).join(ctx_lines)}

ABSOLUTE QUIZ RULES (override everything else):
  • NEVER answer the user's question directly. NEVER point at UI elements.
    NEVER emit [POINT:...] tags. NEVER explain how things work.
  • If the user is greeting / starting ("hello", "what's on my screen", "begin",
    "quiz me", anything), START the quiz: ask ONE short, specific question
    about what's visible on screen — name a button, recognise an icon, predict
    what a click would do, identify the active app, etc.
  • If the user's last message looks like an ANSWER (a noun, a short phrase, a
    yes/no), evaluate it in ≤1 sentence ("Correct!" / "Close — actually..."),
    then immediately ask the NEXT question.
  • Questions should be progressively harder. Vary topic across UI literacy,
    keyboard shortcuts, what's currently visible, predicting outcomes.
  • Keep it warm and encouraging. Never lecture.
  • Format every turn as:  <one-line evaluation if applicable>  <one question>

STYLE: short, friendly, never more than 2 sentences. End every turn with a
question mark."""

    from config import _TECHNICAL_RULES
    base = cfg.custom_instructions.strip()
    base = base.replace("{{CONTEXT}}", chr(10).join(ctx_lines))
    base = base.replace("{{TODAY}}", today)
    return base + _TECHNICAL_RULES + _code_addendum(code_active) + _lang_addendum(language_code) + extra


def _code_addendum(active: bool) -> str:
    if not active:
        return ""
    from tutor_features.code_mode import code_system_prompt_addendum
    return code_system_prompt_addendum()


def _lang_addendum(code: str) -> str:
    from tutor_features.multilang import language_directive
    return language_directive(code)


def _guess_label(transcript: str) -> str:
    """Extract a 1-3 word label from a locate query for the speech bubble.
       'where is the search bar' → 'search bar' """
    t = transcript.lower().strip().rstrip("?.!")
    for kw in ("where is the ", "where's the ", "show me the ",
              "find the ", "locate the ", "click the ", "click on the ",
              "how do i click ", "how do i find ", "how do i open ",
              "point at the ", "point to the ", "highlight the "):
        if kw in t:
            tail = t.split(kw, 1)[1]
            words = tail.split()
            return " ".join(words[:3]) or "here"
    return "right here!"

