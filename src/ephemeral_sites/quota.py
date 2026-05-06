"""Global storage-quota checks for ephemeral-sites.

See ``docs/steps/step-7-quota.md`` for the full contract. Three pure
helpers, no HTTP layer awareness:

- :func:`check_quota` — the decision function (raises :class:`QuotaExceeded`).
- :func:`sum_active_sites_bytes` — DB-side "what's in the index".
- :func:`sum_filesystem_bytes` — ground-truth PVC walk.

The API layer (step 8) wires :func:`check_quota` into the PUT/POST
path; the metrics endpoint (step 14) uses :func:`sum_filesystem_bytes`
to populate ``ephemeral_sites_storage_bytes``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = [
    "QuotaExceeded",
    "check_quota",
    "sum_active_sites_bytes",
    "sum_filesystem_bytes",
]


class QuotaExceeded(Exception):
    """Admitting an upload would push the used total over the cap.

    Maps to HTTP 507 Insufficient Storage (master spec §5.2).

    Attributes:
        current_used: bytes currently stored (sum of sites.size_bytes
            or a filesystem walk; the caller picks).
        incoming: bytes the caller proposed to admit.
        max_total: the configured cap (``maxTotalStorageBytes``).
    """

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


def check_quota(
    *,
    current_used: int,
    incoming: int,
    max_total: int,
) -> None:
    """Raise :class:`QuotaExceeded` if admitting ``incoming`` would
    push the used total over ``max_total``.

    Strict ``>`` comparison: an upload that lands exactly on the cap
    passes. The 20% PVC headroom (50 GiB PVC vs 40 GiB default quota,
    master spec §9) absorbs the few-byte overhead of the metadata row.
    """
    if current_used + incoming > max_total:
        raise QuotaExceeded(
            current_used=current_used,
            incoming=incoming,
            max_total=max_total,
        )


def sum_active_sites_bytes(conn: sqlite3.Connection) -> int:
    """Return ``SUM(size_bytes)`` over every row in ``sites``.

    Includes rows whose ``expires_at`` has passed but have not yet
    been cleaned up (mini-spec §6 Q2 — over-commit is the failure
    mode we avoid; conservative means counting expired-but-unreaped
    bytes).

    Raises:
        sqlite3.OperationalError: if the ``sites`` table does not
            exist (i.e. the caller opened a fresh DB without running
            migrations). Loud fail is preferable to silent zero.
    """
    row = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM sites").fetchone()
    # Row may be a sqlite3.Row or a plain tuple depending on connection config.
    return int(row[0])


def sum_filesystem_bytes(
    sites_root: Path | str,
    *,
    exclude_prefixes: tuple[str, ...] = (".",),
    exclude_suffixes: tuple[str, ...] = (".new", ".old"),
) -> int:
    """Recursively sum file sizes under ``sites_root``.

    Top-level entries whose name starts with any of ``exclude_prefixes``
    (default ``.`` → hides ``.lock/``) or ends with any of
    ``exclude_suffixes`` (default ``.new`` / ``.old`` → hides in-flight
    extractions and rollback leftovers) are skipped entirely.

    Nested hidden files *inside* a kept site dir are counted — we only
    apply the exclusion rule at the top level. Users who ship a
    ``.htaccess`` pay for it out of their quota.

    Returns 0 on a non-existent ``sites_root``.
    """
    root = Path(sites_root)
    if not root.exists():
        return 0

    total = 0
    for top in root.iterdir():
        name = top.name
        if any(name.startswith(p) for p in exclude_prefixes):
            continue
        if any(name.endswith(s) for s in exclude_suffixes):
            continue
        if top.is_file():
            total += top.stat().st_size
        elif top.is_dir():
            for path in top.rglob("*"):
                if path.is_file():
                    total += path.stat().st_size
    return total
