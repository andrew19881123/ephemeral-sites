# API Reference

All API endpoints live under `/api/v1/`. Authentication is `Authorization: Bearer <API_KEY>`
unless noted. Every response carries `X-Request-ID` (uuid4 hex) which is the correlation
key for log lines and appears in the body of every error response.

## Error response shape

All non-2xx responses use:

```json
{
  "error": "not_found",
  "detail": "human-readable string (never contains file paths or secrets)",
  "request_id": "3fa85f64...7dc"
}
```

`error` is a stable machine-readable slug (e.g. `invalid_zip`, `quota_exceeded`).
Branch on that, not on HTTP status alone.

## Sites

### PUT `/api/v1/sites/{slug}`

Create or replace a site. `slug` must match `^[a-z0-9][a-z0-9-]{2,62}$`.

| Body (`multipart/form-data`) | Type | Default | Notes |
|---|---|---|---|
| `file` | .zip | required | max 500 MiB (configurable) |
| `ttl_seconds` | int | 86400 | 60..31536000 or `-1` (permanent) |
| `password` | string | null | basic-auth gate on served content |
| `spa_mode` | bool | true | SPA fallback on 404 |
| `runtime_config` | JSON string | carry-forward | written as `/config.json` |
| `allow_indexing` | bool | false | toggles `X-Robots-Tag: noindex` |
| `labels` | JSON array | null | list of strings |

**200 OK** returns the full site payload **including a plaintext `delete_token`** that
can delete without a bearer (shown exactly once — save it):

```json
{
  "slug": "demo",
  "url": "https://demo.preview.example.test",
  "created_at": "2026-05-06T21:00:00Z",
  "updated_at": "2026-05-06T21:30:00Z",
  "expires_at": "2026-06-05T21:30:00Z",
  "size_bytes": 2457600,
  "files_count": 42,
  "delete_token": "dt_abc123xyz",
  "spa_mode": true,
  "password_protected": false,
  "allow_indexing": false,
  "labels": ["experiment"]
}
```

**Errors:** 400 invalid_slug / invalid_zip / invalid_ttl / malformed_field · 401 invalid_auth_header / invalid_api_key · 403 disabled_api_key · 413 payload_too_large · 507 quota_exceeded.

### POST `/api/v1/sites`

Same body as PUT but the slug is auto-generated (`{adjective}-{noun}-{4hex}`).
Returns **201 Created**.

### GET `/api/v1/sites/{slug}`

Returns the metadata (same shape minus `delete_token`, plus `hits` + `last_hit`).

### PATCH `/api/v1/sites/{slug}`

`Content-Type: application/json`. Every field optional:

```json
{
  "ttl_seconds": 2592000,
  "password": null,
  "allow_indexing": true,
  "labels": ["portfolio"]
}
```

- `password: null` removes the existing password; empty string (`""`) → 400.
- `ttl_seconds` is NON-additive: new `expires_at = now + ttl_seconds`.

Returns the updated metadata.

### DELETE `/api/v1/sites/{slug}`

Two auth modes (use either, not both):

- `Authorization: Bearer <API_KEY>` → event_log reason `"manual"`.
- `X-Delete-Token: dt_...` → event_log reason `"token"` (the token from the create response).

Returns **204 No Content**.

### GET `/api/v1/sites`

Paginated list.

| Query | Default | Notes |
|---|---|---|
| `label` | — | filter: site.labels contains this value |
| `limit` | 50 | 1..200 |
| `offset` | 0 | ≥ 0 |
| `sort` | `-created_at` | `created_at` / `updated_at` / `expires_at` / `slug`, `-` prefix = DESC |

Returns `{"total": N, "items": [SiteMetadata, ...]}`. `delete_token_hash` and
`password_hash` are never returned.

## Probes & metrics

| Path | Purpose |
|---|---|
| `GET /healthz` | Always 200 if process is alive |
| `GET /readyz` | 200 iff DB open AND sites_root writable; 503 otherwise |
| `GET /metrics` | Prometheus text format |

Exposed metrics (see [§5.8 of SPEC.md](SPEC.md)):

```
ephemeral_sites_total                gauge
ephemeral_sites_created_total        counter{api_key_name}
ephemeral_sites_replaced_total       counter{api_key_name}
ephemeral_sites_expired_total        counter
ephemeral_sites_deleted_total        counter{reason}
ephemeral_sites_storage_bytes        gauge
ephemeral_sites_quota_reject_total   counter
```

## Served content (wildcard subdomain)

At `https://{slug}.<base_domain>/<path>`:

- Regular file match → served with `Cache-Control: public, max-age=300` + CSP headers.
- `index.html` → `Cache-Control: no-cache`.
- SPA mode + missing file + non-asset path → falls back to `index.html` (200).
- Path traversal → 400 or 404.
- Expired site → 404.
- Password-protected site → 401 + `WWW-Authenticate: Basic realm="..."`.

Plus two synthetic endpoints:

- `GET /_ephemeral/info` → `{slug, expires_at, hits}` + `no-cache`.
- `GET /config.json` → the `runtime_config` blob if present, 404 otherwise.
