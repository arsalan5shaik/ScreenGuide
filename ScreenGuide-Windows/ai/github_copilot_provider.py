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

