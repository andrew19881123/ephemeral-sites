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

## Using a deployed instance

You were given the URL of a running `ephemeral-sites` instance and a bearer
token. Here is everything you need to push a SPA and retrieve it — no clone,
no chart, no helm.

### Two hosts, one deploy

The chart exposes the service on **two** host patterns under the same base
domain `<BASE>` (e.g. `preview.34-40-79-88.nip.io`):

| Host pattern                    | Purpose                                     | Port usually on |
|---------------------------------|---------------------------------------------|-----------------|
| `api.<BASE>`                    | REST API — read/write site metadata, upload | HTTPS (443)     |
| `<slug>.<BASE>`                 | Served SPA content for the site `<slug>`    | HTTP or HTTPS   |

When someone tells you *"the deploy is at `*.preview.example.com`"*, treat it
as `<BASE> = preview.example.com`; the API host is `api.preview.example.com`
and a site you publish with slug `demo` is served at `demo.preview.example.com`.

The protocol (http vs https) depends on the operator's setup:
- `api.<BASE>` is **typically** HTTPS (operators usually terminate TLS there).
- `<slug>.<BASE>` may be HTTP or HTTPS depending on whether the operator has
  wildcard TLS. If in doubt, `curl -I` both schemes and follow what responds.

### Bearer token format

The value you were given (e.g. `GO6Pbmm8DspQ...`) **is** the token you put
after `Bearer`. You do NOT prepend `main:` or any name — that prefix belongs
only to the server-side `API_KEYS` secret format.

```http
Authorization: Bearer GO6Pbmm8DspQ...
```

Using the wrong format returns `401` for valid-looking tokens or `500`
(`InvalidApiKeysEnv`) if the server-side secret itself is malformed.

### End-to-end example

Replace `BASE` and `TOKEN` with the values you were given.

```bash
BASE="preview.34-40-79-88.nip.io"
TOKEN="<your bearer token — the plain secret, not 'name:secret'>"

# 1. Sanity check: the API is up
curl -fsS "https://api.$BASE/readyz"              # → "ok"

# 2. Package your SPA (anything with an index.html at the root works)
mkdir -p ./dist && echo '<h1>hello</h1>' > ./dist/index.html
(cd ./dist && zip -qr /tmp/site.zip .)
# Python equivalent if `zip` is not installed:
# python3 -c "import zipfile,os; z=zipfile.ZipFile('/tmp/site.zip','w'); \
#   [z.write(os.path.join(r,f), os.path.relpath(os.path.join(r,f),'./dist')) \
#    for r,_,fs in os.walk('./dist') for f in fs]; z.close()"

# 3. Publish under a slug of your choice (PUT is idempotent — re-run to overwrite)
SLUG="demo"
curl -fsS -X PUT "https://api.$BASE/api/v1/sites/$SLUG" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/tmp/site.zip" \
  -F "ttl_seconds=86400"
# → JSON: {"slug":"demo","url":"...","expires_at":"...","delete_token":"dt_..."}
# Save the delete_token — it lets you DELETE the site without the bearer.

# 4. Fetch the served SPA (the protocol may be http or https depending on setup)
curl -fsSI "https://$SLUG.$BASE/" || curl -fsSI "http://$SLUG.$BASE/"

# 5. Inspect metadata (hits counter, expiry)
curl -fsS "https://api.$BASE/api/v1/sites/$SLUG" -H "Authorization: Bearer $TOKEN"
```

### Notes for AI agents / automation

If you are an AI agent handed a repo URL + a running instance URL + a token
and asked to publish a site:

1. First call `GET https://api.<BASE>/readyz` — a 200 confirms the API host
   is reachable and the backend is healthy. If HTTPS fails, try HTTP.
2. The token you receive is the bearer directly. Never synthesize
   `"main:" + token`; that is the server-side storage format, not the wire
   format.
3. The upload is `PUT /api/v1/sites/{slug}` with `multipart/form-data`: a
   `file=@...zip` field plus optional `ttl_seconds` (default 86400). 201/200
   returns a JSON with the final URL and a `delete_token`.
4. To overwrite an existing slug, just PUT again — the endpoint is upsert.
5. Full request/response schemas: [`docs/api-reference.md`](docs/api-reference.md).

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
