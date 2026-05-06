"""FastAPI app factory for ephemeral-sites.

Tests call :func:`create_app` with a tmp-path :class:`Settings` so each
test case gets an isolated DB + sites_root. Production calls it with
default settings (env-driven).
"""

from __future__ import annotations

from fastapi import FastAPI

from ephemeral_sites.config import Settings

from .deps import get_settings_dep
from .errors import register_exception_handlers
from .middleware import RequestIdMiddleware
from .routes_sites import router as sites_router

__all__ = ["create_app"]


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Build a fully wired FastAPI app.

    Arguments:
        settings: Optional override. When provided, ``get_settings_dep``
            is overridden via ``app.dependency_overrides`` so every route
            sees the same test-scoped settings.
    """
    app = FastAPI(title="ephemeral-sites", version="0.1.0")

    # Middleware FIRST (added last → runs first when request enters).
    app.add_middleware(RequestIdMiddleware)

    # Exception handlers.
    register_exception_handlers(app)

    # Routes.
    app.include_router(sites_router)

    # Dependency override for tests.
    if settings is not None:
        app.dependency_overrides[get_settings_dep] = lambda: settings

    return app
