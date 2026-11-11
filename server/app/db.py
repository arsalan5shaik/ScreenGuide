"""Supabase Postgres access via PostgREST.

Uses the service role key, which bypasses RLS — so every query here must
constrain user_id itself. That is deliberate: the policies in the migration
exist to make the *anon* key safe in the shipped app, while this server does
its own scoping and never accepts a user_id from the caller. The only user_id
ever used is the one auth.py resolved from the bearer token.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import HTTPException, status

from app.config import Settings

_log = logging.getLogger("screenguide.db")

JOURNAL = "journal_entries"
SETTINGS = "user_settings"


def to_iso(ts: float) -> str:
    """Unix seconds → ISO-8601 UTC, which is what PostgREST wants."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def from_iso(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        # Postgres renders +00:00; fromisoformat wants that or 'Z' handled.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class SupabaseDB:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._headers = {
            "apikey": settings.supabase_service_key,
            "Authorization": f"Bearer {settings.supabase_service_key}",
            "Content-Type": "application/json",
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        # One pooled client for the process; building one per request is the
        # usual cause of socket exhaustion under load.
        self._client = httpx.AsyncClient(
            base_url=self._settings.rest_url,
            headers=self._headers,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SupabaseDB used before start()")
        return self._client

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = await self.client.request(method, path, **kw)
        except httpx.HTTPError as e:
            _log.exception("supabase request failed")
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Database unreachable: {e!s}",
            ) from e
        if r.status_code >= 400:
            # Surface Postgres's own message — "relation does not exist" is
            # the difference between a five-second fix and an afternoon.
            detail = r.text[:500]
            _log.error("supabase %s %s -> %s %s", method, path, r.status_code, detail)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=f"Database error ({r.status_code}): {detail}",
            )
        return r

    # ── Journal ───────────────────────────────────────────────────────────

    async def upsert_entries(self, user_id: str, rows: list[dict]) -> int:
        """Insert or update by (user_id, client_id). Returns rows written."""
        if not rows:
            return 0
        payload = [{**row, "user_id": user_id} for row in rows]
        await self._request(
            "POST",
            f"/{JOURNAL}",
            params={"on_conflict": "user_id,client_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
        )
        return len(payload)

    async def entries_since(self, user_id: str, since: float,
                            limit: int) -> list[dict]:
        params = {
            "user_id": f"eq.{user_id}",
            "select": ("client_id,question,answer,app_key,window_title,"
                       "provider,model,created_at,updated_at,deleted_at"),
            "order": "updated_at.asc",
            "limit": str(limit),
        }
        if since > 0:
            params["updated_at"] = f"gt.{to_iso(since)}"
        r = await self._request("GET", f"/{JOURNAL}", params=params)
        return r.json()

    async def count_entries(self, user_id: str) -> int:
        r = await self._request(
            "GET",
            f"/{JOURNAL}",
            params={"user_id": f"eq.{user_id}", "select": "client_id",
                    "deleted_at": "is.null", "limit": "1"},
            headers={"Prefer": "count=exact"},
        )
        # PostgREST reports the count in Content-Range as "0-0/N".
        rng = r.headers.get("content-range", "")
        _, _, total = rng.partition("/")
        try:
            return int(total)
        except ValueError:
            return len(r.json())

    # ── Settings ──────────────────────────────────────────────────────────

    async def get_settings(self, user_id: str) -> dict[str, Any]:
        r = await self._request(
            "GET",
            f"/{SETTINGS}",
            params={"user_id": f"eq.{user_id}", "select": "key,value"},
        )
        return {row["key"]: row["value"] for row in r.json()}

    async def put_settings(self, user_id: str, values: dict[str, Any]) -> int:
        if not values:
            return 0
        payload = [
            {"user_id": user_id, "key": k, "value": v} for k, v in values.items()
        ]
        await self._request(
            "POST",
            f"/{SETTINGS}",
            params={"on_conflict": "user_id,key"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
        )
        return len(payload)

    async def ping(self) -> bool:
        """Cheap reachability probe for /healthz."""
        try:
            await self._request(
                "GET", f"/{JOURNAL}",
                params={"select": "client_id", "limit": "1"},
            )
            return True
        except HTTPException:
            return False
