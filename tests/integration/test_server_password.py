"""Integration tests for password-protected served sites (step 12)."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest


@pytest.fixture()
def tiny_zip_content() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", b"<!doctype html>SECRET CONTENT")
    return buf.getvalue()


@pytest.fixture()
def protected_server(api_client, auth_headers, settings, tiny_zip_content):
    """PUT a site with password='s3cr3t' and return a TestClient for the server."""
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.server import app as server_app_module

    api_client.put(
        "/api/v1/sites/locked",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_zip_content, "application/zip")},
        data={"password": "s3cr3t"},
    )
    conn = api_deps._DB_CACHE[settings.db_path]
    srv = server_app_module.create_server_app(settings=settings, db_conn=conn)
    return TestClient(srv)


def _basic(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def test_password_protected_requires_auth(protected_server):
    """Master spec section 11.3."""
    r = protected_server.get("/", headers={"Host": "locked.preview.test"})
    assert r.status_code == 401
    assert "Basic" in r.headers.get("WWW-Authenticate", "")


def test_correct_password_serves_content(protected_server):
    r = protected_server.get(
        "/",
        headers={"Host": "locked.preview.test", "Authorization": _basic("anyuser", "s3cr3t")},
    )
    assert r.status_code == 200
    assert b"SECRET CONTENT" in r.content


def test_wrong_password_returns_401(protected_server):
    r = protected_server.get(
        "/",
        headers={"Host": "locked.preview.test", "Authorization": _basic("x", "nope")},
    )
    assert r.status_code == 401


def test_malformed_basic_header_returns_401(protected_server):
    r = protected_server.get(
        "/",
        headers={"Host": "locked.preview.test", "Authorization": "Basic notbase64"},
    )
    assert r.status_code == 401


def test_non_basic_scheme_returns_401(protected_server):
    r = protected_server.get(
        "/",
        headers={"Host": "locked.preview.test", "Authorization": "Bearer xxx"},
    )
    assert r.status_code == 401


def test_protected_site_gates_ephemeral_info(protected_server):
    r = protected_server.get("/_ephemeral/info", headers={"Host": "locked.preview.test"})
    assert r.status_code == 401


def test_protected_site_gates_config_json(protected_server):
    r = protected_server.get("/config.json", headers={"Host": "locked.preview.test"})
    assert r.status_code == 401


def test_unprotected_site_ignores_auth_header(api_client, auth_headers, settings, tiny_zip_content):
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.server import app as server_app_module

    api_client.put(
        "/api/v1/sites/open",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_zip_content, "application/zip")},
    )
    conn = api_deps._DB_CACHE[settings.db_path]
    srv = server_app_module.create_server_app(settings=settings, db_conn=conn)
    client = TestClient(srv)
    r = client.get(
        "/",
        headers={"Host": "open.preview.test", "Authorization": _basic("x", "y")},
    )
    assert r.status_code == 200


def test_password_with_colon_split_on_first(protected_server):
    """Password containing ':' — Basic auth splits username:password on first colon."""
    # Re-use protected_server which has password 's3cr3t' (no colon).
    # Just verify the "no leak" side — provide a weird password that contains ':'.
    r = protected_server.get(
        "/",
        headers={"Host": "locked.preview.test", "Authorization": _basic("user", "s:3c:r3t")},
    )
    assert r.status_code == 401
