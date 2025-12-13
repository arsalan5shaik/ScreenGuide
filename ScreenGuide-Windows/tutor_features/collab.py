"""
Live collaboration — share a ScreenGuide session with a friend over WebRTC.

Status: SKELETON. The mechanism is laid out, but a working build needs:
  1. A signalling server (WebSocket relay so peers can find each other)
     — easiest free option: PeerJS Cloud or a 50-line Cloudflare Worker.
  2. Implement the data-channel send/recv loop using `aiortc`.
  3. Wire CompanionManager.sig_response_chunk + sig_point_at to broadcast,
     and consume incoming messages on the receiver side.

Why it's a skeleton: WebRTC + NAT traversal + state sync needs careful
testing across two machines on different networks — we can't validate that
in a single coding session. Ship this when you have time to iterate.

How users would use it once finished:
    ┌─ HOST ─────────────────────────┐    ┌─ FRIEND ──────────────────────┐
    │ Tray → Live Session → Start    │    │ Tray → Live Session → Join    │
    │ Tray shows code:  BLU-X4F      │    │ Pop-up: "Enter code:"         │
    │ Friend clicks Join              │    │ Types  BLU-X4F                │
    │ Both ScreenGuides now share:         │    │ Both ScreenGuides now share:       │
    │   • LLM responses               │    │   • LLM responses             │
    │   • Pointer coordinates         │    │   • Pointer coordinates       │
    │   • Q&A history                 │    │   • Q&A history               │
    └────────────────────────────────┘    └────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import secrets
import string
from typing import Callable, Optional


def generate_code() -> str:
    """6-char human-readable code, e.g. 'BLU-X4F'."""
    alphabet = string.ascii_uppercase + string.digits
    a = "".join(secrets.choice(alphabet) for _ in range(3))
    b = "".join(secrets.choice(alphabet) for _ in range(3))
    return f"{a}-{b}"


class CollabSession:
    """Stub for a peer-to-peer session.

    When `aiortc` is installed and a signalling URL is configured, this
    becomes a real WebRTC data-channel session. Until then, calling start()
    just generates a code and logs that the session would have started.
    """

