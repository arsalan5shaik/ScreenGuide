"""
Real-time web search for ScreenGuide — tutor-grade grounding layer.

Strategy (free path, no API key required):
  1. Expand the user's question into 1-2 focused sub-queries.
  2. Hit DuckDuckGo HTML search for each to collect top result URLs.
  3. Fetch the top pages concurrently and strip to plain text.
  4. Assemble a compact, source-cited context block for the LLM.

If TAVILY_API_KEY is set, Tavily's deep-search is used instead (higher
signal-to-noise, faster, cleaner summaries).
"""

from __future__ import annotations

import asyncio
import re
from html import unescape
from typing import List, Tuple
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import httpx

from config import cfg


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_PAGES = 3
PAGE_CHAR_BUDGET = 1400            # per-page excerpt size
OVERALL_CHAR_BUDGET = 5500         # cap on the whole context block
FETCH_TIMEOUT = 6.0


# ── Public API ────────────────────────────────────────────────────────────────

async def search(query: str, max_results: int = MAX_PAGES) -> str:
    """Return a plain-text, source-cited search context."""
    query = query.strip()
    if not query:
        return ""

    if cfg.search_provider() == "tavily":
        try:
            return await _tavily(query, max_results)
        except Exception:
            pass  # fall through to free path

