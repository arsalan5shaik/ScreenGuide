"""Server configuration, read from the environment.

Nothing here has a secret as its default. On Render these come from the
service's Environment tab; locally from server/.env, which is gitignored.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Local development convenience. On Render there is no .env — the platform
# injects real environment variables, and load_dotenv simply finds nothing.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class ConfigError(RuntimeError):
    """Raised at startup when a required setting is missing."""


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise ConfigError(
            f"{key} is not set. On Render add it under the service's "
            f"Environment tab; locally put it in server/.env "
            f"(copy server/.env.example)."
        )
    return val


class Settings:
    def __init__(self) -> None:
        # e.g. https://cuhdmuwglcyzmchirwki.supabase.co
        self.supabase_url: str = _require("SUPABASE_URL").rstrip("/")
        # Publishable. Used only to call Supabase's auth endpoints.
        self.supabase_anon_key: str = _require("SUPABASE_ANON_KEY")
        # Bypasses RLS — must never reach the desktop app or the repo.
        self.supabase_service_key: str = _require("SUPABASE_SERVICE_ROLE_KEY")

        self.port: int = int(os.getenv("PORT", "8000"))
        self.log_level: str = os.getenv("LOG_LEVEL", "info")
        # Seconds a validated token is trusted without re-asking Supabase.
        # Short enough that a revoked session stops working quickly.
        self.token_cache_ttl: int = int(os.getenv("TOKEN_CACHE_TTL", "60"))
        # Largest sync batch accepted in one request.
        self.max_batch: int = int(os.getenv("MAX_SYNC_BATCH", "500"))
        self.allowed_origins: list[str] = [
            o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")
            if o.strip()
        ]

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url}/rest/v1"

    @property
    def auth_url(self) -> str:
        return f"{self.supabase_url}/auth/v1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
