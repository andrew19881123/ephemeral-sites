# Step 11 — Static server + SPA fallback + security headers

**Master spec sections**: [§4.3 Flusso GET static](../SPEC.md), [§5.9 path speciali](../SPEC.md), [§7.2 security headers](../SPEC.md), [§11.3 test_server_serve](../SPEC.md)
**Roadmap entry**: [§16.1 step 11](../SPEC.md)
**Status**: ✅ Complete (2026-05-06, commit `8db59de`)
**Owner**: Andrea Veronesi

---

## 1. Goal

Serve the static content of each site under its wildcard subdomain. Given `https://{slug}.preview.example.com/<path>`, resolve the slug from the `Host` header, look it up in `sites`, and either:

- Serve the requested file with proper Content-Type + security headers, or
- Fall back to `index.html` when `spa_mode=true` and the path doesn't look like a static asset (SPA client routing), or
- Return 404 when the file doesn't exist and SPA fallback doesn't apply.

Also mount two synthetic paths per master spec §5.9:

- `GET /_ephemeral/info` → JSON `{slug, expires_at, hits}` with `Cache-Control: no-cache`.
- `GET /config.json` → the `runtime_config` blob from DB (if present) with `no-cache`; otherwise 404.

Password-protected sites (basic auth) are deferred to **step 12**. Step 11 stores the hash and rejects the whole pipeline cleanly if `password_hash IS NOT NULL` — for now we return 401 on any request to such sites (step 12 replaces this stub with proper Basic auth validation).

---

## 2. Public API / Contract

### 2.1 Module layout

- `src/ephemeral_sites/server/__init__.py` — factory (same pattern as `api.app.create_app`).
- `src/ephemeral_sites/server/app.py` — `create_server_app(*, settings, lookup=None) -> FastAPI`.
- `src/ephemeral_sites/server/resolver.py` — `resolve_slug_from_host(host, base_domain) -> str | None`.
- `src/ephemeral_sites/server/headers.py` — `apply_security_headers(response, *, allow_indexing)`.
- `src/ephemeral_sites/server/spa.py` — `is_asset_path(path) -> bool` (helper for SPA fallback).
- `src/ephemeral_sites/server/lookup.py` — `SiteLookup` (DB-backed with optional TTL cache; step 14 adds metrics).
- `tests/integration/test_server_serve.py` — the spec §11.3 tests.

### 2.2 Host resolution

Given `host="demo.preview.example.com"` and `base_domain="preview.example.com"`:

- Strip `:port` if present.
- If `host.endswith("." + base_domain)`: slug = host removed of that suffix, validated via `slug.validate_slug`. Invalid slug → return None (treated as 404).
- If `host` does not match the wildcard pattern: return None.

### 2.3 SPA asset detection

`is_asset_path(path)` returns True if:
- Path starts with `/static/`, `/assets/`, or `/_ephemeral/`.
- OR path ends with any extension in the validator's `allowed_extensions` (`.js`, `.css`, `.png`, etc.) — we use a small hard-coded subset here: `.js .mjs .css .png .jpg .jpeg .gif .svg .webp .ico .woff .woff2 .ttf .otf .eot .map .json`.

Otherwise (e.g. `/users/42`, `/dashboard`) → False, triggering SPA fallback.

### 2.4 Security headers

Applied on every 200 response:

```
X-Content-Type-Options: nosniff
X-Frame-Options: SAMEORIGIN
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval';
                         style-src 'self' 'unsafe-inline'; img-src 'self' data: https:;
                         font-src 'self' data:; connect-src *;
X-Robots-Tag: noindex, nofollow, noarchive        # only if allow_indexing=false
Cache-Control: no-cache                            # for index.html, config.json, /_ephemeral/*
Cache-Control: public, max-age=300                 # for everything else
```

### 2.5 Hits counter

Deferred: master spec §4.3 step 7 says "INCR hits (async batch ogni 10 accessi per ridurre I/O DB)". Step 11 uses an in-memory counter flushed every 10 hits per slug OR on app shutdown. Step 14 wires Prometheus metrics.

### 2.6 Not in scope

- Password auth enforcement on served content → step 12.
- Host-based routing on the *API* app (API uses a different hostname, no change).
- Range requests, gzip negotiation, etag → v1.1.

---

## 3. Acceptance Criteria

1. `GET https://demo.preview/` → 200, body = `index.html` bytes from disk.
2. Response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Content-Security-Policy`.
3. Default site (`allow_indexing=false`) gets `X-Robots-Tag: noindex, nofollow, noarchive`.
4. A PUT with `allow_indexing=true` → served pages do NOT carry `X-Robots-Tag`.
5. `GET /static/app.js` returns the file with `Cache-Control: public, max-age=300`.
6. `GET /index.html` gets `Cache-Control: no-cache`.
7. `GET /nonexistent-route` with `spa_mode=true` → 200, body = `index.html` (SPA fallback).
8. `GET /nonexistent-route` with `spa_mode=false` → 404.
9. `GET /static/nonexistent.js` → 404 even with `spa_mode=true` (asset-like path is not a SPA route).
10. `GET /../etc/passwd` → 400 or 404 (path traversal rejected, never serves outside site dir).
11. Unknown host (not a wildcard match) → 404.
12. Expired site (`expires_at < now()`) → 404.
13. Password-protected site → 401 with `WWW-Authenticate: Basic realm="..."` (stub — step 12 adds verification).
14. `GET /_ephemeral/info` → `{slug, expires_at, hits}` JSON + `no-cache`.
15. `GET /config.json` on a site with `runtime_config` → that JSON + `no-cache`.
16. `GET /config.json` on a site WITHOUT `runtime_config` → 404.

---

## 4. Test List

- [ ] `tests/integration/test_server_serve.py::test_spa_fallback_to_index_html` (spec §11.3)
- [ ] `tests/integration/test_server_serve.py::test_static_asset_not_fallback` (spec §11.3)
- [ ] `tests/unit/test_server_resolver.py::test_resolve_slug_from_host_strips_suffix`
- [ ] `tests/unit/test_server_resolver.py::test_resolve_slug_from_host_rejects_non_wildcard`
- [ ] `tests/unit/test_server_resolver.py::test_resolve_slug_from_host_invalid_slug_returns_none`
- [ ] `tests/unit/test_server_spa.py::test_is_asset_path_recognises_extensions`
- [ ] `tests/unit/test_server_spa.py::test_is_asset_path_recognises_prefixes`
- [ ] `tests/unit/test_server_spa.py::test_is_asset_path_defaults_false_for_app_routes`
- [ ] `tests/unit/test_server_headers.py::test_security_headers_default_noindex`
- [ ] `tests/unit/test_server_headers.py::test_security_headers_omits_robots_when_indexing_allowed`
- [ ] `tests/integration/test_server_serve.py::test_get_root_returns_index_html`
- [ ] `tests/integration/test_server_serve.py::test_unknown_host_returns_404`
- [ ] `tests/integration/test_server_serve.py::test_expired_site_returns_404`
- [ ] `tests/integration/test_server_serve.py::test_password_protected_returns_401_stub`
- [ ] `tests/integration/test_server_serve.py::test_path_traversal_rejected`
- [ ] `tests/integration/test_server_serve.py::test_ephemeral_info_endpoint`
- [ ] `tests/integration/test_server_serve.py::test_config_json_endpoint_when_present`
- [ ] `tests/integration/test_server_serve.py::test_config_json_endpoint_404_when_absent`

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- Host with port: `demo.preview.example.com:8080` — strip port before matching.
- Case-insensitive host match: `DEMO.preview.example.com` → slug `demo` (slugs are lowercase).
- Trailing slash: `/foo/` equivalent to `/foo`.
- `/` (root) → serve `/index.html`.

### 5.2 Deferred

- Byte-range requests → v1.1.
- gzip / brotli on-the-fly → v1.1.
- Password auth verification → step 12.
- Hits counter persistence → step 14 (shared with metrics refactor).

### 5.3 Explicitly non-goal

- HSTS header → handled by Traefik, not the app.
- CORS → master spec doesn't require it; sites are SPAs talking to arbitrary APIs.

---

## 6. Open Questions

~~Q1: Should `/_ephemeral/info` also carry the `X-Robots-Tag: noindex`?~~
→ Yes. Never index our synthetic endpoints regardless of `allow_indexing`.

~~Q2: If `spa_mode=true` and `index.html` is missing (operator error), what do we serve?~~
→ 500 with `error="index_missing"`. Shouldn't happen (validator requires it) but be defensive.

---

## 7. Done When

- [ ] All tests in §4 green.
- [ ] Coverage ≥ 80% overall.
- [ ] `make check` clean.
- [ ] CLAUDE.md row 11 → ✅.
- [ ] This file → ✅.
