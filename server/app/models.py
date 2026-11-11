"""Request and response shapes for the sync API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

# Generous but finite. A single journal answer is prose, not a document, and
# an unbounded field is a free memory-exhaustion lever for any signed-in user.
MAX_TEXT = 20_000
MAX_SHORT = 512


class JournalEntry(BaseModel):
    """One Q&A exchange, as the client stores it."""

    client_id: str = Field(min_length=1, max_length=64)
    question: str = Field(max_length=MAX_TEXT)
    answer: str = Field(max_length=MAX_TEXT)
    app_key: str = Field(default="", max_length=MAX_SHORT)
    window_title: str = Field(default="", max_length=MAX_SHORT)
    provider: str = Field(default="", max_length=MAX_SHORT)
    model: str = Field(default="", max_length=MAX_SHORT)
    # Unix seconds. created_at is the device's; updated_at is advisory — the
    # server overwrites it on write so clock skew can't bury later edits.
    created_at: float
    updated_at: float = 0.0
    deleted: bool = False

    @field_validator("question", "answer")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return v or ""


class SyncRequest(BaseModel):
    # Only rows changed since the client's last successful sync.
    entries: list[JournalEntry] = Field(default_factory=list)
    # Unix seconds; 0 means "first sync, send me everything".
    since: float = 0.0


class SyncResponse(BaseModel):
    # Rows the client doesn't have, or has an older copy of.
    entries: list[JournalEntry]
    # Watermark to pass as `since` next time. Server clock, not client clock.
    server_time: float
    pushed: int
    pulled: int


class SettingsPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def _bounded(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 200:
            raise ValueError("too many settings keys (max 200)")
        for key in v:
            if len(key) > MAX_SHORT:
                raise ValueError(f"settings key too long: {key[:40]}…")
        return v


class SettingsResponse(BaseModel):
    values: dict[str, Any]
    server_time: float


class MeResponse(BaseModel):
    id: str
    email: Optional[str] = None
    journal_count: int = 0
