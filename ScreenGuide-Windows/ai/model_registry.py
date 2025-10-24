"""
Live model discovery + caching for Claude, OpenAI, and Gemini.

Each provider exposes a "list models" endpoint we hit on demand:
  • Anthropic:  GET /v1/models                    (key in x-api-key)
  • OpenAI:     GET /v1/models                    (key in Authorization)
  • Gemini:     GET /v1beta/models?key=...        (key in query)

Cached per-provider to %LOCALAPPDATA%\\ScreenGuide\\models_<provider>.json with a
30-day TTL — long enough that you don't refetch constantly, short enough
that new model releases land within a month without manual refresh.

GitHub Copilot has its own (separate) implementation in github_copilot_provider.py
because Copilot's flow is more complex (token exchange + per-seat filtering).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from config import cfg


CACHE_TTL_SECONDS = 30 * 24 * 60 * 60   # 30 days


# Curated fallback lists — used when the live endpoint is unreachable AND
# the on-disk cache is empty. Reasonable defaults so ScreenGuide still works
# offline / on first run before refresh completes.
_FALLBACKS: dict[str, list[dict]] = {
    "claude": [
        {"id": "claude-sonnet-4-6",          "label": "Claude Sonnet 4.6", "vision": True},
        {"id": "claude-opus-4-7",            "label": "Claude Opus 4.7",   "vision": True},
        {"id": "claude-haiku-4-5-20251001",  "label": "Claude Haiku 4.5",  "vision": True},
    ],
    "openai": [
        {"id": "gpt-4o",        "label": "GPT-4o",       "vision": True},
        {"id": "gpt-4o-mini",   "label": "GPT-4o mini",  "vision": True},
        {"id": "gpt-4-turbo",   "label": "GPT-4 Turbo",  "vision": True},
    ],
    "gemini": [
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "vision": True},
        {"id": "gemini-2.5-pro",   "label": "Gemini 2.5 Pro",   "vision": True},
        {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "vision": True},
    ],
}

