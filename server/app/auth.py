"""Caller identity, established from a Supabase access token.

The token is validated by asking Supabase who it belongs to, rather than by
verifying a JWT signature locally. That costs a network call, but it means
the server holds no JWT signing secret, keeps working when Supabase rotates
keys, and — unlike signature checks — notices a session that has been
revoked. Results are cached briefly so a sync batch doesn't make one auth
call per request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


@dataclass(frozen=True)
class User:
    id: str
    email: Optional[str]


# token -> (User, expires_at). Bounded below so a flood of junk tokens can't
# grow it without limit.
_cache: dict[str, tuple[User, float]] = {}
_CACHE_MAX = 2048


def _cache_get(token: str) -> Optional[User]:
    hit = _cache.get(token)
    if not hit:
        return None
    user, expires = hit
    if expires < time.monotonic():
        _cache.pop(token, None)
        return None
    return user


def _cache_put(token: str, user: User, ttl: int) -> None:
    if len(_cache) >= _CACHE_MAX:
        # Cheap eviction: drop whatever is already expired, then, if that
        # freed nothing, the oldest entry.
        now = time.monotonic()
        for k in [k for k, (_, exp) in _cache.items() if exp < now]:
            _cache.pop(k, None)
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)), None)
    _cache[token] = (user, time.monotonic() + ttl)


def invalidate(token: str) -> None:
    _cache.pop(token, None)


async def verify_token(token: str, settings: Settings) -> User:
    cached = _cache_get(token)
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{settings.auth_url}/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": settings.supabase_anon_key,
                },
            )
    except httpx.HTTPError as e:
        # Upstream problem, not the caller's fault — don't tell them to log in
        # again when the right move is to retry.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not reach the auth service: {e!s} ",
        ) from e

    if r.status_code == 401:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if r.status_code >= 400:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Auth service returned {r.status_code}.",
        )

    data = r.json()
    uid = data.get("id")
    if not uid:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Auth service response had no user id.",
        )
    user = User(id=uid, email=data.get("email"))
    _cache_put(token, user, settings.token_cache_ttl)
    return user


async def current_user(
    authorization: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> User:
    """FastAPI dependency: the signed-in user, or 401."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await verify_token(token.strip(), settings)
