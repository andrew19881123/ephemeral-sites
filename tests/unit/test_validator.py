"""Unit tests for the ZIP validator.

These tests are derived directly from the mini-spec at
``docs/steps/step-2-validator.md`` §4 (Test List). Each test maps 1:1 to
at least one acceptance criterion in §3 of that mini-spec.

The tests are written *before* the implementation (TDD red phase). Running
them without ``src/ephemeral_sites/validator.py`` must fail with an
``ImportError`` — that is the expected "red" signal.

ZIP fixtures are built in-memory with :mod:`zipfile` so the repository
stays free of opaque binary blobs (see CLAUDE.md §4.3).
"""

from __future__ import annotations

import io
import os
import zipfile

import pytest


# ---------------------------------------------------------------------------
# Module-under-test import. Keep this at the top so the very first thing
# pytest does is try to import validator.py — a clear, loud ImportError is
# the entire point of the red phase.
# ---------------------------------------------------------------------------
from ephemeral_sites import validator as v  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_ALLOWED = frozenset(
    {
        ".html",
        ".htm",
        ".css",
        ".js",
        ".mjs",
        ".json",
        ".map",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".woff",
        ".woff2",
        ".txt",
    }
)


def _cfg(
    *,
    max_zip_size: int = 1024 * 1024,
    max_files_per_site: int = 100,
    max_decompression_ratio: int = 100,
    allowed_extensions: frozenset[str] = DEFAULT_ALLOWED,
) -> "v.ValidatorConfig":
    """Build a ValidatorConfig with test-friendly defaults (1 MiB cap, 100 files)."""
    return v.ValidatorConfig(
        max_zip_size=max_zip_size,
        max_files_per_site=max_files_per_site,
        max_decompression_ratio=max_decompression_ratio,
        allowed_extensions=allowed_extensions,
    )


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    """Build an in-memory ZIP from a ``{filename: content}`` dict.

    ``content`` may be ``bytes`` or ``str`` (auto-utf8). A filename ending in
    ``"/"`` produces a directory entry.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            if name.endswith("/"):
                # Directory entry.
                zi = zipfile.ZipInfo(name)
                zf.writestr(zi, b"")
                continue
            if isinstance(data, str):
                data = data.encode("utf-8")
            zf.writestr(name, data)
    return buf.getvalue()


def _valid_flat_spa_bytes() -> bytes:
    return _zip_bytes(
        {
            "index.html": "<!doctype html><html><body>ok</body></html>",
            "static/app.js": "console.log('hi');",
            "static/app.css": "body { color: red; }",
        }
    )


def _valid_nested_spa_bytes(prefix: str = "dist/") -> bytes:
    return _zip_bytes(
        {
            f"{prefix}index.html": "<!doctype html><html><body>nested</body></html>",
            f"{prefix}static/app.js": "console.log('nested');",
            f"{prefix}static/app.css": "body{}",
        }
    )


def _symlink_zip_bytes(link_name: str = "link", target: str = "/etc/shadow") -> bytes:
    """Create a ZIP whose sole entry is a Unix symlink."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zi = zipfile.ZipInfo(link_name)
        zi.create_system = 3  # Unix
        # Symlink mode: 0o120000 | 0o777, shifted into the high 16 bits of external_attr.
        zi.external_attr = (0o120777 & 0xFFFF) << 16
        zf.writestr(zi, target.encode("utf-8"))
    return buf.getvalue()


def _encrypted_zip_bytes() -> bytes:
    """Produce a ZIP with one password-protected entry (ZipCrypto)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.setpassword(b"secret")
        zi = zipfile.ZipInfo("index.html")
        # Set encryption flag bit (bit 0 of general purpose bit flag).
        zi.flag_bits |= 0x1
        zf.writestr(zi, b"<html></html>")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# §4.1 Happy paths
# ---------------------------------------------------------------------------


def test_accepts_valid_flat_spa():
    result = v.validate_zip(_valid_flat_spa_bytes(), _cfg())
    assert result.flatten_prefix is None
    assert result.files_count == 3
    paths = sorted(e.target_rel_path for e in result.entries)
    assert paths == ["index.html", "static/app.css", "static/app.js"]


def test_accepts_valid_nested_spa_and_flattens():
    result = v.validate_zip(_valid_nested_spa_bytes("dist/"), _cfg())
    assert result.flatten_prefix == "dist/"
    assert result.files_count == 3
    paths = sorted(e.target_rel_path for e in result.entries)
    assert paths == ["index.html", "static/app.css", "static/app.js"]
    # Original zip_name must still carry the prefix (extraction reads it).
    assert all(e.zip_name.startswith("dist/") for e in result.entries)


def test_accepts_bytes_and_stream_inputs_equivalently():
    data = _valid_flat_spa_bytes()
    via_bytes = v.validate_zip(data, _cfg())
    via_stream = v.validate_zip(io.BytesIO(data), _cfg())
    assert via_bytes.files_count == via_stream.files_count
    assert via_bytes.flatten_prefix == via_stream.flatten_prefix


# ---------------------------------------------------------------------------
# §4.2 Path safety (security)
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_rejects_path_traversal_dotdot_leading():
    data = _zip_bytes(
        {
            "index.html": "<html></html>",
            "../../etc/passwd": "root:x:0:0:",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_PATH_TRAVERSAL


@pytest.mark.security
def test_rejects_path_traversal_dotdot_embedded():
    data = _zip_bytes(
        {
            "index.html": "<html></html>",
            "assets/../../evil.html": "boom",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_PATH_TRAVERSAL


@pytest.mark.security
def test_rejects_absolute_path_unix():
    data = _zip_bytes(
        {
            "index.html": "<html></html>",
            "/etc/shadow": "boom",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_ABSOLUTE_PATH


@pytest.mark.security
def test_rejects_absolute_path_windows_drive():
    data = _zip_bytes(
        {
            "index.html": "<html></html>",
            "C:\\Windows\\evil.html": "boom",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_ABSOLUTE_PATH


@pytest.mark.security
def test_rejects_null_byte_in_name():
    data = _zip_bytes(
        {
            "index.html": "<html></html>",
            "evil\x00.html": "boom",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    # Null-byte is a path-safety violation; grouped with PATH_TRAVERSAL.
    assert exc_info.value.reason_code == v.REASON_PATH_TRAVERSAL


@pytest.mark.security
def test_rejects_symlink_entry():
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(_symlink_zip_bytes(), _cfg())
    assert exc_info.value.reason_code == v.REASON_SYMLINK


# ---------------------------------------------------------------------------
# §4.3 Zip bombs (security)
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_rejects_zip_bomb_by_ratio():
    """A highly compressible payload (all zeros) trips the ratio cap."""
    big = b"\x00" * (200 * 1024)  # 200 KiB of zeros, compresses to a few hundred bytes
    data = _zip_bytes({"index.html": "<html></html>", "static/pad.txt": big})
    # Tight ratio (10) and generous size caps, so ONLY the ratio rule fires.
    cfg = _cfg(max_zip_size=10 * 1024 * 1024, max_decompression_ratio=10)
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, cfg)
    assert exc_info.value.reason_code == v.REASON_ZIP_BOMB_RATIO


@pytest.mark.security
def test_rejects_zip_bomb_by_total_uncompressed_size():
    """Total uncompressed payload exceeding max_zip_size*10 must be rejected."""
    # max_zip_size=10KiB => cap on total uncompressed = 100KiB. Send ~200KiB of
    # random-ish content (not a ratio bomb; use non-compressible bytes).
    payload = os.urandom(200 * 1024)
    data = _zip_bytes({"index.html": "<html></html>", "static/blob.txt": payload})
    cfg = _cfg(max_zip_size=10 * 1024, max_decompression_ratio=1000)
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, cfg)
    assert exc_info.value.reason_code == v.REASON_ZIP_BOMB_TOTAL_SIZE


@pytest.mark.security
def test_rejects_zip_bomb_by_single_file_size():
    """A single entry larger than max_zip_size*2 trips the per-file cap."""
    # max_zip_size = 10 KiB => single-file cap = 20 KiB. Send 50 KiB incompressible.
    payload = os.urandom(50 * 1024)
    data = _zip_bytes({"index.html": "<html></html>", "static/big.txt": payload})
    # Generous total cap so we isolate the single-file rule.
    cfg = _cfg(max_zip_size=10 * 1024, max_decompression_ratio=1000)
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, cfg)
    assert exc_info.value.reason_code == v.REASON_ZIP_BOMB_SINGLE_FILE


# ---------------------------------------------------------------------------
# §4.4 Quotas / limits
# ---------------------------------------------------------------------------


def test_rejects_excessive_file_count():
    entries: dict[str, bytes | str] = {"index.html": "<html></html>"}
    # 60 files, cap at 10.
    for i in range(60):
        entries[f"static/f{i}.txt"] = "x"
    data = _zip_bytes(entries)
    cfg = _cfg(max_files_per_site=10)
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, cfg)
    assert exc_info.value.reason_code == v.REASON_TOO_MANY_FILES


def test_rejects_non_whitelisted_extension():
    data = _zip_bytes(
        {
            "index.html": "<html></html>",
            "scripts/evil.php": "<?php echo 'hi'; ?>",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_DISALLOWED_EXTENSION


def test_rejects_ds_store_even_with_valid_spa():
    """Strict whitelist — .DS_Store has no whitelisted extension, so reject."""
    data = _zip_bytes(
        {
            "index.html": "<html></html>",
            ".DS_Store": b"\x00\x00\x00",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_DISALLOWED_EXTENSION


# ---------------------------------------------------------------------------
# §4.5 Structural
# ---------------------------------------------------------------------------


def test_rejects_missing_index_html_at_root_and_no_flatten_candidate():
    data = _zip_bytes(
        {
            "about.html": "<html></html>",
            "static/app.js": "console.log(1);",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_MISSING_INDEX_HTML


def test_rejects_multiple_top_level_folders_without_root_index():
    data = _zip_bytes(
        {
            "foo/index.html": "<html></html>",
            "bar/index.html": "<html></html>",
        }
    )
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_MISSING_INDEX_HTML


def test_rejects_empty_zip():
    data = _zip_bytes({})
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(data, _cfg())
    assert exc_info.value.reason_code == v.REASON_EMPTY_ARCHIVE


def test_rejects_encrypted_zip():
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(_encrypted_zip_bytes(), _cfg())
    assert exc_info.value.reason_code == v.REASON_ENCRYPTED


def test_rejects_invalid_zip_bytes():
    with pytest.raises(v.ValidationError) as exc_info:
        v.validate_zip(b"not a zip at all", _cfg())
    assert exc_info.value.reason_code == v.REASON_INVALID_ZIP


# ---------------------------------------------------------------------------
# §4.6 Contract
# ---------------------------------------------------------------------------


def test_validation_error_carries_reason_code():
    err = v.ValidationError(v.REASON_PATH_TRAVERSAL, "nope")
    assert err.reason_code == v.REASON_PATH_TRAVERSAL
    assert err.detail == "nope"
    assert str(err) == "nope"


def test_reason_codes_are_stable_strings():
    # Guard against accidental enumification or renaming: these names are
    # part of the public contract (API error responses, metrics labels).
    expected = {
        v.REASON_PATH_TRAVERSAL: "path_traversal",
        v.REASON_ABSOLUTE_PATH: "absolute_path",
        v.REASON_SYMLINK: "symlink",
        v.REASON_ZIP_BOMB_RATIO: "zip_bomb_ratio",
        v.REASON_ZIP_BOMB_TOTAL_SIZE: "zip_bomb_total_size",
        v.REASON_ZIP_BOMB_SINGLE_FILE: "zip_bomb_single_file",
        v.REASON_TOO_MANY_FILES: "too_many_files",
        v.REASON_DISALLOWED_EXTENSION: "disallowed_extension",
        v.REASON_MISSING_INDEX_HTML: "missing_index_html",
        v.REASON_EMPTY_ARCHIVE: "empty_archive",
        v.REASON_ENCRYPTED: "encrypted_archive",
        v.REASON_INVALID_ZIP: "invalid_zip",
    }
    for code, expected_value in expected.items():
        assert code == expected_value


def test_validator_performs_no_filesystem_io(tmp_path, monkeypatch):
    """The validator must be pure wrt the filesystem — no files created."""
    monkeypatch.chdir(tmp_path)
    before = set(os.listdir(tmp_path))
    v.validate_zip(_valid_flat_spa_bytes(), _cfg())
    after = set(os.listdir(tmp_path))
    assert before == after
