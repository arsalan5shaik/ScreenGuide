import threading
import time
from typing import Callable

import keyboard

from config import cfg


# Canonical names the `keyboard` lib uses for each modifier, in the order we
# should probe them. is_pressed("ctrl") only matches LEFT ctrl on some layouts,
# so we check the sided variants too.
_MOD_ALIASES = {
    "ctrl":    ("ctrl", "left ctrl", "right ctrl"),
    "control": ("ctrl", "left ctrl", "right ctrl"),
    "alt":     ("alt", "left alt", "right alt"),
    "shift":   ("shift", "left shift", "right shift"),
    "win":     ("windows", "left windows", "right windows"),
    "windows": ("windows", "left windows", "right windows"),
    "cmd":     ("windows", "left windows", "right windows"),
}


def _is_down(token: str) -> bool:
    for alias in _MOD_ALIASES.get(token, (token,)):
        try:
            if keyboard.is_pressed(alias):
                return True
        except Exception:
            continue
    return False


def _norm_token(name: str) -> str:
    """Canonicalize a keyboard-event key name → modifier token."""
    n = (name or "").lower()
    if "ctrl" in n or n == "control":
        return "ctrl"
    if "alt" in n:
        return "alt"
    if "shift" in n:
        return "shift"
    if "windows" in n or n in ("win", "cmd", "meta"):
        return "win"
    return n


class GlobalHotkeyMonitor:
    """
    Registers a system-wide push-to-talk hotkey (default: ctrl+win).
    Fires on_press when held, on_release when released.

