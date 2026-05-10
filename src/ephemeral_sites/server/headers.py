"""Security headers applied to every 200 response from the static server.

See master spec §7.2. ``X-Robots-Tag`` is conditional on ``allow_indexing``.
``Cache-Control`` is NOT set here (policy differs for index / asset /
synthetic endpoints — set at the call site).

The CSP value is configurable per-deployment via ``Settings.csp`` (env var
``EPHEMERAL_CSP`` / Helm value ``app.csp``); the bundled default is
permissive enough to let user-uploaded sites pull resources from any HTTPS
CDN out of the box.
"""

from __future__ import annotations

from starlette.responses import Response

__all__ = ["apply_security_headers", "DEFAULT_CSP", "SECURITY_HEADERS"]

DEFAULT_CSP: str = (
    "default-src 'self' https: data: blob:; "
    "script-src 'self' https: 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' https: 'unsafe-inline'; "
    "img-src 'self' https: data: blob:; "
    "font-src 'self' https: data:; "
    "connect-src *; "
    "media-src 'self' https: data: blob:; "
    "frame-src 'self' https:; "
    "object-src 'none'; "
    "base-uri 'self';"
)

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": DEFAULT_CSP,
}


def apply_security_headers(
    response: Response,
    *,
    allow_indexing: bool,
    csp: str | None = None,
) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = csp or DEFAULT_CSP
    if not allow_indexing:
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
