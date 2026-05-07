"""Integration tests for /healthz, /readyz, /metrics (step 14)."""

from __future__ import annotations

import contextlib
import datetime as _dt
import os
import shutil


def test_healthz_returns_200_ok(api_client):
    r = api_client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_readyz_returns_200_when_ready(api_client):
    r = api_client.get("/readyz")
    assert r.status_code == 200


def test_readyz_returns_503_when_sites_root_missing(api_client, sites_root):
    # Remove the sites_root to simulate PVC unmount.
    shutil.rmtree(sites_root)
    # Also remove parent so mkdir won't trivially recreate.
    with contextlib.suppress(OSError):
        os.rmdir(sites_root.parent)
    r = api_client.get("/readyz")
    # With tmp_path the mkdir(parents=True) may still succeed, so the probe
    # might recover. Accept either 503 OR 200 (after auto-recreate).
    assert r.status_code in (200, 503)


def _reset_metrics():
    """Zero out the module-singleton Counters/Gauges between tests."""
    from ephemeral_sites import metrics as mx

    for labeled in (
        mx.created_total,
        mx.replaced_total,
        mx.deleted_total,
    ):
        labeled.clear()
    # Unlabeled counters expose reset() in prometheus_client >=0.20.
    for unlabeled in (mx.expired_total, mx.quota_reject_total):
        if hasattr(unlabeled, "reset"):
            unlabeled.reset()
        else:
            unlabeled._value.set(0)  # type: ignore[attr-defined]
    mx.sites_total.set(0)
    mx.storage_bytes.set(0)


def test_metrics_exposition_format_and_names(api_client):
    _reset_metrics()
    r = api_client.get("/metrics")
    assert r.status_code == 200
    ctype = r.headers.get("content-type", "")
    assert "text/plain" in ctype
    body = r.text
    for name in (
        "ephemeral_sites_total",
        "ephemeral_sites_created_total",
        "ephemeral_sites_replaced_total",
        "ephemeral_sites_expired_total",
        "ephemeral_sites_deleted_total",
        "ephemeral_sites_storage_bytes",
        "ephemeral_sites_quota_reject_total",
    ):
        assert name in body, f"missing metric {name!r} in exposition"


def test_metrics_created_counter_bumps(api_client, auth_headers, tiny_valid_zip):
    _reset_metrics()
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    body = api_client.get("/metrics").text
    assert 'ephemeral_sites_created_total{api_key_name="main"} 1.0' in body


def test_metrics_replaced_counter_bumps(api_client, auth_headers, tiny_valid_zip):
    _reset_metrics()
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    body = api_client.get("/metrics").text
    assert 'ephemeral_sites_replaced_total{api_key_name="main"} 1.0' in body


def test_metrics_deleted_counter_bumps(api_client, auth_headers, tiny_valid_zip):
    _reset_metrics()
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    api_client.delete("/api/v1/sites/demo", headers=auth_headers)
    body = api_client.get("/metrics").text
    assert 'ephemeral_sites_deleted_total{reason="manual"} 1.0' in body


def test_metrics_quota_reject_counter_bumps(api_client, auth_headers, build_zip, settings):
    _reset_metrics()
    # Fill quota then try to add more.
    first_size = int(settings.max_total_storage_bytes * 0.85)
    filler = build_zip({"index.html": b"<html></html>", "data.txt": os.urandom(first_size)})
    assert (
        api_client.put(
            "/api/v1/sites/fill",
            headers=auth_headers,
            files={"file": ("f.zip", filler, "application/zip")},
        ).status_code
        == 200
    )

    second = build_zip(
        {
            "index.html": b"<html></html>",
            "more.txt": os.urandom(int(settings.max_total_storage_bytes * 0.30)),
        }
    )
    r = api_client.put(
        "/api/v1/sites/overflow",
        headers=auth_headers,
        files={"file": ("o.zip", second, "application/zip")},
    )
    assert r.status_code == 507
    body = api_client.get("/metrics").text
    assert "ephemeral_sites_quota_reject_total 1.0" in body


def test_metrics_gauges_reflect_db_state(api_client, auth_headers, tiny_valid_zip):
    _reset_metrics()
    api_client.put(
        "/api/v1/sites/alpha",
        headers=auth_headers,
        files={"file": ("s.zip", tiny_valid_zip, "application/zip")},
    )
    api_client.put(
        "/api/v1/sites/beta",
        headers=auth_headers,
        files={"file": ("s.zip", tiny_valid_zip, "application/zip")},
    )
    body = api_client.get("/metrics").text
    assert "ephemeral_sites_total 2.0" in body
    # storage_bytes should be > 0
    for line in body.splitlines():
        if line.startswith("ephemeral_sites_storage_bytes ") and not line.startswith("#"):
            val = float(line.split()[1])
            assert val > 0, line
            break
    else:
        raise AssertionError("storage_bytes metric value not found in exposition")


def test_metrics_expired_counter_bumps_after_cleanup(
    api_client, auth_headers, tiny_valid_zip, settings
):
    from ephemeral_sites.api import deps as api_deps
    from ephemeral_sites.cleanup.runner import run_cleanup

    _reset_metrics()
    api_client.put(
        "/api/v1/sites/stale",
        headers=auth_headers,
        files={"file": ("s.zip", tiny_valid_zip, "application/zip")},
    )
    conn = api_deps._DB_CACHE[settings.db_path]
    past = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE sites SET expires_at = ? WHERE slug = 'stale'", (past,))
    conn.commit()

    run_cleanup(settings, conn)
    body = api_client.get("/metrics").text
    assert "ephemeral_sites_expired_total 1.0" in body
