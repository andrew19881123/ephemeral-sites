# ephemeral-sites

> Self-hosted single-user service to publish **ephemeral static SPA sites** from a ZIP upload, deployed on Kubernetes (target: k3s).

![Status](https://img.shields.io/badge/status-WIP-yellow)
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

🚧 **Work in progress — v1.0 under active development.**

See [`docs/SPEC.md`](docs/SPEC.md) for the full technical specification and roadmap, and [`CLAUDE.md`](CLAUDE.md) for contributor / AI-agent development rules (spec-driven + TDD).

Current milestone: **Step 1 — scaffolding** ✅

## Tech stack

- **Language**: Python 3.12
- **Framework**: FastAPI + Uvicorn
- **Database**: SQLite (WAL mode)
- **Deploy target**: Kubernetes (k3s on GCP)
- **TLS**: cert-manager + Let's Encrypt wildcard (Cloudflare DNS-01)
- **Container base**: `python:3.12-slim` hardened (non-root, read-only rootfs)

## Development

```bash
# Install deps
poetry install

# Run tests
poetry run pytest

# Lint & format
poetry run ruff check .
poetry run ruff format .
```

## License

[Apache-2.0](LICENSE) © 2026 Andrea Veronesi
