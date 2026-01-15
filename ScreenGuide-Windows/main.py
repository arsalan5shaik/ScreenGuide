"""
ScreenGuide for Windows — Entry Point.
Boots Qt, spawns overlay+panel+tray, starts ambient mic listener, binds hotkey.
"""

import os
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from config import cfg
from ui.tray import TrayManager
from ui.panel import CompanionPanel, AppState
from ui.overlay import (
    CursorOverlay, MODE_IDLE, MODE_LISTENING, MODE_THINKING, MODE_SPEAKING
)
from hotkey import GlobalHotkeyMonitor, StopHotkey
from companion_manager import CompanionManager


STATE_TO_CURSOR_MODE = {
    AppState.IDLE:      MODE_IDLE,
    AppState.LISTENING: MODE_LISTENING,
    AppState.THINKING:  MODE_THINKING,
    AppState.SPEAKING:  MODE_SPEAKING,
}


def _copilot_login_flow(tray, panel, manager):
    """Run the GitHub device-flow login in a worker thread so the UI stays live."""
    import asyncio, threading
    from ai.github_copilot_provider import device_login

    def _on_code(user_code: str, verification_uri: str):
        """Called as soon as the device code arrives — display it in the panel."""
        msg = (
            f"GitHub Copilot Sign-In\n\n"
            f"1. Visit: {verification_uri}\n"
            f"2. Enter code:  {user_code}\n"
            f"3. Click Authorize — ScreenGuide will sign in automatically."
        )
        # Show in panel (cross-thread safe via Qt signal)
        panel.show_copilot_code(user_code, verification_uri)
        tray.show_notification("GitHub Copilot — enter this code", user_code)

    def _worker():
        try:
            asyncio.run(device_login(on_code=_on_code))
            tray.show_notification(
                "GitHub Copilot",
                "Signed in! Refreshing model list…"
            )
            manager.refresh_copilot_models()
        except Exception as e:
            tray.show_notification("Copilot login failed", str(e))
            panel.show_copilot_error(str(e))

    threading.Thread(target=_worker, daemon=True).start()


def _setup_logging():
    """Rotating runtime log at %LOCALAPPDATA%\\ScreenGuide\\screenguide.log — the #1
    ask from bug reports: with no log file, 'stuck on Listening' class issues
    were undiagnosable."""
    import logging
    from logging.handlers import RotatingFileHandler

    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ScreenGuide"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "screenguide.log", maxBytes=1_000_000, backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
        ))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        logging.getLogger("screenguide").info(
            "=== ScreenGuide starting (python %s) ===", sys.version.split()[0]
        )
    except Exception:
        pass  # logging must never block startup


def main():
    _setup_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("ScreenGuide")
    app.setApplicationDisplayName("ScreenGuide - AI Companion")

    # ── Core components ───────────────────────────────────────────────────────
    manager = CompanionManager()
    panel   = CompanionPanel()
    overlay = CursorOverlay()
    tray    = TrayManager()

    # ── Wire signals ──────────────────────────────────────────────────────────

    # State changes → Panel + Tray + Cursor
    def _on_state(state: AppState):
        panel.set_state(state)
        tray.set_state_icon(state.name.lower())
        overlay.set_mode(STATE_TO_CURSOR_MODE.get(state, MODE_IDLE))

