"""FastAPI dependencies for the ephemeral-sites API.

The helpers here are single-purpose so tests can override any slice:

- :func:`get_settings_dep` — returns the process-wide :class:`Settings`.
  Tests override this via ``app.dependency_overrides`` in the factory.
- :func:`get_db_conn` — opens the SQLite connection (lazy, singleton).
- :func:`get_api_keys` — parses ``settings.api_keys`` exactly once.
- :func:`require_auth` — the bearer-token gate for every write route.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, Request

from ephemeral_sites import auth, db
from ephemeral_sites.config import Settings, get_settings

__all__ = [
    "get_settings_dep",
    "get_db_conn",
    "get_api_keys",
    "require_auth",
]


# ---------------------------------------------------------------------------
# Module-local caches
# ---------------------------------------------------------------------------
#
# The API process opens exactly one DB connection and parses the API keys
# exactly once. Using module-level dicts keyed by the settings path keeps
# tests isolated (each test gets its own tmp_path settings → own cache slot).

_DB_CACHE: dict[str, sqlite3.Connection] = {}
_KEYS_CACHE: dict[str, tuple[auth.ApiKey, ...]] = {}


def get_settings_dep() -> Settings:
    """Dependency wrapper around :func:`ephemeral_sites.config.get_settings`.

    Tests override this via ``app.dependency_overrides`` to inject a
    tmp_path-backed settings object.
    """
    return get_settings()


def get_db_conn(
    settings: Settings = Depends(get_settings_dep),
) -> Iterator[sqlite3.Connection]:
    """Yield the (cached) SQLite connection for this process.

    The connection is opened lazily on first call, migrations are applied,
    and the resulting object is cached on the settings' ``db_path`` key.
    """
    conn = _DB_CACHE.get(settings.db_path)
    if conn is None:
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI's TestClient (and uvicorn workers) may dispatch the
        # request to a different thread than the one that opened the
        # pool. Python's sqlite3 default rejects cross-thread use; the
        # underlying SQLite compiled with THREADSAFE=1 is fine with it
        # as long as only one thread uses the handle at a time, which
        # our per-request FastAPI dependency guarantees. Hence the
        # explicit ``check_same_thread=False``.
        conn = sqlite3.connect(settings.db_path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Apply pragmas + migrations via the db module.
        db._apply_pragmas(conn, read_only=False)  # type: ignore[attr-defined]
        db.run_migrations(conn, backup_dir=None)
        _DB_CACHE[settings.db_path] = conn
    yield conn


def get_api_keys(
    settings: Settings = Depends(get_settings_dep),
) -> tuple[auth.ApiKey, ...]:
    """Parse ``settings.api_keys`` once and cache the result.

    Misconfiguration (empty string, duplicate names, ...) raises
    :class:`auth.InvalidApiKeysEnv` at first call — the FastAPI layer
    propagates that as a 500 via the generic handler, which is the
    fail-fast semantic we want.
    """
    cached = _KEYS_CACHE.get(settings.api_keys)
    if cached is None:
        cached = auth.parse_api_keys_env(settings.api_keys, rounds=settings.bcrypt_rounds)
        _KEYS_CACHE[settings.api_keys] = cached
    return cached


def require_auth(
    request: Request,
    keys: tuple[auth.ApiKey, ...] = Depends(get_api_keys),
) -> auth.ApiKey:
    """Authenticate the request via ``Authorization: Bearer <token>``.

    Raises :class:`auth.InvalidAuthHeader`, :class:`auth.InvalidApiKey`,
    or :class:`auth.DisabledApiKey`; the exception handlers in
    :mod:`.errors` map each to the right HTTP status.
    """
    header = request.headers.get("Authorization")
    token = auth.parse_bearer_header(header)
    key = auth.authenticate(token, keys)
    # Attach the (non-secret) key name to request state so downstream
    # logs / event_log inserts can reference it.
    request.state.api_key_name = key.name
    return key
