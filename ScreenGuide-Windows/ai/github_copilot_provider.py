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

    user_code = d["user_code"]
    verification_uri = d["verification_uri"]
    device_code = d["device_code"]
    interval = max(5, int(d.get("interval", 5)))
    expires_in = int(d.get("expires_in", 900))
    _log_login(f"Got device code. user_code={user_code} interval={interval}s expires_in={expires_in}s")

    print("\n" + "─" * 56)
    print("  GITHUB COPILOT LOGIN")
    print("─" * 56)
    print(f"  1. Open: {verification_uri}")
    print(f"  2. Enter code: {user_code}")
    print(f"  3. Click 'Authorize' in GitHub.")
    print("─" * 56 + "\n")

    # Notify the UI so the code is visible even when there's no terminal
    if on_code is not None:
        try:
            on_code(user_code, verification_uri)
        except Exception:
            pass

    if open_browser:
        try:
            webbrowser.open(verification_uri)
        except Exception:
            pass

    deadline = time.time() + expires_in
    poll_count = 0
    async with httpx.AsyncClient(timeout=15) as client:
        while time.time() < deadline:
            await asyncio.sleep(interval)
            poll_count += 1
            try:
                r = await client.post(
                    ACCESS_TOKEN_URL,
                    data={
                        "client_id": VSCODE_CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )
            except Exception as e:
                _log_login(f"poll #{poll_count} network error: {e}")
                continue
            if r.status_code != 200:
                _log_login(f"poll #{poll_count} HTTP {r.status_code}")
                continue
            body = r.json()
            if "access_token" in body:
                token = body["access_token"]
                _token_path().write_text(json.dumps({"access_token": token}))
                _log_login(f"✅ Signed in after {poll_count} polls. Token saved.")
                print("✅  Signed in. Token saved to", _token_path())
                # Eagerly fetch the model list so the panel reflects what
                # the user actually has access to *right now*.
                try:
                    models = await refresh_models_to_cache()
                    print(f"   Found {len(models)} chat models on your seat. "
                          f"Free: {len([m for m in models if m['multiplier']==0])}.")
                except Exception as e:
                    print(f"   (Could not refresh model list: {e})")
                return token
            if body.get("error") == "authorization_pending":
                if poll_count % 6 == 0:   # log roughly every 30s
                    _log_login(f"poll #{poll_count}: still pending…")
                continue
            if body.get("error") == "slow_down":
                interval += 5
                _log_login(f"poll #{poll_count}: slow_down — interval now {interval}s")
                continue
            if body.get("error") in ("expired_token", "access_denied"):
                _log_login(f"poll #{poll_count}: terminal error {body.get('error')}")
                raise RuntimeError(f"Copilot login failed: {body.get('error')}")
            _log_login(f"poll #{poll_count}: unexpected body keys={list(body.keys())}")

    _log_login(f"❌ Timed out after {poll_count} polls.")
    raise TimeoutError("Copilot device-flow login timed out.")


def load_github_token() -> Optional[str]:
    p = _token_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("access_token")
    except Exception:
        return None


def is_authenticated() -> bool:
    return load_github_token() is not None


# ─── Live model discovery ─────────────────────────────────────────────────────
#
# GitHub Copilot exposes `GET /models` which returns every model the user's
# Copilot seat can currently access, along with its billing multiplier
# (0 = free / does not burn a premium request, ≥1 = consumes premium quota).
#
# We auto-refresh this list whenever:
#   • cache is older than MODEL_CACHE_TTL_SECONDS
#   • user signs in via device flow
#   • user clicks "Refresh Copilot models" in the tray menu
#   • user switches to Copilot from another provider

async def fetch_copilot_token_only() -> str:
    """Exchange the GitHub OAuth token for a Copilot session token (one-shot)."""
    gh = load_github_token()
    if not gh:
        raise RuntimeError("Not signed in to GitHub Copilot.")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            COPILOT_TOKEN_URL,
            headers={
                "Authorization": f"token {gh}",
                "Editor-Version": EDITOR_VERSION,
                "Editor-Plugin-Version": EDITOR_PLUGIN,
                "User-Agent": USER_AGENT,
            },
        )
    if r.status_code == 401:
        raise RuntimeError(
            "GitHub rejected your token (401). Re-run the login from "
            "Tray → Model → Sign in to GitHub Copilot…"
        )
    if r.status_code == 403:
        raise RuntimeError(
            "Your GitHub account doesn't have an active Copilot subscription "
            "(403). Verify at https://github.com/settings/copilot — Free, Pro, "
            "and Education seats all work; the seat just needs to be active."
        )
    r.raise_for_status()
    return r.json()["token"]

