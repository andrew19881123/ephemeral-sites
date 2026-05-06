"""SQLite connection factory and forward-only migration engine.

See ``docs/steps/step-4-db-migrations.md`` for the full public contract.
The module builds the three tables fixed by master spec §6.1 (``sites``,
``api_keys``, ``event_log``) and owns nothing else — CRUD helpers live
in the feature modules that need them (steps 6, 8+).

Design invariants:

- Forward-only. No downgrades. To roll back, restore the
  ``ephemeral-sites.db.backup-v{N-1}`` file written before the
  offending migration.
- Each migration runs inside ``BEGIN IMMEDIATE`` / ``COMMIT``; on
  exception, the transaction is rolled back, ``user_version`` is
  unchanged, and the exception bubbles up (better to fail loudly at
  startup than carry a half-migrated schema into production).
- ``PRAGMA user_version`` is the source of truth for the current
  schema revision. No custom ``schema_migrations`` table.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MIGRATIONS",
    "Migration",
    "get_schema_version",
    "open_db",
    "run_migrations",
]

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migration primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    """One forward step of the schema evolution.

    Migrations are linear: each ``target_version`` is one greater than the
    previous migration's target. ``up`` runs inside a transaction;
    raising rolls the whole step back.
    """

    target_version: int
    description: str
    up: Callable[[sqlite3.Connection], None]


# ---------------------------------------------------------------------------
# Migration v0 → v1: initial schema (master spec §6.1)
# ---------------------------------------------------------------------------

_V1_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sites (
    slug              TEXT PRIMARY KEY,
    path              TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    expires_at        TEXT,
    size_bytes        INTEGER NOT NULL,
    files_count       INTEGER NOT NULL,
    password_hash     TEXT,
    delete_token_hash TEXT NOT NULL,
    spa_mode          INTEGER NOT NULL DEFAULT 1,
    allow_indexing    INTEGER NOT NULL DEFAULT 0,
    hits              INTEGER NOT NULL DEFAULT 0,
    last_hit          TEXT,
    created_by        TEXT NOT NULL,
    labels            TEXT,
    runtime_config    TEXT
);

CREATE INDEX IF NOT EXISTS idx_sites_expires
    ON sites(expires_at) WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sites_created
    ON sites(created_at);

CREATE TABLE IF NOT EXISTS api_keys (
    name        TEXT PRIMARY KEY,
    key_hash    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    last_used   TEXT,
    disabled    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL,
    event       TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    api_key     TEXT,
    metadata    TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_log_slug
    ON event_log(slug);

CREATE INDEX IF NOT EXISTS idx_event_log_ts
    ON event_log(timestamp);
"""


def _migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
    """Create the initial schema — three tables and four indexes."""
    conn.executescript(_V1_SCHEMA_SQL)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        target_version=1,
        description="initial schema (sites, api_keys, event_log)",
        up=_migrate_v0_to_v1,
    ),
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current ``PRAGMA user_version`` (0 on a fresh DB)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA user_version does not accept bind parameters.
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool = False) -> None:
    """Apply runtime PRAGMAs required by master spec §6.1.

    ``journal_mode = WAL`` and ``synchronous = NORMAL`` both write to the
    database file and cannot be applied on a read-only connection — they
    are skipped in that mode. ``foreign_keys`` and ``busy_timeout`` are
    per-connection flags that do not write, so they are always applied.
    """
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")


def run_migrations(
    conn: sqlite3.Connection,
    *,
    backup_dir: Path | str | None = None,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> int:
    """Apply pending migrations in order; return the final schema version.

    For each migration whose ``target_version`` is strictly greater than
    the current ``user_version``:

    1. If ``backup_dir`` is set AND the DB has a backing file that is
       non-empty, copy it to ``{backup_dir}/ephemeral-sites.db.backup-v{N-1}``.
    2. ``BEGIN IMMEDIATE`` — serializes with any concurrent writer.
    3. Invoke ``migration.up(conn)``.
    4. Set ``PRAGMA user_version = target_version``.
    5. ``COMMIT``.

    On exception anywhere in 3–4: ``ROLLBACK``, re-raise.
    """
    backup_path = Path(backup_dir) if backup_dir is not None else None

    # Validate the registry is linear (defensive — the property test also
    # guards this, but catching it here produces a clearer stack if it
    # ever regresses).
    for i, m in enumerate(migrations, start=1):
        if m.target_version != i:
            raise ValueError(
                f"migrations not strictly linear: expected target_version={i}, "
                f"got {m.target_version} at index {i - 1}"
            )

    for migration in migrations:
        current = get_schema_version(conn)
        if migration.target_version <= current:
            continue

        if backup_path is not None:
            _backup_db_file(conn, backup_path, current)

        try:
            conn.execute("BEGIN IMMEDIATE")
            migration.up(conn)
            _set_schema_version(conn, migration.target_version)
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception(
                "migration v%s → v%s failed, rolled back",
                current,
                migration.target_version,
            )
            raise

        log.info(
            "migration v%s → v%s applied (%s)",
            current,
            migration.target_version,
            migration.description,
        )

    return get_schema_version(conn)


def _backup_db_file(conn: sqlite3.Connection, backup_dir: Path, current_version: int) -> None:
    """Copy the SQLite file backing ``conn`` to ``backup_dir``.

    A no-op when the connection has no disk file (``:memory:``) or when
    the file is empty (a fresh DB that we're about to migrate for the
    first time — nothing to preserve).
    """
    row = conn.execute("PRAGMA database_list").fetchone()
    if not row:
        return
    db_file = row[2] if len(row) > 2 else ""
    if not db_file:
        return

    db_path = Path(db_file)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return

    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"ephemeral-sites.db.backup-v{current_version}"
    shutil.copy2(db_path, dest)
    log.info("pre-migration backup written: %s", dest)


def open_db(
    path: Path | str,
    *,
    read_only: bool = False,
    apply_migrations: bool = True,
    backup_dir: Path | str | None = None,
) -> sqlite3.Connection:
    """Open ``path`` as a SQLite DB, apply required PRAGMAs, run migrations.

    Parameters:
        path: Filesystem path. In read-only mode the file must already
            exist; otherwise the file is created if missing.
        read_only: If True, open with ``mode=ro`` via URI and do NOT run
            migrations (regardless of ``apply_migrations``).
        apply_migrations: When True (default) and not ``read_only``, run
            :func:`run_migrations` after opening.
        backup_dir: Passed to :func:`run_migrations`.

    Returns:
        An open ``sqlite3.Connection`` with ``row_factory = sqlite3.Row``.
    """
    str_path = str(path)

    if read_only:
        uri = f"file:{str_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    else:
        conn = sqlite3.connect(str_path, isolation_level=None)

    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn, read_only=read_only)

    if apply_migrations and not read_only:
        run_migrations(conn, backup_dir=backup_dir)

    return conn
