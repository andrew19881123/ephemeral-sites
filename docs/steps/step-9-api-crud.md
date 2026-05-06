# Step 9 — API CRUD siblings

**Master spec sections**: [§5.3 POST](../SPEC.md), [§5.4 GET {slug}](../SPEC.md), [§5.5 DELETE](../SPEC.md), [§5.6 PATCH](../SPEC.md), [§5.7 LIST](../SPEC.md), [§11.3 test_api_lifecycle](../SPEC.md)
**Roadmap entry**: [§16.1 step 9](../SPEC.md)
**Status**: ✅ Complete (2026-05-06, commit `b2b7b99`)
**Owner**: Andrea Veronesi

---

## 1. Goal

Complete the CRUD surface around the primary PUT endpoint. After this step the API exposes POST (auto-slug create), GET (fetch metadata), DELETE (by bearer or delete token), PATCH (mutate metadata only), and LIST (paginated). The spec-mandated `test_post_get_patch_delete` lifecycle test goes green.

---

## 2. Public API / Contract

### 2.1 Routes added

- `POST   /api/v1/sites` → 201, same body as PUT, slug auto-generated via `slug.generate_unique_slug`
- `GET    /api/v1/sites/{slug}` → 200 (`SiteResponse` without `delete_token`) | 401 | 404
- `DELETE /api/v1/sites/{slug}` → 204 | 401 (bearer OR X-Delete-Token) | 404
- `PATCH  /api/v1/sites/{slug}` → 200 | 400 | 401 | 404
- `GET    /api/v1/sites` → 200, `{total, items[]}` paginated

### 2.2 Shared details

- `delete_token` is returned ONLY in PUT/POST (create/replace responses). GET/PATCH/LIST return `SiteMetadataResponse` (same shape minus `delete_token`, plus `hits`, `last_hit`).
- `delete_token_hash` and `password_hash` never leave the DB.
- PATCH body is `application/json`. Every field optional. `ttl_seconds=-1` = permanent. `password=null` = remove existing. `password=""` = 400. `labels=null` = remove.
- DELETE accepts `Authorization: Bearer ...` OR `X-Delete-Token: dt_...`. If both present, bearer wins. At least one must validate.
- LIST: `limit` default 50, max 200. `offset` default 0. `sort` defaults to `-created_at`. Sortable fields: `created_at`, `updated_at`, `expires_at`, `slug`. `-` prefix = DESC.
- LIST filtering by label: row matches iff `labels` JSON array contains the given value.

### 2.3 Module layout

- `src/ephemeral_sites/api/routes_sites.py` — extended with 5 new handlers.
- `src/ephemeral_sites/api/models.py` — `SiteMetadataResponse`, `PatchSiteRequest`, `ListSitesResponse`.
- `tests/integration/test_api_lifecycle.py` — the spec-mandated POST→GET→PATCH→DELETE loop.
- `tests/integration/test_api_list.py` — pagination + filtering.
- `tests/integration/test_api_delete.py` — bearer vs token auth paths.

### 2.4 DELETE auth

```python
def authorize_delete(
    request: Request,
    slug: str,
    settings: Settings,
    conn: sqlite3.Connection,
    keys: tuple[ApiKey, ...],
) -> str:
    """Return 'bearer'|'token' on success. Raises InvalidAuthHeader/InvalidApiKey on fail.

    Precedence:
    1. If Authorization header present → delegate to require_auth semantics.
       Success returns 'bearer'.
    2. Else if X-Delete-Token header present → look up delete_token_hash for
       slug; verify_delete_token(value, hash). Match returns 'token'.
    3. Neither → InvalidAuthHeader.
    """
```

The reason ("bearer" vs "token") is stored in `event_log.metadata` JSON as `{"reason": "manual"}` vs `{"reason": "token"}`.

---

## 3. Acceptance Criteria

1. POST /api/v1/sites with valid bearer + ZIP → 201, `slug` matches `{adj}-{noun}-{4hex}`, row exists.
2. POST collision retry: if generate_unique_slug exhausts 5 attempts, 500.
3. GET /api/v1/sites/{slug} on existing site → 200, shape = SiteMetadataResponse (no delete_token), includes hits=0 and last_hit=null on fresh row.
4. GET on missing slug → 404 with `error="not_found"`.
5. GET without auth → 401.
6. DELETE with bearer on existing → 204, site dir gone, row gone, event 'deleted' with `reason="manual"`.
7. DELETE with X-Delete-Token on existing → 204, event reason="token".
8. DELETE with wrong X-Delete-Token → 401.
9. DELETE on missing slug → 404.
10. DELETE without any auth → 401.
11. PATCH ttl_seconds=300 → expires_at = now + 300s (not additive).
12. PATCH password="newpass" → row's password_hash verifies "newpass".
13. PATCH password=null → row's password_hash is NULL.
14. PATCH labels=["x","y"] → row's labels JSON = '["x","y"]'.
15. PATCH allow_indexing toggles.
16. PATCH on missing slug → 404.
17. PATCH with empty body → 200, no changes.
18. LIST with no sites → `{total: 0, items: []}`.
19. LIST with 3 sites → `total=3`, items sorted by `-created_at` default.
20. LIST `?limit=1&offset=1` paginates correctly.
21. LIST `?label=foo` returns only sites with "foo" in labels.
22. LIST `?sort=slug` sorts ASC by slug.
23. LIST `?limit=201` → 400 (cap violated).
24. PATCH/DELETE/GET/LIST all include X-Request-ID and conform to ErrorResponse shape on errors.
25. Test `test_post_get_patch_delete` (spec §11.3) ties 1+3+11+6 into a single flow.

---

## 4. Test List

### 4.1 `tests/integration/test_api_lifecycle.py`

- [ ] `test_post_get_patch_delete` — the spec-mandated lifecycle (POST create, GET verify, PATCH extend TTL, DELETE, GET 404)

### 4.2 `tests/integration/test_api_post.py`

- [ ] `test_post_auto_slug_creates_site`
- [ ] `test_post_returns_201`
- [ ] `test_post_auto_slug_matches_adj_noun_4hex`
- [ ] `test_post_collision_exhaustion_returns_500` (mock rng)
- [ ] `test_post_without_auth_returns_401`

### 4.3 `tests/integration/test_api_get.py`

- [ ] `test_get_existing_site_returns_metadata_without_delete_token`
- [ ] `test_get_missing_slug_returns_404`
- [ ] `test_get_without_auth_returns_401`

### 4.4 `tests/integration/test_api_delete.py`

- [ ] `test_delete_with_bearer_succeeds_and_logs_reason_manual`
- [ ] `test_delete_with_valid_token_succeeds_and_logs_reason_token`
- [ ] `test_delete_with_wrong_token_returns_401`
- [ ] `test_delete_missing_slug_returns_404`
- [ ] `test_delete_without_any_auth_returns_401`
- [ ] `test_delete_removes_dir_and_row_and_lock_file`

### 4.5 `tests/integration/test_api_patch.py`

- [ ] `test_patch_ttl_seconds_replaces_expires_at`
- [ ] `test_patch_password_sets_hash`
- [ ] `test_patch_password_null_removes_hash`
- [ ] `test_patch_password_empty_string_returns_400`
- [ ] `test_patch_labels_replaces_array`
- [ ] `test_patch_allow_indexing_toggles`
- [ ] `test_patch_missing_slug_returns_404`
- [ ] `test_patch_empty_body_noop_returns_200`

### 4.6 `tests/integration/test_api_list.py`

- [ ] `test_list_empty_returns_empty_items`
- [ ] `test_list_returns_all_sites_default_sort_desc_created`
- [ ] `test_list_limit_and_offset`
- [ ] `test_list_filter_by_label`
- [ ] `test_list_sort_by_slug_asc`
- [ ] `test_list_limit_over_max_returns_400`

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- DELETE idempotency: second DELETE of the same slug returns 404 (the row/dir are gone by then). The spec doesn't require 204-idempotent; 404 is the cleaner signal.
- PATCH with a body containing only unknown fields: Pydantic v2 `extra="ignore"` — silent pass (200 noop).
- GET/LIST on an expired-but-not-reaped site: for now include it. The cleanup CronJob (step 13) is what filters by `expires_at > now()`. Step 9 does NOT filter, so API + DB stay a single source.

### 5.2 Deferred

- Rate limiting on all routes → step 14.
- Metrics labels for each route → step 14.
- LIST `?sort` on composite fields → not needed in v1.

### 5.3 Explicitly non-goal

- HATEOAS links in responses — not in spec.

---

## 6. Open Questions

~~Q1: If a PATCH changes ttl_seconds to a shorter value that places expires_at in the past, reject or accept?~~
→ Accept. The operator clearly wants to expire the site "soon"; cleanup picks it up on next tick. Master spec §5.6 doesn't reject; we follow.

~~Q2: DELETE by delete-token — must we gate by the slug matching OR accept any valid token?~~
→ Gate by slug. `X-Delete-Token` is scoped to the slug in the path. A leaked token can only delete its own site, not any other.

~~Q3: LIST — include `delete_token` ever? No. Confirmed by spec §5.7.~~

---

## 7. Done When

- [ ] All tests in §4 green (~30 new).
- [ ] Coverage stays ≥ 80% overall.
- [ ] `make check` clean.
- [ ] CLAUDE.md roadmap row 9 → ✅.
- [ ] Status flipped to ✅.
