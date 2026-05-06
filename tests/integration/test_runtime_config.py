"""Integration tests for runtime_config persistence (step 10)."""

from __future__ import annotations

import json


def test_config_json_served_from_param(api_client, auth_headers, tiny_valid_zip, sites_root):
    """Master spec section 11.3: PUT with runtime_config lands on disk as config.json."""
    cfg = json.dumps({"api_url": "https://api.example.com", "feature_x": True})
    r = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        data={"runtime_config": cfg},
    )
    assert r.status_code == 200, r.text

    on_disk = (sites_root / "demo" / "config.json").read_text(encoding="utf-8")
    assert json.loads(on_disk) == json.loads(cfg)


def test_db_runtime_config_matches_disk(
    api_client, auth_headers, tiny_valid_zip, sites_root, open_conn
):
    cfg = json.dumps({"hello": "world"})
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        data={"runtime_config": cfg},
    )

    conn = open_conn()
    try:
        row = conn.execute("SELECT runtime_config FROM sites WHERE slug='demo'").fetchone()
        assert row is not None
        stored = row[0]
        assert json.loads(stored) == json.loads(cfg)
    finally:
        conn.close()


def test_replace_without_runtime_config_preserves_previous(
    api_client, auth_headers, tiny_valid_zip, tiny_valid_zip_v2, sites_root
):
    cfg = json.dumps({"keep": "me"})
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v1.zip", tiny_valid_zip, "application/zip")},
        data={"runtime_config": cfg},
    )
    # Second PUT WITHOUT runtime_config field.
    r2 = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v2.zip", tiny_valid_zip_v2, "application/zip")},
    )
    assert r2.status_code == 200

    on_disk = (sites_root / "demo" / "config.json").read_text(encoding="utf-8")
    assert json.loads(on_disk) == json.loads(cfg)


def test_replace_with_new_runtime_config_overwrites(
    api_client, auth_headers, tiny_valid_zip, tiny_valid_zip_v2, sites_root
):
    cfg1 = json.dumps({"v": 1})
    cfg2 = json.dumps({"v": 2})
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v1.zip", tiny_valid_zip, "application/zip")},
        data={"runtime_config": cfg1},
    )
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v2.zip", tiny_valid_zip_v2, "application/zip")},
        data={"runtime_config": cfg2},
    )
    on_disk = (sites_root / "demo" / "config.json").read_text(encoding="utf-8")
    assert json.loads(on_disk) == json.loads(cfg2)


def test_empty_string_is_treated_as_absent(
    api_client, auth_headers, tiny_valid_zip, tiny_valid_zip_v2, sites_root
):
    cfg = json.dumps({"kept": True})
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v1.zip", tiny_valid_zip, "application/zip")},
        data={"runtime_config": cfg},
    )
    # Empty string should carry forward.
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("v2.zip", tiny_valid_zip_v2, "application/zip")},
        data={"runtime_config": ""},
    )
    on_disk = (sites_root / "demo" / "config.json").read_text(encoding="utf-8")
    assert json.loads(on_disk) == json.loads(cfg)
