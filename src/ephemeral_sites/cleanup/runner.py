"""Cleanup runner for ephemeral-sites.

Invoked by the Kubernetes CronJob (step 15) every 5 minutes. Deletes
expired sites from disk + DB and (on Mondays) purges old event_log rows.

See ``docs/steps/step-13-cleanup.md`` for the contract.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from dataclasses import dataclass

from ephemeral_sites import metrics as mx
from ephemeral_sites import storage
from ephemeral_sites.config import Settings

__all__ = ["run_cleanup", "CleanupResult"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupResult:
    expired_slugs: tuple[str, ...]
    purged_events: int


def _iso_now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(iso: str) -> _dt.datetime:
    # Parse with timezone (ends with 'Z').
    return _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.UTC)


def run_cleanup(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    now_iso: str | None = None,
    event_retention_days: int = 90,
) -> CleanupResult:
    """One cleanup pass. See mini-spec §2.2."""
    now_iso = now_iso or _iso_now()

    # 1. Find expired sites.
    rows = conn.execute(
        "SELECT slug FROM sites WHERE expires_at IS NOT NULL AND expires_at < ?",
        (now_iso,),
    ).fetchall()

    expired_slugs: list[str] = []
    for row in rows:
        slug = row[0] if not hasattr(row, "keys") else row["slug"]
        try:
            storage.delete_site(
                sites_root=settings.sites_root,
                slug=slug,
                lock_dir=settings.lock_dir,
            )
        except OSError:
            log.exception("cleanup: failed to delete site directory for slug=%s", slug)
            continue
        try:
            with conn:
                conn.execute("DELETE FROM sites WHERE slug = ?", (slug,))
                conn.execute(
                    "INSERT INTO event_log (slug, event, timestamp, api_key) VALUES (?, ?, ?, ?)",
                    (slug, "expired", now_iso, None),
                )
        except sqlite3.Error:
            log.exception("cleanup: DB update failed for slug=%s", slug)
            continue
        expired_slugs.append(slug)

    # 2. Weekly event_log purge on Monday (ISO weekday 1).
    purged = 0
    if _parse_iso(now_iso).isoweekday() == 1:
        cutoff = (_parse_iso(now_iso) - _dt.timedelta(days=event_retention_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        count_before = conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE timestamp < ?", (cutoff,)
        ).fetchone()[0]
        with conn:
            conn.execute("DELETE FROM event_log WHERE timestamp < ?", (cutoff,))
        purged = int(count_before)

    if expired_slugs:
        mx.expired_total.inc(len(expired_slugs))

    if expired_slugs or purged > 0:
        log.info(
            "cleanup: reaped %d site(s), purged %d event(s)",
            len(expired_slugs),
            purged,
        )
    else:
        log.debug("cleanup: nothing to do")

    return CleanupResult(
        expired_slugs=tuple(expired_slugs),
        purged_events=purged,
    )
