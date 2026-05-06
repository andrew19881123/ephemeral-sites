# Step 2 — ZIP validator

**Master spec sections**: [§7.1 Validation ZIP (critica)](../SPEC.md), [§11.3 Test critici da implementare da subito](../SPEC.md), [§4.2 Flusso PUT point 4](../SPEC.md)
**Roadmap entry**: [§16.1 step 2](../SPEC.md)
**Status**: ✅ Complete (2026-05-06, commit `f612aca`)
**Owner**: Andrea Veronesi

---

## 1. Goal

Implement the ZIP safety gate used by `PUT /api/v1/sites/{slug}` (and `POST /api/v1/sites`) to reject dangerous archives **before** any filesystem write. The validator answers one question: "is this ZIP safe to extract under `/data/sites/<slug>/`?" and, as a side output, tells the caller how to extract it (flatten a single top-level folder if present).

This is the single most security-critical module of the project. Every rule here prevents a concrete class of attack:

| Rule | Attack it prevents |
|------|-------------------|
| Reject path traversal (`..`, leading `/`, empty segments, Windows drive letters) | Write outside the site sandbox (`/etc/shadow`, another site's dir) |
| Reject symlinks | Read-through or overwrite arbitrary files on the pod |
| Reject zip bombs (ratio, total size, single-file size) | Disk exhaustion / DoS even when global quota check passes |
| Reject excessive file count | Inode exhaustion / slow extraction DoS |
| Reject non-whitelisted extensions | Serving `.php`, `.exe`, etc. and widening the attack surface |
| Require `index.html` | Useless uploads + fail-fast for the user |

The validator is **pure**: no I/O other than reading the ZIP bytes, no logging side effects, no `time.time()` / `uuid` usage. This makes it trivially unit-testable.

---

## 2. Public API / Contract

### 2.1 Module layout

- `src/ephemeral_sites/validator.py` — the validator (~150 lines expected).
- `tests/unit/test_validator.py` — tests (one class per rejection reason + happy paths).

No new runtime dependencies; uses stdlib `zipfile`, `io`, `pathlib`, `dataclasses`.

### 2.2 Types

```python
from dataclasses import dataclass, field
from typing import BinaryIO


# Reason codes — stable taxonomy for error responses + logs + metrics.
REASON_PATH_TRAVERSAL        = "path_traversal"
REASON_ABSOLUTE_PATH         = "absolute_path"
REASON_SYMLINK               = "symlink"
REASON_ZIP_BOMB_RATIO        = "zip_bomb_ratio"
REASON_ZIP_BOMB_TOTAL_SIZE   = "zip_bomb_total_size"
REASON_ZIP_BOMB_SINGLE_FILE  = "zip_bomb_single_file"
REASON_TOO_MANY_FILES        = "too_many_files"
REASON_DISALLOWED_EXTENSION  = "disallowed_extension"
REASON_MISSING_INDEX_HTML    = "missing_index_html"
REASON_EMPTY_ARCHIVE         = "empty_archive"
REASON_ENCRYPTED             = "encrypted_archive"
REASON_INVALID_ZIP           = "invalid_zip"


class ValidationError(Exception):
    """Validation failure. Maps to HTTP 400. Carries a machine-readable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class ValidatorConfig:
    """Runtime limits the validator enforces. Injected from app config / Helm values."""

    max_zip_size: int              # bytes; caller enforces content-length separately
    max_files_per_site: int        # count of non-directory entries
    max_decompression_ratio: int   # e.g. 100 means 1:100 (uncompressed/compressed)
    allowed_extensions: frozenset[str]  # lowercase, leading dot (".html", ".css", ...)


@dataclass(frozen=True)
class ValidatedEntry:
    """One file to extract. Directories are NOT included."""

    zip_name: str         # original name inside the ZIP (for `ZipFile.read(...)`)
    target_rel_path: str  # POSIX-style path relative to the site root AFTER flattening


@dataclass(frozen=True)
class ValidationResult:
    """Result of a successful validation.

    Caller uses `entries` to extract each file to
    `/data/sites/{slug}.new/{entry.target_rel_path}`.
    """

    entries: tuple[ValidatedEntry, ...]
    total_uncompressed_size: int
    files_count: int
    flatten_prefix: str | None  # None means no flattening; else the prefix that was stripped
```

### 2.3 Functions

```python
def validate_zip(source: bytes | BinaryIO, config: ValidatorConfig) -> ValidationResult:
    """Validate a ZIP archive intended for publishing as a static SPA site.

    `source` is either raw bytes or a readable binary stream (e.g. a SpooledTemporaryFile
    from FastAPI's UploadFile).

    Runs all rules; returns on the first violation (fail-fast). The order of checks is:

      1. Parse ZIP structure (catch BadZipFile → REASON_INVALID_ZIP)
      2. Reject encrypted entries (REASON_ENCRYPTED)
      3. Reject symlinks (REASON_SYMLINK)
      4. Reject path traversal / absolute paths (REASON_PATH_TRAVERSAL, REASON_ABSOLUTE_PATH)
      5. Reject disallowed extensions (REASON_DISALLOWED_EXTENSION)
      6. Count files → enforce max_files_per_site (REASON_TOO_MANY_FILES)
      7. Per-entry: reject single-file bomb (REASON_ZIP_BOMB_SINGLE_FILE)
      8. Aggregate: reject total size bomb (REASON_ZIP_BOMB_TOTAL_SIZE)
      9. Aggregate: reject ratio bomb (REASON_ZIP_BOMB_RATIO)
     10. Reject empty archive (REASON_EMPTY_ARCHIVE) and determine index.html
         location → flatten if all files live under a single top-level folder
         (REASON_MISSING_INDEX_HTML if index.html is not found after flattening)

    Returns:
        ValidationResult with the entry list, sizes, flatten decision.

    Raises:
        ValidationError: first rule violated.
    """
```

### 2.4 Normalization rules (private helpers, documented here so tests agree)

The validator must normalize every `ZipInfo.filename` before applying path rules:

- Replace `\\` with `/` (Windows-created ZIPs use backslash).
- Reject if the resulting path contains:
  - a segment equal to `..`
  - a leading `/` (absolute Unix path)
  - a drive-letter prefix (`^[A-Za-z]:[/\\]`) (absolute Windows path)
  - any embedded null byte
- A `ZipInfo.filename` ending in `/` is a directory entry — not counted in `files_count`, not added to `entries`, and still subject to path-traversal rules on its segments.

### 2.5 Symlink detection

ZIP stores Unix file mode in the top 16 bits of `ZipInfo.external_attr` under a Unix-created archive (`create_system == 3`). A symlink has file-type bits `0o120000`. The validator rejects an entry if:

```python
((zinfo.external_attr >> 16) & 0o170000) == 0o120000
```

### 2.6 Encryption detection

A ZIP entry is encrypted iff `zinfo.flag_bits & 0x1`. Reject on first encrypted entry.

### 2.7 Flattening rules

After all safety checks pass:

1. If a file named `index.html` exists at the root → no flattening, `flatten_prefix = None`.
2. Else, if all non-directory entries share a common top-level directory segment `X/` AND `X/index.html` exists → flatten: `flatten_prefix = "X/"`, each entry's `target_rel_path = zip_name[len("X/"):]`.
3. Else → raise `REASON_MISSING_INDEX_HTML`.

---

## 3. Acceptance Criteria

1. Happy path: a ZIP with `index.html`, `static/app.js`, `static/app.css` at root validates, returns 3 entries, `flatten_prefix is None`.
2. Happy path flatten: a ZIP whose entries all live under `dist/` and include `dist/index.html` validates, returns 3 entries with `target_rel_path` stripped of `dist/`, `flatten_prefix == "dist/"`.
3. An entry named `../../etc/passwd` is rejected with `REASON_PATH_TRAVERSAL`.
4. An entry with a path segment `..` anywhere (`foo/../bar.html`) is rejected with `REASON_PATH_TRAVERSAL`.
5. An entry with an absolute Unix path (`/etc/shadow`) is rejected with `REASON_ABSOLUTE_PATH`.
6. An entry with a Windows drive letter (`C:\Windows\evil.html`) is rejected with `REASON_ABSOLUTE_PATH`.
7. An entry that is a Unix symlink (external_attr encodes mode 0o120777) is rejected with `REASON_SYMLINK`.
8. A zip with an entry whose uncompressed size is greater than `max_zip_size * 2` is rejected with `REASON_ZIP_BOMB_SINGLE_FILE`.
9. A zip whose total uncompressed size is greater than `max_zip_size * 10` is rejected with `REASON_ZIP_BOMB_TOTAL_SIZE`.
10. A zip whose compression ratio exceeds `max_decompression_ratio` (uncompressed/compressed > 100) is rejected with `REASON_ZIP_BOMB_RATIO`.
11. A zip with more than `max_files_per_site` non-directory entries is rejected with `REASON_TOO_MANY_FILES`.
12. A zip containing an entry with a non-whitelisted extension (e.g. `.php`, `.DS_Store`) is rejected with `REASON_DISALLOWED_EXTENSION`.
13. A zip without `index.html` anywhere valid is rejected with `REASON_MISSING_INDEX_HTML`.
14. A zip with entries under multiple top-level folders and no root `index.html` is rejected with `REASON_MISSING_INDEX_HTML`.
15. An empty zip is rejected with `REASON_EMPTY_ARCHIVE`.
16. A password-protected (encrypted) zip is rejected with `REASON_ENCRYPTED`.
17. Bytes that are not a valid ZIP are rejected with `REASON_INVALID_ZIP`.
18. `ValidationError.reason_code` is a stable string drawn from the `REASON_*` constants (no free-form messages).
19. The validator does not perform any filesystem I/O (test: temporary cwd contains no new files after running).
20. The validator accepts both `bytes` and a binary stream as input.

---

## 4. Test List

All tests live in `tests/unit/test_validator.py` and use a helper `_make_zip(entries)` that builds in-memory ZIPs from a dict of `{path: content}` (entries can also be directory markers or symlink records, specified via a small dataclass for the non-trivial cases).

### 4.1 Happy paths

- [ ] `test_accepts_valid_flat_spa`
- [ ] `test_accepts_valid_nested_spa_and_flattens`
- [ ] `test_accepts_bytes_and_stream_inputs_equivalently`

### 4.2 Path safety (security)

Marked `@pytest.mark.security`:

- [ ] `test_rejects_path_traversal_dotdot_leading`
- [ ] `test_rejects_path_traversal_dotdot_embedded`
- [ ] `test_rejects_absolute_path_unix`
- [ ] `test_rejects_absolute_path_windows_drive`
- [ ] `test_rejects_null_byte_in_name`
- [ ] `test_rejects_symlink_entry`

### 4.3 Zip bombs (security)

Marked `@pytest.mark.security`:

- [ ] `test_rejects_zip_bomb_by_ratio`
- [ ] `test_rejects_zip_bomb_by_total_uncompressed_size`
- [ ] `test_rejects_zip_bomb_by_single_file_size`

### 4.4 Quotas / limits

- [ ] `test_rejects_excessive_file_count`
- [ ] `test_rejects_non_whitelisted_extension`

### 4.5 Structural

- [ ] `test_rejects_missing_index_html_at_root_and_no_flatten_candidate`
- [ ] `test_rejects_multiple_top_level_folders_without_root_index`
- [ ] `test_rejects_empty_zip`
- [ ] `test_rejects_encrypted_zip`
- [ ] `test_rejects_invalid_zip_bytes`

### 4.6 Contract

- [ ] `test_validation_error_carries_reason_code`
- [ ] `test_reason_codes_are_stable_strings`
- [ ] `test_validator_performs_no_filesystem_io`

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- Windows backslash paths — normalized to `/` before checks.
- Directory entries (`ZipInfo.filename` ending in `/`) — skipped in `entries`, still subject to path-traversal rule on segments.
- Unicode filenames (UTF-8 ZIP flag bit 11) — accepted; extension check uses `str.casefold()` to match whitelist case-insensitively.
- ZIP64 (large archives with >2^32 bytes or >65535 entries) — supported transparently by stdlib `zipfile`.

### 5.2 Deferred

- **Extraction** — handled in step 5 (`storage.py`) with atomic swap. Validator only plans.
- **Runtime config injection** (`config.json`) — step 10. Validator doesn't know about it.
- **Content sniffing** (e.g. "does this `.png` actually start with PNG magic?") — not required by the spec; whitelist of extensions is the line of defense.

### 5.3 Explicitly non-goal (from master spec §14 and §2.2 design note)

- Virus / malware scanning of file contents.
- Supporting archive formats other than ZIP (no tar.gz, no 7z, no rar).

---

## 6. Open Questions

(None remaining — mini-spec approved.)

~~Q1: Should the validator silently skip `.DS_Store` / `Thumbs.db` or reject the whole ZIP?~~
→ Reject. Strict whitelist. The error detail includes the offending filename so the user learns to `zip -x '*.DS_Store'`. Cheap to fix on the user's side, avoids a whole class of "I didn't know that file was there" bugs.

~~Q2: Where does the `max_decompression_ratio` threshold apply — globally or per-file?~~
→ Globally, as spec §7.1 states ("ratio globale"). Per-file ratio is not meaningful for SPAs (a tiny manifest.json can have absurd ratios without being a bomb). The single-file-size cap already covers single-file bombs.

~~Q3: `max_zip_size` in the config — is it the *compressed* bound or the *decompressed* bound?~~
→ Compressed bound (what the HTTP upload carries). The validator computes two derived limits:
  `total_uncompressed_cap = max_zip_size * 10` (from spec §7.1 rule 3.ii)
  `single_file_cap        = max_zip_size * 2`  (from spec §7.1 rule 3.iii)
This keeps one knob in the config (`max_zip_size`) that scales both derived limits.

---

## 7. Done When

- [x] All tests in §4 committed and green on CI.
- [x] Coverage ≥ 90% on `validator.py` (it's business-critical security logic).
- [x] Ruff clean on `validator.py` and `test_validator.py`.
- [x] `make check` green locally.
- [x] Roadmap table in [`CLAUDE.md`](../../CLAUDE.md) §8 updated (Step 2 → ✅).
- [x] This file's Status flipped to ✅.
