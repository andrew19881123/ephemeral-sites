"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["SiteResponse", "ErrorResponse"]


class SiteResponse(BaseModel):
    """Response body for PUT/POST/GET/PATCH /api/v1/sites/{slug}."""

    slug: str
    url: str
    created_at: str
    updated_at: str
    expires_at: str | None
    size_bytes: int
    files_count: int
    # Plaintext delete token: returned ONLY in the create/replace response.
    # The DB stores only the bcrypt hash. Never echoed again.
    delete_token: str
    spa_mode: bool
    password_protected: bool
    allow_indexing: bool
    labels: list[str] | None = None


class ErrorResponse(BaseModel):
    """Uniform error payload. All non-2xx responses use this shape.

    - ``error``: machine-readable slug (e.g. ``"invalid_zip"``) — stable,
      safe to branch on.
    - ``detail``: human-readable string — never contains file paths,
      secrets, or stack traces (log hygiene per master spec §7.6).
    - ``request_id``: echoes the ``X-Request-ID`` response header so
      users can correlate with server logs.
    """

    error: str
    detail: str
    request_id: str
