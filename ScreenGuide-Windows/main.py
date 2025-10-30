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

