# Step 4 — DB + migrations

**Master spec sections**: [§6.1 Schema SQLite](../SPEC.md), [§6.3 Event log retention](../SPEC.md), [§11.3 test_db.py](../SPEC.md), [§4.2 Flusso PUT (DB transaction)](../SPEC.md)
**Roadmap entry**: [§16.1 step 4](../SPEC.md)
**Status**: 🟡 Approved, in progress
**Owner**: Andrea Veronesi

---

## 1. Goal

Deliver the persistence layer: a single SQLite file at `/data/db/ephemeral-sites.db` containing three tables (`sites`, `api_keys`, `event_log`) with the exact schema fixed by master spec §6.1, plus a forward-only migration system based on `PRAGMA user_version` that runs idempotently at application start-up.

No queries against these tables are implemented in this step — those ride in later steps (8+ for `sites`, 6 for `api_keys`, everywhere for `event_log`). Step 4 is purely: **connection factory + schema creation + version bookkeeping + backups**.

The module is the foundation every other data-touching step will sit on. It must be boring, correct, and test-covered.

---

## 2. Public API / Contract

### 2.1 Module layout

- `src/ephemeral_sites/db.py` — connection factory, migration engine, public helpers.
- `tests/unit/test_db.py` — all tests for this step.

No new runtime deps; uses stdlib `sqlite3`, `pathlib`, `shutil` (for backup), `dataclasses`, `logging`.

### 2.2 Connection factory

```python
def open_db(
    path: Path | str,
    *,
    read_only: bool = False,
    apply_migrations: bool = True,
    backup_dir: Path | str | None = None,
) -> sqlite3.Connection:
    """Open the SQLite file at ``path``, apply required PRAGMAs, and
    (unless read_only) run any pending migrations.

    PRAGMAs applied, in order:
      - journal_mode = WAL
      - foreign_keys = ON
      - busy_timeout = 5000  (ms)
      - synchronous = NORMAL

    If ``read_only`` is True, opens with URI mode=ro and skips migrations.

    ``backup_dir`` — if provided, pre-migration backups are written there
    as ``ephemeral-sites.db.backup-v{N-1}``. If None, no backups are
    taken. In production this should be set; in tests we typically pass
    None to keep tmp_path uncluttered.

    Returns a `sqlite3.Connection` with ``row_factory = sqlite3.Row``
    so callers can index rows by column name.
    """
```

### 2.3 Migration engine

```python
@dataclass(frozen=True)
class Migration:
    """One step of the schema evolution.

    ``target_version`` must be one greater than the previous Migration's
    target_version (migrations are linear, no branching).
    ``up`` is called with an open connection inside a BEGIN/COMMIT block;
    raising any exception rolls back the transaction and aborts further
    migrations.
    """
    target_version: int
    description: str
    up: Callable[[sqlite3.Connection], None]


MIGRATIONS: tuple[Migration, ...]  # module-level registry, sorted by target_version


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return PRAGMA user_version (0 on a fresh DB)."""


def run_migrations(
    conn: sqlite3.Connection,
    *,
    backup_dir: Path | None = None,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> int:
    """Apply all migrations whose target_version > current user_version.

    For each pending migration (in target_version order):
      1. If backup_dir is set AND the DB file is non-empty: copy it to
         ``{backup_dir}/ephemeral-sites.db.backup-v{current_version}``.
      2. BEGIN IMMEDIATE.
      3. Call migration.up(conn).
      4. ``PRAGMA user_version = target_version``.
      5. COMMIT. (On exception: ROLLBACK, re-raise.)

    Returns the final schema version.
    """
```

### 2.4 Migration v0 → v1: initial schema

Single migration for now, carrying the entire schema in master spec §6.1:

- Table `sites` with the 17 columns (PK `slug`, NOT NULL where specified, defaults matching spec).
- Index `idx_sites_expires` on `expires_at` WHERE `expires_at IS NOT NULL` (partial index).
- Index `idx_sites_created` on `created_at`.
- Table `api_keys` with 5 columns (PK `name`, disabled default 0).
- Table `event_log` with 6 columns (PK `id` AUTOINCREMENT).
- Index `idx_event_log_slug` on `event_log(slug)`.
- Index `idx_event_log_ts` on `event_log(timestamp)`.

### 2.5 Not in scope here

- **No CRUD helpers** (`insert_site`, `get_site`, etc.). Those live in later steps where the API routes need them.
- **No connection pool / thread-local session**. The app is single-replica and FastAPI will manage connection lifecycle (`async with` or per-request dependency). This module just returns an opened `sqlite3.Connection`.
- **No async wrapper**. SQLite with a single writer is fine on the sync thread; we avoid `aiosqlite` complexity. The API layer will call `open_db` inside `run_in_threadpool` when needed.
- **Event log purge** (spec §6.3 90-day retention). Lives in the cleanup module (step 13).

---

## 3. Acceptance Criteria

1. `open_db(path)` on a non-existent path creates the file.
2. After `open_db`, `PRAGMA journal_mode` returns `wal`.
3. After `open_db`, `PRAGMA foreign_keys` returns `1`.
4. After `open_db`, `PRAGMA busy_timeout` returns `5000`.
5. After `open_db`, `PRAGMA synchronous` returns `1` (NORMAL).
6. After `open_db` on a fresh DB, `get_schema_version(conn)` returns the highest `target_version` in MIGRATIONS (currently 1).
7. After `open_db`, the three tables `sites`, `api_keys`, `event_log` all exist.
8. After `open_db`, the indexes `idx_sites_expires`, `idx_sites_created`, `idx_event_log_slug`, `idx_event_log_ts` all exist.
9. `idx_sites_expires` is a partial index with predicate `expires_at IS NOT NULL` (verified via `sqlite_master.sql`).
10. Table `sites` has exactly the 17 columns specified by master spec §6.1.
11. Table `api_keys` has exactly the 5 columns specified.
12. Table `event_log` has exactly the 6 columns specified, and `id` is declared `INTEGER PRIMARY KEY AUTOINCREMENT`.
13. `sites.slug` is declared `PRIMARY KEY`.
14. `sites.password_hash` allows NULL; `sites.delete_token_hash` does NOT allow NULL.
15. `open_db` applied twice on the same path is idempotent (no errors, schema unchanged).
16. `run_migrations` on an already-current DB is a no-op (no backup taken, no SQL run).
17. `run_migrations` writes a backup file named `ephemeral-sites.db.backup-v{N-1}` before applying migration vN, when `backup_dir` is provided.
18. `run_migrations` does NOT write a backup file if `backup_dir` is `None`.
19. `run_migrations` rolls back and leaves `user_version` unchanged if a migration's `up` raises.
20. `open_db(..., read_only=True)` on a fresh path raises (read-only open of a non-existent DB).
21. `open_db(..., read_only=True)` on an existing DB does NOT run migrations.
22. A connection returned by `open_db` has `row_factory = sqlite3.Row`.
23. MIGRATIONS is sorted by `target_version` and each `target_version` is exactly one more than the previous.

---

## 4. Test List

All in `tests/unit/test_db.py`, using pytest's `tmp_path` fixture for file paths.

### 4.1 open_db basics

- [ ] `test_open_db_creates_file_on_fresh_path`
- [ ] `test_open_db_sets_journal_mode_wal`
- [ ] `test_open_db_sets_foreign_keys_on`
- [ ] `test_open_db_sets_busy_timeout_5000`
- [ ] `test_open_db_sets_synchronous_normal`
- [ ] `test_open_db_row_factory_is_sqlite_row`
- [ ] `test_open_db_twice_is_idempotent`
- [ ] `test_open_db_readonly_on_missing_file_raises`
- [ ] `test_open_db_readonly_does_not_run_migrations`

### 4.2 Schema

- [ ] `test_schema_creates_sites_table_with_expected_columns`
- [ ] `test_schema_creates_api_keys_table_with_expected_columns`
- [ ] `test_schema_creates_event_log_table_with_expected_columns`
- [ ] `test_sites_slug_is_primary_key`
- [ ] `test_sites_delete_token_hash_is_not_null`
- [ ] `test_sites_password_hash_is_nullable`
- [ ] `test_event_log_id_is_autoincrement`
- [ ] `test_schema_creates_expected_indexes`
- [ ] `test_idx_sites_expires_is_partial`

### 4.3 Migrations

- [ ] `test_get_schema_version_on_fresh_db_returns_0_before_migrations`
- [ ] `test_migration_v0_to_v1` — core test spec §11.3 mandates
- [ ] `test_run_migrations_idempotent_when_current`
- [ ] `test_run_migrations_creates_backup_before_applying`
- [ ] `test_run_migrations_skips_backup_when_backup_dir_none`
- [ ] `test_run_migrations_rolls_back_on_failure`
- [ ] `test_migrations_registry_is_strictly_linear`

### 4.4 Contract

- [ ] `test_migration_dataclass_is_frozen`
- [ ] `test_final_user_version_matches_highest_migration`

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- Fresh DB (file does not exist) → create + migrate to current version in one `open_db` call.
- Pre-existing DB at current version → PRAGMAs re-applied (cheap), no migration.
- Migration failure → transaction rolled back, `user_version` unchanged, exception bubbles up (startup fails loudly; better than a half-migrated DB silently corrupting later operations).

### 5.2 Deferred

- **Downgrade migrations** — deployment is forward-only per master spec §12.3. If a future operator needs to roll back, they restore from the pre-migration backup.
- **Concurrent migration from two processes** — out of scope in v1 (`replicas: 1` per master spec §3.3). SQLite's `BEGIN IMMEDIATE` would serialize them anyway.
- **Schema introspection helpers** (list tables, diff expected vs actual) — nice-to-have, not spec-mandated.

### 5.3 Explicitly non-goal

- **ORM** (SQLAlchemy, etc.). Spec says SQLite + stdlib. Adding an ORM would more than double the dependency footprint for no measurable benefit at this scale.
- **Per-row access layer** (DAO objects). Each feature module owns its own queries against the connection; this is the spirit of the master spec's "small and boring" stack.

---

## 6. Open Questions

(None — mini-spec approved.)

~~Q1: Single migration with the full schema, or multiple logical migrations (one per table) for v1?~~
→ Single migration. The initial schema is atomic from a deploy perspective; a partial v1 schema would be unusable anyway. Future additions (new tables, new columns) each get their own migration with their own `target_version`.

~~Q2: Should we use `PRAGMA user_version` or a custom `schema_migrations` table?~~
→ `PRAGMA user_version`. It's a single integer, free of race conditions with the migration itself (both live inside the same transaction), and costs zero bytes extra. Custom tables are standard in Django/Rails because those ecosystems expect N-step metadata (who ran what when, in which direction); we don't have that need.

~~Q3: Should `open_db` take an already-open connection so tests can inject `:memory:`?~~
→ No — we always take a `path`. For tests we use `tmp_path / "test.db"`. Using `:memory:` is tempting but creates a divergence between test and prod behavior (WAL works on disk, not in memory; path-based invariants differ). Real file in tmp_path costs ~a millisecond and is the right fidelity.

---

## 7. Done When

- [ ] All 27 tests in §4 committed and green on CI.
- [ ] Coverage ≥ 90% on `db.py` (business-critical).
- [ ] Ruff clean on changed files.
- [ ] `make check` green locally.
- [ ] Roadmap table in [`CLAUDE.md`](../../CLAUDE.md) §8 updated (Step 4 → ✅).
- [ ] This file's Status flipped to ✅.
