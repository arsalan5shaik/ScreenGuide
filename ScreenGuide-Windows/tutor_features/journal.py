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
import uuid
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
    _migrate_sync_columns(conn)
    return conn


def _migrate_sync_columns(conn: sqlite3.Connection) -> None:
    """Add the columns cloud sync needs to an existing journal.

    The local database predates sync and is the user's own history, so this
    has to be additive — no rebuild, no data loss. Runs on every connect and
    is a no-op once applied.

    client_id is the identity the server syncs on; the autoincrement rowid
    can't serve because it isn't stable across machines or a local rebuild.
    """
    have = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
    added = False
    if "client_id" not in have:
        conn.execute("ALTER TABLE entries ADD COLUMN client_id TEXT")
        added = True
    if "updated_at" not in have:
        conn.execute("ALTER TABLE entries ADD COLUMN updated_at REAL")
        added = True
    if "deleted_at" not in have:
        conn.execute("ALTER TABLE entries ADD COLUMN deleted_at REAL")
        added = True
    if not added:
        return

    # Backfill: existing rows get an id and a timestamp so they're eligible
    # for the first push instead of being invisible to sync forever.
    conn.execute(
        "UPDATE entries SET updated_at = created_at WHERE updated_at IS NULL"
    )
    rows = conn.execute(
        "SELECT id FROM entries WHERE client_id IS NULL OR client_id = ''"
    ).fetchall()
    for (rid,) in rows:
        conn.execute("UPDATE entries SET client_id = ? WHERE id = ?",
                     (str(uuid.uuid4()), rid))
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_entries_client "
        "ON entries (client_id)"
    )


# ─── Logging ──────────────────────────────────────────────────────────────────

def log_qa(
    question: str,
    answer: str,
    *,
    app_key: str = "",
    window_title: str = "",
    provider: str = "",
    model: str = "",
    tags: str = "",
) -> int:
    if not question.strip() or not answer.strip():
        return -1
    now = time.time()
    # First review tomorrow by default
    next_review = now + _INTERVALS_DAYS[0] * 86400
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO entries (created_at, app_key, window_title, question, "
            "answer, provider, model, streak, next_review_at, tags, "
            "client_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (now, app_key, window_title, question.strip(), answer.strip(),
             provider, model, next_review, tags, str(uuid.uuid4()), now),
        )
        return cur.lastrowid


# ─── Query helpers ────────────────────────────────────────────────────────────

def entries_since(seconds_ago: float) -> list[dict]:
    cutoff = time.time() - seconds_ago
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE created_at >= ? AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("PRAGMA table_info(entries)").fetchall()]
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def entries_today() -> list[dict]:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return entries_since(time.time() - today_start.timestamp())


def entries_this_week() -> list[dict]:
    return entries_since(7 * 86400)


def entries_all() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def _cols(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]


def _build_filter(text: str = "", since: Optional[float] = None,
                  app_key: str = "") -> tuple[str, list]:
    """Shared WHERE clause for the history browser's search + counts."""
    # Tombstoned rows stay in the table so the delete can sync, but they are
    # gone as far as every reader is concerned.
    where, params = ["deleted_at IS NULL"], []
    if since is not None:
        where.append("created_at >= ?")
        params.append(since)
    if app_key:
        where.append("app_key = ?")
        params.append(app_key)
    if text.strip():
        # Match either side of the exchange — users look for a remembered
        # phrase from the answer as often as from their own question.
        where.append("(question LIKE ? OR answer LIKE ?)")
        like = f"%{text.strip()}%"
        params += [like, like]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def search(text: str = "", since: Optional[float] = None, app_key: str = "",
           limit: int = 200, offset: int = 0) -> list[dict]:
    """Filtered history, newest first. Powers the history browser."""
    clause, params = _build_filter(text, since, app_key)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM entries{clause} ORDER BY created_at DESC "
            f"LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        cols = _cols(conn)
    return [dict(zip(cols, r)) for r in rows]


def count(text: str = "", since: Optional[float] = None,
          app_key: str = "") -> int:
    """Total matches for a filter, ignoring limit/offset."""
    clause, params = _build_filter(text, since, app_key)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM entries{clause}", params
        ).fetchone()
    return int(row[0]) if row else 0


def distinct_apps() -> list[str]:
    """App keys that actually appear in the journal, for the filter dropdown."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT app_key FROM entries "
            "WHERE app_key IS NOT NULL AND app_key != '' ORDER BY app_key"
        ).fetchall()
    return [r[0] for r in rows]


def delete(entry_id: int) -> bool:
    """Tombstone an entry so the deletion can reach other devices.

    A hard DELETE would be undone by the next sync: the other device still
    has the row, sees the server doesn't, and pushes it straight back.
    Everything that reads entries filters deleted_at, so it stays invisible
    locally either way.
    """
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE entries SET deleted_at = ?, updated_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (now, now, entry_id),
        )
        return cur.rowcount > 0


# ─── Sync support ─────────────────────────────────────────────────────────

def changed_since(ts: float, limit: int = 500) -> list[dict]:
    """Rows modified since `ts`, tombstones included — the push set."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE COALESCE(updated_at, created_at) > ? "
            "ORDER BY COALESCE(updated_at, created_at) ASC LIMIT ?",
            (ts, limit),
        ).fetchall()
        cols = _cols(conn)
    return [dict(zip(cols, r)) for r in rows]


def get_by_client_id(client_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM entries WHERE client_id = ?", (client_id,)
        ).fetchone()
        if not row:
            return None
        return dict(zip(_cols(conn), row))


def apply_remote(entry: dict) -> str:
    """Merge one server row into the local journal.

    Last-write-wins on updated_at. Returns what happened, for the sync
    report: "inserted", "updated" or "skipped".
    """
    cid = entry.get("client_id")
    if not cid:
        return "skipped"
    incoming_ts = float(entry.get("updated_at") or entry.get("created_at") or 0)
    existing = get_by_client_id(cid)

    if existing is not None:
        local_ts = float(existing.get("updated_at")
                         or existing.get("created_at") or 0)
        if local_ts >= incoming_ts:
            return "skipped"          # local copy is newer or identical
        with _connect() as conn:
            conn.execute(
                "UPDATE entries SET question = ?, answer = ?, app_key = ?, "
                "window_title = ?, provider = ?, model = ?, updated_at = ?, "
                "deleted_at = ? WHERE client_id = ?",
                (entry.get("question", ""), entry.get("answer", ""),
                 entry.get("app_key", ""), entry.get("window_title", ""),
                 entry.get("provider", ""), entry.get("model", ""),
                 incoming_ts,
                 incoming_ts if entry.get("deleted") else None,
                 cid),
            )
        return "updated"

    if entry.get("deleted"):
        # A tombstone for a row this device never had — nothing to erase, and
        # inserting it would just add a hidden row.
        return "skipped"

    created = float(entry.get("created_at") or incoming_ts or time.time())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO entries (created_at, app_key, window_title, question, "
            "answer, provider, model, streak, next_review_at, tags, "
            "client_id, updated_at, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, '', ?, ?, NULL)",
            (created, entry.get("app_key", ""), entry.get("window_title", ""),
             entry.get("question", ""), entry.get("answer", ""),
             entry.get("provider", ""), entry.get("model", ""),
             created + _INTERVALS_DAYS[0] * 86400, cid, incoming_ts),
        )
    return "inserted"


def purge_deleted(older_than_s: float = 30 * 86400) -> int:
    """Drop tombstones that have had long enough to propagate."""
    cutoff = time.time() - older_than_s
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM entries WHERE deleted_at IS NOT NULL AND deleted_at < ?",
            (cutoff,),
        )
        return cur.rowcount


def due_for_review(limit: int = 5) -> list[dict]:
    """Spaced-repetition: pull entries whose `next_review_at` is in the past."""
    now = time.time()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM entries WHERE next_review_at IS NOT NULL AND "
            "deleted_at IS NULL AND "
            "next_review_at <= ? ORDER BY next_review_at ASC LIMIT ?",
            (now, limit),
        ).fetchall()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]


def mark_reviewed(entry_id: int, correct: bool) -> None:
    """Update streak + next review interval."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT streak FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return
        streak = row[0] or 0
        if correct:
            streak += 1
            interval_days = _INTERVALS_DAYS[min(streak, len(_INTERVALS_DAYS) - 1)]
        else:
            streak = 0
            interval_days = 1
        next_review = time.time() + interval_days * 86400
        conn.execute(
            "UPDATE entries SET streak = ?, next_review_at = ? WHERE id = ?",
            (streak, next_review, entry_id),
        )


# ─── Summarisers — used for "what did I learn today" voice replies ────────────

def summarise(entries: list[dict], header: str = "") -> str:
    if not entries:
        return f"{header}Nothing logged yet."
    lines = [header.strip()] if header else []
    for e in entries[:10]:
        when = datetime.fromtimestamp(e["created_at"]).strftime("%I:%M %p")
        q = e["question"][:80]
        lines.append(f"• {when} — {q}")
    if len(entries) > 10:
        lines.append(f"…and {len(entries) - 10} more.")
    return "\n".join(lines)
