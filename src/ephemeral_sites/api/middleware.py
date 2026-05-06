"""HTTP middleware for the ephemeral-sites API."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

__all__ = ["RequestIdMiddleware", "REQUEST_ID_HEADER"]

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a stable request id to every request.

    Behaviour:

    - If the client sends ``X-Request-ID``, that value is reused.
    - Otherwise a fresh ``uuid4().hex`` is generated.
    - The id is stored on ``request.state.request_id`` so downstream
      code (especially exception handlers) can include it in the
      response body.
    - The same id is echoed back as an ``X-Request-ID`` response header.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
