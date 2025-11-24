"""
Knowledge Journal + Spaced Repetition.

Every Q&A ScreenGuide has gets logged to a SQLite db at:
    %LOCALAPPDATA%\\ScreenGuide\\journal.db

Voice queries that surface this:
    "what did I learn today"            → today's entries
    "what did I learn this week"        → past 7 days
    "show me my journal"                → all-time
    "quiz me on what I learned"         → spaced-repetition pull

Spaced repetition uses the SM-2 lite algorithm:
    intervals (days):  1, 3, 7, 14, 30
    each entry has `next_review_at` and `streak` columns
    "correct" answer  → streak +1, push next_review out
    "wrong"  answer   → reset streak to 0, due tomorrow
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


_INTERVALS_DAYS = (1, 3, 7, 14, 30, 60, 120)


def _db_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / "ScreenGuide"
    d.mkdir(parents=True, exist_ok=True)
    return d / "journal.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      REAL NOT NULL,
            app_key         TEXT,
            window_title    TEXT,
            question        TEXT NOT NULL,
            answer          TEXT NOT NULL,
            provider        TEXT,
            model           TEXT,
            streak          INTEGER DEFAULT 0,
            next_review_at  REAL,
            tags            TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_entries_created "
        "ON entries (created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_entries_due "
        "ON entries (next_review_at) WHERE next_review_at IS NOT NULL"
    )
    return conn

