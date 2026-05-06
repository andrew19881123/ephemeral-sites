"""ZIP archive validator for ephemeral-sites.

See ``docs/steps/step-2-validator.md`` for the full contract (public API,
acceptance criteria, rejection taxonomy). This module is intentionally pure:
no filesystem I/O, no logging, no time/uuid side effects. The validator
inspects ZIP metadata (``zipfile.ZipInfo``) to plan a safe extraction, and
raises :class:`ValidationError` on the first rule violation (fail-fast).

The result is a :class:`ValidationResult` that the storage layer (step 5)
uses to actually write files under ``/data/sites/<slug>.new/``.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

# ---------------------------------------------------------------------------
# Public rejection taxonomy. These constants are part of the public contract:
# they appear as the ``reason_code`` on every ``ValidationError``, and bubble
# up through the API error response, Prometheus metrics labels, and log
# lines. Renaming them is a breaking change.
# ---------------------------------------------------------------------------

REASON_PATH_TRAVERSAL: str = "path_traversal"
REASON_ABSOLUTE_PATH: str = "absolute_path"
REASON_SYMLINK: str = "symlink"
REASON_ZIP_BOMB_RATIO: str = "zip_bomb_ratio"
REASON_ZIP_BOMB_TOTAL_SIZE: str = "zip_bomb_total_size"
REASON_ZIP_BOMB_SINGLE_FILE: str = "zip_bomb_single_file"
REASON_TOO_MANY_FILES: str = "too_many_files"
REASON_DISALLOWED_EXTENSION: str = "disallowed_extension"
REASON_MISSING_INDEX_HTML: str = "missing_index_html"
REASON_EMPTY_ARCHIVE: str = "empty_archive"
REASON_ENCRYPTED: str = "encrypted_archive"
REASON_INVALID_ZIP: str = "invalid_zip"


class ValidationError(Exception):
    """A ZIP rejected by :func:`validate_zip`.

    Carries a machine-readable ``reason_code`` (one of the ``REASON_*``
    module constants) in addition to the human-readable ``detail``.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class ValidatorConfig:
    """Runtime limits the validator enforces.

    Attributes:
        max_zip_size: Maximum accepted size of the *compressed* ZIP payload
            (the HTTP upload body). Two derived caps are computed from this
            single knob so config stays small:

                - single-file uncompressed cap = ``max_zip_size * 2``
                - total uncompressed cap       = ``max_zip_size * 10``

            Rationale: mini-spec §6 Q3 and master spec §7.1 rule 3.
        max_files_per_site: Maximum number of non-directory entries.
        max_decompression_ratio: Global ratio cap (uncompressed / compressed).
            A value of 100 means a 1:100 blowup is allowed, beyond that
            the archive is deemed a zip bomb.
        allowed_extensions: Whitelist of file extensions (lowercase, with
            leading dot, e.g. ``".html"``). Any entry whose extension is
            not in this set causes the whole archive to be rejected.
    """

    max_zip_size: int
    max_files_per_site: int
    max_decompression_ratio: int
    allowed_extensions: frozenset[str]


@dataclass(frozen=True)
class ValidatedEntry:
    """One file-to-extract, as planned by the validator.

    Attributes:
        zip_name: The original ``ZipInfo.filename`` — feed this to
            :meth:`zipfile.ZipFile.read` / ``open``.
        target_rel_path: The POSIX-style path (forward slashes) where the
            file should land, relative to the site root, after any
            flattening. Already free of path-traversal and absolute-path
            hazards by construction.
    """

    zip_name: str
    target_rel_path: str


@dataclass(frozen=True)
class ValidationResult:
    """Result of a successful validation.

    Attributes:
        entries: Tuple of :class:`ValidatedEntry`, one per file to extract.
            Directory entries are filtered out.
        total_uncompressed_size: Sum of ``ZipInfo.file_size`` across the
            non-directory entries. Useful for downstream quota logging.
        files_count: ``len(entries)``.
        flatten_prefix: ``None`` if the archive had ``index.html`` at the
            root; otherwise the top-level directory prefix (e.g. ``"dist/"``)
            that has been stripped from every ``target_rel_path``.
    """

    entries: tuple[ValidatedEntry, ...]
    total_uncompressed_size: int
    files_count: int
    flatten_prefix: str | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Drive-letter prefix (absolute Windows path).
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _normalize(name: str) -> str:
    """Normalize backslashes to forward slashes; leave the rest untouched.

    Windows-created ZIPs occasionally use ``\\`` as a path separator, while
    POSIX implementations use ``/``. All subsequent rules operate on the
    forward-slash form.
    """
    return name.replace("\\", "/")


def _unsafe_path_reason(normalized_name: str) -> str | None:
    """Return a ``REASON_*`` code if the path is unsafe, else ``None``.

    Checks performed (in order):

    - embedded null byte → :data:`REASON_PATH_TRAVERSAL`
    - leading ``/`` (absolute Unix path) → :data:`REASON_ABSOLUTE_PATH`
    - drive-letter prefix (``C:\\``) → :data:`REASON_ABSOLUTE_PATH`
    - any segment equal to ``..`` → :data:`REASON_PATH_TRAVERSAL`
    """
    if "\x00" in normalized_name:
        return REASON_PATH_TRAVERSAL
    if normalized_name.startswith("/"):
        return REASON_ABSOLUTE_PATH
    if _WIN_DRIVE_RE.match(normalized_name):
        return REASON_ABSOLUTE_PATH
    segments = normalized_name.split("/")
    if any(s == ".." for s in segments):
        return REASON_PATH_TRAVERSAL
    return None


def _is_symlink(zinfo: zipfile.ZipInfo) -> bool:
    """True iff the entry is a Unix-style symbolic link.

    Unix file-type bits live in the top 16 bits of ``external_attr`` when
    the entry was created on a Unix system (``create_system == 3``). A
    symlink has file-type bits ``0o120000``.
    """
    if zinfo.create_system != 3:
        return False
    mode = (zinfo.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _is_encrypted(zinfo: zipfile.ZipInfo) -> bool:
    """True iff bit 0 of the general-purpose bit flag is set (ZIP encryption)."""
    return bool(zinfo.flag_bits & 0x1)


def _extension(normalized_name: str) -> str:
    """Return the lower-case extension including leading dot, or ``""``."""
    basename = normalized_name.rsplit("/", 1)[-1]
    if "." not in basename:
        return ""
    return "." + basename.rsplit(".", 1)[-1].casefold()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_zip(
    source: bytes | bytearray | BinaryIO,
    config: ValidatorConfig,
) -> ValidationResult:
    """Validate a ZIP archive intended for publishing as a static SPA site.

    Parameters:
        source: Raw bytes of the archive, or any seekable binary stream
            (e.g. a FastAPI ``UploadFile.file``).
        config: :class:`ValidatorConfig` with the runtime limits.

    Returns:
        A :class:`ValidationResult` ready to drive extraction.

    Raises:
        ValidationError: on the first rule violation (fail-fast). Check
            ordering is documented in the mini-spec §2.3.
    """
    bio: BinaryIO = BytesIO(bytes(source)) if isinstance(source, bytes | bytearray) else source

    # 1. Parse ZIP structure. A BadZipFile here means we were given garbage.
    try:
        with zipfile.ZipFile(bio) as zf:
            infolist = list(zf.infolist())
    except zipfile.BadZipFile as exc:
        raise ValidationError(REASON_INVALID_ZIP, f"invalid zip: {exc}") from exc

    # Partition into directory and file entries. Directory entries end in "/".
    file_infos: list[zipfile.ZipInfo] = [z for z in infolist if not z.filename.endswith("/")]

    # 2. Encryption — any encrypted entry means the archive is unusable.
    for zi in infolist:
        if _is_encrypted(zi):
            raise ValidationError(
                REASON_ENCRYPTED,
                f"encrypted entry: {zi.filename!r}",
            )

    # 3. Symlinks — reject on first match.
    for zi in infolist:
        if _is_symlink(zi):
            raise ValidationError(REASON_SYMLINK, f"symlink entry: {zi.filename!r}")

    # 4. Path safety — applies to ALL entries (including directory markers).
    for zi in infolist:
        normalized = _normalize(zi.filename)
        reason = _unsafe_path_reason(normalized)
        if reason is not None:
            raise ValidationError(reason, f"unsafe path: {zi.filename!r}")

    # 5. Extension whitelist — applied only to file entries (dirs have none).
    for zi in file_infos:
        normalized = _normalize(zi.filename)
        ext = _extension(normalized)
        if ext not in config.allowed_extensions:
            raise ValidationError(
                REASON_DISALLOWED_EXTENSION,
                f"disallowed extension {ext!r} on {zi.filename!r}",
            )

    # 6. File count.
    if len(file_infos) > config.max_files_per_site:
        raise ValidationError(
            REASON_TOO_MANY_FILES,
            f"{len(file_infos)} files exceed max {config.max_files_per_site}",
        )

    # 7. Single-file bomb — before total, so we attribute precisely.
    single_file_cap = config.max_zip_size * 2
    for zi in file_infos:
        if zi.file_size > single_file_cap:
            raise ValidationError(
                REASON_ZIP_BOMB_SINGLE_FILE,
                f"{zi.filename!r} uncompressed size {zi.file_size} exceeds cap {single_file_cap}",
            )

    # 8. Total uncompressed size bomb.
    total_uncompressed = sum(zi.file_size for zi in file_infos)
    total_cap = config.max_zip_size * 10
    if total_uncompressed > total_cap:
        raise ValidationError(
            REASON_ZIP_BOMB_TOTAL_SIZE,
            f"total uncompressed {total_uncompressed} exceeds cap {total_cap}",
        )

    # 9. Ratio bomb (global). Guard against division by zero when the archive
    # is entirely STORED 0-byte entries — no bomb possible in that case.
    total_compressed = sum(zi.compress_size for zi in file_infos)
    if total_compressed > 0:
        ratio = total_uncompressed / total_compressed
        if ratio > config.max_decompression_ratio:
            raise ValidationError(
                REASON_ZIP_BOMB_RATIO,
                f"compression ratio {ratio:.1f} exceeds cap {config.max_decompression_ratio}",
            )

    # 10. Structural — empty and index.html discovery.
    if not file_infos:
        raise ValidationError(REASON_EMPTY_ARCHIVE, "archive contains no files")

    names = [_normalize(zi.filename) for zi in file_infos]

    if "index.html" in names:
        # Root index — no flattening needed.
        flatten_prefix: str | None = None
        entries = tuple(
            ValidatedEntry(zip_name=zi.filename, target_rel_path=_normalize(zi.filename))
            for zi in file_infos
        )
    else:
        # Look for a single top-level folder containing index.html.
        has_root_level_file = any("/" not in n for n in names)
        top_level_dirs = {n.split("/", 1)[0] for n in names if "/" in n}
        if has_root_level_file or len(top_level_dirs) != 1:
            raise ValidationError(
                REASON_MISSING_INDEX_HTML,
                "no index.html at root and no single top-level folder to flatten",
            )
        prefix = top_level_dirs.pop() + "/"
        if f"{prefix}index.html" not in names:
            raise ValidationError(
                REASON_MISSING_INDEX_HTML,
                f"top-level folder {prefix!r} does not contain index.html",
            )
        flatten_prefix = prefix
        entries = tuple(
            ValidatedEntry(
                zip_name=zi.filename,
                target_rel_path=_normalize(zi.filename)[len(prefix) :],
            )
            for zi in file_infos
        )

    return ValidationResult(
        entries=entries,
        total_uncompressed_size=total_uncompressed,
        files_count=len(file_infos),
        flatten_prefix=flatten_prefix,
    )
