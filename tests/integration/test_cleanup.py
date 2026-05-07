"""Integration tests for the cleanup runner (step 13)."""

from __future__ import annotations

import datetime as _dt
import io
import zipfile
from pathlib import Path


def _put_site(api_client, auth_headers, slug: str, ttl: int = 3600, zip_bytes: bytes | None = None):
    if zip_bytes is None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("index.html", b"<!doctype html>")
        zip_bytes = buf.getvalue()
    r = api_client.put(
        f"/api/v1/sites/{slug}",
        headers=auth_headers,
        files={"file": ("spa.zip", zip_bytes, "application/zip")},
        data={"ttl_seconds": str(ttl)},
    )
    assert r.status_code == 200, r.text


def _set_expires(conn, slug: str, when: str | None):
    conn.execute("UPDATE sites SET expires_at = ? WHERE slug = ?", (when, slug))


def test_cleanup_empty_db(settings, db_path: Path):
    from ephemeral_sites import db
    from ephemeral_sites.cleanup.runner import run_cleanup

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.open_db(db_path)
    try:
        result = run_cleanup(settings, conn)
        assert result.expired_slugs == ()
        assert result.purged_events == 0
    finally:
        conn.close()


def test_cleanup_reaps_expired_site(api_client, auth_headers, settings, sites_root):
    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.cleanup.runner import run_cleanup

    _put_site(api_client, auth_headers, "stale")
    conn = api_deps._DB_CACHE[settings.db_path]
    past = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_expires(conn, "stale", past)

    result = run_cleanup(settings, conn)
    assert "stale" in result.expired_slugs

    assert not (sites_root / "stale").exists()
    row = conn.execute("SELECT slug FROM sites WHERE slug = 'stale'").fetchone()
    assert row is None
    evt = conn.execute(
        "SELECT event FROM event_log WHERE slug = 'stale' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert evt[0] == "expired"


def test_cleanup_skips_future_expiry(api_client, auth_headers, settings, sites_root):
    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.cleanup.runner import run_cleanup

    _put_site(api_client, auth_headers, "alive")
    conn = api_deps._DB_CACHE[settings.db_path]
    future = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_expires(conn, "alive", future)

    result = run_cleanup(settings, conn)
    assert "alive" not in result.expired_slugs
    assert (sites_root / "alive").exists()


def test_cleanup_skips_permanent_site(api_client, auth_headers, settings, sites_root):
    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.cleanup.runner import run_cleanup

    _put_site(api_client, auth_headers, "permasite", ttl=-1)
    conn = api_deps._DB_CACHE[settings.db_path]

    result = run_cleanup(settings, conn)
    assert "permasite" not in result.expired_slugs
    assert (sites_root / "permasite").exists()


def test_cleanup_reaps_multiple(api_client, auth_headers, settings):
    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.cleanup.runner import run_cleanup

    _put_site(api_client, auth_headers, "expired-a")
    _put_site(api_client, auth_headers, "expired-b")
    conn = api_deps._DB_CACHE[settings.db_path]
    past = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_expires(conn, "expired-a", past)
    _set_expires(conn, "expired-b", past)

    result = run_cleanup(settings, conn)
    assert set(result.expired_slugs) == {"expired-a", "expired-b"}


def test_cleanup_purges_event_log_on_monday(api_client, auth_headers, settings):
    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.cleanup.runner import run_cleanup

    _put_site(api_client, auth_headers, "somesite")
    conn = api_deps._DB_CACHE[settings.db_path]
    # Much older than (monday_iso - 90d). 2025-01-01 is safely before 2025-10-07.
    ancient = "2025-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO event_log (slug, event, timestamp, api_key) VALUES (?, ?, ?, ?)",
        ("somesite", "created", ancient, "test"),
    )
    conn.commit()

    # Simulate Monday by injecting a Monday now_iso (2026-01-05 was a Monday)
    monday_iso = "2026-01-05T12:00:00Z"
    result = run_cleanup(settings, conn, now_iso=monday_iso)
    # At least one row purged.
    assert result.purged_events >= 1
    row = conn.execute("SELECT 1 FROM event_log WHERE timestamp = ?", (ancient,)).fetchone()
    assert row is None


def test_cleanup_skips_event_log_purge_on_non_monday(api_client, auth_headers, settings):
    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.cleanup.runner import run_cleanup

    _put_site(api_client, auth_headers, "somesite")
    conn = api_deps._DB_CACHE[settings.db_path]
    # Much older than (monday_iso - 90d). 2025-01-01 is safely before 2025-10-07.
    ancient = "2025-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO event_log (slug, event, timestamp, api_key) VALUES (?, ?, ?, ?)",
        ("somesite", "created", ancient, "test"),
    )
    conn.commit()

    # 2026-01-06 was a Tuesday.
    result = run_cleanup(settings, conn, now_iso="2026-01-06T12:00:00Z")
    assert result.purged_events == 0
    row = conn.execute("SELECT 1 FROM event_log WHERE timestamp = ?", (ancient,)).fetchone()
    assert row is not None
