# ephemeral-sites

> Self-hosted single-user service to publish **ephemeral static SPA sites** from a ZIP upload, deployed on Kubernetes (target: k3s).

![Status](https://img.shields.io/badge/status-v1.0--feature--complete-brightgreen)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Python](https://img.shields.io/badge/python-3.12+-blue)

## What is it?

`ephemeral-sites` is a self-hosted monouser service exposing a REST API to publish Single Page Applications (SPA) as short-lived public URLs. Send a ZIP archive, get back a shareable `https://<slug>.preview.your-domain.dev` URL that expires after a configurable TTL.

**Primary use cases:**

- Quick & dirty experiments shared on Twitter/forums (TTL 24-48h)
- Iterative prototypes re-deployed many times under the same slug
- Persistent demos for portfolio (TTL indefinite)
- A/B comparison of two versions side-by-side
- Landing pages for events that expire after the talk
- Deploys from CI (GitHub Actions → `PUT /api/v1/sites/{slug}`)

## Quickstart (for users)

```bash
# Build your SPA
npm run build
cd dist && zip -r ../dist.zip . && cd ..

# Deploy with a 30-day TTL
curl -X PUT "https://api.preview.your-domain.dev/api/v1/sites/my-demo" \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@dist.zip" \
  -F "ttl_seconds=2592000"

# → https://my-demo.preview.your-domain.dev
```

## Status

✅ **v1.0 feature-complete (17/18 roadmap steps done).**

All business logic, HTTP API, static server, cleanup, metrics, Helm chart, and
CLI helpers are implemented with ≥ 91% test coverage. The remaining step
(end-to-end testing on a real k3d cluster) is runtime infrastructure, not code.

See [`docs/SPEC.md`](docs/SPEC.md) for the master spec, [`docs/steps/`](docs/steps/)
for per-step mini-specs, and [`CLAUDE.md`](CLAUDE.md) for contributor rules.

## API overview

| Method | Path | Purpose |
|---|---|---|
| `PUT`    | `/api/v1/sites/{slug}` | Create or replace a site from a ZIP upload |
| `POST`   | `/api/v1/sites`        | Create with auto-generated slug |
| `GET`    | `/api/v1/sites/{slug}` | Fetch site metadata (hits, expires_at, ...) |
| `PATCH`  | `/api/v1/sites/{slug}` | Mutate metadata: ttl / password / labels / allow_indexing |
| `DELETE` | `/api/v1/sites/{slug}` | Remove a site (bearer OR one-shot `X-Delete-Token`) |
| `GET`    | `/api/v1/sites`        | Paginated list (filter by label, sort by created_at/slug/...) |
| `GET`    | `/healthz`             | Liveness probe |
| `GET`    | `/readyz`              | Readiness (DB reachable + sites_root writable) |
| `GET`    | `/metrics`             | Prometheus text format exporter |

Served content (separate wildcard host) adds two synthetic endpoints:

- `GET https://{slug}.<base_domain>/_ephemeral/info` → `{slug, expires_at, hits}`
- `GET https://{slug}.<base_domain>/config.json`     → the `runtime_config` blob

See [`docs/api-reference.md`](docs/api-reference.md) for request/response schemas.

## Tech stack

- **Language**: Python 3.12
- **Framework**: FastAPI + Uvicorn
- **Database**: SQLite (WAL mode)
- **Deploy target**: Kubernetes (k3s on GCP)
- **TLS**: cert-manager + Let's Encrypt wildcard (Cloudflare DNS-01)
- **Container base**: `python:3.12-slim` hardened (non-root, read-only rootfs)

## Development

The repo ships a `Makefile` that wraps lint + tests + coverage in a single fast command (same checks CI runs):

```bash
make install        # install dev deps (poetry if available, else pip)
make check          # lint + test + coverage (~1s on warm venv)
make test-fast      # quick test loop while iterating
make format         # auto-fix formatting and lint issues
```

Run `make check` before every push — CI is the safety net, not the dev loop.

Raw equivalents (if you don't have `make`):

```bash
ruff check . && ruff format --check .
pytest -v --cov --cov-report=term-missing
```

See [`CLAUDE.md`](CLAUDE.md) for the full contributor workflow.

## License

[Apache-2.0](LICENSE) © 2026 Andrea Veronesi
