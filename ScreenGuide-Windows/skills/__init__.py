"""
Skill system — user-defined voice triggers + custom behaviours.

Drop a .py file into this folder. Each file exposes a SKILL dict at module
level:

    SKILL = {
        "name":        "Self Mode",
        "trigger":     r"(self ?mode|allow ?clicks|enable ?clicking)",
        "description": "Lets ScreenGuide click for you instead of pointing.",
        "handler":     handle_self_mode,   # async fn(manager, transcript) -> str
    }

Loading happens at startup via load_all().  Triggers are tested *before*
the LLM runs — same priority as built-in 'next' / 'stop' commands.

Status: loader + interface stable; ship your own skills here.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Awaitable, Callable, Optional

# Skill dict shape (for type hints — using TypedDict would be stricter)
# {
#     "name":        str,
#     "trigger":     str,           # regex
#     "description": str,
#     "handler":     Callable[[manager, transcript], Awaitable[str]],
# }

_loaded: list[dict] = []


def _user_skills_dir() -> Path:
    """User-level skills dir at ~/.screenguide/skills/ — survives reinstall."""
    return Path.home() / ".screenguide" / "skills"


def load_all() -> list[dict]:
    """Discover + import every skill module in this package and ~/.screenguide/skills."""
    global _loaded
    _loaded = []

    # Bundled skills (shipped with ScreenGuide)
    here = Path(__file__).parent
    for f in here.glob("*.py"):
        if f.name.startswith("_"):
            continue
        _try_import(f)

