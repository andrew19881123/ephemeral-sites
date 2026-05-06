"""Unit tests for the DB connection factory and migration engine.

Derived 1:1 from docs/steps/step-4-db-migrations.md §4.
Red phase: import fails with ImportError (module does not exist yet).

Tests use pytest's `tmp_path` fixture for on-disk SQLite files — per
mini-spec §6 Q3 we don't use `:memory:` because WAL semantics and
path-based behavior diverge.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ephemeral_sites import db


# ---------------------------------------------------------------------------
# §4.1 open_db basics
# ---------------------------------------------------------------------------


def test_open_db_creates_file_on_fresh_path(tmp_path: Path):
    dbfile = tmp_path / "fresh.db"
    assert not dbfile.exists()
    conn = db.open_db(dbfile)
    try:
        assert dbfile.exists()
    finally:
        conn.close()


def test_open_db_sets_journal_mode_wal(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_open_db_sets_foreign_keys_on(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        val = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert val == 1
    finally:
        conn.close()


def test_open_db_sets_busy_timeout_5000(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert val == 5000
    finally:
        conn.close()


def test_open_db_sets_synchronous_normal(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        # NORMAL is 1. See https://www.sqlite.org/pragma.html#pragma_synchronous
        val = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert val == 1
    finally:
        conn.close()


def test_open_db_row_factory_is_sqlite_row(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_open_db_twice_is_idempotent(tmp_path: Path):
    dbfile = tmp_path / "a.db"
    c1 = db.open_db(dbfile)
    c1.close()
    # Second open must not raise and schema must be unchanged.
    c2 = db.open_db(dbfile)
    try:
        tables = {
            row[0]
            for row in c2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"sites", "api_keys", "event_log"} <= tables
    finally:
        c2.close()


def test_open_db_readonly_on_missing_file_raises(tmp_path: Path):
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        db.open_db(missing, read_only=True)


def test_open_db_readonly_does_not_run_migrations(tmp_path: Path):
    # Create a fresh file with no schema (bypass open_db).
    dbfile = tmp_path / "empty.db"
    dbfile.touch()
    conn = db.open_db(dbfile, read_only=True)
    try:
        # No migrations should have been attempted; user_version stays 0 and
        # no sites table exists.
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 0
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "sites" not in tables
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §4.2 Schema
# ---------------------------------------------------------------------------


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple]:
    """Return PRAGMA table_info rows for ``table``."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [tuple(r) for r in rows]


def test_schema_creates_sites_table_with_expected_columns(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        cols = [r[1] for r in _columns(conn, "sites")]
        expected = [
            "slug",
            "path",
            "created_at",
            "updated_at",
            "expires_at",
            "size_bytes",
            "files_count",
            "password_hash",
            "delete_token_hash",
            "spa_mode",
            "allow_indexing",
            "hits",
            "last_hit",
            "created_by",
            "labels",
            "runtime_config",
        ]
        assert cols == expected, f"sites columns differ: got {cols}"
    finally:
        conn.close()


def test_schema_creates_api_keys_table_with_expected_columns(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        cols = [r[1] for r in _columns(conn, "api_keys")]
        expected = ["name", "key_hash", "created_at", "last_used", "disabled"]
        assert cols == expected
    finally:
        conn.close()


def test_schema_creates_event_log_table_with_expected_columns(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        cols = [r[1] for r in _columns(conn, "event_log")]
        expected = ["id", "slug", "event", "timestamp", "api_key", "metadata"]
        assert cols == expected
    finally:
        conn.close()


def test_sites_slug_is_primary_key(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        rows = conn.execute("PRAGMA table_info(sites)").fetchall()
        slug_row = next(r for r in rows if r[1] == "slug")
        # pk column: 1 = first pk, 0 = not a pk
        assert slug_row[5] == 1
    finally:
        conn.close()


def test_sites_delete_token_hash_is_not_null(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        rows = conn.execute("PRAGMA table_info(sites)").fetchall()
        row = next(r for r in rows if r[1] == "delete_token_hash")
        # notnull column: 1 = NOT NULL
        assert row[3] == 1
    finally:
        conn.close()


def test_sites_password_hash_is_nullable(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        rows = conn.execute("PRAGMA table_info(sites)").fetchall()
        row = next(r for r in rows if r[1] == "password_hash")
        assert row[3] == 0  # NULL allowed
    finally:
        conn.close()


def test_event_log_id_is_autoincrement(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        # Check via sqlite_master.sql (INTEGER PRIMARY KEY AUTOINCREMENT
        # creates a sqlite_sequence row once a value is inserted; easier to
        # read the DDL).
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='event_log'"
        ).fetchone()[0]
        assert "AUTOINCREMENT" in sql.upper()
    finally:
        conn.close()


def test_schema_creates_expected_indexes(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        idxs = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        expected = {
            "idx_sites_expires",
            "idx_sites_created",
            "idx_event_log_slug",
            "idx_event_log_ts",
        }
        assert expected <= idxs, f"missing indexes: {expected - idxs}"
    finally:
        conn.close()


def test_idx_sites_expires_is_partial(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_sites_expires'"
        ).fetchone()[0]
        # Partial index must contain a WHERE clause against expires_at.
        assert "WHERE" in sql.upper()
        assert "expires_at" in sql.lower()
        assert "NOT NULL" in sql.upper()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §4.3 Migrations
# ---------------------------------------------------------------------------


def test_get_schema_version_on_fresh_db_returns_0_before_migrations(
    tmp_path: Path,
):
    # Open the raw SQLite file without going through open_db (so migrations
    # are not applied), then inspect user_version via the public helper.
    dbfile = tmp_path / "raw.db"
    conn = sqlite3.connect(dbfile)
    try:
        assert db.get_schema_version(conn) == 0
    finally:
        conn.close()


def test_migration_v0_to_v1(tmp_path: Path):
    """Core test mandated by master spec §11.3.

    Starting from a fresh DB at user_version 0, run_migrations brings
    the schema to v1 with all tables and indexes present.
    """
    dbfile = tmp_path / "migrate.db"
    conn = sqlite3.connect(dbfile)
    try:
        assert db.get_schema_version(conn) == 0
        final = db.run_migrations(conn)
        assert final == 1
        assert db.get_schema_version(conn) == 1
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"sites", "api_keys", "event_log"} <= tables
    finally:
        conn.close()


def test_run_migrations_idempotent_when_current(tmp_path: Path):
    dbfile = tmp_path / "a.db"
    # First open_db runs migrations.
    conn = db.open_db(dbfile)
    try:
        first = db.get_schema_version(conn)
        # Running again must not change the version and must not raise.
        final = db.run_migrations(conn)
        assert final == first
    finally:
        conn.close()


def test_run_migrations_creates_backup_before_applying(tmp_path: Path):
    dbfile = tmp_path / "a.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Pre-seed a real file so the backup has something to copy. Open
    # without migrations first.
    conn = sqlite3.connect(dbfile)
    conn.execute("CREATE TABLE sentinel (x INTEGER)")
    conn.execute("INSERT INTO sentinel VALUES (42)")
    conn.commit()
    conn.close()

    # Now run migrations, which should back up the existing file.
    conn = sqlite3.connect(dbfile)
    try:
        db.run_migrations(conn, backup_dir=backup_dir)
    finally:
        conn.close()

    backups = list(backup_dir.iterdir())
    assert any(p.name.startswith("ephemeral-sites.db.backup-v0") for p in backups), (
        f"expected backup file starting with backup-v0, got {[p.name for p in backups]}"
    )


def test_run_migrations_skips_backup_when_backup_dir_none(tmp_path: Path):
    dbfile = tmp_path / "a.db"
    # Pre-seed the file.
    conn = sqlite3.connect(dbfile)
    conn.execute("CREATE TABLE sentinel (x INTEGER)")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(dbfile)
    try:
        db.run_migrations(conn, backup_dir=None)
    finally:
        conn.close()
    # No file named backup-v* should exist anywhere in tmp_path.
    backups = [p for p in tmp_path.rglob("*backup-v*")]
    assert backups == []


def test_run_migrations_rolls_back_on_failure(tmp_path: Path):
    dbfile = tmp_path / "a.db"
    conn = sqlite3.connect(dbfile)

    def failing_up(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE pre (x INTEGER)")
        raise RuntimeError("boom")

    bad = db.Migration(target_version=1, description="bad", up=failing_up)

    try:
        with pytest.raises(RuntimeError, match="boom"):
            db.run_migrations(conn, migrations=(bad,))
        # user_version must still be 0 (rollback happened).
        assert db.get_schema_version(conn) == 0
        # The "pre" table must NOT exist (rollback).
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pre" not in tables
    finally:
        conn.close()


def test_migrations_registry_is_strictly_linear():
    versions = [m.target_version for m in db.MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1)), (
        f"MIGRATIONS versions not strictly linear: {versions}"
    )


# ---------------------------------------------------------------------------
# §4.4 Contract
# ---------------------------------------------------------------------------


def test_migration_dataclass_is_frozen():
    m = db.Migration(target_version=1, description="x", up=lambda c: None)
    with pytest.raises((AttributeError, TypeError)):  # dataclass(frozen=True) raises
        m.target_version = 2  # type: ignore[misc]


def test_final_user_version_matches_highest_migration(tmp_path: Path):
    conn = db.open_db(tmp_path / "a.db")
    try:
        highest = max(m.target_version for m in db.MIGRATIONS)
        assert db.get_schema_version(conn) == highest
    finally:
        conn.close()
