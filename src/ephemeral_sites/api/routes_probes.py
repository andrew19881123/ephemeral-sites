"""Probes and Prometheus exposition endpoints (step 14).

- ``GET /healthz`` — liveness (always 200 if process is alive).
- ``GET /readyz``  — readiness (DB + sites_root writable).
- ``GET /metrics`` — Prometheus text format.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from ephemeral_sites import metrics as mx
from ephemeral_sites.config import Settings

from .deps import get_db_conn, get_settings_dep

__all__ = ["router"]

router = APIRouter(tags=["probes"])


@router.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@router.get("/readyz")
async def readyz(
    settings: Settings = Depends(get_settings_dep),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> Response:
    # DB reachable?
    try:
        conn.execute("SELECT 1").fetchone()
    except sqlite3.Error as exc:
        return JSONResponse(
            {"error": "not_ready", "detail": f"db: {exc.__class__.__name__}"},
            status_code=503,
        )
    # sites_root writable?
    sites_root = Path(settings.sites_root)
    probe = sites_root / f".readyz-{uuid.uuid4().hex}"
    try:
        sites_root.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
    except OSError:
        return JSONResponse(
            {"error": "not_ready", "detail": "sites_root not writable"},
            status_code=503,
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(probe)
    return PlainTextResponse("ok")


@router.get("/metrics")
async def metrics_endpoint(
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> Response:
    mx.refresh_state_gauges(conn)
    body = mx.render_metrics()
    return Response(content=body, media_type=mx.CONTENT_TYPE_LATEST)
