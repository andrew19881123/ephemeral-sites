"""Storage atomic-swap layer for ephemeral-sites.

See ``docs/steps/step-5-storage.md`` for the full contract. The module
owns ``/data/sites/`` and provides two operations, both under a per-slug
``flock``:

- :func:`extract_site` — unpack a validated ZIP into ``{slug}.new``,
  then perform the two-rename atomic swap that yields the zero-404
  window guaranteed by master spec §4.2 step 7.
- :func:`delete_site` — remove ``{slug}/`` under the same lock.

Platform: POSIX (Linux, macOS). Windows is not supported (master spec
§3.2 deploys to Kubernetes on Linux).
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import errno
import fcntl
import io
import logging
import os
import shutil
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .validator import ValidationResult

__all__ = [
    "ExtractionError",
    "ExtractionResult",
    "delete_site",
    "extract_site",
]

log = logging.getLogger(__name__)


class ExtractionError(OSError):
    """Filesystem-level failure during :func:`extract_site`.

    Raised only after rollback has been performed, so the filesystem is
    in a clean, retryable state when this propagates out.
    """


@dataclass(frozen=True)
class ExtractionResult:
    """Bookkeeping returned after a successful :func:`extract_site`."""

    slug: str
    site_path: Path
    files_written: int
    total_bytes_written: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# renameat2 with RENAME_EXCHANGE (Linux 3.15+, glibc 2.28+) swaps two paths
# atomically in a single syscall — zero-window directory swap, which is
# what makes test_overwrite_no_404_window pass reliably.
#
# On older systems the symbol is not exported by libc; we fall back to the
# two-rename protocol described in mini-spec §2.5 (with a larger micro-
# second window tolerated by the real HTTP server's retry+cache).

_RENAME_EXCHANGE = 2
_AT_FDCWD = -100


def _load_renameat2():
    try:
        libc_name = ctypes.util.find_library("c")
        if not libc_name:
            return None
        libc = ctypes.CDLL(libc_name, use_errno=True)
        func = libc.renameat2
    except (OSError, AttributeError):
        return None
    func.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    func.restype = ctypes.c_int
    return func


_renameat2 = _load_renameat2()


def _atomic_exchange(a: Path, b: Path) -> bool:
    """Atomically exchange the directory entries ``a`` and ``b``.

    Returns ``True`` if the exchange completed via ``renameat2
    RENAME_EXCHANGE``; ``False`` if the syscall or flag is not
    supported on this kernel/libc (caller should fall back to the
    two-rename path).

    Raises :class:`OSError` on any other failure (permissions, cross-
    filesystem, ENOSPC, etc.).
    """
    if _renameat2 is None:
        return False
    result = _renameat2(
        _AT_FDCWD,
        os.fsencode(a),
        _AT_FDCWD,
        os.fsencode(b),
        _RENAME_EXCHANGE,
    )
    if result == 0:
        return True
    err = ctypes.get_errno()
    if err in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
        return False
    raise OSError(err, os.strerror(err))


@contextlib.contextmanager
def _per_slug_lock(lock_dir: Path, slug: str) -> Iterator[None]:
    """Acquire an exclusive flock on ``{lock_dir}/{slug}.lock``.

    The lock file itself is created if missing and left on disk
    afterwards — empty, harmless, reusable.
    """
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{slug}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _path_is_inside(candidate: Path, base_resolved: Path) -> bool:
    """True iff ``candidate.resolve()`` is inside ``base_resolved``."""
    try:
        candidate.resolve().relative_to(base_resolved)
        return True
    except ValueError:
        return False


def _open_zip(source: bytes | bytearray | BinaryIO) -> zipfile.ZipFile:
    """Open a ZipFile from raw bytes or a binary stream."""
    if isinstance(source, bytes | bytearray):
        bio: BinaryIO = io.BytesIO(bytes(source))
    else:
        bio = source
    try:
        return zipfile.ZipFile(bio)
    except zipfile.BadZipFile as exc:
        raise ExtractionError(f"invalid zip: {exc}") from exc


def _safe_rmtree(path: Path) -> None:
    """Best-effort ``rm -rf``; swallows errors (used only on cleanup paths)."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_site(
    *,
    sites_root: Path | str,
    slug: str,
    zip_source: bytes | bytearray | BinaryIO,
    validation: ValidationResult,
    runtime_config: str | None = None,
    lock_dir: Path | str | None = None,
) -> ExtractionResult:
    """Extract a validated ZIP to ``sites_root/{slug}/`` with atomic swap.

    See mini-spec §2.5 for the full protocol. The short form:

        mkdir {slug}.new → write entries (+ optional config.json) →
          rename {slug} → {slug}.old (if existed) →
          rename {slug}.new → {slug} →
          rm -rf {slug}.old

    All under ``flock({lock_dir}/{slug}.lock)`` so concurrent calls on
    the same slug serialize. Different slugs proceed in parallel.

    On any exception, rolls back (removes ``{slug}.new``, restores
    ``{slug}`` from ``{slug}.old`` if the first rename had succeeded)
    and re-raises as :class:`ExtractionError`.
    """
    sites_root_path = Path(sites_root)
    lock_path = Path(lock_dir) if lock_dir is not None else sites_root_path / ".lock"

    try:
        sites_root_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExtractionError(f"cannot create sites_root {sites_root_path}: {exc}") from exc

    new_dir = sites_root_path / f"{slug}.new"
    old_dir = sites_root_path / f"{slug}.old"
    final_dir = sites_root_path / slug

    try:
        with _per_slug_lock(lock_path, slug):
            return _extract_under_lock(
                new_dir=new_dir,
                old_dir=old_dir,
                final_dir=final_dir,
                slug=slug,
                zip_source=zip_source,
                validation=validation,
                runtime_config=runtime_config,
            )
    except ExtractionError:
        # Already wrapped; cleanup already performed inside the locked helper.
        raise
    except OSError as exc:
        # Any filesystem failure outside the locked section (lock
        # acquisition, sites_root creation) — still clean the stale
        # .new dir if we somehow have one.
        _safe_rmtree(new_dir)
        raise ExtractionError(f"extraction failed: {exc}") from exc


def _extract_under_lock(
    *,
    new_dir: Path,
    old_dir: Path,
    final_dir: Path,
    slug: str,
    zip_source: bytes | bytearray | BinaryIO,
    validation: ValidationResult,
    runtime_config: str | None,
) -> ExtractionResult:
    """The body of :func:`extract_site`, with the per-slug flock held.

    Any exception out of this function implies the filesystem is dirty;
    the caller (:func:`extract_site`) guarantees rollback has run by
    the time the exception reaches user code.
    """
    # Clean up any leftover from a previous crashed attempt.
    _safe_rmtree(new_dir)

    zf = _open_zip(zip_source)
    total_bytes = 0
    files_written = 0
    swap_started = False

    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        new_resolved = new_dir.resolve()

        for entry in validation.entries:
            dest = new_dir / entry.target_rel_path
            # Defense in depth: even if the validator misses a traversal,
            # verify the resolved destination stays inside {slug}.new/.
            if not _path_is_inside(dest, new_resolved):
                raise ExtractionError(
                    f"extraction path escapes site dir: {entry.target_rel_path!r}"
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry.zip_name) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            total_bytes += dest.stat().st_size
            files_written += 1

        if runtime_config is not None:
            (new_dir / "config.json").write_text(runtime_config, encoding="utf-8")

        # Atomic swap. When ``final_dir`` does not yet exist, a single
        # rename is already atomic (zero-window, by definition). When it
        # does, we try renameat2 RENAME_EXCHANGE first — that is a single
        # syscall that atomically swaps two directory entries on Linux
        # 3.15+ with glibc 2.28+, giving a true zero-404 window. If the
        # kernel or libc doesn't support it, fall back to the two-rename
        # protocol (master spec §4.2 step 7) which has a microsecond
        # window that the HTTP server's cache+retry absorbs.
        swap_started = True
        had_old = final_dir.exists()
        if not had_old:
            new_dir.rename(final_dir)
        elif _atomic_exchange(new_dir, final_dir):
            # new_dir now holds the OLD content; remove it.
            _safe_rmtree(new_dir)
        else:
            # Fallback: two-rename with small window.
            final_dir.rename(old_dir)
            try:
                new_dir.rename(final_dir)
            except OSError:
                if not final_dir.exists():
                    with contextlib.suppress(OSError):
                        old_dir.rename(final_dir)
                raise
            _safe_rmtree(old_dir)

    except BaseException:
        # Rollback. _safe_rmtree swallows errors — we don't want the
        # rollback path to mask the original exception.
        if not swap_started:
            _safe_rmtree(new_dir)
        else:
            # Swap was in progress. {slug}.new was renamed to {slug}
            # successfully only if we did not hit the except branch
            # above; otherwise it may still exist. Best effort.
            _safe_rmtree(new_dir)
            _safe_rmtree(old_dir)
        raise
    finally:
        zf.close()

    return ExtractionResult(
        slug=slug,
        site_path=final_dir,
        files_written=files_written,
        total_bytes_written=total_bytes,
    )


def delete_site(
    *,
    sites_root: Path | str,
    slug: str,
    lock_dir: Path | str | None = None,
) -> bool:
    """Remove ``sites_root/{slug}/`` under the per-slug flock.

    Returns ``True`` if a directory existed and was removed, ``False``
    if the slug had nothing to delete (idempotent).
    """
    sites_root_path = Path(sites_root)
    if not sites_root_path.exists():
        return False

    lock_path = Path(lock_dir) if lock_dir is not None else sites_root_path / ".lock"
    target = sites_root_path / slug

    with _per_slug_lock(lock_path, slug):
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True
