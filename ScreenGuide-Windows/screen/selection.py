"""
Read whatever text the user currently has selected.

If someone highlights a paragraph and then asks "what does this mean?", the
selection *is* the question's subject. Making them describe what they've
already pointed at is the thing this is meant to avoid.

Two tiers, cheapest and safest first:

  1. UI Automation — asks the focused control for its selected text. Reads
     nothing else and touches no shared state. Works in most native controls
     and in Chromium/Firefox when accessibility is active.

  2. Clipboard round-trip — sends Ctrl+C and reads what lands. Works almost
     everywhere UIA doesn't, but the clipboard is shared state, so this path
     stashes the current contents, uses a sentinel to tell "copied nothing"
     apart from "copied the same thing again", and puts the original back.

Tier 2 can be switched off for anyone who would rather their clipboard were
never touched.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

_log = logging.getLogger("screenguide.selection")

# Longest selection passed to the model. Enough for a few pages of prose;
# past this the screenshot is the better signal anyway.
MAX_CHARS = 8000

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
# Marks "the clipboard still holds what we put there", i.e. the copy did
# nothing, which is how an empty selection is detected.
#
# Must contain no NUL: clipboard text is NUL-terminated, so a sentinel
# starting with one reads back as "" and every probe would look like a
# successful copy of empty text.
_SENTINEL = "⁣screenguide-probe⁣"

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

# Handles are pointer-sized. Without explicit restypes ctypes assumes c_int,
# which silently truncates every handle to 32 bits on a 64-bit build and makes
# each of these calls fail.
k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
k32.GlobalAlloc.restype = wintypes.HGLOBAL
k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
k32.GlobalLock.restype = wintypes.LPVOID
k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
k32.GlobalUnlock.restype = wintypes.BOOL
k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
k32.GlobalFree.restype = wintypes.HGLOBAL
u32.GetClipboardData.argtypes = [wintypes.UINT]
u32.GetClipboardData.restype = wintypes.HANDLE
u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
u32.SetClipboardData.restype = wintypes.HANDLE
u32.OpenClipboard.argtypes = [wintypes.HWND]
u32.OpenClipboard.restype = wintypes.BOOL
u32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
u32.IsClipboardFormatAvailable.restype = wintypes.BOOL


@dataclass
class Selection:
    text: str
    source: str          # "uia" | "clipboard"

    @property
    def truncated(self) -> bool:
        return len(self.text) >= MAX_CHARS


# ── Clipboard primitives ──────────────────────────────────────────────────

def _open_clipboard(retries: int = 8) -> bool:
    """The clipboard is a single global lock; another app may hold it."""
    for _ in range(retries):
        if u32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _read_clipboard_text() -> Optional[str]:
    if not _open_clipboard():
        return None
    try:
        if not u32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return None
        handle = u32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None
        ptr = k32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.c_wchar_p(ptr).value
        finally:
            k32.GlobalUnlock(handle)
    except Exception:
        return None
    finally:
        u32.CloseClipboard()


def _write_clipboard_text(text: str) -> bool:
    if not _open_clipboard():
        return False
    try:
        u32.EmptyClipboard()
        if text is None:
            return True
        buf = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buf)
        handle = k32.GlobalAlloc(_GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = k32.GlobalLock(handle)
        if not ptr:
            return False
        ctypes.memmove(ptr, buf, size)
        k32.GlobalUnlock(handle)
        # Ownership of the handle passes to the clipboard — do not free it.
        if not u32.SetClipboardData(_CF_UNICODETEXT, handle):
            k32.GlobalFree(handle)
            return False
        return True
    except Exception:
        return False
    finally:
        u32.CloseClipboard()


def _clipboard_has_nontext() -> bool:
    """True if the clipboard holds something we cannot preserve.

    Restoring only puts text back, so an image or file list would be
    destroyed by the round-trip. Detect that and decline instead.
    """
    if not _open_clipboard():
        return True            # can't inspect it, so assume it matters
    try:
        has_text = bool(u32.IsClipboardFormatAvailable(_CF_UNICODETEXT))
        # CF_BITMAP=2, CF_DIB=8, CF_HDROP=15
        has_other = any(u32.IsClipboardFormatAvailable(f) for f in (2, 8, 15))
        return has_other and not has_text
    finally:
        u32.CloseClipboard()


# ── Tier 1: UI Automation ─────────────────────────────────────────────────

def _selection_via_uia(timeout_s: float = 0.6) -> Optional[str]:
    try:
        import uiautomation as auto
    except Exception:
        return None
    try:
        auto.SetGlobalSearchTimeout(timeout_s)
        focused = auto.GetFocusedControl()
        if focused is None:
            return None
        # TextPattern only. ValuePattern would return the control's entire
        # contents rather than the selected part, which is the opposite of
        # what's wanted — a whole document instead of the highlighted line.
        fn = getattr(focused, "GetTextPattern", None)
        if fn is None:
            return None
        try:
            pattern = fn()
        except Exception:
            return None
        if pattern is None:
            return None
        try:
            ranges = pattern.GetSelection()
        except Exception:
            return None
        if not ranges:
            return None
        parts = []
        for i in range(len(ranges)):
            try:
                parts.append(ranges.GetElement(i).GetText(MAX_CHARS))
            except Exception:
                pass
        text = "".join(p for p in parts if p)
        return text if text.strip() else None
    except Exception as e:
        _log.debug("UIA selection failed: %s", e)
        return None


# ── Tier 2: clipboard round-trip ──────────────────────────────────────────

def _selection_via_clipboard(settle_s: float = 0.12) -> Optional[str]:
    if _clipboard_has_nontext():
        _log.debug("clipboard holds non-text — skipping copy probe")
        return None

    original = _read_clipboard_text()
    if not _write_clipboard_text(_SENTINEL):
        return None
    try:
        try:
            import keyboard
            keyboard.send("ctrl+c")
        except Exception as e:
            _log.debug("could not send ctrl+c: %s", e)
            return None

        # Give the foreground app a moment to service the copy.
        deadline = time.monotonic() + max(settle_s, 0.05) * 6
        got = None
        while time.monotonic() < deadline:
            time.sleep(settle_s / 3)
            current = _read_clipboard_text()
            # Still the sentinel => the app hasn't copied (yet, or at all).
            # Empty => treat as nothing copied rather than as an empty
            # selection, so a slow app doesn't read as "no selection".
            if current and current != _SENTINEL:
                got = current
                break
        return got
    finally:
        # Always hand the clipboard back, including when nothing was copied.
        _write_clipboard_text(original if original is not None else "")


# ── Public API ────────────────────────────────────────────────────────────

def get_selection(allow_clipboard: bool = True) -> Optional[Selection]:
    """The user's current selection, or None if there isn't one.

    Never raises: a failure here must not take a question down with it.
    """
    try:
        text = _selection_via_uia()
        source = "uia"
        if not (text and text.strip()) and allow_clipboard:
            text = _selection_via_clipboard()
            source = "clipboard"
        if not text:
            return None
        text = text.strip()
        if not text:
            return None
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        return Selection(text=text, source=source)
    except Exception as e:
        _log.debug("selection capture failed: %s", e)
        return None


def format_for_prompt(sel: Selection) -> str:
    """Prompt block describing the selection."""
    suffix = "\n…(truncated)" if sel.truncated else ""
    return (
        "\n\nHIGHLIGHTED TEXT: the user has selected the following on screen "
        "right now. Treat it as the subject of their question — they should "
        "not have to re-describe or paste it. If the question is vague "
        "(\"what does this mean?\", \"explain this\", \"summarise\"), it "
        "refers to this text.\n"
        "<<<SELECTION\n"
        f"{sel.text}{suffix}\n"
        "SELECTION\n"
    )
