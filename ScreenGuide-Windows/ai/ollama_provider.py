import base64
from typing import AsyncIterator, List

import httpx

from ai.base_provider import BaseLLMProvider, Message
from ai.ollama_models_registry import is_vision_capable
from config import cfg


class OllamaProvider(BaseLLMProvider):
    """
    Streams responses from a local Ollama instance.

    Auto-picks the right model per call:
        • Screenshots present → cfg.get_ollama_model("vision")
        • No screenshots      → cfg.get_ollama_model("text")

    A caller may still pass an explicit `model=` to override that choice
    (e.g. the panel's manual model dropdown).
    """

    def __init__(self):
        self._base = cfg.ollama_host.rstrip("/")
        # Kept for backward compat with old code paths reading self._model
        self._model = cfg.ollama_model

    def _pick_model(self, has_screenshots: bool) -> str:
        return cfg.get_ollama_model("vision" if has_screenshots else "text")

    async def stream_response(
        self,
        user_text: str,
        screenshots_b64: List[str],
        history: List[Message],
        system_prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        # Resolution order:
        #   1. explicit `model=` arg (panel override)
        #   2. cfg vision/text slot based on attachment kind
        if model:
            chosen = model
        else:
            chosen = self._pick_model(bool(screenshots_b64))

        messages = [{"role": "system", "content": system_prompt}]

        for msg in history:
            messages.append({"role": msg.role, "content": msg.content})

