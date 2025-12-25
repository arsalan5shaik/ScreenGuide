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


def is_ollama_installed() -> bool:
    """Return True if the `ollama` binary is on PATH (server may still be off)."""
    return shutil.which("ollama") is not None


def list_installed_models() -> List[str]:
    """Return the list of model tags installed locally. Empty list if Ollama is off."""
    base = cfg.ollama_host.rstrip("/")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=3.0)
        r.raise_for_status()
        data = r.json()
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def is_model_installed(name: str) -> bool:
    """Check whether a specific Ollama model tag (e.g. 'llama3.2:3b') is pulled."""
    if not name:
        return False
    installed = list_installed_models()
    # Ollama returns tags like 'llama3.2:3b'. Match by exact tag *or* base name
    # so callers can pass either 'llama3.2' or 'llama3.2:3b'.
    if name in installed:
        return True
    base = name.split(":", 1)[0]
    return any(m.split(":", 1)[0] == base for m in installed)


# ─── Pull a model with progress ───────────────────────────────────────────────

def pull_model(
    name: str,
    on_progress: Optional[Callable[[str, float], None]] = None,
    timeout: float = 1800.0,
) -> bool:
    """
    Pull an Ollama model, streaming progress.

    on_progress(status, percent) is called as the pull progresses.
        status:  human-readable string (e.g. "downloading manifest")
        percent: 0.0–100.0 (or 0.0 if unknown)

    Returns True when the pull finishes successfully.
    """
    base = cfg.ollama_host.rstrip("/")
    payload = {"name": name, "stream": True}

