# Step 8 — API PUT upsert

**Master spec sections**: [§4.2 Flusso PUT](../SPEC.md), [§5.1 Auth](../SPEC.md), [§5.2 Endpoint PUT](../SPEC.md), [§6.1 schema `sites` + `event_log`](../SPEC.md), [§9 Helm values (limits)](../SPEC.md), [§11.3 test_api_upsert.py (3 tests)](../SPEC.md)
**Roadmap entry**: [§16.1 step 8](../SPEC.md)
**Status**: ⏳ Draft
**Owner**: Andrea Veronesi

---

## 1. Goal

Stand up the FastAPI application and wire the **primary endpoint** — `PUT /api/v1/sites/{slug}` — end-to-end, connecting the six previously-green modules (validator, slug, db, storage, auth, quota) into a real HTTP flow. After this step, a `curl` with a valid bearer token and a valid ZIP produces an on-disk site directory, a row in `sites`, an entry in `event_log`, and a JSON response with a one-time delete token.

This is the first step where we have an HTTP surface, so it also introduces the glue that every subsequent route (steps 9–14) will reuse: config loader, `request_id` middleware, typed exception handlers, auth dependency, DB session dependency. The marginal cost of adding the CRUD endpoints in step 9 should be just the route functions.

---

## 2. Public API / Contract

### 2.1 Module layout

New files:

- `src/ephemeral_sites/config.py` — `Settings` (Pydantic `BaseSettings`) + `get_settings()`.
- `src/ephemeral_sites/api/__init__.py` — empty marker.
- `src/ephemeral_sites/api/app.py` — `create_app(settings=None, db_conn=None) -> FastAPI` factory.
- `src/ephemeral_sites/api/deps.py` — FastAPI dependencies.
- `src/ephemeral_sites/api/errors.py` — exception handlers + `error_response()` helper.
- `src/ephemeral_sites/api/middleware.py` — `RequestIdMiddleware`.
- `src/ephemeral_sites/api/models.py` — Pydantic response/request models.
- `src/ephemeral_sites/api/routes_sites.py` — router containing the PUT route.
- `tests/integration/test_api_upsert.py` — the three master-spec tests + supporting cases.
- `tests/integration/conftest.py` — shared fixtures (`api_client`, `zip_bytes`, `auth_headers`).
- `tests/unit/test_config.py` — settings loading/validation.

Touched:

- `pyproject.toml` — add `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `python-multipart`, `httpx` (test dep for `TestClient`).
- `CLAUDE.md` §8 — mark step 8 as ✅ after completion.

### 2.2 `config.py` — Settings

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API process.

    Populated from environment variables at startup. Every field mirrors a
    Helm `values.yaml` key under `limits:` / `auth:` / `paths:` (master
    spec §9) — the container passes them via env.
    """

    model_config = SettingsConfigDict(
        env_prefix="EPHEMERAL_",
        env_file=".env",
        extra="ignore",
    )

    # --- auth ---
    api_keys: str = Field(default="", description="Raw API_KEYS value; parsed at startup.")

    # --- paths ---
    db_path: str = Field(default="/data/db/ephemeral-sites.db")
    sites_root: str = Field(default="/data/sites")
    lock_dir: str = Field(default="/data/sites/.lock")

    # --- limits (master spec §9 defaults) ---
    max_zip_size: int = Field(default=500 * 1024 * 1024)        # 500 MiB
    max_files_per_site: int = Field(default=5000)
    max_total_storage_bytes: int = Field(default=40 * 1024**3)  # 40 GiB
    max_decompression_ratio: int = Field(default=100)
    default_ttl_seconds: int = Field(default=86400)             # 1 day
    max_ttl_seconds: int = Field(default=31536000)              # 1 year
    allow_permanent: bool = Field(default=True)

    # --- misc ---
    base_domain: str = Field(default="preview.example.test")    # for building public URL
    bcrypt_rounds: int = Field(default=12)                       # tests override to 4


def get_settings() -> Settings:
    """FastAPI dependency: return the process-wide settings instance.

    The cached instance is mutable only in tests (via ``app.dependency_overrides``).
    """
```

### 2.3 `api/models.py` — Pydantic schemas

```python
class SiteResponse(BaseModel):
    slug: str
    url: str
    created_at: str  # ISO 8601 UTC
    updated_at: str
    expires_at: str | None
    size_bytes: int
    files_count: int
    delete_token: str           # plaintext — ONLY in the create/replace response
    spa_mode: bool
    password_protected: bool
    allow_indexing: bool
    labels: list[str] | None


class ErrorResponse(BaseModel):
    error: str        # machine code, e.g. "invalid_zip", "quota_exceeded"
    detail: str       # human string — never contains secrets or file paths
    request_id: str   # uuid4 hex, echoes the X-Request-ID response header
```

### 2.4 `api/errors.py` — exception-to-HTTP mapping

| Exception (source module) | HTTP | `error` slug |
|---|---|---|
| `InvalidAuthHeader` (auth) | 401 | `invalid_auth_header` |
| `InvalidApiKey` (auth) | 401 | `invalid_api_key` |
| `DisabledApiKey` (auth) | 403 | `disabled_api_key` |
| `InvalidSlugError` (slug) | 400 | `invalid_slug` |
| `ValidationError` (validator) | 400 | `invalid_zip` (plus `reason_code` in `detail` — never the raw filename) |
| `QuotaExceeded` (quota) | 507 | `quota_exceeded` |
| `ExtractionError` (storage) | 500 | `extraction_failed` |
| (upload > `max_zip_size`) | 413 | `payload_too_large` |
| anything else uncaught | 500 | `internal_error` |

**Log hygiene** (master spec §7.6): the `detail` string is always sanitized — no stack traces, no filesystem paths, no token echoes. The user's `request_id` is the pointer back to the structured log line.

### 2.5 `api/middleware.py` — request IDs

```python
class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign a uuid4 hex to every request.

    - Reads ``X-Request-ID`` from the incoming headers if present (to
      honour upstream traces), otherwise generates one.
    - Attaches it to ``request.state.request_id``.
    - Echoes it back as ``X-Request-ID`` on the response.
    - Ensures error responses include it in their JSON body.
    """
```

### 2.6 `api/deps.py` — dependencies

```python
def get_settings_dep() -> Settings: ...       # wraps config.get_settings()

def get_db_conn(settings: Settings = Depends(...)) -> Iterator[sqlite3.Connection]:
    """Open (or resume) the SQLite connection from settings.db_path.

    Applies migrations on first call; reuses a singleton in subsequent
    calls (thread-local is fine — FastAPI is single-process here).
    """

def get_api_keys(settings: Settings = Depends(...)) -> tuple[ApiKey, ...]:
    """Parse settings.api_keys ONCE and cache the tuple.

    Failure at startup (InvalidApiKeysEnv) is allowed to crash — we
    fail-fast on misconfiguration.
    """

def require_auth(
    request: Request,
    keys: tuple[ApiKey, ...] = Depends(get_api_keys),
) -> ApiKey:
    """Pull Authorization header, call auth.parse_bearer_header +
    auth.authenticate. Attaches ``request.state.api_key_name`` to the
    request for downstream logging. Raises on failure; the typed
    handler in errors.py maps to 401/403.
    """
```

### 2.7 `api/routes_sites.py` — the PUT route

```python
router = APIRouter(prefix="/api/v1/sites", tags=["sites"])


@router.put("/{slug}", response_model=SiteResponse, status_code=200)
async def put_site(
    slug: str,
    file: UploadFile = File(...),
    ttl_seconds: int = Form(86400),
    password: str | None = Form(None),
    spa_mode: bool = Form(True),
    runtime_config: str | None = Form(None),       # raw JSON string
    allow_indexing: bool = Form(False),
    labels: str | None = Form(None),               # raw JSON array
    api_key: ApiKey = Depends(require_auth),
    settings: Settings = Depends(get_settings_dep),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> SiteResponse:
    """Upsert a site. See master spec §4.2 for the 9-step flow."""
```

Implementation order inside the handler (mirrors master spec §4.2):

1. `slug.validate_slug(slug)` — 400 on malformed.
2. Parse `runtime_config` and `labels` (JSON); reject malformed → 400.
3. Clamp `ttl_seconds`: accept `-1` iff `allow_permanent`; otherwise require `60 <= ttl <= max_ttl_seconds`.
4. Stream `file` to `tempfile.NamedTemporaryFile(delete=False)` under `/data/tmp/`; enforce `max_zip_size` as bytes accumulate → 413.
5. `validator.validate_zip(tmp_path, ValidatorConfig(...))` → 400 on failure (maps `reason_code` to `detail`, **never** the filename).
6. `quota.sum_active_sites_bytes(conn)` + estimated incoming size → `quota.check_quota(...)` → 507 on fail. **Estimate = `validation.total_uncompressed_size`** (what we know we'll write; no double counting).
7. Generate delete token: `auth.generate_delete_token(rounds=settings.bcrypt_rounds)`.
8. Hash password (if present): `auth.hash_secret(password, rounds=settings.bcrypt_rounds)`.
9. `storage.extract_site(...)` — writes `.new/`, `flock`s, swaps atomically. Returns `ExtractionResult(size_bytes, files_count)`.
10. `conn.execute("INSERT INTO sites ... ON CONFLICT(slug) DO UPDATE SET ...", ...)` inside a transaction.
11. `conn.execute("INSERT INTO event_log(slug, event, ...)", ...)` — `event='created'` for first insert, `'replaced'` otherwise. Detect via `conn.execute("SELECT 1 FROM sites WHERE slug=? AND created_at=updated_at", ...)` or return `cursor.rowcount` + the INSERT/UPDATE branch.
12. `conn.commit()`.
13. Build `SiteResponse` — `delete_token` is the plaintext (shown exactly once), `password_protected = password is not None`.
14. On any exception after step 9 succeeded: best-effort rollback of the DB transaction (storage has already swapped, but a missing DB row is recoverable by a later reconciliation; master spec §7 doesn't require storage rollback here).

### 2.8 `api/app.py` — the factory

```python
def create_app(
    *,
    settings: Settings | None = None,
    db_conn: sqlite3.Connection | None = None,
) -> FastAPI:
    """Construct a FastAPI app with all middleware + routers + handlers.

    Parameters exist for tests: pass a ``Settings`` with tmp paths and
    rounds=4, and optionally an already-open SQLite conn, to avoid
    filesystem churn.
    """
```

The factory wires:

- `RequestIdMiddleware`
- Exception handlers from `errors.py`
- The `routes_sites.router`
- Dependency overrides for tests (via `app.dependency_overrides[get_settings_dep] = lambda: settings`).

### 2.9 Not in scope here

- **Rate limiting** (master spec §5.2 → 429). Step 14 adds the bucket; step 8 just leaves the 429 slot free in `errors.py`.
- **POST /api/v1/sites** (auto-slug), **GET/DELETE/PATCH/LIST**. Step 9.
- **Password protection on the served site**. Step 12; step 8 only *stores* the hash.
- **Runtime config injection into the served site**. Step 10; step 8 only *stores* the JSON blob in `sites.runtime_config` and passes it to `extract_site` (which already writes `config.json` per step 5).
- **Metrics labels / counters**. Step 14 increments `ephemeral_sites_created_total` / `_replaced_total`; step 8 just logs `created` vs `replaced` in `event_log`.
- **413 handling at ASGI layer**. FastAPI's `UploadFile` streams by chunks; we enforce the limit in-handler (accumulator check). The httpx TestClient permits large bodies, so the test uses a small `max_zip_size=1000` to verify rejection.

---

## 3. Acceptance Criteria

### 3.1 Config (unit)

1. `Settings()` with no env set returns defaults (500 MiB zip, 5000 files, 40 GiB quota, etc.).
2. `EPHEMERAL_MAX_ZIP_SIZE=1024 python -c "..."` yields `settings.max_zip_size == 1024`.
3. `EPHEMERAL_API_KEYS="main:x,ci:y"` round-trips through `Settings().api_keys`.
4. Unknown env vars under the prefix are ignored (`extra="ignore"` — don't crash on stale env).

### 3.2 Happy path (integration — master spec `test_put_creates_site`)

5. `PUT /api/v1/sites/demo` with a valid bearer + valid ZIP returns 200.
6. Response JSON contains `slug=="demo"`, `url` starting with `https://demo.`, `size_bytes > 0`, `files_count >= 1`, `delete_token` starting with `"dt_"`, `password_protected == false`.
7. A row exists in `sites` with that slug, `created_at == updated_at`, and `delete_token_hash` verifying the returned plaintext.
8. A row exists in `event_log` with `event='created'`.
9. `/data/sites/demo/index.html` exists on disk and contains the expected bytes.

### 3.3 Replace (integration — master spec `test_put_same_slug_replaces_content`)

10. Second PUT to `/api/v1/sites/demo` with a different ZIP returns 200.
11. `created_at` is unchanged from the first upload; `updated_at` has advanced.
12. `event_log` now has two rows for `slug='demo'`: `created` then `replaced`.
13. `/data/sites/demo/index.html` contains the **new** bytes; no leftover files from the old version at the top level.

### 3.4 Zero-404 during swap (integration — master spec `test_put_same_slug_no_404_during_swap`)

14. With one replica and a site already deployed, a concurrent reader thread polling `/data/sites/demo/index.html` (via direct filesystem read, not HTTP — the static server is step 11) never observes the path as missing during a second PUT. This exercises `storage.extract_site`'s `renameat2(RENAME_EXCHANGE)` path. Skipped with a reason on non-Linux runners.

### 3.5 Auth (integration)

15. `PUT` without `Authorization` header → 401, `error="invalid_auth_header"`.
16. `PUT` with `Authorization: Bearer wrong` → 401, `error="invalid_api_key"`.
17. `PUT` with `Authorization: Bearer <disabled_key>` → 403, `error="disabled_api_key"`.
18. Response body includes `request_id` matching the `X-Request-ID` response header.

### 3.6 Validation (integration)

19. `PUT /api/v1/sites/INVALID_SLUG` → 400, `error="invalid_slug"`.
20. `PUT` with a path-traversal ZIP → 400, `error="invalid_zip"`, and the `detail` does NOT contain the literal entry name `../../etc/passwd` (log hygiene).
21. `PUT` with `ttl_seconds=10` (below min 60) and `allow_permanent=true` → 400, `error="invalid_ttl"`.
22. `PUT` with `ttl_seconds=-1` and `allow_permanent=true` → 200, `expires_at=null` in response, DB row has `expires_at IS NULL`.
23. `PUT` with a ZIP exceeding `max_zip_size` (test override to small value) → 413, `error="payload_too_large"`.

### 3.7 Quota (integration)

24. With a tiny `max_total_storage_bytes` and one site already filling it, a second PUT → 507, `error="quota_exceeded"`; no partial files left on disk under `/data/sites/` (no `*.new` leftovers).

### 3.8 Middleware / contract

25. Every response — success and error — carries `X-Request-ID`.
26. If the client sends `X-Request-ID: abc`, the response echoes `abc`. Otherwise the server-generated uuid4 hex is used.
27. Error bodies always match `ErrorResponse` (JSON with `error`, `detail`, `request_id`).

---

## 4. Test List

### 4.1 Unit — `tests/unit/test_config.py` (4)

- [ ] `test_settings_defaults_match_spec`
- [ ] `test_settings_reads_env_prefix`
- [ ] `test_settings_ignores_unknown_env`
- [ ] `test_settings_api_keys_pass_through`

### 4.2 Integration — `tests/integration/test_api_upsert.py`

Master-spec mandated (3):

- [ ] `test_put_creates_site`
- [ ] `test_put_same_slug_replaces_content`
- [ ] `test_put_same_slug_no_404_during_swap` (Linux only; skip otherwise)

Auth (4):

- [ ] `test_put_without_auth_returns_401`
- [ ] `test_put_with_wrong_bearer_returns_401`
- [ ] `test_put_with_disabled_key_returns_403`
- [ ] `test_put_error_body_carries_request_id`

Validation (5):

- [ ] `test_put_invalid_slug_returns_400`
- [ ] `test_put_path_traversal_zip_returns_400_no_filename_leak` (security-marked)
- [ ] `test_put_ttl_below_minimum_returns_400`
- [ ] `test_put_ttl_minus_one_permanent_stored_as_null`
- [ ] `test_put_zip_over_max_size_returns_413`

Quota (1):

- [ ] `test_put_over_quota_returns_507_no_leftover_new_dirs`

Middleware / contract (3):

- [ ] `test_response_has_x_request_id_header`
- [ ] `test_client_supplied_x_request_id_is_echoed`
- [ ] `test_error_body_shape_matches_ErrorResponse`

### 4.3 Security

Tests 20 (path-traversal filename leak) is marked `@pytest.mark.security`.

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- **Upload stream exceeds `max_zip_size` mid-body**: reject with 413 as soon as the accumulator passes the cap; close and unlink the temp file; do NOT run the validator or touch quota/storage.
- **ZIP that validates but has 0 files after flattening**: already handled by the validator (`EMPTY_ARCHIVE` reason); API layer just maps to 400.
- **Two concurrent PUTs to the same slug**: `storage.extract_site` already serializes via `flock`. The second one waits, then swaps on top. The `event_log` rows are both `replaced` (or one `created` + one `replaced`) — consistent with the spec. Not explicitly tested at the API layer in step 8 — step 18's E2E covers it.
- **Client sends `Content-Type: application/json` by mistake** instead of `multipart/form-data`: FastAPI's `File(...)` / `Form(...)` auto-rejects with 422. We let that propagate (422, not 400 — FastAPI's convention for request-shape errors).

### 5.2 Deferred

- **Rate limiting** (429) → step 14, along with metrics.
- **Atomic DB+filesystem recovery** if `conn.commit()` fails after `extract_site` succeeded → leaves an orphan site directory. Step 13's cleanup job reaps it on next scan (`sites.slug` missing from DB = garbage, remove).
- **Labels indexing / filtering** → step 9 (LIST endpoint).
- **POST auto-slug collision retry** → step 9.

### 5.3 Explicitly non-goal

- **JSON body for PUT** — master spec §5.2 says `multipart/form-data`. Only.
- **Pre-upload signed URLs** (à la S3 PUT) — not in spec, not in scope.
- **Chunked upload resume** — single-user, single-replica, small ZIPs; not worth the complexity.

---

## 6. Open Questions

(None — mini-spec approved.)

~~Q1: Where does the site URL host come from? Hard-coded from `settings.base_domain` or derived from the request's `Host` header?~~
→ `settings.base_domain` (Helm value). The API pod and the static server pod share a domain, but the API may sit behind a different ingress hostname than the one users hit. We don't want to leak the internal ingress host into response bodies. The Helm chart feeds `EPHEMERAL_BASE_DOMAIN` from `values.yaml`.

~~Q2: `event_log.api_key` — store the name or the hash?~~
→ The name (free-form label). It's already non-secret (master spec §5.1: "nomi liberi usati solo per log"). Storing the hash would add work for no audit value.

~~Q3: On `replaced`, do we keep the same `delete_token_hash` or rotate it?~~
→ **Rotate**. A replaced site returns a *new* delete token in the response; the old one stops working. Makes the delete-token contract "the last PUT response is the only thing that can DELETE without an API key". Simpler than mixing rotation logic with an "extend existing token" path.

~~Q4: If `password` is present in the form but empty string, is that "remove password" or 400?~~
→ Treat empty string as 400. The field is optional (absence = no password). `""` is a client bug and we fail loud. `PATCH` in step 9 will accept explicit `null` to remove; PUT doesn't have that mode.

~~Q5: The static server is step 11; how do we test `no_404_during_swap` now?~~
→ Direct filesystem probe. The test spawns a reader thread that `os.stat("/data/sites/demo/index.html")` in a tight loop while the PUT runs in the main thread; any `FileNotFoundError` fails the test. This is what the storage step 5 test already does — step 8 just wraps it in an API call instead of calling `extract_site` directly.

---

## 7. Done When

- [ ] All tests in §4 committed and green on CI.
- [ ] Overall coverage ≥ 80%; `api/` routes ≥ 80% per master spec §11.4.
- [ ] `make check` clean locally.
- [ ] `src/ephemeral_sites/api/` tree is importable; `uvicorn ephemeral_sites.api.app:create_app --factory` would boot the app (smoke-tested in unit test, not CI-run).
- [ ] Roadmap table in [`CLAUDE.md`](../../CLAUDE.md) §8 updated (Step 8 → ✅).
- [ ] This file's Status flipped to ✅.
