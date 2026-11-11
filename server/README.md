# ScreenGuide Sync API

Account and journal sync for the ScreenGuide desktop app. FastAPI on Render,
Postgres + Auth on Supabase.

It does **not** proxy AI. Models still run locally (Ollama) or against the
user's own key, so this service costs nothing per question and holds no AI
credentials.

```
Desktop app ──1. email+password──▶ Supabase Auth ──▶ access token
            ──2. Bearer token────▶ This API ──service key──▶ Supabase Postgres
```

The app authenticates against Supabase directly, so **no password ever
reaches this server**. This server only ever sees a bearer token, asks
Supabase who it belongs to, and scopes every query to that user.

---

## 1. Database

Supabase Dashboard → **SQL Editor** → **New query** → paste all of
[`migrations/001_init.sql`](migrations/001_init.sql) → **Run**.

It is idempotent — safe to run again after edits.

Creates `profiles`, `journal_entries`, `user_settings`, the `updated_at`
triggers, a trigger that creates a profile for every new signup, and RLS
policies restricting every row to its owner.

Verify: **Table Editor** should list the three tables, each marked *RLS
enabled*.

## 2. Keys

Supabase Dashboard → **Project Settings → API Keys**. You need three values:

| Value | Where it goes | Secret? |
|---|---|---|
| Project URL (`https://<ref>.supabase.co`) | Render **and** the desktop `.env` | No |
| `anon` / publishable key | Render **and** the desktop `.env` | No — designed to ship in clients |
| `service_role` key | **Render only** | **Yes.** Bypasses RLS. Never in the app, never in git. |

If the `service_role` key is ever pasted somewhere public, rotate it in the
dashboard immediately.

## 3. Deploy to Render

Render → **New → Blueprint** → select the `ScreenGuide` repo. Render reads
[`render.yaml`](render.yaml) and will prompt for the three Supabase values,
because they're marked `sync: false` and deliberately absent from the repo.

Or without a blueprint — **New → Web Service**:

| Field | Value |
|---|---|
| Root directory | `server` |
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/healthz` |

Then add `SUPABASE_URL`, `SUPABASE_ANON_KEY` and
`SUPABASE_SERVICE_ROLE_KEY` under **Environment**.

Render injects `$PORT`; binding a fixed port makes the deploy fail its
health check.

Check it:

```bash
curl https://<your-service>.onrender.com/healthz    # liveness, no DB
curl https://<your-service>.onrender.com/readyz     # includes a DB round-trip
```

`/readyz` returning 503 means the service is up but can't reach Postgres —
usually a wrong `SUPABASE_URL` or a `service_role` key that wasn't saved.

> **Free tier:** the service sleeps after ~15 minutes idle and takes 30–60s
> to wake. The client allows 90s for this, so the first sync after a quiet
> period is slow but succeeds.

## 4. Point the app at it

In `ScreenGuide-Windows/.env`:

```env
SYNC_API_URL=https://<your-service>.onrender.com
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_ANON_KEY=<anon key>
```

Restart ScreenGuide → **⚙ Settings → Account** → create an account and sign
in. Leave `SYNC_API_URL` empty and the app stays entirely local, with the
Account tab disabled.

---

## API

All `/v1` routes require `Authorization: Bearer <supabase access token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness. No auth, no database. |
| GET | `/readyz` | Readiness, including a database round-trip. |
| GET | `/v1/me` | Current user and journal entry count. |
| POST | `/v1/journal/sync` | Push changed entries, pull everything newer than `since`. |
| GET | `/v1/settings` | Stored settings for this account. |
| PUT | `/v1/settings` | Replace/merge settings. |

Interactive docs at `/docs` once deployed.

### How sync works

Entries are identified by a client-minted UUID (`client_id`), not a row id,
so an entry survives a local database rebuild and can't collide between
machines. Each exchange pushes everything changed since the client's last
watermark, then pulls everything the server has changed since then; conflicts
resolve last-write-wins on `updated_at`, which Postgres stamps server-side so
a device with a wrong clock can't pin a row into the future.

Deletes are tombstones. A hard delete can't propagate — the other device
still has the row, notices the server doesn't, and pushes it straight back.

## Local development

```bash
cd server
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # then fill in the three Supabase values
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Tests use a stand-in Supabase and need no credentials or network:

```bash
.venv\Scripts\python -m pytest tests/ -q
```
