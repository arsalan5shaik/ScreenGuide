"""
Whisper.cpp STT — same engine [Handy](https://github.com/cjpais/Handy) uses.

3-5× faster than faster-whisper on the same hardware because it ships with
GPU acceleration (CUDA on NVIDIA, Vulkan on AMD/Intel) and uses quantised
GGML weights instead of CTranslate2.

Setup:
    pip install pywhispercpp

The first call auto-downloads the model GGML file (~150 MB for `base.en`)
into ~/.cache/whisper.cpp/. After that it's fully offline.

If pywhispercpp isn't installed, this provider raises ImportError so the
manager can fall back to faster-whisper without crashing.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import wave
from pathlib import Path
from typing import Optional

from audio.stt.base_stt import BaseSTT
from audio.capture import pcm16_to_wav, trim_silence
from config import cfg


# Model size:  tiny / base / small / medium / large
# NOTE: do NOT default to a `.en` suffix — those are English-only and will
# mistranscribe every other language as garbled English-sounding text.
# WHISPERCPP_MODEL in .env lets you pick your own; WHISPER_MODEL is reused
# as a fallback so users don't need to configure the same thing twice.
DEFAULT_MODEL = os.getenv("WHISPERCPP_MODEL", "") or cfg.whisper_model or "base"


class WhisperCppSTT(BaseSTT):
    """Local STT via whisper.cpp + pywhispercpp."""

    def __init__(self, model: Optional[str] = None):
        try:
            from pywhispercpp.model import Model    # type: ignore
        except ImportError as e:
            raise ImportError(
                "whisper.cpp not installed. Run:  pip install pywhispercpp"
            ) from e

