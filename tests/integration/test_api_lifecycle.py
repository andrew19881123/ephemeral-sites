"""Integration tests for POST/GET/DELETE/PATCH/LIST on /api/v1/sites.

Red tests for step 9; see ``docs/steps/step-9-api-crud.md``.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Lifecycle — master spec section 11.3 test_post_get_patch_delete
# ---------------------------------------------------------------------------


def test_post_get_patch_delete(api_client, auth_headers, tiny_valid_zip):
    """POST to create with auto-slug, GET to verify, PATCH to extend TTL, DELETE."""
    # POST: auto-slug
    r_create = api_client.post(
        "/api/v1/sites",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert r_create.status_code == 201, r_create.text
    created = r_create.json()
    slug = created["slug"]
    assert re.match(r"^[a-z]+-[a-z]+-[a-f0-9]{4}$", slug), f"bad auto-slug: {slug}"

    # GET
    r_get = api_client.get(f"/api/v1/sites/{slug}", headers=auth_headers)
    assert r_get.status_code == 200, r_get.text
    got = r_get.json()
    assert got["slug"] == slug
    assert "delete_token" not in got  # never leaked on GET
    assert got["hits"] == 0
    assert got["last_hit"] is None

    # PATCH: extend TTL
    r_patch = api_client.patch(
        f"/api/v1/sites/{slug}",
        headers=auth_headers,
        json={"ttl_seconds": 3600},
    )
    assert r_patch.status_code == 200, r_patch.text

    # DELETE with bearer
    r_del = api_client.delete(f"/api/v1/sites/{slug}", headers=auth_headers)
    assert r_del.status_code == 204

    # GET now 404
    r_gone = api_client.get(f"/api/v1/sites/{slug}", headers=auth_headers)
    assert r_gone.status_code == 404


# ---------------------------------------------------------------------------
# POST auto-slug
# ---------------------------------------------------------------------------


def test_post_without_auth_returns_401(api_client, tiny_valid_zip):
    r = api_client.post(
        "/api/v1/sites",
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /{slug}
# ---------------------------------------------------------------------------


def test_get_missing_slug_returns_404(api_client, auth_headers):
    r = api_client.get("/api/v1/sites/nonexistent", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_get_without_auth_returns_401(api_client):
    r = api_client.get("/api/v1/sites/demo")
    assert r.status_code == 401


def test_get_returns_metadata_without_delete_token(
    api_client, auth_headers, tiny_valid_zip
):
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    r = api_client.get("/api/v1/sites/demo", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "delete_token" not in body
    assert body["slug"] == "demo"
    assert "hits" in body
    assert "last_hit" in body


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_with_bearer_succeeds(
    api_client, auth_headers, tiny_valid_zip, sites_root, open_conn
):
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    r = api_client.delete("/api/v1/sites/demo", headers=auth_headers)
    assert r.status_code == 204
    assert not (sites_root / "demo").exists()

    conn = open_conn()
    try:
        row = conn.execute("SELECT slug FROM sites WHERE slug='demo'").fetchone()
        assert row is None
        evt = conn.execute(
            "SELECT event, metadata FROM event_log WHERE slug='demo' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert evt[0] == "deleted"
        assert "manual" in (evt[1] or "")
    finally:
        conn.close()


def test_delete_with_valid_delete_token_succeeds(
    api_client, auth_headers, tiny_valid_zip, open_conn
):
    r_put = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    token = r_put.json()["delete_token"]

    r = api_client.delete(
        "/api/v1/sites/demo", headers={"X-Delete-Token": token}
    )
    assert r.status_code == 204

    conn = open_conn()
    try:
        evt = conn.execute(
            "SELECT metadata FROM event_log WHERE slug='demo' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert evt is not None and "token" in (evt[0] or "")
    finally:
        conn.close()


def test_delete_with_wrong_delete_token_returns_401(
    api_client, auth_headers, tiny_valid_zip
):
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    r = api_client.delete(
        "/api/v1/sites/demo", headers={"X-Delete-Token": "dt_wrongtoken123"}
    )
    assert r.status_code == 401


def test_delete_missing_slug_returns_404(api_client, auth_headers):
    r = api_client.delete("/api/v1/sites/nonexistent", headers=auth_headers)
    assert r.status_code == 404


def test_delete_without_any_auth_returns_401(api_client, auth_headers, tiny_valid_zip):
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    r = api_client.delete("/api/v1/sites/demo")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


def test_patch_ttl_extends_expires_at(api_client, auth_headers, tiny_valid_zip, open_conn):
    r_put = api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    old_expires = r_put.json()["expires_at"]

    r = api_client.patch(
        "/api/v1/sites/demo",
        headers=auth_headers,
        json={"ttl_seconds": 7200},
    )
    assert r.status_code == 200
    new_expires = r.json()["expires_at"]
    assert new_expires is not None
    # New expires_at should be different from the original (recomputed from now).
    assert new_expires != old_expires


def test_patch_password_sets_and_clears(api_client, auth_headers, tiny_valid_zip, open_conn):
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )

    # Set
    r_set = api_client.patch(
        "/api/v1/sites/demo",
        headers=auth_headers,
        json={"password": "secretpw"},
    )
    assert r_set.status_code == 200
    assert r_set.json()["password_protected"] is True

    # Clear via null
    r_clear = api_client.patch(
        "/api/v1/sites/demo",
        headers=auth_headers,
        json={"password": None},
    )
    assert r_clear.status_code == 200
    assert r_clear.json()["password_protected"] is False


def test_patch_password_empty_string_returns_400(
    api_client, auth_headers, tiny_valid_zip
):
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    r = api_client.patch(
        "/api/v1/sites/demo",
        headers=auth_headers,
        json={"password": ""},
    )
    assert r.status_code == 400


def test_patch_labels_replaces_array(api_client, auth_headers, tiny_valid_zip):
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    r = api_client.patch(
        "/api/v1/sites/demo",
        headers=auth_headers,
        json={"labels": ["portfolio", "ml"]},
    )
    assert r.status_code == 200
    assert r.json()["labels"] == ["portfolio", "ml"]


def test_patch_missing_slug_returns_404(api_client, auth_headers):
    r = api_client.patch(
        "/api/v1/sites/nope",
        headers=auth_headers,
        json={"ttl_seconds": 3600},
    )
    assert r.status_code == 404


def test_patch_empty_body_noop_returns_200(api_client, auth_headers, tiny_valid_zip):
    api_client.put(
        "/api/v1/sites/demo",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
    )
    r = api_client.patch("/api/v1/sites/demo", headers=auth_headers, json={})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------


def test_list_empty(api_client, auth_headers):
    r = api_client.get("/api/v1/sites", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_list_three_sites_default_sort_desc_created(
    api_client, auth_headers, tiny_valid_zip
):
    import time

    for slug in ("alpha", "bravo", "charlie"):
        api_client.put(
            f"/api/v1/sites/{slug}",
            headers=auth_headers,
            files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        )
        time.sleep(1.05)  # ensure distinct created_at at 1s granularity

    r = api_client.get("/api/v1/sites", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    slugs_in_order = [it["slug"] for it in body["items"]]
    # Default sort = -created_at → charlie (last created) first.
    assert slugs_in_order == ["charlie", "bravo", "alpha"]


def test_list_limit_and_offset(api_client, auth_headers, tiny_valid_zip):
    import time

    for slug in ("a1", "b2", "c3"):
        api_client.put(
            f"/api/v1/sites/{slug}",
            headers=auth_headers,
            files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        )
        time.sleep(1.05)

    r = api_client.get("/api/v1/sites?limit=1&offset=1", headers=auth_headers)
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1


def test_list_filter_by_label(api_client, auth_headers, tiny_valid_zip):
    api_client.put(
        "/api/v1/sites/site-a",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        data={"labels": '["experiment"]'},
    )
    api_client.put(
        "/api/v1/sites/site-b",
        headers=auth_headers,
        files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        data={"labels": '["portfolio"]'},
    )

    r = api_client.get("/api/v1/sites?label=experiment", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "site-a"


def test_list_sort_by_slug_asc(api_client, auth_headers, tiny_valid_zip):
    for slug in ("zeta", "alpha", "mu"):
        api_client.put(
            f"/api/v1/sites/{slug}",
            headers=auth_headers,
            files={"file": ("spa.zip", tiny_valid_zip, "application/zip")},
        )

    r = api_client.get("/api/v1/sites?sort=slug", headers=auth_headers)
    body = r.json()
    assert [it["slug"] for it in body["items"]] == ["alpha", "mu", "zeta"]


def test_list_limit_over_max_returns_400(api_client, auth_headers):
    r = api_client.get("/api/v1/sites?limit=201", headers=auth_headers)
    assert r.status_code == 400
