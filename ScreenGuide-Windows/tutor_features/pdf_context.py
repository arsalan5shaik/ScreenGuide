"""
PDF / document context.

Drag a PDF onto the ScreenGuide panel → its text is extracted and held as context
for the next question. *"Explain page 3 of my chemistry notes"* works because
the extracted text is appended to the system prompt.

Backend: pypdf (pure Python, no native deps). For .docx use python-docx.
For .txt / .md we just read the file. Anything else → plain bytes preview.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# Ceiling per attachment so we don't blow past LLM context windows.
MAX_CHARS_PER_DOC = 60_000


def extract_text(path: str | Path) -> str:
    """Best-effort text extraction. Returns empty string on failure."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    suf = p.suffix.lower()

    if suf in (".txt", ".md", ".csv", ".log", ".py", ".js", ".ts",
               ".json", ".yaml", ".yml", ".html", ".xml", ".sql"):
        try:
            return p.read_text(encoding="utf-8", errors="replace")[:MAX_CHARS_PER_DOC]
        except Exception:
            return ""

    if suf == ".pdf":
        try:
            from pypdf import PdfReader   # type: ignore
        except ImportError:
            return f"(install `pypdf` to read {p.name})"
        try:
            reader = PdfReader(str(p))
            chunks = []
            total = 0
            for i, page in enumerate(reader.pages):
                t = (page.extract_text() or "").strip()
                if not t:
                    continue
                chunk = f"\n[Page {i + 1}]\n{t}"
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_CHARS_PER_DOC:
                    break
            return "".join(chunks)[:MAX_CHARS_PER_DOC]
        except Exception as e:
            return f"(failed to read PDF: {e})"

