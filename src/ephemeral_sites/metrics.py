"""Prometheus metrics for ephemeral-sites.

A single module-level ``CollectorRegistry`` + metric singletons that the
business code and the /metrics endpoint share. Master spec §5.8 lists
the full set; step 14 implements the state + action metrics, deferring
HTTP request latency to a future step.
"""

from __future__ import annotations

import sqlite3

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge
from prometheus_client import generate_latest as _generate_latest

__all__ = [
    "REGISTRY",
    "CONTENT_TYPE_LATEST",
    "sites_total",
    "created_total",
    "replaced_total",
    "expired_total",
    "deleted_total",
    "storage_bytes",
    "quota_reject_total",
    "render_metrics",
    "refresh_state_gauges",
]

REGISTRY = CollectorRegistry()

sites_total = Gauge(
    "ephemeral_sites_total",
    "Number of sites currently in the DB (includes expired-but-not-reaped).",
    registry=REGISTRY,
)

created_total = Counter(
    "ephemeral_sites_created_total",
    "Sites created via PUT/POST.",
    labelnames=("api_key_name",),
    registry=REGISTRY,
)

replaced_total = Counter(
    "ephemeral_sites_replaced_total",
    "Sites replaced (PUT on existing slug).",
    labelnames=("api_key_name",),
    registry=REGISTRY,
)

expired_total = Counter(
    "ephemeral_sites_expired_total",
    "Sites reaped by the cleanup CronJob after their TTL elapsed.",
    registry=REGISTRY,
)

deleted_total = Counter(
    "ephemeral_sites_deleted_total",
    "Sites deleted via DELETE endpoint.",
    labelnames=("reason",),  # manual | token
    registry=REGISTRY,
)

storage_bytes = Gauge(
    "ephemeral_sites_storage_bytes",
    "Sum of sites.size_bytes across all rows.",
    registry=REGISTRY,
)

quota_reject_total = Counter(
    "ephemeral_sites_quota_reject_total",
    "PUT/POST requests rejected with HTTP 507 (global quota full).",
    registry=REGISTRY,
)


def refresh_state_gauges(conn: sqlite3.Connection) -> None:
    """Repopulate the two state gauges from the DB.

    Called on every ``GET /metrics`` — the table is small (≤ 100 rows)
    and the exposition endpoint is hit at most once per 30s by the
    Prometheus scraper.
    """
    row = conn.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM sites").fetchone()
    if row is not None:
        sites_total.set(int(row[0]))
        storage_bytes.set(int(row[1]))


def render_metrics() -> bytes:
    """Return the Prometheus exposition body for the shared registry."""
    return _generate_latest(REGISTRY)
