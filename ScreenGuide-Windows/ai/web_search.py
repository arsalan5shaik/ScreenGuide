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

    seen: set[str] = set()
    hits: list[Tuple[str, str]] = []   # (title, url)

    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        search_tasks = [_ddg_html_search(client, q) for q in sub_queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        for result in search_results:
            if isinstance(result, Exception):
                continue
            for title, url in result:
                if url in seen:
                    continue
                seen.add(url)
                hits.append((title, url))
                if len(hits) >= max_results * 2:
                    break
            if len(hits) >= max_results * 2:
                break

        hits = hits[:max_results]
        if not hits:
            return ""

        fetch_tasks = [_fetch_text(client, url) for _, url in hits]
        pages = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    parts: list[str] = []
    for i, ((title, url), page) in enumerate(zip(hits, pages), 1):
        body = "" if isinstance(page, Exception) else page
        body = (body or "").strip()[:PAGE_CHAR_BUDGET]
        if not body:
            continue
        parts.append(f"[{i}] {title} — {url}\n{body}")

    return _truncate("\n\n".join(parts), OVERALL_CHAR_BUDGET)


# ── Query expansion ───────────────────────────────────────────────────────────

_STOPWORDS = {
    "what", "whats", "what's", "how", "why", "when", "where", "who",
    "is", "are", "the", "a", "an", "of", "on", "in", "to", "for",
    "do", "does", "i", "you", "me", "this", "that", "please", "tell",
    "explain", "screenguide", "hey",
}

