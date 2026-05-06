"""Integration tests for the static server (step 11)."""

from __future__ import annotations

import datetime as _dt
import io
import json as _json
import zipfile
from pathlib import Path

import pytest


@pytest.fixture()
def spa_zip_with_static() -> bytes:
    """Small SPA with index.html + static/app.js."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", b"<!doctype html><title>demo</title>")
        zf.writestr("static/app.js", b"console.log('hello');")
    return buf.getvalue()


@pytest.fixture()
def server_client(settings, api_client, auth_headers, spa_zip_with_static):
    """A TestClient for the static server app, with a `demo` site already PUT."""
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.server import app as server_app_module

    # Seed a site via the API.
    r = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", spa_zip_with_static, "application/zip")},
    )
    assert r.status_code == 200

    # Reuse the exact DB connection the API populated.
    conn = api_deps._DB_CACHE[settings.db_path]
    server_app = server_app_module.create_server_app(settings=settings, db_conn=conn)
    return TestClient(server_app)


# ---------------------------------------------------------------------------
# Master spec section 11.3 tests
# ---------------------------------------------------------------------------


def test_spa_fallback_to_index_html(server_client):
    r = server_client.get(
        "/any/nonexistent/route", headers={"Host": "demo.preview.test"}
    )
    assert r.status_code == 200
    assert b"demo" in r.content or b"<!doctype" in r.content.lower()


def test_static_asset_not_fallback(server_client):
    r = server_client.get(
        "/static/nonexistent.js", headers={"Host": "demo.preview.test"}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Basic serving
# ---------------------------------------------------------------------------


def test_get_root_returns_index_html(server_client):
    r = server_client.get("/", headers={"Host": "demo.preview.test"})
    assert r.status_code == 200
    # Cache-Control: no-cache for index.html
    assert "no-cache" in r.headers.get("Cache-Control", "")


def test_serve_existing_static_asset(server_client):
    r = server_client.get("/static/app.js", headers={"Host": "demo.preview.test"})
    assert r.status_code == 200
    assert b"console.log" in r.content
    cc = r.headers.get("Cache-Control", "")
    assert "public" in cc and "max-age=300" in cc


def test_security_headers_noindex_by_default(server_client):
    r = server_client.get("/", headers={"Host": "demo.preview.test"})
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "Referrer-Policy" in r.headers
    assert "Content-Security-Policy" in r.headers
    assert "noindex" in r.headers.get("X-Robots-Tag", "")


def test_unknown_host_returns_404(server_client):
    r = server_client.get("/", headers={"Host": "unrelated.other.domain"})
    assert r.status_code == 404


def test_missing_slug_returns_404(server_client):
    r = server_client.get("/", headers={"Host": "nosuchsite.preview.test"})
    assert r.status_code == 404


def test_path_traversal_rejected(server_client):
    r = server_client.get("/../../etc/passwd", headers={"Host": "demo.preview.test"})
    # httpx normalises '..' segments client-side; ensure we either 404 or 400,
    # but never leak /etc/passwd content.
    assert r.status_code in (400, 404)


def test_ephemeral_info_endpoint(server_client):
    r = server_client.get("/_ephemeral/info", headers={"Host": "demo.preview.test"})
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "demo"
    assert "expires_at" in body
    assert "hits" in body
    assert r.headers.get("Cache-Control") == "no-cache"


def test_config_json_404_when_absent(server_client):
    """The demo fixture has no runtime_config."""
    r = server_client.get("/config.json", headers={"Host": "demo.preview.test"})
    assert r.status_code == 404


def test_config_json_present(api_client, auth_headers, settings, spa_zip_with_static):
    """Separate site with runtime_config — check /config.json serves it."""
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.server import app as server_app_module

    cfg = _json.dumps({"hello": "world"})
    api_client.put(
        "/api/v1/sites/withcfg",
        headers=auth_headers,
        files={"file": ("spa.zip", spa_zip_with_static, "application/zip")},
        data={"runtime_config": cfg},
    )
    conn = api_deps._DB_CACHE[settings.db_path]
    srv = server_app_module.create_server_app(settings=settings, db_conn=conn)
    client = TestClient(srv)
    r = client.get("/config.json", headers={"Host": "withcfg.preview.test"})
    assert r.status_code == 200
    assert r.json() == {"hello": "world"}
    assert r.headers.get("Cache-Control") == "no-cache"


def test_expired_site_returns_404(
    api_client, auth_headers, spa_zip_with_static, settings
):
    """Set expires_at in the past via DB, then query."""
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.server import app as server_app_module

    api_client.put(
        "/api/v1/sites/stale",
        headers=auth_headers,
        files={"file": ("spa.zip", spa_zip_with_static, "application/zip")},
    )
    conn = api_deps._DB_CACHE[settings.db_path]
    past = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute(
        "UPDATE sites SET expires_at = ? WHERE slug = 'stale'", (past,)
    )

    srv = server_app_module.create_server_app(settings=settings, db_conn=conn)
    client = TestClient(srv)
    r = client.get("/", headers={"Host": "stale.preview.test"})
    assert r.status_code == 404


def test_password_protected_returns_401(
    api_client, auth_headers, spa_zip_with_static, settings
):
    """Password-protected site returns 401 with WWW-Authenticate (step 12 refines)."""
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.server import app as server_app_module

    api_client.put(
        "/api/v1/sites/locked",
        headers=auth_headers,
        files={"file": ("spa.zip", spa_zip_with_static, "application/zip")},
        data={"password": "secret"},
    )
    conn = api_deps._DB_CACHE[settings.db_path]
    srv = server_app_module.create_server_app(settings=settings, db_conn=conn)
    client = TestClient(srv)
    r = client.get("/", headers={"Host": "locked.preview.test"})
    assert r.status_code == 401
    assert "Basic" in r.headers.get("WWW-Authenticate", "")


def test_allow_indexing_omits_robots(
    api_client, auth_headers, spa_zip_with_static, settings
):
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.server import app as server_app_module

    api_client.put(
        "/api/v1/sites/indexable",
        headers=auth_headers,
        files={"file": ("spa.zip", spa_zip_with_static, "application/zip")},
        data={"allow_indexing": "true"},
    )
    conn = api_deps._DB_CACHE[settings.db_path]
    srv = server_app_module.create_server_app(settings=settings, db_conn=conn)
    client = TestClient(srv)
    r = client.get("/", headers={"Host": "indexable.preview.test"})
    assert r.status_code == 200
    assert "X-Robots-Tag" not in r.headers


def test_spa_mode_false_returns_404_for_unknown_route(
    api_client, auth_headers, spa_zip_with_static, settings
):
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.server import app as server_app_module

    api_client.put(
        "/api/v1/sites/notspa",
        headers=auth_headers,
        files={"file": ("spa.zip", spa_zip_with_static, "application/zip")},
        data={"spa_mode": "false"},
    )
    conn = api_deps._DB_CACHE[settings.db_path]
    srv = server_app_module.create_server_app(settings=settings, db_conn=conn)
    client = TestClient(srv)
    r = client.get("/no-such-route", headers={"Host": "notspa.preview.test"})
    assert r.status_code == 404
