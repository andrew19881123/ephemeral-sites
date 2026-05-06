"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SiteResponse",
    "SiteMetadataResponse",
    "PatchSiteRequest",
    "ListSitesResponse",
    "ErrorResponse",
]


class SiteResponse(BaseModel):
    """Response body for PUT/POST create/replace.

    Includes the plaintext ``delete_token`` which is shown exactly once.
    """

    slug: str
    url: str
    created_at: str
    updated_at: str
    expires_at: str | None
    size_bytes: int
    files_count: int
    delete_token: str
    spa_mode: bool
    password_protected: bool
    allow_indexing: bool
    labels: list[str] | None = None


class SiteMetadataResponse(BaseModel):
    """Response body for GET/PATCH/LIST — same shape as :class:`SiteResponse`
    minus ``delete_token`` (which never leaves the DB after the initial
    create/replace) plus ``hits`` and ``last_hit`` (static-server counters
    from master spec §5.4)."""

    slug: str
    url: str
    created_at: str
    updated_at: str
    expires_at: str | None
    size_bytes: int
    files_count: int
    spa_mode: bool
    password_protected: bool
    allow_indexing: bool
    labels: list[str] | None = None
    hits: int = 0
    last_hit: str | None = None


class PatchSiteRequest(BaseModel):
    """Body of PATCH /api/v1/sites/{slug}.

    Every field optional. Per master spec §5.6:
    - ``ttl_seconds=-1`` → permanent; positive values reset expires_at
      (non-additive).
    - ``password=None``  → remove existing password.
    - ``password=""``    → 400 (caller mistake, fail loud).
    - ``labels=None``    → keep existing. To clear, pass ``[]``.
    """

    model_config = ConfigDict(extra="ignore")

    ttl_seconds: int | None = None
    password: str | None = Field(default=None)
    allow_indexing: bool | None = None
    labels: list[str] | None = None

    # Sentinel tracking: Pydantic v2 exposes ``model_fields_set`` which the
    # handler uses to distinguish "not in body" from "explicitly null".


class ListSitesResponse(BaseModel):
    """Response body for GET /api/v1/sites."""

    total: int
    items: list[SiteMetadataResponse]


class ErrorResponse(BaseModel):
    """Uniform error payload. All non-2xx responses use this shape."""

    error: str
    detail: str
    request_id: str
