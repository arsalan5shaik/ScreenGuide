"""Supabase authentication for the desktop app.

The app talks to Supabase's auth endpoints directly rather than routing
sign-in through our own server: the password then never touches our
infrastructure, and session refresh is Supabase's problem rather than
something to reimplement.

Tokens are cached under %LOCALAPPDATA%. The file is written with an
owner-only ACL — it holds a refresh token, which is a long-lived credential.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import httpx

_log = logging.getLogger("screenguide.auth")

# Supabase refuses weaker passwords anyway; failing locally gives a better
# message than a 422 from the API.
MIN_PASSWORD = 6


class AuthError(RuntimeError):
    """Sign-in failed for a reason worth showing the user."""


@dataclass
class Session:
    access_token: str
    refresh_token: str
    expires_at: float          # unix seconds
    user_id: str
    email: str

    @property
    def expired(self) -> bool:
        # Refresh a minute early so a long sync can't start on a token that
        # dies mid-flight.
        return time.time() >= self.expires_at - 60


def _state_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / "ScreenGuide"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path() -> Path:
    return _state_dir() / "session.json"


def _lock_down(path: Path) -> None:
    """Restrict the token file to the current user.

    Files under %LOCALAPPDATA% inherit permissions that can include wider
    groups; a refresh token is a credential and shouldn't rely on that.
    """
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r",
             "/grant:r", f"{os.environ.get('USERNAME', '')}:F"],
            check=False, capture_output=True, timeout=10,
        )
    except Exception:
        pass        # best effort — never block sign-in on an ACL tweak


def load_session() -> Optional[Session]:
    try:
        raw = json.loads(_session_path().read_text(encoding="utf-8"))
        return Session(**raw)
    except (OSError, ValueError, TypeError):
        return None


def save_session(s: Optional[Session]) -> None:
    path = _session_path()
    if s is None:
        try:
            path.unlink()
        except OSError:
            pass
        return
    try:
        path.write_text(json.dumps(asdict(s)), encoding="utf-8")
        _lock_down(path)
    except OSError as e:
        _log.warning("could not persist session: %s", e)


class SupabaseAuth:
    def __init__(self, url: str, anon_key: str):
        self._url = url.rstrip("/")
        self._key = anon_key

    @property
    def configured(self) -> bool:
        return bool(self._url and self._key)

    def _headers(self) -> dict:
        return {"apikey": self._key, "Content-Type": "application/json"}

    def _post(self, path: str, payload: dict, params: dict | None = None) -> dict:
        try:
            r = httpx.post(f"{self._url}/auth/v1{path}", json=payload,
                           params=params, headers=self._headers(), timeout=20)
        except httpx.HTTPError as e:
            raise AuthError(f"Couldn't reach the sign-in service: {e}") from e
        if r.status_code >= 400:
            raise AuthError(self._explain(r))
        return r.json()

    @staticmethod
    def _explain(r: httpx.Response) -> str:
        try:
            body = r.json()
        except ValueError:
            return f"Sign-in failed ({r.status_code})."
        msg = (body.get("msg") or body.get("error_description")
               or body.get("message") or body.get("error") or "")
        low = msg.lower()
        if "invalid login" in low or r.status_code == 400 and "credential" in low:
            return "Wrong email or password."
        if "already registered" in low or "already exists" in low:
            return "That email already has an account — sign in instead."
        if "confirm" in low and "email" in low:
            return ("Check your inbox and confirm the address before "
                    "signing in.")
        return msg or f"Sign-in failed ({r.status_code})."

    @staticmethod
    def _to_session(data: dict) -> Session:
        token = data.get("access_token")
        if not token:
            # Sign-up with email confirmation returns a user but no session.
            raise AuthError(
                "Account created. Check your email to confirm the address, "
                "then sign in."
            )
        user = data.get("user") or {}
        return Session(
            access_token=token,
            refresh_token=data.get("refresh_token", ""),
            expires_at=time.time() + float(data.get("expires_in", 3600)),
            user_id=user.get("id", ""),
            email=user.get("email", ""),
        )

    def sign_in(self, email: str, password: str) -> Session:
        data = self._post("/token", {"email": email, "password": password},
                          params={"grant_type": "password"})
        return self._to_session(data)

    def sign_up(self, email: str, password: str) -> Session:
        if len(password) < MIN_PASSWORD:
            raise AuthError(
                f"Password must be at least {MIN_PASSWORD} characters.")
        data = self._post("/signup", {"email": email, "password": password})
        return self._to_session(data)

    def refresh(self, session: Session) -> Session:
        if not session.refresh_token:
            raise AuthError("Session has no refresh token — sign in again.")
        data = self._post("/token", {"refresh_token": session.refresh_token},
                          params={"grant_type": "refresh_token"})
        return self._to_session(data)

    def sign_out(self, session: Session) -> None:
        try:
            httpx.post(
                f"{self._url}/auth/v1/logout",
                headers={**self._headers(),
                         "Authorization": f"Bearer {session.access_token}"},
                timeout=10,
            )
        except httpx.HTTPError:
            pass        # local sign-out still proceeds
