"""
LM Studio provider.

LM Studio exposes an OpenAI-compatible REST server (default:
http://localhost:1234/v1) once you press "Start Server" in its Developer
tab. This provider talks to that endpoint directly over HTTP so we don't
need the `openai` SDK's base_url plumbing — same approach as
ollama_provider.py, kept dependency-free and consistent with the rest of
this file's style.

No API key is required for local use; LM Studio ignores the field.
"""

import json
from typing import AsyncIterator, List

import httpx

from ai.base_provider import BaseLLMProvider, Message
from config import cfg


class LMStudioProvider(BaseLLMProvider):
    """
    Streams responses from a local LM Studio server (OpenAI-compatible
    /v1/chat/completions endpoint).

    Model selection: LM Studio serves whichever model is currently loaded
    in the app. cfg.lmstudio_model, if set, is sent explicitly (useful if
    you keep several models loaded); otherwise we ask LM Studio to use
    whatever's active via a placeholder id, which it accepts.
    """

    def __init__(self):
        self._base = cfg.lmstudio_host.rstrip("/")
        self._model = cfg.lmstudio_model

    async def stream_response(
        self,
        user_text: str,
        screenshots_b64: List[str],
        history: List[Message],
        system_prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        chosen = model or self._model or "local-model"

        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            messages.append({"role": msg.role, "content": msg.content})

