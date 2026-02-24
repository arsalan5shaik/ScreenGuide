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

    return await _free_deep_search(query, max_results)


def build_search_context(results: str) -> str:
    if not results.strip():
        return ""
    return (
        "\n\n[Web Search Results — ground factual / recent claims in these. "
        "Cite source numbers like [1] when you use them.]\n"
        + results
        + "\n[End of search results]\n"
    )


# ── Tavily (premium path) ─────────────────────────────────────────────────────

async def _tavily(query: str, max_results: int) -> str:
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": cfg.tavily_api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": True,
    }
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()

    parts: list[str] = []
    if data.get("answer"):
        parts.append(f"Summary: {data['answer']}")

    for i, result in enumerate(data.get("results", []), 1):
        title = result.get("title", "").strip()
        url_str = result.get("url", "")
        body = (result.get("raw_content") or result.get("content") or "").strip()
        body = body[:PAGE_CHAR_BUDGET]
        parts.append(f"[{i}] {title} — {url_str}\n{body}")

    return _truncate("\n\n".join(parts), OVERALL_CHAR_BUDGET)


# ── Free deep search: DuckDuckGo HTML + page fetch ────────────────────────────

async def _free_deep_search(query: str, max_results: int) -> str:
    sub_queries = _expand_query(query)

