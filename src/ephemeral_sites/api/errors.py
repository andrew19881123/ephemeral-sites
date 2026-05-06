"""Exception-to-HTTP mapping for the ephemeral-sites API.

Every domain exception from the business modules (auth, slug, validator,
quota, storage) maps to a specific HTTP status + an :class:`ErrorResponse`
body. The ``detail`` string is always sanitized: no file paths, no token
echoes, no stack traces (master spec §7.6 log hygiene).

Custom exceptions local to the API layer:

- :class:`PayloadTooLarge` (413) — upload exceeded ``max_zip_size``
- :class:`InvalidTtl` (400) — ``ttl_seconds`` out of allowed range
- :class:`MalformedField` (400) — ``runtime_config`` / ``labels`` not valid JSON
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ephemeral_sites import auth, quota, slug, storage, validator

from .middleware import REQUEST_ID_HEADER
from .models import ErrorResponse

__all__ = [
    "PayloadTooLarge",
    "InvalidTtl",
    "MalformedField",
    "register_exception_handlers",
]

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API-local exceptions
# ---------------------------------------------------------------------------


class PayloadTooLarge(Exception):
    """Upload body exceeded ``max_zip_size``. Maps to HTTP 413."""


class InvalidTtl(Exception):
    """``ttl_seconds`` outside the allowed range. Maps to HTTP 400."""


class MalformedField(Exception):
    """A JSON-typed form field (``runtime_config``, ``labels``) was not
    valid JSON. Maps to HTTP 400."""

    def __init__(self, field: str) -> None:
        super().__init__(f"field '{field}' is not valid JSON")
        self.field = field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or request.headers.get(REQUEST_ID_HEADER, "")


def _error_json(
    request: Request,
    *,
    status: int,
    error: str,
    detail: str,
) -> JSONResponse:
    body = ErrorResponse(
        error=error,
        detail=detail,
        request_id=_request_id(request),
    ).model_dump()
    response = JSONResponse(status_code=status, content=body)
    # RequestIdMiddleware also sets the response header, but error handlers can
    # short-circuit the middleware chain; set it here for safety.
    if _request_id(request):
        response.headers[REQUEST_ID_HEADER] = _request_id(request)
    return response


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_invalid_auth_header(request: Request, exc: Exception) -> JSONResponse:
    return _error_json(
        request,
        status=401,
        error="invalid_auth_header",
        detail="missing or malformed Authorization header",
    )


async def _handle_invalid_api_key(request: Request, exc: Exception) -> JSONResponse:
    return _error_json(
        request,
        status=401,
        error="invalid_api_key",
        detail="bearer token does not match any known API key",
    )


async def _handle_disabled_api_key(request: Request, exc: Exception) -> JSONResponse:
    return _error_json(
        request,
        status=403,
        error="disabled_api_key",
        detail="this API key has been disabled",
    )


async def _handle_invalid_slug(request: Request, exc: Exception) -> JSONResponse:
    return _error_json(
        request,
        status=400,
        error="invalid_slug",
        detail="slug does not match ^[a-z0-9][a-z0-9-]{2,62}$",
    )


async def _handle_validation_error(
    request: Request, exc: validator.ValidationError
) -> JSONResponse:
    # Log hygiene: include reason_code (safe taxonomy constant) but NOT the
    # raw detail (which may echo attacker-controlled paths).
    return _error_json(
        request,
        status=400,
        error="invalid_zip",
        detail=f"archive rejected: {exc.reason_code}",
    )


async def _handle_quota_exceeded(request: Request, exc: quota.QuotaExceeded) -> JSONResponse:
    return _error_json(
        request,
        status=507,
        error="quota_exceeded",
        detail=(
            f"admitting this upload would exceed the global storage quota "
            f"(current={exc.current_used}, incoming={exc.incoming}, "
            f"max={exc.max_total})"
        ),
    )


async def _handle_extraction_error(request: Request, exc: storage.ExtractionError) -> JSONResponse:
    log.exception("extraction failed", exc_info=exc)
    return _error_json(
        request,
        status=500,
        error="extraction_failed",
        detail="internal error during archive extraction",
    )


async def _handle_payload_too_large(request: Request, exc: PayloadTooLarge) -> JSONResponse:
    return _error_json(
        request,
        status=413,
        error="payload_too_large",
        detail="upload exceeds max_zip_size",
    )


async def _handle_invalid_ttl(request: Request, exc: InvalidTtl) -> JSONResponse:
    return _error_json(
        request,
        status=400,
        error="invalid_ttl",
        detail=str(exc),
    )


async def _handle_malformed_field(request: Request, exc: MalformedField) -> JSONResponse:
    return _error_json(
        request,
        status=400,
        error="malformed_field",
        detail=f"field '{exc.field}' is not valid JSON",
    )


async def _handle_request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI raises this on missing Form fields / wrong content type.
    return _error_json(
        request,
        status=422,
        error="request_validation",
        detail="request body failed validation",
    )


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error_json(
        request,
        status=exc.status_code,
        error=f"http_{exc.status_code}",
        detail=str(exc.detail),
    )


async def _handle_uncaught(request: Request, exc: Exception) -> JSONResponse:
    log.exception("uncaught exception", exc_info=exc)
    return _error_json(
        request,
        status=500,
        error="internal_error",
        detail="an internal error occurred",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register every domain exception handler on ``app``."""
    app.add_exception_handler(auth.InvalidAuthHeader, _handle_invalid_auth_header)
    app.add_exception_handler(auth.InvalidApiKey, _handle_invalid_api_key)
    app.add_exception_handler(auth.DisabledApiKey, _handle_disabled_api_key)
    app.add_exception_handler(slug.InvalidSlugError, _handle_invalid_slug)
    app.add_exception_handler(validator.ValidationError, _handle_validation_error)
    app.add_exception_handler(quota.QuotaExceeded, _handle_quota_exceeded)
    app.add_exception_handler(storage.ExtractionError, _handle_extraction_error)
    app.add_exception_handler(PayloadTooLarge, _handle_payload_too_large)
    app.add_exception_handler(InvalidTtl, _handle_invalid_ttl)
    app.add_exception_handler(MalformedField, _handle_malformed_field)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_uncaught)
