"""Cloud sync for the journal.

The local SQLite journal stays authoritative while offline. Sync is a
push-then-pull exchange with the Render API, merged last-write-wins on
updated_at. Nothing here blocks the UI: callers run it on a worker thread.

Deliberately simple. Last-write-wins can lose a concurrent edit to the same
entry on two machines, but journal entries are append-mostly — the realistic
conflict is "two devices created different rows", which this handles exactly
right because identity is a client-minted UUID.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from config import cfg
from sync.auth import AuthError, Session, SupabaseAuth, load_session, save_session
from tutor_features import journal

_log = logging.getLogger("screenguide.sync")

BATCH = 400          # below the server's 500 cap, with headroom


@dataclass
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error:
            return self.error
        if not (self.pushed or self.inserted or self.updated):
            return "Already up to date."
        bits = []
        if self.pushed:
            bits.append(f"{self.pushed} sent")
        if self.inserted:
            bits.append(f"{self.inserted} new")
        if self.updated:
            bits.append(f"{self.updated} updated")
        return "Synced — " + ", ".join(bits) + "."


def _state_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / "ScreenGuide"
    d.mkdir(parents=True, exist_ok=True)
    return d / "sync_state.json"


def _load_watermark(user_id: str) -> float:
    """Last server_time successfully synced, per account.

    Keyed by user id so signing into a different account doesn't inherit
    the previous one's watermark and silently skip its history.
    """
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return float(data.get(user_id, 0.0))
    except (OSError, ValueError, TypeError):
        return 0.0


def _save_watermark(user_id: str, ts: float) -> None:
    try:
        path = _state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data[user_id] = ts
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as e:
        _log.warning("could not save sync watermark: %s", e)


class SyncEngine:
    """Owns the session and talks to the sync API."""

    def __init__(self):
        self._auth = SupabaseAuth(cfg.supabase_url, cfg.supabase_anon_key)
        self._session: Optional[Session] = load_session()

    # ── Account ───────────────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        """False when the build has no backend wired up."""
        return bool(cfg.sync_api_url) and self._auth.configured

    @property
    def signed_in(self) -> bool:
        return self._session is not None

    @property
    def email(self) -> str:
        return self._session.email if self._session else ""

    def sign_in(self, email: str, password: str) -> None:
        self._session = self._auth.sign_in(email.strip(), password)
        save_session(self._session)

    def sign_up(self, email: str, password: str) -> None:
        self._session = self._auth.sign_up(email.strip(), password)
        save_session(self._session)

    def sign_out(self) -> None:
        if self._session:
            self._auth.sign_out(self._session)
        self._session = None
        save_session(None)

    def _token(self) -> str:
        if self._session is None:
            raise AuthError("Not signed in.")
        if self._session.expired:
            _log.info("refreshing expired session")
            self._session = self._auth.refresh(self._session)
            save_session(self._session)
        return self._session.access_token

    # ── Sync ──────────────────────────────────────────────────────────────

    def sync(self, progress: Optional[Callable[[str], None]] = None) -> SyncResult:
        """Run one full push/pull exchange. Safe to call repeatedly."""
        def say(msg: str):
            if progress:
                try:
                    progress(msg)
                except Exception:
                    pass

        if not self.configured:
            return SyncResult(error="Cloud sync isn't configured in this build.")
        if not self.signed_in:
            return SyncResult(error="Sign in to sync.")

        try:
            token = self._token()
        except AuthError as e:
            # Refresh token is dead — make the user sign in rather than
            # retrying forever against a credential that can't work.
            self._session = None
            save_session(None)
            return SyncResult(error=f"{e} Please sign in again.")

        user_id = self._session.user_id if self._session else ""
        since = _load_watermark(user_id)
        result = SyncResult()

        try:
            while True:
                local = journal.changed_since(since, limit=BATCH)
                say(f"Sending {len(local)} entr{'y' if len(local) == 1 else 'ies'}…")
                payload = {
                    "since": since,
                    "entries": [self._to_wire(r) for r in local],
                }
                data = self._post("/v1/journal/sync", payload, token)

                result.pushed += int(data.get("pushed", 0))
                incoming = data.get("entries", [])
                result.pulled += len(incoming)
                say(f"Merging {len(incoming)}…")
                for row in incoming:
                    what = journal.apply_remote(row)
                    if what in ("inserted", "updated", "skipped"):
                        setattr(result, what, getattr(result, what) + 1)

                since = float(data.get("server_time", time.time()))
                _save_watermark(user_id, since)

                # Both sides drained means we're done; a full page either way
                # means there may be more.
                if len(local) < BATCH and len(incoming) < BATCH:
                    break
        except AuthError as e:
            return SyncResult(error=str(e))
        except httpx.HTTPError as e:
            return SyncResult(error=f"Couldn't reach the sync server: {e}")
        except Exception as e:
            _log.exception("sync failed")
            return SyncResult(error=f"Sync failed: {e}")

        return result

    def _post(self, path: str, payload: dict, token: str) -> dict:
        url = cfg.sync_api_url.rstrip("/") + path
        r = httpx.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {token}"},
            # Render's free tier cold-starts; the first request after idle
            # can take the better part of a minute.
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0),
        )
        if r.status_code == 401:
            raise AuthError("Session rejected by the server.")
        if r.status_code >= 400:
            detail = r.text[:300]
            raise RuntimeError(f"Server returned {r.status_code}: {detail}")
        return r.json()

    @staticmethod
    def _to_wire(row: dict) -> dict:
        return {
            "client_id": row.get("client_id") or "",
            "question": (row.get("question") or "")[:20000],
            "answer": (row.get("answer") or "")[:20000],
            "app_key": (row.get("app_key") or "")[:512],
            "window_title": (row.get("window_title") or "")[:512],
            "provider": (row.get("provider") or "")[:512],
            "model": (row.get("model") or "")[:512],
            "created_at": float(row.get("created_at") or 0),
            "updated_at": float(row.get("updated_at")
                                or row.get("created_at") or 0),
            "deleted": bool(row.get("deleted_at")),
        }

    def account_info(self) -> dict:
        """Ask the server what it has, for the Account tab."""
        if not (self.configured and self.signed_in):
            return {}
        try:
            token = self._token()
            r = httpx.get(
                cfg.sync_api_url.rstrip("/") + "/v1/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(connect=10.0, read=90.0,
                                      write=30.0, pool=10.0),
            )
            if r.status_code >= 400:
                return {"error": f"{r.status_code}"}
            return r.json()
        except Exception as e:
            return {"error": str(e)}
