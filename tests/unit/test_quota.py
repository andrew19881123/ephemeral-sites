"""Unit tests for the global storage quota module.

Derived 1:1 from docs/steps/step-7-quota.md §4.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ephemeral_sites import db, quota


# ---------------------------------------------------------------------------
# §4.1 check_quota (5)
# ---------------------------------------------------------------------------


def test_check_quota_passes_under_limit():
    # No raise when total would be below cap.
    quota.check_quota(current_used=10, incoming=20, max_total=100)


def test_check_quota_passes_at_exact_limit():
    # The cap itself is accepted (strict > per mini-spec §6 Q1).
    quota.check_quota(current_used=40, incoming=60, max_total=100)


def test_check_quota_raises_over_limit():
    with pytest.raises(quota.QuotaExceeded):
        quota.check_quota(current_used=50, incoming=51, max_total=100)


def test_quota_exceeded_attributes_populated():
    try:
        quota.check_quota(current_used=90, incoming=15, max_total=100)
    except quota.QuotaExceeded as exc:
        assert exc.current_used == 90
        assert exc.incoming == 15
        assert exc.max_total == 100
    else:
        pytest.fail("expected QuotaExceeded")


def test_quota_exceeded_str_contains_numbers():
    try:
        quota.check_quota(current_used=90, incoming=15, max_total=100)
    except quota.QuotaExceeded as exc:
        text = str(exc)
        assert "90" in text
        assert "15" in text
        assert "100" in text


# ---------------------------------------------------------------------------
# §4.2 sum_active_sites_bytes (4)
# ---------------------------------------------------------------------------


def _open_fresh_db(tmp_path: Path) -> sqlite3.Connection:
    return db.open_db(tmp_path / "quota-test.db")


def _insert_site(conn: sqlite3.Connection, *, slug: str, size_bytes: int, expires_at=None):
    conn.execute(
        """
        INSERT INTO sites
            (slug, path, created_at, updated_at, expires_at, size_bytes,
             files_count, delete_token_hash, created_by)
        VALUES (?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', ?, ?,
                1, 'hash', 'test')
        """,
        (slug, f"/data/sites/{slug}", expires_at, size_bytes),
    )
    conn.commit()


def test_sum_active_sites_bytes_empty_db(tmp_path: Path):
    conn = _open_fresh_db(tmp_path)
    try:
        assert quota.sum_active_sites_bytes(conn) == 0
    finally:
        conn.close()


def test_sum_active_sites_bytes_aggregates_rows(tmp_path: Path):
    conn = _open_fresh_db(tmp_path)
    try:
        _insert_site(conn, slug="a", size_bytes=100)
        _insert_site(conn, slug="b", size_bytes=200)
        _insert_site(conn, slug="c", size_bytes=300)
        assert quota.sum_active_sites_bytes(conn) == 600
    finally:
        conn.close()


def test_sum_active_sites_bytes_includes_rows_regardless_of_expiry(tmp_path: Path):
    """Expired-but-not-reaped rows are included (mini-spec §6 Q2)."""
    conn = _open_fresh_db(tmp_path)
    try:
        _insert_site(conn, slug="live", size_bytes=100, expires_at="2099-01-01T00:00:00Z")
        _insert_site(conn, slug="expired", size_bytes=50, expires_at="2020-01-01T00:00:00Z")
        _insert_site(conn, slug="permanent", size_bytes=25, expires_at=None)
        assert quota.sum_active_sites_bytes(conn) == 175
    finally:
        conn.close()


def test_sum_active_sites_bytes_raises_on_missing_sites_table(tmp_path: Path):
    """An unmigrated DB has no sites table — raise loud rather than 0."""
    dbfile = tmp_path / "bare.db"
    conn = sqlite3.connect(dbfile)
    try:
        with pytest.raises(sqlite3.OperationalError):
            quota.sum_active_sites_bytes(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §4.3 sum_filesystem_bytes (5)
# ---------------------------------------------------------------------------


def test_sum_filesystem_bytes_missing_root_returns_zero(tmp_path: Path):
    assert quota.sum_filesystem_bytes(tmp_path / "does-not-exist") == 0


def test_sum_filesystem_bytes_empty_root_returns_zero(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    assert quota.sum_filesystem_bytes(root) == 0


def test_sum_filesystem_bytes_sums_regular_files(tmp_path: Path):
    root = tmp_path / "sites"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "index.html").write_bytes(b"x" * 100)
    (root / "demo" / "style.css").write_bytes(b"y" * 50)
    (root / "other").mkdir()
    (root / "other" / "index.html").write_bytes(b"z" * 25)
    assert quota.sum_filesystem_bytes(root) == 175


def test_sum_filesystem_bytes_excludes_top_level_dotdirs(tmp_path: Path):
    root = tmp_path / "sites"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "index.html").write_bytes(b"x" * 100)
    (root / ".lock").mkdir()
    (root / ".lock" / "demo.lock").write_bytes(b"_" * 1000)  # should NOT count
    assert quota.sum_filesystem_bytes(root) == 100


def test_sum_filesystem_bytes_excludes_new_and_old_dirs(tmp_path: Path):
    root = tmp_path / "sites"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "index.html").write_bytes(b"x" * 100)
    # In-flight extraction / rollback leftovers.
    (root / "demo.new").mkdir()
    (root / "demo.new" / "index.html").write_bytes(b"N" * 500)
    (root / "demo.old").mkdir()
    (root / "demo.old" / "index.html").write_bytes(b"O" * 500)
    assert quota.sum_filesystem_bytes(root) == 100


# ---------------------------------------------------------------------------
# §4.4 Contract (2)
# ---------------------------------------------------------------------------


def test_quota_exceeded_is_exception_subclass():
    # Plain Exception, not OSError or ValueError (mini-spec §3 AC #15).
    assert issubclass(quota.QuotaExceeded, Exception)
    assert not issubclass(quota.QuotaExceeded, OSError)
    assert not issubclass(quota.QuotaExceeded, ValueError)


def test_exceeds_global_quota_returns_507_semantics(tmp_path: Path):
    """The master-spec §11.3 test: when current_used + incoming >
    max_total, the module raises the exception that the API layer will
    map to HTTP 507. We emulate the PUT path: query DB for current_used,
    then check quota with an incoming estimate.
    """
    conn = _open_fresh_db(tmp_path)
    try:
        _insert_site(conn, slug="fills-the-quota", size_bytes=99 * 1024)
        current = quota.sum_active_sites_bytes(conn)
        with pytest.raises(quota.QuotaExceeded) as excinfo:
            quota.check_quota(
                current_used=current,
                incoming=2 * 1024,  # would push us over 100 KiB
                max_total=100 * 1024,
            )
        assert excinfo.value.current_used == 99 * 1024
        assert excinfo.value.incoming == 2 * 1024
        assert excinfo.value.max_total == 100 * 1024
    finally:
        conn.close()
