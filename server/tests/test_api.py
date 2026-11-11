"""API tests against a stand-in Supabase.

A fake PostgREST/auth backend lets the real routing, auth dependency,
validation and sync merge logic run end to end without needing network access
or live credentials.
"""

import os
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "service-key")

from app import auth as auth_mod  # noqa: E402
from app import db as db_mod  # noqa: E402
from app import main as main_mod  # noqa: E402
from app.config import get_settings  # noqa: E402

GOOD_TOKEN = "good-token"
USER_ID = "11111111-2222-3333-4444-555555555555"


class FakeTable:
    """Just enough PostgREST to exercise the sync path."""

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}

    def upsert(self, payload: list[dict]):
        for row in payload:
            key = (row["user_id"], row["client_id"])
            existing = self.rows.get(key, {})
            merged = {**existing, **row}
            # The schema's trigger stamps updated_at server-side.
            merged["updated_at"] = db_mod.to_iso(time.time())
            self.rows[key] = merged

    def select(self, user_id: str, since_iso: str | None, limit: int):
        out = [r for k, r in self.rows.items() if k[0] == user_id]
        if since_iso:
            cutoff = db_mod.from_iso(since_iso)
            out = [r for r in out if db_mod.from_iso(r.get("updated_at")) > cutoff]
        out.sort(key=lambda r: r.get("updated_at", ""))
        return out[:limit]


JOURNAL = FakeTable()
SETTINGS_ROWS: dict[tuple[str, str], dict] = {}


def fake_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)

    # ── auth ──
    if url.endswith("/auth/v1/user"):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token != GOOD_TOKEN:
            return httpx.Response(401, json={"msg": "invalid token"})
        return httpx.Response(200, json={"id": USER_ID, "email": "a@b.com"})

    # ── journal ──
    if "/journal_entries" in url:
        if request.method == "POST":
            JOURNAL.upsert(request.read() and __import__("json").loads(request.read()))
            return httpx.Response(201, json=[])
        params = request.url.params
        uid = params.get("user_id", "").removeprefix("eq.")
        if params.get("select") == "client_id" and "count=exact" in request.headers.get("Prefer", ""):
            live = [r for k, r in JOURNAL.rows.items()
                    if k[0] == uid and not r.get("deleted_at")]
            return httpx.Response(
                200, json=[], headers={"content-range": f"0-0/{len(live)}"})
        since = params.get("updated_at", "")
        since_iso = since.removeprefix("gt.") if since else None
        rows = JOURNAL.select(uid, since_iso, int(params.get("limit", "500")))
        return httpx.Response(200, json=rows)

    # ── settings ──
    if "/user_settings" in url:
        import json as _json
        if request.method == "POST":
            for row in _json.loads(request.read()):
                SETTINGS_ROWS[(row["user_id"], row["key"])] = row
            return httpx.Response(201, json=[])
        uid = request.url.params.get("user_id", "").removeprefix("eq.")
        return httpx.Response(200, json=[
            {"key": k[1], "value": r["value"]}
            for k, r in SETTINGS_ROWS.items() if k[0] == uid
        ])

    return httpx.Response(404, json={"msg": f"unhandled {url}"})


@pytest.fixture(autouse=True)
def wire_fakes(monkeypatch):
    JOURNAL.rows.clear()
    SETTINGS_ROWS.clear()
    auth_mod._cache.clear()
    transport = httpx.MockTransport(fake_handler)

    real_async_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    with TestClient(main_mod.app) as c:
        yield c


AUTH = {"Authorization": f"Bearer {GOOD_TOKEN}"}


def entry(cid, q="q", a="a", created=None, deleted=False):
    return {
        "client_id": cid, "question": q, "answer": a,
        "created_at": created or time.time(), "updated_at": time.time(),
        "deleted": deleted,
    }


def test_healthz_needs_no_auth(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_auth_required(client):
    assert client.get("/v1/me").status_code == 401
    assert client.post("/v1/journal/sync", json={"entries": []}).status_code == 401


def test_bad_token_rejected(client):
    r = client.get("/v1/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_me(client):
    r = client.get("/v1/me", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["id"] == USER_ID


def test_push_then_pull_roundtrip(client):
    r = client.post("/v1/journal/sync",
                    json={"entries": [entry("c1", "where is save"),
                                      entry("c2", "how to export")],
                          "since": 0},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pushed"] == 2
    got = {e["client_id"] for e in body["entries"]}
    assert got == {"c1", "c2"}

    # A second device with since=0 sees both.
    r2 = client.post("/v1/journal/sync", json={"entries": [], "since": 0},
                     headers=AUTH)
    assert {e["client_id"] for e in r2.json()["entries"]} == {"c1", "c2"}


def test_incremental_pull_excludes_old(client):
    client.post("/v1/journal/sync", json={"entries": [entry("old")], "since": 0},
                headers=AUTH)
    time.sleep(1.1)
    watermark = time.time()
    time.sleep(0.1)
    client.post("/v1/journal/sync", json={"entries": [entry("new")], "since": 0},
                headers=AUTH)
    r = client.post("/v1/journal/sync",
                    json={"entries": [], "since": watermark}, headers=AUTH)
    ids = {e["client_id"] for e in r.json()["entries"]}
    assert "new" in ids and "old" not in ids, ids


def test_delete_propagates_as_tombstone(client):
    client.post("/v1/journal/sync", json={"entries": [entry("gone")], "since": 0},
                headers=AUTH)
    client.post("/v1/journal/sync",
                json={"entries": [entry("gone", deleted=True)], "since": 0},
                headers=AUTH)
    r = client.post("/v1/journal/sync", json={"entries": [], "since": 0},
                    headers=AUTH)
    row = next(e for e in r.json()["entries"] if e["client_id"] == "gone")
    assert row["deleted"] is True


def test_batch_limit_enforced(client):
    big = [entry(f"c{i}") for i in range(get_settings().max_batch + 1)]
    r = client.post("/v1/journal/sync", json={"entries": big, "since": 0},
                    headers=AUTH)
    assert r.status_code == 413


def test_oversized_text_rejected(client):
    bad = entry("x")
    bad["answer"] = "z" * 25_000
    r = client.post("/v1/journal/sync", json={"entries": [bad], "since": 0},
                    headers=AUTH)
    assert r.status_code == 422


def test_settings_roundtrip(client):
    r = client.put("/v1/settings",
                   json={"values": {"vision_model": "qwen2.5vl:3b",
                                    "send_count": 1}},
                   headers=AUTH)
    assert r.status_code == 200
    assert r.json()["values"]["vision_model"] == "qwen2.5vl:3b"
    r2 = client.get("/v1/settings", headers=AUTH)
    assert r2.json()["values"]["send_count"] == 1


def test_user_cannot_reach_another_users_rows(client):
    """The caller never supplies user_id — it comes from the token only."""
    client.post("/v1/journal/sync", json={"entries": [entry("mine")], "since": 0},
                headers=AUTH)
    JOURNAL.rows[("other-user", "theirs")] = {
        "user_id": "other-user", "client_id": "theirs",
        "question": "secret", "answer": "secret",
        "created_at": db_mod.to_iso(time.time()),
        "updated_at": db_mod.to_iso(time.time()),
    }
    r = client.post("/v1/journal/sync", json={"entries": [], "since": 0},
                    headers=AUTH)
    ids = {e["client_id"] for e in r.json()["entries"]}
    assert ids == {"mine"}, f"leaked another user's rows: {ids}"
