"""Integration tests for PUT /api/v1/sites/{slug}.

Red tests for step 8; see ``docs/steps/step-8-api-put-upsert.md`` for
the public contract and acceptance criteria. These tests are expected
to fail until the FastAPI app is implemented.

Three of these tests are master-spec mandated (``docs/SPEC.md`` §11.3):

- test_put_creates_site
- test_put_same_slug_replaces_content
- test_put_same_slug_no_404_during_swap
"""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# §3.2 Happy path — master-spec "test_put_creates_site"
# ---------------------------------------------------------------------------


def test_put_creates_site(api_client, auth_headers, tiny_valid_zip, open_conn, sites_root):
    """Master spec §11.3. A valid bearer + ZIP creates the site end-to-end."""
    resp = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["slug"] == "demo"
    assert body["url"].startswith("https://demo.")
    assert body["size_bytes"] > 0
    assert body["files_count"] >= 1
    assert body["delete_token"].startswith("dt_")
    assert body["password_protected"] is False
    assert body["spa_mode"] is True

    # Filesystem side-effect
    assert (sites_root / "demo" / "index.html").exists()

    # DB side-effect
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT slug, created_at, updated_at, delete_token_hash FROM sites WHERE slug='demo'"
        ).fetchone()
        assert row is not None, "site row missing"
        slug, created, updated, delete_hash = row
        assert slug == "demo"
        assert created == updated  # first insert

        from ephemeral_sites import auth

        assert auth.verify_delete_token(body["delete_token"], delete_hash)

        evt = conn.execute(
            "SELECT event FROM event_log WHERE slug='demo' ORDER BY id ASC"
        ).fetchall()
        assert [r[0] for r in evt] == ["created"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §3.3 Replace — master-spec "test_put_same_slug_replaces_content"
# ---------------------------------------------------------------------------


def test_put_same_slug_replaces_content(
    api_client, auth_headers, tiny_valid_zip, tiny_valid_zip_v2, open_conn, sites_root
):
    """Master spec §11.3. Second PUT replaces content; updates updated_at; creates
    a 'replaced' event."""
    r1 = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v1.zip", tiny_valid_zip, "application/zip")},
    )
    assert r1.status_code == 200, r1.text
    created_at_1 = r1.json()["created_at"]

    # Tiny delay so updated_at differs.
    time.sleep(1.05)

    r2 = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v2.zip", tiny_valid_zip_v2, "application/zip")},
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()

    assert body2["created_at"] == created_at_1, "created_at must be stable on replace"
    assert body2["updated_at"] > created_at_1, "updated_at must advance on replace"

    # On-disk content is the new version.
    contents = (sites_root / "demo" / "index.html").read_bytes()
    assert b"v2 NEW" in contents

    conn = open_conn()
    try:
        evt = conn.execute(
            "SELECT event FROM event_log WHERE slug='demo' ORDER BY id ASC"
        ).fetchall()
        assert [r[0] for r in evt] == ["created", "replaced"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §3.4 Zero-404 during swap — master-spec "test_put_same_slug_no_404_during_swap"
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="renameat2(RENAME_EXCHANGE) is Linux-specific; zero-404 guarantee only there",
)
def test_put_same_slug_no_404_during_swap(
    api_client, auth_headers, tiny_valid_zip, tiny_valid_zip_v2, sites_root
):
    """Master spec §11.3. Concurrent readers never observe a missing path during
    a second PUT — exercises storage.extract_site's renameat2 swap."""
    # Seed: first PUT.
    r1 = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v1.zip", tiny_valid_zip, "application/zip")},
    )
    assert r1.status_code == 200

    target = sites_root / "demo" / "index.html"
    stop = threading.Event()
    seen_missing = []

    def poll():
        while not stop.is_set():
            try:
                os.stat(target)
            except FileNotFoundError:
                seen_missing.append(time.time_ns())
                return
            time.sleep(0.0005)

    t = threading.Thread(target=poll, daemon=True)
    t.start()
    try:
        r2 = api_client.put(
            "/api/v1/sites/demo",
            headers=auth_headers,
            files={"file": ("v2.zip", tiny_valid_zip_v2, "application/zip")},
        )
        assert r2.status_code == 200, r2.text
    finally:
        stop.set()
        t.join(timeout=2.0)

    assert not seen_missing, f"observed {len(seen_missing)} missing-path events during swap"


# ---------------------------------------------------------------------------
# §3.5 Auth
# ---------------------------------------------------------------------------


def test_put_without_auth_returns_401(api_client, tiny_valid_zip):
    resp = api_client.put(
        "/api/v1/sites/demo",
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "invalid_auth_header"
    assert "request_id" in body


def test_put_with_wrong_bearer_returns_401(api_client, tiny_valid_zip):
    resp = api_client.put(
        "/api/v1/sites/demo",
        headers={"Authorization": "Bearer wrong-key"},
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"


def test_put_with_disabled_key_returns_403(api_client, disabled_auth_headers, tiny_valid_zip):
    resp = api_client.put(
        "/api/v1/sites/demo",
        headers=disabled_auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "disabled_api_key"


def test_put_error_body_carries_request_id(api_client, tiny_valid_zip):
    resp = api_client.put(
        "/api/v1/sites/demo",
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert resp.status_code == 401
    assert resp.headers.get("X-Request-ID")
    assert resp.json()["request_id"] == resp.headers["X-Request-ID"]


# ---------------------------------------------------------------------------
# §3.6 Validation
# ---------------------------------------------------------------------------


def test_put_invalid_slug_returns_400(api_client, auth_headers, tiny_valid_zip):
    resp = api_client.put(
        "/api/v1/sites/INVALID_SLUG",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_slug"


@pytest.mark.security
def test_put_path_traversal_zip_returns_400_no_filename_leak(
    api_client, auth_headers, build_zip
):
    """The error 'detail' must NOT echo the attacker-controlled entry name."""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Include a valid-looking index.html so we get past the "missing index" check
        # to the traversal check.
        zf.writestr("index.html", b"<html></html>")
        zf.writestr("../../etc/passwd", b"r00t:x:0:0")
    malicious = buf.getvalue()

    resp = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("bad.zip", malicious, "application/zip")},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_zip"
    # Log hygiene: never echo the exact malicious path in a user-visible field.
    assert "../../etc/passwd" not in body["detail"]
    assert "etc/passwd" not in body["detail"]


def test_put_ttl_below_minimum_returns_400(api_client, auth_headers, tiny_valid_zip):
    resp = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        data={"ttl_seconds": "10"},  # below 60s minimum
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_ttl"


def test_put_ttl_minus_one_permanent_stored_as_null(
    api_client, auth_headers, tiny_valid_zip, open_conn
):
    resp = api_client.put(
        "/api/v1/sites/permasite",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        data={"ttl_seconds": "-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expires_at"] is None

    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT expires_at FROM sites WHERE slug='permasite'"
        ).fetchone()
        assert row is not None and row[0] is None
    finally:
        conn.close()


def test_put_zip_over_max_size_returns_413(api_client, auth_headers, settings, build_zip):
    """Override max_zip_size to a tiny value via the settings already in use."""
    # Build a payload larger than settings.max_zip_size. We squeeze incompressible
    # random-ish data through deflate so the compressed stream still exceeds the cap.
    big = os.urandom(settings.max_zip_size + 2048)
    oversize = build_zip({"index.html": b"<html></html>", "blob.bin": big})
    assert len(oversize) > settings.max_zip_size

    resp = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("big.zip", oversize, "application/zip")},
    )
    # Either 413 at stream time or 400 if the enforcement is purely in-handler
    # after full read; spec calls for 413. We assert on 413 and let red drive.
    assert resp.status_code == 413
    assert resp.json()["error"] == "payload_too_large"


# ---------------------------------------------------------------------------
# §3.7 Quota
# ---------------------------------------------------------------------------


def test_put_over_quota_returns_507_no_leftover_new_dirs(
    api_client, auth_headers, build_zip, sites_root, settings
):
    """Fill the quota then attempt another PUT; expect 507 and no *.new leftovers."""
    # The initial PUT uses ~most of the quota. We size the zip payload with a
    # single large file of roughly max_total_storage_bytes bytes.
    big_bytes = b"A" * (settings.max_total_storage_bytes - 1024)
    filler = build_zip({"index.html": b"<html></html>", "data.bin": big_bytes})

    r1 = api_client.put(
        "/api/v1/sites/filler",
        headers=auth_headers,
        files={"file": ("filler.zip", filler, "application/zip")},
    )
    # The filler itself might not fit under single-file / ratio caps; if it
    # doesn't, we skip the quota test for this config (not all settings combos
    # can produce a borderline fill). Assert 200 first; if 400, skip.
    if r1.status_code != 200:
        pytest.skip(
            f"filler upload did not fit under validator caps (got {r1.status_code}); "
            "tune settings to exercise quota path"
        )

    second = build_zip({"index.html": b"<html></html>", "more.bin": b"B" * (2 * 1024 * 1024)})
    r2 = api_client.put(
        "/api/v1/sites/overflow",
        headers=auth_headers,
        files={"file": ("overflow.zip", second, "application/zip")},
    )
    assert r2.status_code == 507
    assert r2.json()["error"] == "quota_exceeded"

    # No stray *.new directories under /data/sites
    leftovers = [p for p in sites_root.iterdir() if p.name.endswith(".new")]
    assert not leftovers, f"stray .new dirs after quota reject: {leftovers}"


# ---------------------------------------------------------------------------
# §3.8 Middleware / contract
# ---------------------------------------------------------------------------


def test_response_has_x_request_id_header(api_client, auth_headers, tiny_valid_zip):
    resp = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")


def test_client_supplied_x_request_id_is_echoed(api_client, auth_headers, tiny_valid_zip):
    rid = "client-supplied-id-1234"
    resp = api_client.put(
        "/api/v1/sites/demo",
        headers={**auth_headers, "X-Request-ID": rid},
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == rid


def test_error_body_shape_matches_ErrorResponse(api_client):
    """Missing auth → standard ErrorResponse shape: error, detail, request_id."""
    resp = api_client.put("/api/v1/sites/demo")
    # Even without any body, this should be a 401 (auth check precedes body parse
    # in our middleware / dep chain). FastAPI may return 422 if Form parse fails
    # first; either way the body shape must conform.
    body = resp.json()
    assert set(body.keys()) >= {"error", "detail", "request_id"}
    assert isinstance(body["error"], str)
    assert isinstance(body["detail"], str)
    assert isinstance(body["request_id"], str)
