"""Security headers applied to every 200 response from the static server.

See master spec §7.2. ``X-Robots-Tag`` is conditional on ``allow_indexing``.
``Cache-Control`` is NOT set here (policy differs for index / asset /
synthetic endpoints — set at the call site).
"""

from __future__ import annotations

from starlette.responses import Response

__all__ = ["apply_security_headers", "SECURITY_HEADERS"]

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src *;"
    ),
}


def apply_security_headers(response: Response, *, allow_indexing: bool) -> None:
    for k, v in SECURITY_HEADERS.items():
        response.headers[k] = v
    if not allow_indexing:
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
