"""FastAPI app factory for the static content server.

Serves per-site SPAs under wildcard subdomains. Given ``Host: demo.<base_domain>``,
the app resolves ``slug=demo``, looks it up in the ``sites`` DB, and returns
files from ``{sites_root}/demo/``.

See ``docs/steps/step-11-static-server.md`` for the full contract.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import json
import logging
import mimetypes
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ephemeral_sites import auth as auth_mod
from ephemeral_sites import db as db_mod
from ephemeral_sites.config import Settings, get_settings

from .headers import apply_security_headers
from .resolver import resolve_slug_from_host
from .spa import is_asset_path

__all__ = ["create_server_app"]

log = logging.getLogger(__name__)


_SITE_COLS = (
    "slug, spa_mode, allow_indexing, password_hash, expires_at, hits, last_hit, runtime_config"
)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _site_is_expired(row) -> bool:
    expires = row["expires_at"] if hasattr(row, "keys") else row[4]
    if expires is None:
        return False
    return expires < _now_iso()


def _fetch_site(conn: sqlite3.Connection, slug: str):
    return conn.execute(
        f"SELECT {_SITE_COLS} FROM sites WHERE slug = ?",  # noqa: S608
        (slug,),
    ).fetchone()


def _safe_path(site_dir: Path, url_path: str) -> Path | None:
    """Resolve ``url_path`` against ``site_dir``. Returns None if traversal."""
    cleaned = url_path.lstrip("/")
    if not cleaned:
        cleaned = "index.html"
    candidate = (site_dir / cleaned).resolve()
    try:
        candidate.relative_to(site_dir.resolve())
    except ValueError:
        return None
    return candidate


def _verify_basic_auth(auth_header: str | None, password_hash_str: str) -> bool:
    """Validate an ``Authorization: Basic ...`` header against the stored hash.

    Returns True iff the header is well-formed, the scheme is Basic, base64
    decodes cleanly, and the password (everything after the first ':')
    matches the stored bcrypt hash.
    """
    if not auth_header:
        return False
    if not auth_header.lower().startswith("basic "):
        return False
    encoded = auth_header[6:].strip()
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")
    except (binascii.Error, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    _, password = decoded.split(":", 1)
    stored = (
        password_hash_str.encode("utf-8")
        if isinstance(password_hash_str, str)
        else password_hash_str
    )
    return auth_mod.verify_secret(password, stored)


def _unauthorized_response(slug: str) -> JSONResponse:
    response = JSONResponse({"error": "unauthorized"}, status_code=401)
    response.headers["WWW-Authenticate"] = f'Basic realm="ephemeral-sites:{slug}", charset="UTF-8"'
    return response


def _response_for_file(path: Path, *, allow_indexing: bool, no_cache: bool) -> Response:
    data = path.read_bytes()
    ctype, _ = mimetypes.guess_type(path.name)
    response = Response(content=data, media_type=ctype or "application/octet-stream")
    apply_security_headers(response, allow_indexing=allow_indexing)
    if no_cache:
        response.headers["Cache-Control"] = "no-cache"
    else:
        response.headers["Cache-Control"] = "public, max-age=300"
    return response


def create_server_app(
    *,
    settings: Settings | None = None,
    db_conn: sqlite3.Connection | None = None,
) -> FastAPI:
    """Build the static-content FastAPI app.

    Both parameters are optional so that ``uvicorn ... --factory`` can
    invoke the factory with no arguments in production; tests still pass
    explicit instances so they share state with the API layer.

    When ``settings`` is ``None`` the process-wide env-driven
    :class:`Settings` is used. When ``db_conn`` is ``None`` a read-only
    SQLite connection is opened against ``settings.db_path`` (the static
    server never writes).
    """
    if settings is None:
        settings = get_settings()
    if db_conn is None:
        db_conn = db_mod.open_db(Path(settings.db_path), read_only=True)

    app = FastAPI(title="ephemeral-sites static server", version="0.1.0")

    sites_root = Path(settings.sites_root)

    @app.get("/{url_path:path}")
    async def serve(request: Request, url_path: str) -> Response:
        host = request.headers.get("host", "")
        slug = resolve_slug_from_host(host, settings.base_domain)
        if slug is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        row = _fetch_site(db_conn, slug)
        if row is None:
            return JSONResponse({"error": "not_found"}, status_code=404)

        if _site_is_expired(row):
            return JSONResponse({"error": "not_found"}, status_code=404)

        # Password gate (step 12): verify Basic auth against password_hash.
        if row["password_hash"]:
            auth_header = request.headers.get("authorization") or request.headers.get(
                "Authorization"
            )
            if not _verify_basic_auth(auth_header, row["password_hash"]):
                return _unauthorized_response(slug)

        normalized = "/" + url_path if not url_path.startswith("/") else url_path

        # Synthetic endpoints
        if normalized == "/_ephemeral/info":
            body = {
                "slug": slug,
                "expires_at": row["expires_at"],
                "hits": row["hits"] or 0,
            }
            response = JSONResponse(body)
            apply_security_headers(response, allow_indexing=False)
            response.headers["Cache-Control"] = "no-cache"
            return response

        if normalized == "/config.json":
            rc = row["runtime_config"]
            if rc is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            try:
                decoded = json.loads(rc)
            except (TypeError, ValueError):
                decoded = rc
            response = JSONResponse(decoded)
            apply_security_headers(response, allow_indexing=bool(row["allow_indexing"]))
            response.headers["Cache-Control"] = "no-cache"
            return response

        site_dir = sites_root / slug
        if not site_dir.exists():
            return JSONResponse({"error": "not_found"}, status_code=404)

        target = _safe_path(site_dir, normalized)
        if target is None:
            return JSONResponse({"error": "bad_request"}, status_code=400)

        allow_indexing = bool(row["allow_indexing"])
        spa_mode = bool(row["spa_mode"])
        is_index_like = normalized in ("/", "/index.html")

        if target.is_file():
            return _response_for_file(
                target,
                allow_indexing=allow_indexing,
                no_cache=is_index_like,
            )

        # File missing — SPA fallback?
        if spa_mode and not is_asset_path(normalized):
            index_path = site_dir / "index.html"
            if index_path.is_file():
                return _response_for_file(index_path, allow_indexing=allow_indexing, no_cache=True)

        return JSONResponse({"error": "not_found"}, status_code=404)

    return app
