# Step 5 — Storage atomic swap

**Master spec sections**: [§4.2 Flusso PUT extraction + swap](../SPEC.md), [§5.5 DELETE semantics](../SPEC.md), [§6.2 Filesystem layout](../SPEC.md), [§11.3 test_storage_atomic](../SPEC.md)
**Roadmap entry**: [§16.1 step 5](../SPEC.md)
**Status**: 🟡 Approved, in progress
**Owner**: Andrea Veronesi

---

## 1. Goal

Provide the filesystem layer that takes a validated ZIP (step 2 output) and lands it under `/data/sites/{slug}/` with two guarantees that the rest of the system depends on:

1. **Zero-404 window on re-deploy** — between the old and new content being visible to the HTTP server, the path `/data/sites/{slug}/` is never missing. Achieved via POSIX atomic `rename()` of sibling directories on the same filesystem, serialized by `flock` on a per-slug lock file.
2. **Rollback on failure** — any exception during extraction leaves the filesystem in a clean state: `{slug}.new/` removed, `{slug}/` (if it existed) untouched, no `{slug}.old/` leftover. The caller can retry without manual cleanup.

This module is the "doing" counterpart to the validator's "planning". It owns the filesystem; nothing else in the project writes directly under `/data/sites/`.

---

## 2. Public API / Contract

### 2.1 Module layout

- `src/ephemeral_sites/storage.py` — the extraction / swap / delete functions + lock helper.
- `tests/unit/test_storage.py` — tests including the concurrency test `test_overwrite_no_404_window` (spec §11.3).

No new runtime deps; uses stdlib `fcntl`, `pathlib`, `shutil`, `zipfile`, `contextlib`, `json`.

**Platform**: POSIX (Linux, macOS). `fcntl` is not available on Windows. The deploy target is Kubernetes on Linux per master spec §3.2, so this is acceptable. Tests skip on non-POSIX.

### 2.2 Exceptions

```python
class ExtractionError(OSError):
    """Filesystem-level failure during extract_site. Maps to HTTP 500.

    Raised only after rollback has been performed, so the filesystem is
    in a clean, retryable state when this propagates out.
    """
```

### 2.3 Types

```python
@dataclass(frozen=True)
class ExtractionResult:
    """Bookkeeping returned to the API layer after a successful extract."""
    slug: str
    site_path: Path       # absolute path to the final /data/sites/{slug}/
    files_written: int    # equals len(validation.entries)
    total_bytes_written: int
```

### 2.4 Functions

```python
def extract_site(
    *,
    sites_root: Path,
    slug: str,
    zip_source: bytes | BinaryIO,
    validation: ValidationResult,
    runtime_config: str | None = None,
    lock_dir: Path | None = None,
) -> ExtractionResult:
    """Extract a validated ZIP to sites_root/{slug}/ atomically.

    Parameters:
        sites_root: The parent directory (e.g. /data/sites). Created if missing.
        slug: Already validated (step 3) and safe to use as a path segment.
        zip_source: Raw bytes or a seekable binary stream of the same ZIP
            that was fed to validator.validate_zip(). The validator did
            NOT extract, so we re-read here.
        validation: The ValidationResult returned by the validator.
            entries[i].zip_name is used to read from the ZIP;
            entries[i].target_rel_path is the extraction destination.
        runtime_config: If provided, written as {slug}.new/config.json
            before the swap. Must be a JSON string (caller serializes).
        lock_dir: Directory holding the flock files. Default:
            sites_root / ".lock". Created if missing.

    Returns:
        ExtractionResult on success.

    Raises:
        ExtractionError: on any filesystem or extraction failure. The
            filesystem is clean (no leftover tmp dirs) before this is raised.
    """


def delete_site(
    *,
    sites_root: Path,
    slug: str,
    lock_dir: Path | None = None,
) -> bool:
    """Remove sites_root/{slug}/ under the same per-slug flock.

    Returns:
        True if a directory existed and was removed. False if the slug
        had no directory (idempotent delete — callers that want a 404
        must check the DB first).
    """
```

### 2.5 Atomic-swap protocol

The critical section, under `flock(sites_root/.lock/{slug}.lock, LOCK_EX)`:

```
1. If sites_root/{slug}.new exists → rm -rf  (abandoned previous attempt)
2. mkdir sites_root/{slug}.new (parents ok)
3. For each entry in validation.entries:
   a. Compute dest = sites_root/{slug}.new / entry.target_rel_path
   b. Realpath check: dest must resolve inside {slug}.new (defense in
      depth — the validator already rejected path traversal, but a
      bug in _either_ module must not breach the sandbox).
   c. mkdir dest.parent (ok_exist)
   d. With zf.open(entry.zip_name) as src, dest.open("wb") as dst:
        shutil.copyfileobj(src, dst)
4. If runtime_config is provided → write {slug}.new/config.json (utf-8).
5. fsync directory {slug}.new (durability — matters on real disks).
6. If sites_root/{slug} exists → rename to {slug}.old.
7. rename {slug}.new → {slug}.
8. If {slug}.old existed → rm -rf {slug}.old.
```

On exception during 1–7:

- `rm -rf {slug}.new` (ignore errors).
- If step 6 succeeded but 7 failed: `rename {slug}.old → {slug}` to restore
  the old content, then `rm -rf {slug}.old` if partial. In practice step
  7 is a rename inside the same directory — it can only fail if the
  filesystem is full or crashes; the "bring back old" path is a
  belt-and-suspenders recovery.
- Always release the flock (via `with` block).

The per-slug lock guarantees:

- Two concurrent `extract_site` calls on the same slug serialize; the
  second one sees the new content.
- A concurrent `delete_site` on the same slug serializes.
- A concurrent extract on a *different* slug proceeds in parallel (the
  lock is per-slug, not global).

### 2.6 Zero-404 window proof

Between step 6 and step 7, `{slug}` temporarily does not exist. That window is one `rename()` syscall — microseconds under normal load. More importantly, an HTTP reader that was reading `{slug}/index.html` holds an open FD (or inode via directory walk) that keeps pointing at the old inode until it's done. The rename at step 6 does not unlink; it moves the directory entry to `{slug}.old`, still reachable by any FD already open. The rename at step 7 replaces the directory entry for `{slug}` atomically. A reader that opens `{slug}/index.html` between steps 6 and 7 will get `ENOENT`. The test `test_overwrite_no_404_window` exercises a long read thread during a redeploy and asserts it never observes `ENOENT` on the *directory* (a brief fail on step 6→7 is possible for unlucky timing, but we retry with a read loop that mimics HTTP serve-then-reopen behavior; see §2.7).

### 2.7 Test strategy for `test_overwrite_no_404_window`

The real HTTP server (step 11) will:
- Re-open `{slug}/index.html` on each request.
- With the cache (60s TTL per spec §4.3) hitting the already-open DB row.
- Under a reader request for 10ms, the cache absorbs most of the window.

The test models a **conservative** reader: in a thread, loop reading `{slug}/index.html` for 200ms while main thread re-deploys. Assert that >= 99% of reads succeed. A single flake over the rename-rename sub-millisecond window is accepted because the cache+retry in the real server absorbs it; asserting 100% would be over-fitting to the test environment.

### 2.8 Defense-in-depth path check

Even though the validator rejects path traversal, `extract_site` re-checks that `dest.resolve()` is inside `{slug}.new/` after join. If not, raise `ExtractionError` with a stable reason string (logged). Two layers of defense; neither trusts the other.

---

## 3. Acceptance Criteria

1. `extract_site` creates `sites_root/{slug}/` containing exactly the files listed in `validation.entries`, at their `target_rel_path` positions (flattening already baked into the ValidationResult).
2. File contents match `zf.read(entry.zip_name)` byte-for-byte.
3. When `runtime_config` is provided (non-None), `sites_root/{slug}/config.json` exists and equals the provided string.
4. When `runtime_config` is `None`, no `config.json` file is created.
5. After a successful call, `sites_root/{slug}.new` does not exist.
6. After a successful call, `sites_root/{slug}.old` does not exist.
7. Re-deploying the same slug replaces the content atomically: old files not present in the new archive are gone, new files are present, files common to both carry the new content.
8. `extract_site` on a non-existent `sites_root` creates it.
9. `extract_site` on a non-existent `lock_dir` (or the default `sites_root/.lock`) creates it.
10. `delete_site` on an existing slug removes `sites_root/{slug}/` and returns `True`.
11. `delete_site` on a missing slug returns `False` without raising.
12. Two concurrent `extract_site` calls on the **same** slug serialize (second sees first's result via the flock).
13. An exception raised mid-extraction (simulated ZIP read failure) leaves `sites_root/{slug}.new` absent and `sites_root/{slug}/` unchanged, then re-raises as `ExtractionError`.
14. An attempt to write an entry whose resolved path escapes `{slug}.new/` is rejected with `ExtractionError` (defense in depth vs validator).
15. Parent directories inside the site (e.g. `static/` for `static/app.js`) are created as needed.
16. `test_overwrite_no_404_window` observes ≥ 99% successful reads during a concurrent redeploy.
17. `ExtractionError` is a subclass of `OSError`.

---

## 4. Test List

All in `tests/unit/test_storage.py`. Tests that rely on `fcntl` are marked `pytest.mark.skipif(not sys.platform.startswith("linux") and sys.platform != "darwin")` — we're POSIX-only per master spec §3.2.

### 4.1 Happy path

- [ ] `test_extract_site_creates_files_matching_entries`
- [ ] `test_extract_site_file_contents_match_zip`
- [ ] `test_extract_site_creates_parent_dirs_inside_site`
- [ ] `test_extract_site_creates_sites_root_if_missing`
- [ ] `test_extract_site_creates_lock_dir_if_missing`
- [ ] `test_extract_site_writes_runtime_config_when_provided`
- [ ] `test_extract_site_no_config_json_when_runtime_config_none`
- [ ] `test_extract_site_returns_populated_result`

### 4.2 Atomic swap

- [ ] `test_extract_site_leaves_no_new_dir_after_success`
- [ ] `test_extract_site_leaves_no_old_dir_after_success`
- [ ] `test_extract_site_redeploy_replaces_content`
- [ ] `test_extract_site_redeploy_drops_files_not_in_new_archive`

### 4.3 Error / rollback

- [ ] `test_extract_site_rollback_leaves_new_dir_absent_on_failure`
- [ ] `test_extract_site_rollback_leaves_existing_site_untouched_on_failure`
- [ ] `test_extract_site_rejects_path_escaping_site_dir` (@pytest.mark.security)
- [ ] `test_extract_site_raises_extraction_error_on_io_failure`

### 4.4 Concurrency (the critical tests)

- [ ] `test_extract_site_serializes_concurrent_same_slug`
- [ ] `test_extract_site_parallel_different_slugs_do_not_block`
- [ ] `test_overwrite_no_404_window` — core concurrency test from spec §11.3

### 4.5 Delete

- [ ] `test_delete_site_removes_existing`
- [ ] `test_delete_site_returns_false_on_missing`
- [ ] `test_delete_site_serializes_with_extract`

### 4.6 Contract

- [ ] `test_extraction_error_is_oserror`

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- ValidationResult with 0 entries — invalid per validator (empty_archive), never reaches here. But defensively, the happy path code does not special-case 0 entries (the loop is simply empty), so it works.
- `target_rel_path` with nested directories (e.g. `static/css/app.css`) — parent dirs created automatically.
- UTF-8 filenames — Python `pathlib.Path` handles them natively on modern Linux/macOS.
- Same slug called twice in quick succession — the flock serializes them; the second one overwrites the first's result cleanly.

### 5.2 Deferred

- **fsync durability guarantees** — we `fsync` the new-site directory before swap, but not on every file. Master spec does not mandate fsync; the cost (5–50 ms per extraction) outweighs the durability benefit for an ephemeral-by-design service.
- **Quota enforcement** — step 7 (quota.py) checks the free space *before* calling extract_site. Here we assume enough space; if the disk fills mid-extract, `copyfileobj` raises `OSError` and the rollback path fires.
- **Windows support** — POSIX-only. `fcntl` makes concurrency safe on Linux / macOS; Windows would need a different mechanism (MSVCRT file locking via `msvcrt.locking` or a named mutex). Not in scope.

### 5.3 Explicitly non-goal

- **Streaming extraction for memory efficiency** — master spec §7.1 caps the upload at `max_zip_size` (default 500 MiB) and the pod has 512 MiB memory limit (spec §9 app.resources). We don't hold the whole ZIP in memory; `zipfile.ZipFile` opens each entry as a stream and `shutil.copyfileobj` copies in 64 KiB chunks. Good enough.
- **Compression-on-write** — files are stored uncompressed on disk. The static server serves them raw; Traefik compresses the HTTP response if the client requests it.

---

## 6. Open Questions

(None — mini-spec approved.)

~~Q1: Per-slug flock vs. global lock?~~
→ Per-slug. A global lock would unnecessarily serialize all deployments (the service is single-user so contention is rare, but paying the cost for nothing is wasteful). The per-slug flock is the minimal correct scope.

~~Q2: `fcntl.flock` vs `fcntl.lockf` vs `portalocker` package?~~
→ `fcntl.flock` (advisory, BSD-style). `lockf` is POSIX locks which have broken inheritance semantics (releasing one FD releases all locks on the file). `portalocker` is a non-stdlib wrapper that adds no value here. `flock` is the simplest primitive that gives us what we need.

~~Q3: Should `extract_site` take the already-opened `zipfile.ZipFile`, or open it internally?~~
→ Open internally from `zip_source`. The validator takes the raw bytes/stream; the extractor does the same. Callers don't share open ZipFile instances between modules — the interface stays simple at the cost of a second parse of the central directory (~microseconds).

~~Q4: Should we re-run the full validator inside `extract_site` for defense in depth?~~
→ No. That's wasted work and blurs the layering. We *do* re-check the resolved extraction path stays inside `{slug}.new/` (§2.8) because that's a cheap final gate against path-traversal bugs in the validator. Re-running the full validator (zip bomb, extension whitelist, etc.) would double the CPU cost for every upload.

---

## 7. Done When

- [ ] All 22 tests in §4 committed and green on CI.
- [ ] Coverage ≥ 90% on `storage.py`.
- [ ] Ruff clean on changed files.
- [ ] `make check` green locally (concurrency test must be deterministic — reruns 10× without flaking).
- [ ] Roadmap table in [`CLAUDE.md`](../../CLAUDE.md) §8 updated (Step 5 → ✅).
- [ ] This file's Status flipped to ✅.
