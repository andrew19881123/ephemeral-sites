# Step 14 — Metrics + health endpoints

**Master spec sections**: [§5.8 probes & metrics](../SPEC.md)
**Roadmap entry**: [§16.1 step 14](../SPEC.md)
**Status**: ⏳ Draft

---

## 1. Goal

Wire the probes and the Prometheus exporter: `GET /healthz` (liveness), `GET /readyz` (readiness: DB open + sites_root writable), `GET /metrics` (prometheus text format). Also emit the counters/gauges listed in master spec §5.8 from the business paths already in place (PUT/POST/DELETE/cleanup). No new middleware for HTTP request metrics in this step — master spec §5.8 mentions them but they're non-critical and easy to add later via starlette middleware; we keep the scope focused on the state gauges/counters that give operational value day one.

---

## 2. Contract

### 2.1 Module layout

- `src/ephemeral_sites/metrics.py` — `prometheus_client` registry + metric definitions + `render_metrics()` helper.
- `src/ephemeral_sites/api/routes_probes.py` — the 3 routes.
- Wired in `api/app.py::create_app`.

### 2.2 Metrics

```
ephemeral_sites_total                 gauge      (sites in DB)
ephemeral_sites_created_total         counter    (api_key_name)
ephemeral_sites_replaced_total        counter    (api_key_name)
ephemeral_sites_expired_total         counter
ephemeral_sites_deleted_total         counter    (reason)
ephemeral_sites_storage_bytes         gauge      (sum of sites.size_bytes)
ephemeral_sites_quota_reject_total    counter
```

Dropped from v1 scope (deferred — master spec §5.8 lists them, not needed for step 14 acceptance):
- `ephemeral_sites_http_requests_total` / `_duration_seconds` — needs request middleware; add in v1.1.
- `ephemeral_sites_rate_limit_hit_total` — step 14 doesn't add the rate limiter.

### 2.3 Wiring

- `put_site`: on create → `created_total.inc(api_key_name)` + refresh gauges. On replace → `replaced_total.inc`. On `QuotaExceeded` (via exception handler) → `quota_reject_total.inc`.
- `delete_site_route`: `deleted_total.labels(reason).inc()`.
- `cleanup.runner.run_cleanup`: `expired_total.inc(len(expired_slugs))`.
- Gauges (`total`, `storage_bytes`) are refreshed on-demand inside `GET /metrics` via a single DB query — cheap for ≤ 100 sites.

### 2.4 Endpoints

- `GET /healthz` → 200 + plain text "ok".
- `GET /readyz` → 200 if `SELECT 1` on DB works AND `sites_root` is writable (touch+unlink). 503 otherwise with JSON reason.
- `GET /metrics` → 200 + `text/plain; version=0.0.4` + prometheus exposition.

---

## 3. Acceptance Criteria

1. `GET /healthz` always returns 200 with body `"ok"`.
2. `GET /readyz` returns 200 when DB is open and sites_root writable.
3. `GET /readyz` returns 503 with `error="not_ready"` when sites_root doesn't exist.
4. `GET /metrics` returns 200 with `Content-Type: text/plain; version=0.0.4` and includes every metric name from §2.2.
5. After one PUT (create) the body contains `ephemeral_sites_created_total{api_key_name="main"} 1.0`.
6. After a second PUT on same slug, `ephemeral_sites_replaced_total` bumps.
7. After a DELETE with bearer, `ephemeral_sites_deleted_total{reason="manual"}` bumps.
8. After a quota 507, `ephemeral_sites_quota_reject_total` bumps.
9. `ephemeral_sites_total` gauge reflects the count of rows in `sites`.
10. `ephemeral_sites_storage_bytes` gauge reflects `SUM(size_bytes)`.
11. After cleanup reaps 2 sites, `ephemeral_sites_expired_total` increased by 2.

---

## 4. Test List

- [ ] `tests/integration/test_probes.py::test_healthz_returns_200_ok`
- [ ] `tests/integration/test_probes.py::test_readyz_returns_200_when_ready`
- [ ] `tests/integration/test_probes.py::test_readyz_returns_503_when_sites_root_missing`
- [ ] `tests/integration/test_probes.py::test_metrics_exposition_format_and_names`
- [ ] `tests/integration/test_probes.py::test_metrics_created_counter_bumps`
- [ ] `tests/integration/test_probes.py::test_metrics_replaced_counter_bumps`
- [ ] `tests/integration/test_probes.py::test_metrics_deleted_counter_bumps`
- [ ] `tests/integration/test_probes.py::test_metrics_quota_reject_counter_bumps`
- [ ] `tests/integration/test_probes.py::test_metrics_gauges_reflect_db_state`
- [ ] `tests/integration/test_probes.py::test_metrics_expired_counter_bumps_after_cleanup`

---

## 5. Done When

- [ ] 10 tests green.
- [ ] `make check` clean.
- [ ] CLAUDE.md + this file → ✅.
