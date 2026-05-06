# Step 7 — Global storage quota

**Master spec sections**: [§3.3 "Quota globale storage"](../SPEC.md), [§4.2 Flusso PUT punto 3](../SPEC.md), [§5.2 HTTP 507](../SPEC.md), [§9 maxTotalStorageBytes default 40 GiB](../SPEC.md), [§11.3 test_quota](../SPEC.md)
**Roadmap entry**: [§16.1 step 7](../SPEC.md)
**Status**: 🟡 Approved, in progress
**Owner**: Andrea Veronesi

---

## 1. Goal

Gate every upload against a **global** storage budget. Before extracting a ZIP (step 5) and committing the new row (step 8), the API layer computes `current_used + estimated_incoming` and refuses with HTTP 507 Insufficient Storage if that total would exceed the configured `maxTotalStorageBytes` (default 40 GiB, master spec §9).

The quota is **anti-abuse**, not anti-user: if the main API key leaks, a massive deploy burst is the cheapest damage vector and the quota caps it before the PVC fills up, which would take the entire pod down (including the read path).

This module stays small: a pure decision function + two "how much are we using?" utilities (DB sum, filesystem walk), no HTTP knowledge, no integration with validator/storage yet. Wiring into the PUT path happens in step 8.

---

## 2. Public API / Contract

### 2.1 Module layout

- `src/ephemeral_sites/quota.py` — decision function + the two summation helpers.
- `tests/unit/test_quota.py` — tests.

No new runtime deps; stdlib only.

### 2.2 Exception

```python
class QuotaExceeded(Exception):
    """Global storage quota would be exceeded by this upload. HTTP 507."""

    def __init__(
        self,
        *,
        current_used: int,
        incoming: int,
        max_total: int,
    ) -> None:
        super().__init__(
            f"quota exceeded: {current_used + incoming} > {max_total} "
            f"(current={current_used}, incoming={incoming})"
        )
        self.current_used = current_used
        self.incoming = incoming
        self.max_total = max_total
```

The attributes are accessible so the API layer can structure the 507 response with machine-readable fields (`available_bytes`, `requested_bytes`) if it wants to — step 8 decides.

### 2.3 Decision function

```python
def check_quota(
    *,
    current_used: int,
    incoming: int,
    max_total: int,
) -> None:
    """Raise :class:`QuotaExceeded` if admitting ``incoming`` would push
    the used total over ``max_total``.

    The test is ``current_used + incoming > max_total`` (strict). An
    upload that lands exactly on the cap is accepted; the cap itself
    is a soft limit nibbled by default 20 % headroom in the Helm
    values (50 GiB PVC, 40 GiB quota per master spec §9).
    """
```

### 2.4 Usage summation helpers

```python
def sum_active_sites_bytes(conn: sqlite3.Connection) -> int:
    """``SELECT COALESCE(SUM(size_bytes), 0) FROM sites``.

    Returns the sum over every row in the ``sites`` table — including
    rows whose ``expires_at`` is in the past but have not yet been
    cleaned up by the CronJob. This is conservative: treating
    soon-to-be-reaped rows as "used" is safer than excluding them
    and over-committing.
    """


def sum_filesystem_bytes(
    sites_root: Path,
    *,
    exclude_prefixes: tuple[str, ...] = (".",),
    exclude_suffixes: tuple[str, ...] = (".new", ".old"),
) -> int:
    """Recursively sum file sizes under ``sites_root``.

    Top-level entries whose name starts with any of ``exclude_prefixes``
    (default: ``.``, covering ``.lock/``) or ends with any of
    ``exclude_suffixes`` (default: ``.new``, ``.old`` — in-flight
    extractions / rollbacks) are skipped.

    Returns 0 if ``sites_root`` does not exist.
    """
```

The two helpers exist because the API layer has two valid ways to answer "how much am I using":

- **DB sum**: fast (SQL aggregate over an indexed table), reflects what the metadata layer believes. Used by the PUT path — consistent with the row it is about to insert.
- **Filesystem walk**: slow but ground-truth. Used by the metrics endpoint (step 14) and by a future consistency check. Also the right choice on startup before the DB exists (degenerate paths).

Either return value can feed :func:`check_quota`. The module does not pick one over the other — the caller chooses based on context.

### 2.5 Not in scope here

- **Enforcement wiring** into the PUT/POST routes. Step 8 imports these helpers and adds the 507 branch before calling `storage.extract_site`.
- **Per-API-key quotas** (master spec §5.1 says "tutte le keys hanno stesso potere", so no per-key quota in v1).
- **Metrics**: `ephemeral_sites_storage_bytes` gauge exists in master spec §5.8, populated in step 14.

---

## 3. Acceptance Criteria

1. `check_quota(current_used=0, incoming=10, max_total=100)` returns `None` silently.
2. `check_quota(current_used=90, incoming=10, max_total=100)` returns `None` (exactly at cap accepted).
3. `check_quota(current_used=90, incoming=11, max_total=100)` raises `QuotaExceeded`.
4. The raised exception's `.current_used`, `.incoming`, `.max_total` attributes carry the input values.
5. `str(QuotaExceeded(...))` includes the three numbers so operators can see what happened in logs.
6. `sum_active_sites_bytes(conn)` on a fresh DB returns `0`.
7. `sum_active_sites_bytes(conn)` after inserting 3 rows with `size_bytes = 100, 200, 300` returns `600`.
8. `sum_active_sites_bytes(conn)` ignores the `expires_at` column — it sums all rows.
9. `sum_active_sites_bytes(conn)` handles `COALESCE(NULL, 0) = 0` correctly (if `size_bytes` is ever nullable, sum is still integer).
10. `sum_filesystem_bytes(path)` on a non-existent path returns `0`.
11. `sum_filesystem_bytes(path)` sums every regular file under a populated tree.
12. `sum_filesystem_bytes` excludes the `.lock/` directory by default (dotfile rule).
13. `sum_filesystem_bytes` excludes `{slug}.new/` and `{slug}.old/` directories by default.
14. `sum_filesystem_bytes` follows the default exclusions only at the TOP level; nested `.foo` inside a site dir is counted (we don't walk-rewrite inside site directories).
15. `QuotaExceeded` is a subclass of `Exception` (plain), not `OSError` or `ValueError` — it's a domain-level resource-exhaustion signal.

---

## 4. Test List

All in `tests/unit/test_quota.py`. Uses `tmp_path` for filesystem tests and the real `ephemeral_sites.db` module for the SQL helper (the DB layer is already green and mature).

### 4.1 check_quota (5)

- [ ] `test_check_quota_passes_under_limit`
- [ ] `test_check_quota_passes_at_exact_limit`
- [ ] `test_check_quota_raises_over_limit`
- [ ] `test_quota_exceeded_attributes_populated`
- [ ] `test_quota_exceeded_str_contains_numbers`

### 4.2 sum_active_sites_bytes (4)

- [ ] `test_sum_active_sites_bytes_empty_db`
- [ ] `test_sum_active_sites_bytes_aggregates_rows`
- [ ] `test_sum_active_sites_bytes_includes_rows_regardless_of_expiry`
- [ ] `test_sum_active_sites_bytes_requires_sites_table_present`  (integration hook — raise on missing table? or return 0? Spec silent; we raise.)

### 4.3 sum_filesystem_bytes (5)

- [ ] `test_sum_filesystem_bytes_missing_root_returns_zero`
- [ ] `test_sum_filesystem_bytes_empty_root_returns_zero`
- [ ] `test_sum_filesystem_bytes_sums_regular_files`
- [ ] `test_sum_filesystem_bytes_excludes_top_level_dotdirs`
- [ ] `test_sum_filesystem_bytes_excludes_new_and_old_dirs`

### 4.4 Contract (2)

- [ ] `test_quota_exceeded_is_exception_subclass`
- [ ] `test_exceeds_global_quota_returns_507_semantics`  (the master-spec §11.3 test: a small end-to-end showing that when current_used + incoming > max_total, QuotaExceeded is raised, which is what the API layer will map to 507)

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- Zero-byte files (`size_bytes = 0`, empty SPAs) — counted as 0, no special case.
- `incoming = 0` — passes check_quota as long as current_used ≤ max_total (vacuous case; API layer would not call quota with 0, but the function behaves sensibly).

### 5.2 Deferred

- **Incremental size tracking via inotify**. Would avoid `walk()` on the static server side. Not needed at ≤ 100 sites.
- **Per-API-key quota** (spec §5.1 explicitly single-tier).
- **Grace period**: accepting a small overage (e.g. +10 %) and warning asynchronously. Master spec says strict cap; we follow.

### 5.3 Explicitly non-goal

- **Free-space probing of the PVC** (`os.statvfs`). That's Kubernetes/PVC health, not our quota. The 40 GiB cap leaves 20 % PVC headroom (50 GiB PVC, master spec §9) so the statvfs-based signal arrives later.

---

## 6. Open Questions

(None — mini-spec approved.)

~~Q1: Strict `>` or non-strict `>=` for the cap?~~
→ Strict `>`. A deploy that fits *exactly* in the remaining budget is accepted. The 20 % PVC headroom (PVC 50 GiB vs quota 40 GiB) absorbs the few-byte overhead of the metadata row + lock file + .db-wal.

~~Q2: Should `sum_active_sites_bytes` filter by `expires_at IS NULL OR expires_at > now()`?~~
→ No. Cleanup is asynchronous (every 5 min per master spec §3.3). An expired-but-not-reaped row still occupies bytes on the PVC; over-committing would defeat the point of the quota. Include everything.

~~Q3: Hard-code the exclusions list (`.lock`, `.new`, `.old`) or expose them?~~
→ Expose as kwargs with sensible defaults. The metrics endpoint (step 14) may want different exclusions; exposing costs one line of API surface and saves a refactor.

~~Q4: What about hidden files placed inside a site directory (e.g. `dist/.htaccess` from an accidental glob)?~~
→ Counted. The top-level dot exclusion covers our own infra (`.lock/`); once we descend into a user site, everything counts toward the quota they're paying for (… well, the owner is paying for; this is single-user).

---

## 7. Done When

- [ ] All 16 tests in §4 committed and green on CI.
- [ ] Coverage ≥ 90% on `quota.py`.
- [ ] Ruff clean.
- [ ] `make check` green locally.
- [ ] Roadmap table in [`CLAUDE.md`](../../CLAUDE.md) §8 updated (Step 7 → ✅).
- [ ] This file's Status flipped to ✅.
