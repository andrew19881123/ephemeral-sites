"""Shared fixtures for integration tests.

The fixtures build up progressively:

- ``settings`` / ``db_conn`` / ``sites_root`` — isolated per test via ``tmp_path``
- ``api_keys`` — one enabled ("main:secret") + one disabled ("banned:banned")
- ``api_client`` — ``httpx`` TestClient wired to an ephemeral ``FastAPI`` app
- ``auth_headers`` — convenience dict with a valid bearer
- ``build_zip`` — helper to craft small, deterministic ZIP payloads
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# The api module does not exist yet; importing at module-scope would explode
# collection for *every* integration test. We gate the import behind a fixture
# so the red test file can still be collected and show a clean ImportError.


@pytest.fixture()
def sites_root(tmp_path: Path) -> Path:
    root = tmp_path / "sites"
    root.mkdir()
    (root / ".lock").mkdir()
    return root


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "db" / "ephemeral.db"


@pytest.fixture()
def settings(sites_root: Path, db_path: Path):
    from ephemeral_sites.config import Settings

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        api_keys="main:secret",  # disabled key injected via override in api_keys_tuple
        db_path=str(db_path),
        sites_root=str(sites_root),
        lock_dir=str(sites_root / ".lock"),
        max_zip_size=1024 * 1024,  # 1 MiB plenty for fixtures
        max_files_per_site=50,
        max_total_storage_bytes=10 * 1024 * 1024,  # 10 MiB — easy to saturate
        max_decompression_ratio=100,
        default_ttl_seconds=3600,
        max_ttl_seconds=7 * 24 * 3600,
        allow_permanent=True,
        base_domain="preview.test",
        bcrypt_rounds=4,  # fast tests
    )


@pytest.fixture()
def api_keys_tuple(settings):
    """Parse the test settings api_keys with rounds=4. Provides one enabled
    key (name='main', secret='secret') and one disabled key (name='banned',
    secret='bannedsecret')."""
    from ephemeral_sites import auth

    # Test format: "main:secret,banned:bannedsecret:disabled"
    # Our parse_api_keys_env supports only name:secret; so we manually build.
    enabled = auth.ApiKey(
        name="main",
        hashed=auth.hash_secret("secret", rounds=4),
        disabled=False,
    )
    banned = auth.ApiKey(
        name="banned",
        hashed=auth.hash_secret("bannedsecret", rounds=4),
        disabled=True,
    )
    return (enabled, banned)


@pytest.fixture()
def api_client(settings, api_keys_tuple) -> Iterator:
    """TestClient backed by a real FastAPI app wired to tmp_path."""
    from fastapi.testclient import TestClient

    from ephemeral_sites.api import app as app_module
    from ephemeral_sites.api import deps

    app = app_module.create_app(settings=settings)

    # Override: bypass env-parsing path; use the tuple we built directly.
    app.dependency_overrides[deps.get_api_keys] = lambda: api_keys_tuple

    with TestClient(app) as client:
        yield client


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer secret"}


@pytest.fixture()
def disabled_auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bannedsecret"}


@pytest.fixture()
def build_zip():
    """Factory: build_zip({'index.html': b'<html>...</html>', ...}) -> bytes."""

    def _build(members: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, payload in members.items():
                zf.writestr(name, payload)
        return buf.getvalue()

    return _build


@pytest.fixture()
def tiny_valid_zip(build_zip) -> bytes:
    return build_zip({"index.html": b"<!doctype html><title>v1</title>"})


@pytest.fixture()
def tiny_valid_zip_v2(build_zip) -> bytes:
    return build_zip({"index.html": b"<!doctype html><title>v2 NEW</title>"})


@pytest.fixture()
def open_conn(db_path: Path):
    """Re-open the DB after the API has populated it (tests assert on rows)."""

    def _open() -> sqlite3.Connection:
        return sqlite3.connect(str(db_path))

    return _open
