"""Unit tests for server helpers: host resolver, SPA detector, headers."""

from __future__ import annotations


def test_resolve_slug_from_host_strips_suffix():
    from ephemeral_sites.server.resolver import resolve_slug_from_host

    assert resolve_slug_from_host("demo.preview.example.com", "preview.example.com") == "demo"


def test_resolve_slug_from_host_strips_port():
    from ephemeral_sites.server.resolver import resolve_slug_from_host

    assert resolve_slug_from_host("demo.preview.test:8080", "preview.test") == "demo"


def test_resolve_slug_from_host_case_insensitive():
    from ephemeral_sites.server.resolver import resolve_slug_from_host

    # slug portion lowercased
    assert resolve_slug_from_host("DEMO.Preview.Test", "preview.test") == "demo"


def test_resolve_slug_from_host_rejects_non_wildcard():
    from ephemeral_sites.server.resolver import resolve_slug_from_host

    assert resolve_slug_from_host("api.preview.test", "other.domain") is None


def test_resolve_slug_from_host_invalid_slug_returns_none():
    from ephemeral_sites.server.resolver import resolve_slug_from_host

    # "ab" is only 2 chars — invalid slug regex
    assert resolve_slug_from_host("ab.preview.test", "preview.test") is None


def test_resolve_slug_no_subdomain_returns_none():
    from ephemeral_sites.server.resolver import resolve_slug_from_host

    # host equals the base domain → no slug
    assert resolve_slug_from_host("preview.test", "preview.test") is None


def test_is_asset_path_recognises_extensions():
    from ephemeral_sites.server.spa import is_asset_path

    assert is_asset_path("/static/app.js")
    assert is_asset_path("/main.css")
    assert is_asset_path("/images/logo.png")


def test_is_asset_path_recognises_prefixes():
    from ephemeral_sites.server.spa import is_asset_path

    assert is_asset_path("/static/anything")
    assert is_asset_path("/assets/x/y/z")
    assert is_asset_path("/_ephemeral/info")


def test_is_asset_path_defaults_false_for_app_routes():
    from ephemeral_sites.server.spa import is_asset_path

    assert not is_asset_path("/users/42")
    assert not is_asset_path("/dashboard")
    assert not is_asset_path("/")


def test_apply_security_headers_default_noindex():
    from starlette.responses import Response

    from ephemeral_sites.server.headers import apply_security_headers

    r = Response()
    apply_security_headers(r, allow_indexing=False)
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "Referrer-Policy" in r.headers
    assert "Content-Security-Policy" in r.headers
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"


def test_apply_security_headers_omits_robots_when_indexing_allowed():
    from starlette.responses import Response

    from ephemeral_sites.server.headers import apply_security_headers

    r = Response()
    apply_security_headers(r, allow_indexing=True)
    assert "X-Robots-Tag" not in r.headers
