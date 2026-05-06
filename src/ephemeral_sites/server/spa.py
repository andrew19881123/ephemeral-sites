"""SPA fallback heuristic.

A request to an arbitrary path on a ``spa_mode=true`` site that matches
neither an existing file nor an "asset-like" path falls back to
``index.html`` (client-side routing). This module owns the decision of
what counts as "asset-like".
"""

from __future__ import annotations

__all__ = ["is_asset_path", "ASSET_EXTENSIONS", "ASSET_PREFIXES"]


ASSET_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".js",
        ".mjs",
        ".css",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".avif",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".map",
        ".json",
        ".xml",
        ".txt",
        ".pdf",
    }
)


ASSET_PREFIXES: tuple[str, ...] = ("/static/", "/assets/", "/_ephemeral/")


def is_asset_path(path: str) -> bool:
    """True iff ``path`` looks like a static asset (never SPA-fallback)."""
    lower = path.lower()
    for p in ASSET_PREFIXES:
        if lower.startswith(p):
            return True
    # Trailing extension check.
    dot = lower.rfind(".")
    if dot == -1 or dot < lower.rfind("/"):
        return False
    return lower[dot:] in ASSET_EXTENSIONS
