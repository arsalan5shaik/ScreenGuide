"""One-shot cloud setup: apply the schema, verify it, and deploy to Render.

Reads everything from server/.env (gitignored) so no credential is ever typed
on a command line, where it would land in shell history.

    python scripts/setup_cloud.py            # schema + verify, no deploy
    python scripts/setup_cloud.py --deploy   # also create the Render service
    python scripts/setup_cloud.py --verify   # check an existing setup only

Each step is skipped with a clear message when its credential is missing, so
a partial .env still gets you as far as it can.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

# The Windows console defaults to cp1252 and raises on anything
# outside it; force UTF-8 so output can't crash the setup run.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MIGRATION = ROOT / "migrations" / "001_init.sql"
RENDER_API = "https://api.render.com/v1"
REPO = os.getenv("RENDER_REPO", "https://github.com/arsalan5shaik/ScreenGuide")
SERVICE_NAME = os.getenv("RENDER_SERVICE_NAME", "screenguide-sync")

OK, BAD, INFO = "[ok]", "[!!]", "[--]"


def need(key: str) -> str | None:
    v = (os.getenv(key) or "").strip()
    return v or None


# ── Schema ────────────────────────────────────────────────────────────────

def split_statements(sql: str) -> list[str]:
    """Split SQL into statements on top-level semicolons.

    A semicolon only ends a statement when it isn't inside a $$-quoted
    function body, a line or block comment, a string literal, or a quoted
    identifier. All five appear in the migration — notably a `--` comment
    that contains a semicolon, which a naive split cuts a CREATE TABLE in
    half on, silently producing nonsense.
    """
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    tag: str | None = None          # active $$ / $name$ tag

    while i < n:
        ch = sql[i]

        if tag is not None:
            if sql.startswith(tag, i):
                buf.append(tag)
                i += len(tag)
                tag = None
            else:
                buf.append(ch)
                i += 1
            continue

        # -- line comment
        if ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end + 1
            buf.append(sql[i:end])
            i = end
            continue

        # /* block comment */  (Postgres nests these)
        if ch == "/" and sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth += 1
                    j += 2
                elif sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            buf.append(sql[i:j])
            i = j
            continue

        # 'string literal' — '' is an escaped quote
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            buf.append(sql[i:j])
            i = j
            continue

        # "quoted identifier"
        if ch == '"':
            j = sql.find('"', i + 1)
            j = n if j == -1 else j + 1
            buf.append(sql[i:j])
            i = j
            continue

        # $$ or $tag$ dollar quote
        if ch == "$":
            end = sql.find("$", i + 1)
            if end != -1 and all(c.isalnum() or c == "_"
                                 for c in sql[i + 1:end]):
                tag = sql[i:end + 1]
                buf.append(tag)
                i = end + 1
                continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)

    # Drop fragments that are only comments/whitespace — nothing to execute.
    def has_code(stmt: str) -> bool:
        stripped = []
        for line in stmt.splitlines():
            line = line.split("--", 1)[0].strip()
            if line:
                stripped.append(line)
        return bool(stripped)

    return [s for s in out if has_code(s)]


def apply_schema(dsn: str) -> bool:
    import psycopg

    sql = MIGRATION.read_text(encoding="utf-8")
    statements = split_statements(sql)
    print(f"{INFO} applying {MIGRATION.name}: {len(statements)} statements")

    try:
        conn = psycopg.connect(dsn, connect_timeout=20, autocommit=True)
    except Exception as e:
        print(f"{BAD} could not connect: {e}")
        msg = str(e).lower()
        if "password authentication" in msg:
            print("     Wrong password in SUPABASE_DB_URL. Supabase -> "
                  "Settings -> Database -> Reset database password.")
        elif "could not translate host" in msg or "timeout" in msg:
            print("     Host unreachable. Use the *Session pooler* connection "
                  "string if your network is IPv4-only.")
        return False

    failed = []
    with conn:
        for n, stmt in enumerate(statements, 1):
            head = " ".join(stmt.split())[:68]
            try:
                with conn.cursor() as cur:
                    cur.execute(stmt)
                print(f"  {OK} [{n}/{len(statements)}] {head}")
            except Exception as e:
                # Keep going: one statement failing (usually a permission on
                # auth.users) shouldn't stop the tables from being created.
                print(f"  {BAD} [{n}/{len(statements)}] {head}")
                print(f"       {e}")
                failed.append((n, head, str(e)))

    if not failed:
        print(f"{OK} schema applied cleanly")
        return True

    print(f"{BAD} {len(failed)} statement(s) failed")
    if any("auth.users" in h or "auth.users" in err for _, h, err in failed):
        print("     The trigger on auth.users needs elevated rights. If only "
              "that failed, everything still works — a profile row is simply "
              "not auto-created on signup, which nothing depends on.")
    return False


def verify_schema(dsn: str) -> bool:
    import psycopg

    want = {"profiles", "journal_entries", "user_settings"}
    try:
        with psycopg.connect(dsn, connect_timeout=20) as conn, conn.cursor() as cur:
            cur.execute(
                "select tablename, rowsecurity from pg_tables "
                "where schemaname = 'public'"
            )
            found = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute(
                "select tablename, count(*) from pg_policies "
                "where schemaname = 'public' group by tablename"
            )
            policies = dict(cur.fetchall())
    except Exception as e:
        print(f"{BAD} could not verify: {e}")
        return False

    ok = True
    for t in sorted(want):
        if t not in found:
            print(f"{BAD} table missing: {t}")
            ok = False
        elif not found[t]:
            print(f"{BAD} {t}: RLS is NOT enabled")
            ok = False
        else:
            print(f"{OK} {t}: present, RLS on, {policies.get(t, 0)} polic"
                  f"{'y' if policies.get(t, 0) == 1 else 'ies'}")
    return ok


# ── Render ────────────────────────────────────────────────────────────────

def _render(method: str, path: str, key: str, body: dict | None = None):
    req = urllib.request.Request(
        RENDER_API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:
        return 0, str(e)


def deploy_render(key: str) -> bool:
    status, owners = _render("GET", "/owners?limit=20", key)
    if status != 200 or not owners:
        print(f"{BAD} Render auth failed ({status}): {owners}")
        return False
    owner_id = owners[0]["owner"]["id"]
    print(f"{OK} Render account: {owners[0]['owner'].get('email', owner_id)}")

    status, existing = _render(
        "GET", f"/services?name={SERVICE_NAME}&limit=20", key)
    if status == 200 and existing:
        svc = existing[0]["service"]
        print(f"{INFO} service '{SERVICE_NAME}' already exists: {svc.get('serviceDetails', {}).get('url', svc['id'])}")
        print("     Pushing to GitHub redeploys it automatically.")
        return True

    env_vars = []
    for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        v = need(k)
        if not v:
            print(f"{BAD} {k} missing from server/.env -- Render needs it")
            return False
        env_vars.append({"key": k, "value": v})
    env_vars.append({"key": "PYTHON_VERSION", "value": "3.12.7"})

    body = {
        "type": "web_service",
        "name": SERVICE_NAME,
        "ownerId": owner_id,
        "repo": REPO,
        "branch": "main",
        "autoDeploy": "yes",
        "rootDir": "server",
        "serviceDetails": {
            "env": "python",
            "plan": "free",
            "region": "oregon",
            "healthCheckPath": "/healthz",
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
            },
        },
        "envVars": env_vars,
    }
    status, resp = _render("POST", "/services", key, body)
    if status not in (200, 201):
        print(f"{BAD} could not create service ({status}): {resp}")
        if status == 400 and "repo" in str(resp).lower():
            print("     Render can't see the repo. Connect your GitHub account "
                  "once at Render -> Account Settings -> GitHub, then re-run.")
        return False
    svc = resp.get("service", resp)
    url = svc.get("serviceDetails", {}).get("url", "")
    print(f"{OK} created '{SERVICE_NAME}'  id={svc.get('id')}")
    print(f"{OK} url: {url or '(assigned shortly)'}")
    print(f"{INFO} first build takes a few minutes; watch it in the dashboard")
    return True


# ── Client wiring ─────────────────────────────────────────────────────────

def write_client_env(api_url: str) -> None:
    """Point the desktop app at the deployed API."""
    client_env = ROOT.parent / "ScreenGuide-Windows" / ".env"
    url, anon = need("SUPABASE_URL"), need("SUPABASE_ANON_KEY")
    if not (url and anon):
        print(f"{INFO} skipping client .env -- SUPABASE_URL/ANON_KEY not set")
        return
    values = {"SYNC_API_URL": api_url, "SUPABASE_URL": url,
              "SUPABASE_ANON_KEY": anon}
    lines = (client_env.read_text(encoding="utf-8").splitlines(keepends=True)
             if client_env.exists() else [])
    for key, val in values.items():
        for i, line in enumerate(lines):
            if line.lstrip().startswith(f"{key}="):
                lines[i] = f"{key}={val}\n"
                break
        else:
            lines.append(f"{key}={val}\n")
    client_env.write_text("".join(lines), encoding="utf-8")
    print(f"{OK} wrote SYNC_API_URL / SUPABASE_* into {client_env}")


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true",
                    help="also create the Render web service")
    ap.add_argument("--verify", action="store_true",
                    help="only check an existing setup")
    ap.add_argument("--api-url", default="",
                    help="deployed API URL, to write into the client .env")
    args = ap.parse_args()

    print("=" * 60)
    print("ScreenGuide cloud setup")
    print("=" * 60)

    dsn = need("SUPABASE_DB_URL")
    failures = []

    if not dsn:
        print(f"{INFO} SUPABASE_DB_URL not set -- skipping schema.")
        print("     Supabase -> Project Settings -> Database -> Connection "
              "string -> URI, then put it in server/.env")
    elif args.verify:
        if not verify_schema(dsn):
            failures.append("schema verify")
    else:
        if apply_schema(dsn):
            if not verify_schema(dsn):
                failures.append("schema verify")
        else:
            failures.append("schema apply")

    if args.deploy:
        key = need("RENDER_API_KEY")
        if not key:
            print(f"{INFO} RENDER_API_KEY not set -- skipping deploy.")
            print("     Render -> Account Settings -> API Keys -> Create key")
        elif not deploy_render(key):
            failures.append("render deploy")

    if args.api_url:
        write_client_env(args.api_url.rstrip("/"))

    print("=" * 60)
    if failures:
        print("INCOMPLETE:", ", ".join(failures))
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
