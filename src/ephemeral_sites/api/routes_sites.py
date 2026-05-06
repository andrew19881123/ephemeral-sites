"""PUT /api/v1/sites/{slug} — the primary upsert endpoint.

See ``docs/steps/step-8-api-put-upsert.md`` for the master contract.

The handler orchestrates the six already-green business modules:

    validate_slug → parse form fields → stream upload → validate_zip →
    check_quota → extract_site → INSERT/UPSERT sites → INSERT event_log

All domain exceptions (InvalidSlugError, ValidationError, QuotaExceeded,
ExtractionError, PayloadTooLarge, InvalidTtl) propagate to the handlers
registered by :func:`errors.register_exception_handlers`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi import Path as PathParam

from ephemeral_sites import auth, quota, storage, validator
from ephemeral_sites.config import Settings

from .deps import get_db_conn, get_settings_dep, require_auth
from .errors import InvalidTtl, MalformedField, PayloadTooLarge
from .models import SiteResponse

__all__ = ["router"]

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sites", tags=["sites"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with second resolution."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_expires_at(*, ttl_seconds: int, now_iso: str) -> str | None:
    """Given a positive ttl, return ``now + ttl`` as ISO string; else ``None``."""
    if ttl_seconds == -1:
        return None
    now_dt = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return (now_dt + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_ttl(ttl_seconds: int, settings: Settings) -> None:
    """Raise :class:`InvalidTtl` if ``ttl_seconds`` is out of range."""
    if ttl_seconds == -1:
        if not settings.allow_permanent:
            raise InvalidTtl("permanent TTL (-1) is not allowed by configuration")
        return
    if ttl_seconds < settings.min_ttl_seconds:
        raise InvalidTtl(f"ttl_seconds must be >= {settings.min_ttl_seconds} (or -1 for permanent)")
    if ttl_seconds > settings.max_ttl_seconds:
        raise InvalidTtl(f"ttl_seconds must be <= {settings.max_ttl_seconds}")


def _parse_json_field(raw: str | None, field: str) -> object | None:
    """Parse a JSON-typed form field; empty/None → None; malformed → 400."""
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MalformedField(field) from exc


async def _stream_upload_to_tempfile(upload: UploadFile, max_bytes: int, workdir: Path) -> Path:
    """Copy ``upload`` to a fresh temp file, bailing out at ``max_bytes``.

    Raises :class:`PayloadTooLarge` if the accumulated bytes exceed the cap.
    On any exception the temp file is unlinked before re-raising.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(prefix="upload-", suffix=".zip", dir=str(workdir))
    tmp_path = Path(tmp_path_str)
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await upload.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PayloadTooLarge()
                out.write(chunk)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise
    return tmp_path


def _existing_created_at(conn: sqlite3.Connection, slug_value: str) -> str | None:
    row = conn.execute("SELECT created_at FROM sites WHERE slug = ?", (slug_value,)).fetchone()
    return None if row is None else row[0]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.put(
    "/{slug}",
    response_model=SiteResponse,
    status_code=200,
    responses={
        400: {"description": "Invalid slug, ZIP, TTL, or JSON field"},
        401: {"description": "Missing or invalid Authorization header"},
        403: {"description": "API key disabled"},
        413: {"description": "Upload exceeds max_zip_size"},
        507: {"description": "Global storage quota exhausted"},
    },
)
async def put_site(
    request: Request,
    slug: str = PathParam(..., description="Site slug (regex-validated)"),  # noqa: B008
    file: UploadFile = File(...),  # noqa: B008
    ttl_seconds: int = Form(None),
    password: str | None = Form(None),
    spa_mode: bool = Form(True),
    runtime_config: str | None = Form(None),
    allow_indexing: bool = Form(False),
    labels: str | None = Form(None),
    api_key: auth.ApiKey = Depends(require_auth),
    settings: Settings = Depends(get_settings_dep),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> SiteResponse:
    """Upsert a site. See master spec §4.2 + step-8 mini-spec §2.7."""
    # 1. Slug (400 on invalid).
    from ephemeral_sites.slug import validate_slug  # local import to avoid collision

    validate_slug(slug)

    # 2. Form fields: ttl defaulting, JSON parse.
    effective_ttl = settings.default_ttl_seconds if ttl_seconds is None else ttl_seconds
    _validate_ttl(effective_ttl, settings)

    parsed_runtime_config = _parse_json_field(runtime_config, "runtime_config")
    parsed_labels = _parse_json_field(labels, "labels")
    if password == "":
        raise InvalidTtl("password, if present, must be non-empty")  # reuse 400 slug

    # 3. Stream upload to temp file; enforce max_zip_size mid-body.
    tmp_dir = Path(settings.sites_root).parent / "tmp"
    tmp_path = await _stream_upload_to_tempfile(file, settings.max_zip_size, tmp_dir)

    try:
        # 4. Validate ZIP.
        v_config = validator.ValidatorConfig(
            max_zip_size=settings.max_zip_size,
            max_files_per_site=settings.max_files_per_site,
            max_decompression_ratio=settings.max_decompression_ratio,
            allowed_extensions=frozenset(settings.allowed_extensions),
        )
        with tmp_path.open("rb") as fh:
            validation = validator.validate_zip(fh, v_config)

        # 5. Quota check — use validator's uncompressed total as the estimate.
        current_used = quota.sum_active_sites_bytes(conn)
        quota.check_quota(
            current_used=current_used,
            incoming=validation.total_uncompressed_size,
            max_total=settings.max_total_storage_bytes,
        )

        # 6. Generate delete token + optional password hash.
        token_plain, token_hash = auth.generate_delete_token(rounds=settings.bcrypt_rounds)
        password_hash = (
            auth.hash_secret(password, rounds=settings.bcrypt_rounds) if password else None
        )

        # 7. Extract to disk (atomic swap).
        runtime_config_serialized = (
            json.dumps(parsed_runtime_config) if parsed_runtime_config is not None else None
        )
        with tmp_path.open("rb") as zip_stream:
            extraction = storage.extract_site(
                sites_root=settings.sites_root,
                slug=slug,
                zip_source=zip_stream,
                validation=validation,
                runtime_config=runtime_config_serialized,
                lock_dir=settings.lock_dir,
            )

        # 8. DB transaction — UPSERT + event_log.
        now_iso = _iso_utc_now()
        prior_created_at = _existing_created_at(conn, slug)
        is_create = prior_created_at is None

        created_at = prior_created_at if prior_created_at is not None else now_iso
        updated_at = now_iso
        expires_at = _compute_expires_at(ttl_seconds=effective_ttl, now_iso=now_iso)

        labels_json = json.dumps(parsed_labels) if parsed_labels is not None else None
        password_hash_str = password_hash.decode("utf-8") if password_hash is not None else None
        delete_token_hash_str = token_hash.decode("utf-8")

        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO sites (
                        slug, path, created_at, updated_at, expires_at,
                        size_bytes, files_count, password_hash, delete_token_hash,
                        spa_mode, allow_indexing, hits, last_hit,
                        created_by, labels, runtime_config
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        path = excluded.path,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at,
                        size_bytes = excluded.size_bytes,
                        files_count = excluded.files_count,
                        password_hash = excluded.password_hash,
                        delete_token_hash = excluded.delete_token_hash,
                        spa_mode = excluded.spa_mode,
                        allow_indexing = excluded.allow_indexing,
                        labels = excluded.labels,
                        runtime_config = excluded.runtime_config
                    """,
                    (
                        slug,
                        str(extraction.site_path),
                        created_at,
                        updated_at,
                        expires_at,
                        extraction.total_bytes_written,
                        extraction.files_written,
                        password_hash_str,
                        delete_token_hash_str,
                        1 if spa_mode else 0,
                        1 if allow_indexing else 0,
                        api_key.name,
                        labels_json,
                        runtime_config_serialized,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO event_log (slug, event, timestamp, api_key, metadata)
                    VALUES (?, ?, ?, ?, NULL)
                    """,
                    (
                        slug,
                        "created" if is_create else "replaced",
                        now_iso,
                        api_key.name,
                    ),
                )
        except Exception:
            log.exception(
                "DB transaction failed after filesystem swap for slug=%s "
                "(site dir now orphaned, cleanup will reap)",
                slug,
            )
            raise

        return SiteResponse(
            slug=slug,
            url=f"https://{slug}.{settings.base_domain}",
            created_at=created_at,
            updated_at=updated_at,
            expires_at=expires_at,
            size_bytes=extraction.total_bytes_written,
            files_count=extraction.files_written,
            delete_token=token_plain,
            spa_mode=spa_mode,
            password_protected=password_hash is not None,
            allow_indexing=allow_indexing,
            labels=list(parsed_labels) if isinstance(parsed_labels, list) else None,
        )
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
