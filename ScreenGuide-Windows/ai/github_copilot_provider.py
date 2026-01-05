"""
GitHub Copilot provider — uses the device-flow OAuth handshake (same flow
VS Code / nvim-copilot use) so students don't need to paste an API key.

Default model is `gpt-4o-mini` — it's on Copilot's *no premium request* tier
at the time of writing, so Free/Pro/Student seats don't burn premium quota.

Auth flow (one-time, ~30 seconds):
    python -m ai.github_copilot_provider login
    → prints a 9-char user code + opens https://github.com/login/device
    → you paste the code, click "Authorize"
    → token cached to %LOCALAPPDATA%\\ScreenGuide\\github_token.json

Chat flow (every call):
    GitHub token  → exchange for short-lived Copilot token (cached to ~25 min)
                  → stream from https://api.githubcopilot.com/chat/completions

Note: this uses the same public client_id VS Code ships with. It's unofficial
— GitHub has not published a public Copilot Chat API — but it's the de-facto
standard path dozens of community clients (copilot.vim, copilot.lua, aider,
etc.) use. Your Copilot subscription is consumed normally.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import AsyncIterator, List, Optional

import httpx

from ai.base_provider import BaseLLMProvider, Message


VSCODE_CLIENT_ID = "Iv1.b507a08c87ecfe98"   # Public VS Code Copilot client id
DEVICE_CODE_URL  = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_CHAT_URL   = "https://api.githubcopilot.com/chat/completions"
COPILOT_MODELS_URL = "https://api.githubcopilot.com/models"

# Fallback used only if the live /models endpoint is unreachable AND the local
# cache is empty. Real list is fetched from GitHub on first run + every login.
FALLBACK_MODEL = "gpt-4o-mini"
MAX_TOKENS = 1024

# Cache TTL — refetch /models if cache is older than this.
MODEL_CACHE_TTL_SECONDS = 6 * 60 * 60   # 6 hours

# Editor identity. Bumping these every release year matches what VS Code's
# Copilot Chat extension actually sends — older values get 400'd.
EDITOR_VERSION = "vscode/1.96.0"
EDITOR_PLUGIN  = "copilot-chat/0.23.1"
USER_AGENT     = "GitHubCopilotChat/0.23.1"


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / "ScreenGuide"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _token_path() -> Path:
    return _data_dir() / "github_token.json"


def _models_cache_path() -> Path:
    return _data_dir() / "copilot_models.json"


# ─── Device-flow login ────────────────────────────────────────────────────────

def _login_log_path() -> Path:
    return _data_dir() / "copilot_login.log"


def _log_login(line: str) -> None:
    """Append a timestamped line to the login log so we can debug failures
    after the fact (the user can grab this from %LOCALAPPDATA%\\ScreenGuide\\)."""
    try:
        from datetime import datetime
        with open(_login_log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {line}\n")
    except Exception:
        pass


async def device_login(open_browser: bool = True,
                       on_code: "Optional[callable]" = None) -> str:
    """Run the GitHub device-code OAuth flow. Returns the github access token.

    on_code(user_code, verification_uri) — optional callback fired once the
    device code is known, before we start polling. The UI uses this to display
    the code in the panel instead of (or in addition to) the terminal.
    """
    _log_login("=== device_login() started ===")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            DEVICE_CODE_URL,
            data={"client_id": VSCODE_CLIENT_ID, "scope": "read:user"},
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        d = r.json()

