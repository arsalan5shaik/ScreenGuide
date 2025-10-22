"""
Ollama bootstrap utilities.

Most users who hit "ScreenGuide listens but won't answer" don't have Ollama
installed (or installed it without pulling a model). This module:

  • Detects whether Ollama is running     → is_ollama_running()
  • Lists installed models                → list_installed_models()
  • Detects whether a model is pulled     → is_model_installed(name)
  • Streams a pull with a progress cb    → pull_model(name, on_progress)
  • Downloads the official Ollama setup   → download_ollama_installer(dest)
  • Launches the official Ollama setup    → run_ollama_installer(path)

Everything is sync httpx so it's safe to call from any thread.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

import httpx

from config import cfg


OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/OllamaSetup.exe"

# Default models we recommend for the free tier. Kept small so the
# download finishes in a reasonable time on a typical home connection.
DEFAULT_TEXT_MODEL = "llama3.2:3b"          # ~2 GB
DEFAULT_VISION_MODEL = "qwen2.5vl:3b"        # ~3 GB


# ─── Detection ────────────────────────────────────────────────────────────────

def is_ollama_running(timeout: float = 1.5) -> bool:
    """Return True if the Ollama HTTP server is reachable."""
    base = cfg.ollama_host.rstrip("/")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False

