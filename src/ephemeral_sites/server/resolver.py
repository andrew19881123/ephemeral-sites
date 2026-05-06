"""Host → slug resolution for the wildcard subdomain static server.

Given an incoming ``Host`` header like ``demo.preview.example.com`` and a
configured ``base_domain`` of ``preview.example.com``, return ``"demo"``.
Returns ``None`` for anything that doesn't match the wildcard pattern
or produces an invalid slug (caller maps to 404).
"""

from __future__ import annotations

from ephemeral_sites.slug import InvalidSlugError, validate_slug

__all__ = ["resolve_slug_from_host"]


def resolve_slug_from_host(host: str, base_domain: str) -> str | None:
    """Return the slug if ``host`` is ``<slug>.<base_domain>``, else ``None``."""
    if not host:
        return None

    # Strip port if present.
    if ":" in host:
        host = host.split(":", 1)[0]

    host_lc = host.lower()
    base_lc = base_domain.lower().strip(".")

    suffix = "." + base_lc
    if not host_lc.endswith(suffix):
        return None
    candidate = host_lc[: -len(suffix)]
    if not candidate:
        return None
    try:
        validate_slug(candidate)
    except InvalidSlugError:
        return None
    return candidate
