"""
Rolling screenshot history.

A background thread captures the screen on a fixed interval and writes each
frame to a folder on this machine. Anything older than the retention window is
deleted on every tick, so the folder is self-limiting — it never grows past
roughly (retention / interval) files.

The point is temporal context: a single frame tells the model what is on
screen, two frames a minute apart tell it what the user just did. Questions
like "what did I just change?" or "why did that dialog appear?" need the
earlier frame to be answerable at all.

Everything stays local. Nothing here uploads, and the folder is plain JPEGs
the user can open, audit or delete by hand.
"""

from __future__ import annotations

import base64
import io
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import mss
from PIL import Image

_log = logging.getLogger("screenguide.history")

# Written as <prefix><timestamp>.jpg; the timestamp sorts lexicographically,
# so ordering never needs to stat the files.
_STAMP_FMT = "%Y%m%d-%H%M%S"
_PREFIX = "screen-"
_SUFFIX = ".jpg"


@dataclass
class Capture:
    path: Path
    taken_at: float          # unix seconds

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.taken_at)

    def as_base64_jpeg(self) -> Optional[str]:
        try:
            return base64.b64encode(self.path.read_bytes()).decode("utf-8")
        except OSError:
            return None       # pruned or locked between listing and reading


def _parse_stamp(path: Path) -> Optional[float]:
    """Timestamp encoded in a capture's filename, or None if it isn't ours.

    Tolerates the "-NNN" disambiguator appended when two captures land in the
    same second: only the leading YYYYMMDD-HHMMSS is significant. Parsing the
    whole stem would make those files unreadable here, and anything this
    function can't parse is invisible to listing *and* to pruning — it would
    sit in the folder forever.
    """
    name = path.name
    if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
        return None
    stamp = name[len(_PREFIX):-len(_SUFFIX)]
    head = stamp[:15]                      # YYYYMMDD-HHMMSS
    try:
        base = datetime.strptime(head, _STAMP_FMT).timestamp()
    except ValueError:
        return None
    # Keep same-second captures in creation order.
    tail = stamp[16:] if len(stamp) > 15 else ""
    if tail.isdigit():
        base += min(999, int(tail)) / 1000.0
    return base


def _encode(img: Image.Image, max_width: int, quality: int = 70) -> bytes:
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)),
                         Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


class ScreenshotHistory:
    """Periodic screen capture with time-based pruning."""

    def __init__(
        self,
        folder: Path,
        interval_s: int = 60,
        retention_min: int = 60,
        max_width: int = 1280,
        enabled: bool = True,
        on_capture: Optional[Callable[[Path], None]] = None,
    ):
        self.folder = Path(folder)
        self.interval_s = max(5, int(interval_s))
        self.retention_s = max(60, int(retention_min) * 60)
        self.max_width = int(max_width)
        self._enabled = bool(enabled)
        self._on_capture = on_capture

        self._stop = threading.Event()
        self._wake = threading.Event()      # lets settings changes take effect now
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="screenshot-history", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3)
        self._thread = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        """Turn capture on/off without tearing the thread down.

        Turning it off also clears what's already stored: someone disabling
        screen capture means it, and leaving a folder of screenshots behind
        would be the opposite of what they asked for.
        """
        self._enabled = bool(on)
        if not on:
            self.clear()
        self._wake.set()

    def configure(self, *, interval_s=None, retention_min=None,
                  max_width=None) -> None:
        if interval_s is not None:
            self.interval_s = max(5, int(interval_s))
        if retention_min is not None:
            self.retention_s = max(60, int(retention_min) * 60)
        if max_width is not None:
            self.max_width = int(max_width)
        self._wake.set()      # apply the new interval without waiting out the old one

    # ── Worker ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._enabled:
                try:
                    self.capture_now()
                except Exception as e:
                    # Capture can fail transiently — locked screen, display
                    # change, GPU hiccup. Never let it kill the thread.
                    self._last_error = str(e)
                    _log.warning("capture failed: %s", e)
            try:
                self.prune()
            except Exception as e:
                _log.warning("prune failed: %s", e)

            self._wake.wait(timeout=self.interval_s)
            self._wake.clear()

    # ── Operations ────────────────────────────────────────────────────────

    def capture_now(self) -> Optional[Path]:
        """Grab the virtual screen (all monitors) and write one JPEG."""
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            # mss instances are not shareable across threads — build one per call.
            with mss.mss() as sct:
                raw = sct.grab(sct.monitors[0])    # 0 = whole virtual screen
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            data = _encode(img, self.max_width)

            stamp = datetime.now().strftime(_STAMP_FMT)
            path = self.folder / f"{_PREFIX}{stamp}{_SUFFIX}"
            if path.exists():
                # Same-second capture (manual grab landing on a tick) — keep both.
                path = self.folder / f"{_PREFIX}{stamp}-{int(time.time()*1000)%1000:03d}{_SUFFIX}"
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)      # atomic: readers never see a half-written file
            self._last_error = None

        if self._on_capture:
            try:
                self._on_capture(path)
            except Exception:
                pass
        return path

    def list_captures(self, newest_first: bool = True) -> list[Capture]:
        try:
            files = list(self.folder.glob(f"{_PREFIX}*{_SUFFIX}"))
        except OSError:
            return []
        out = []
        for f in files:
            ts = _parse_stamp(f)
            if ts is None:
                continue
            out.append(Capture(path=f, taken_at=ts))
        out.sort(key=lambda c: (c.taken_at, c.path.name), reverse=newest_first)
        return out

    def recent(self, count: int) -> list[Capture]:
        """The `count` newest captures, oldest first.

        Oldest-first matters: the model should read them as a sequence ending
        at 'now', the same order a person would flip through them.
        """
        if count <= 0:
            return []
        newest = self.list_captures(newest_first=True)[:count]
        return list(reversed(newest))

    def prune(self) -> int:
        """Delete captures older than the retention window. Returns count."""
        cutoff = time.time() - self.retention_s
        removed = 0
        for cap in self.list_captures(newest_first=False):
            if cap.taken_at >= cutoff:
                break          # sorted oldest-first, so everything after is newer
            try:
                cap.path.unlink()
                removed += 1
            except OSError:
                pass
        # Sweep interrupted writes so a crashed capture can't leak disk.
        try:
            for part in self.folder.glob("*.part"):
                if time.time() - part.stat().st_mtime > 60:
                    part.unlink()
        except OSError:
            pass
        if removed:
            _log.info("pruned %d screenshot(s) older than %ds",
                      removed, self.retention_s)
        return removed

    def clear(self) -> int:
        """Delete every stored capture."""
        removed = 0
        for cap in self.list_captures():
            try:
                cap.path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    # ── Reporting (for the settings window) ───────────────────────────────

    def stats(self) -> dict:
        caps = self.list_captures(newest_first=True)
        total = 0
        for c in caps:
            try:
                total += c.path.stat().st_size
            except OSError:
                pass
        return {
            "count": len(caps),
            "bytes": total,
            "newest_age_s": caps[0].age_s if caps else None,
            "oldest_age_s": caps[-1].age_s if caps else None,
            "folder": str(self.folder),
            "enabled": self._enabled,
            "last_error": self._last_error,
        }
