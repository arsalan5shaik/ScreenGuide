"""ScreenGuide cloud sync API.

Deployed on Render; backed by Supabase Postgres. The desktop app authenticates
against Supabase directly and sends the resulting access token here as a
bearer token — this service never sees a password, and never trusts a user id
supplied by the caller.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.auth import User, current_user
from app.config import Settings, get_settings
from app.db import SupabaseDB, from_iso, to_iso
from app.models import (
    JournalEntry, MeResponse, SettingsPayload, SettingsResponse,
    SyncRequest, SyncResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
_log = logging.getLogger("screenguide.api")

_db: SupabaseDB | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db
    settings = get_settings()
    _db = SupabaseDB(settings)
    await _db.start()
    _log.info("connected to %s", settings.supabase_url)
    try:
        yield
    finally:
        await _db.close()
        _db = None


app = FastAPI(
    title="ScreenGuide Sync",
    version="1.0.0",
    description="Account and journal sync for the ScreenGuide desktop app.",
    lifespan=lifespan,
)

def _cors_origins() -> list[str]:
    """CORS matters little here — the desktop client is not a browser — but a
    misconfigured value shouldn't stop the service booting."""
    try:
        return get_settings().allowed_origins or ["*"]
    except Exception:
        return ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def db() -> SupabaseDB:
    if _db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server is still starting up.",
        )
    return _db


# ── Health ────────────────────────────────────────────────────────────────

@app.get("/healthz", tags=["meta"])
async def healthz():
    """Liveness. Render pings this; it must not require a database."""
    return {"ok": True, "service": "screenguide-sync", "time": time.time()}


@app.get("/readyz", tags=["meta"])
async def readyz():
    """Readiness — includes the database round-trip."""
    ok = await db().ping()
    if not ok:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not reachable.",
        )
    return {"ok": True, "database": "reachable", "time": time.time()}


# ── Account ───────────────────────────────────────────────────────────────

@app.get("/v1/me", response_model=MeResponse, tags=["account"])
async def me(user: User = Depends(current_user)):
    count = await db().count_entries(user.id)
    return MeResponse(id=user.id, email=user.email, journal_count=count)


# ── Journal sync ──────────────────────────────────────────────────────────

@app.post("/v1/journal/sync", response_model=SyncResponse, tags=["journal"])
async def sync_journal(
    payload: SyncRequest,
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
):
    """Push local changes, then pull everything newer than `since`.

    Last-write-wins on updated_at. Push happens first so a row the client is
    sending doesn't come straight back in the same response as an older copy.
    """
    if len(payload.entries) > settings.max_batch:
        raise HTTPException(
            413,   # Content Too Large; the constant was renamed in Starlette
            detail=(f"Too many entries in one sync "
                    f"({len(payload.entries)} > {settings.max_batch}). "
                    f"Send them in smaller batches."),
        )

    rows = []
    for e in payload.entries:
        rows.append({
            "client_id": e.client_id,
            "question": e.question,
            "answer": e.answer,
            "app_key": e.app_key,
            "window_title": e.window_title,
            "provider": e.provider,
            "model": e.model,
            "created_at": to_iso(e.created_at),
            "deleted_at": to_iso(e.updated_at or e.created_at) if e.deleted else None,
        })
    pushed = await db().upsert_entries(user.id, rows)

    # +1s of slack on the watermark: Postgres timestamps have sub-second
    # precision and a strict `>` comparison against the previous server_time
    # can skip a row written inside the same tick as the last sync.
    remote = await db().entries_since(
        user.id, max(0.0, payload.since - 1.0), settings.max_batch,
    )
    out = [
        JournalEntry(
            client_id=r["client_id"],
            question=r.get("question") or "",
            answer=r.get("answer") or "",
            app_key=r.get("app_key") or "",
            window_title=r.get("window_title") or "",
            provider=r.get("provider") or "",
            model=r.get("model") or "",
            created_at=from_iso(r.get("created_at")),
            updated_at=from_iso(r.get("updated_at")),
            deleted=bool(r.get("deleted_at")),
        )
        for r in remote
    ]
    return SyncResponse(
        entries=out,
        server_time=time.time(),
        pushed=pushed,
        pulled=len(out),
    )


# ── Settings sync ─────────────────────────────────────────────────────────

@app.get("/v1/settings", response_model=SettingsResponse, tags=["settings"])
async def get_settings_values(user: User = Depends(current_user)):
    return SettingsResponse(values=await db().get_settings(user.id),
                            server_time=time.time())


@app.put("/v1/settings", response_model=SettingsResponse, tags=["settings"])
async def put_settings_values(
    payload: SettingsPayload,
    user: User = Depends(current_user),
):
    await db().put_settings(user.id, payload.values)
    return SettingsResponse(values=await db().get_settings(user.id),
                            server_time=time.time())
