# Step 13 — Cleanup CronJob

**Master spec sections**: [§4.4 Flusso Cleanup](../SPEC.md), [§11.3 test_cleanup](../SPEC.md), [§6.3 event_log retention](../SPEC.md)
**Roadmap entry**: [§16.1 step 13](../SPEC.md)
**Status**: ⏳ Draft

---

## 1. Goal

Deliver the periodic reaper that deletes expired sites from disk + DB and purges old event_log rows. Runs every 5 minutes as a Kubernetes CronJob (wiring in step 15) but is exposed today as a module-level `run_cleanup()` function that can be invoked directly, tested, and entry-pointed from `python -m ephemeral_sites.cleanup`.

---

## 2. Public API / Contract

### 2.1 Module layout

- `src/ephemeral_sites/cleanup/__init__.py` — marker.
- `src/ephemeral_sites/cleanup/runner.py` — `run_cleanup(settings, conn) -> CleanupResult`.
- `src/ephemeral_sites/cleanup/__main__.py` — CLI entry point (reads env, opens DB, runs once, exits).
- `tests/integration/test_cleanup.py`.

### 2.2 Signatures

```python
@dataclass(frozen=True)
class CleanupResult:
    expired_slugs: tuple[str, ...]
    purged_events: int


def run_cleanup(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    now_iso: str | None = None,
    event_retention_days: int = 90,
) -> CleanupResult:
    """Execute one cleanup pass (master spec §4.4).

    Steps:
      1. SELECT slug FROM sites WHERE expires_at < now().
      2. For each: storage.delete_site + DELETE row + INSERT event_log (event='expired').
      3. If (weekly): DELETE event_log WHERE timestamp < now() - 90d.

    Returns counts for observability.
    """
```

### 2.3 Weekly purge cadence

Spec: "settimanalmente". We purge when `datetime.utcnow().isoweekday() == 1` (Monday). In tests inject `now_iso` to force either branch.

### 2.4 Scope

- No locking / leader election (single replica deployment per master spec §3.2).
- No metrics emission at this step (step 14 wires `ephemeral_sites_expired_total`).

---

## 3. Acceptance Criteria

1. Fresh DB, no rows → `run_cleanup` returns `CleanupResult(expired_slugs=(), purged_events=0)`.
2. One expired site → row deleted, directory deleted, event_log has `event='expired'`.
3. One not-yet-expired site (expires_at > now) → untouched.
4. Permanent site (expires_at IS NULL) → untouched.
5. Two expired sites → both reaped; returned tuple contains both slugs.
6. On Monday (day 1), event_log rows older than 90d are DELETEd.
7. On non-Monday, event_log purge is skipped.
8. If `storage.delete_site` fails for a slug (e.g. permission), the DB row is NOT deleted and the error is logged but does not abort the whole run.

---

## 4. Test List

- [ ] `test_cleanup_empty_db`
- [ ] `test_cleanup_reaps_expired_site`
- [ ] `test_cleanup_skips_future_expiry`
- [ ] `test_cleanup_skips_permanent_site`
- [ ] `test_cleanup_reaps_multiple`
- [ ] `test_cleanup_purges_event_log_on_monday`
- [ ] `test_cleanup_skips_event_log_purge_on_non_monday`

---

## 5. Done When

- [ ] 7 tests green.
- [ ] `make check` clean.
- [ ] Entry point `python -m ephemeral_sites.cleanup` works.
- [ ] CLAUDE.md + this file → ✅.
