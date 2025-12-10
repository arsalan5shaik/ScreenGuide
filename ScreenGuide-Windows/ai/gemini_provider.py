"""
Google Gemini provider — uses the REST SSE streaming API directly (no extra deps).
Default model: gemini-2.5-flash (fast, cheap, vision-capable).
"""

import json
from typing import AsyncIterator, List

import httpx

from ai.base_provider import BaseLLMProvider, Message
from config import cfg

DEFAULT_MODEL = "gemini-2.5-flash"
STREAM_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"
)


class GeminiProvider(BaseLLMProvider):

    def __init__(self):
        self._api_key = cfg.google_api_key

    async def stream_response(
        self,
        user_text: str,
        screenshots_b64: List[str],
        history: List[Message],
        system_prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        model = model or DEFAULT_MODEL

        contents = []
        for msg in history:
            role = "user" if msg.role == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg.content}],
            })

        parts: list = []
        for img_b64 in screenshots_b64:
            parts.append({
                "inline_data": {"mime_type": "image/jpeg", "data": img_b64},
            })
        parts.append({"text": user_text})
        contents.append({"role": "user", "parts": parts})

