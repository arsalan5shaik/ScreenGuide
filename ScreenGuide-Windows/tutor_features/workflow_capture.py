"""
Workflow capture — record a sequence of clicks + keystrokes the user
performs, then let ScreenGuide explain or replay them.

Status: foundation laid. Hotkey + recording loop work. Replay layer is
intentionally a stub — playback of synthetic input is a security concern
and needs a careful UX (preview, confirm, undo) before it's safe to ship.

Use:
    capt = WorkflowCapture()
    capt.start()      # Ctrl+Alt+R → starts capture
    # ... user clicks around, types
    capt.stop()       # Ctrl+Alt+R again → stops, returns events list

Each event:  {"t": float, "kind": "click"|"key", "data": {...}}
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class WorkflowCapture:
    def __init__(self):
        self._events: list[dict] = []
        self._t0: float = 0.0
        self._is_running = False
        self._listener_kb = None
        self._listener_mouse = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> bool:
        try:
            from pynput import mouse, keyboard   # type: ignore
        except ImportError:
            return False
        self._events = []
        self._t0 = time.monotonic()
        self._is_running = True

        def on_click(x, y, button, pressed):
            if pressed and self._is_running:
                self._events.append({
                    "t": time.monotonic() - self._t0,
                    "kind": "click",
                    "data": {"x": x, "y": y, "button": str(button)},
                })

