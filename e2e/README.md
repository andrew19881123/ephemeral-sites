# End-to-end tests on k3d

These scripts validate the full deploy path: chart install, ingress routing,
acceptance curls. They are intentionally OUTSIDE the pytest suite (too slow,
require real infra) — run them manually before a release or on a dedicated
CI job.

## Prerequisites

- `docker`, `k3d` >= 5, `kubectl`, `helm`, `curl`, `jq`, `zip`
- Ports 8080 / 8443 free on the host (k3d load balancer binding)

## Usage

```bash
# Full loop: create cluster → deploy → acceptance → teardown
./e2e/run.sh

# Or: deploy manually elsewhere and run acceptance against it
EPHEMERAL_API=https://api.preview.example.com \
EPHEMERAL_TOKEN=plainkey_xxx \
HOST_HEADER_API="" \
HOST_HEADER_SITE=demo.preview.example.com \
  ./e2e/acceptance.sh
```

## What `run.sh` does

1. Creates a throwaway k3d cluster (`es-e2e`).
2. Installs cert-manager (TLS disabled in the chart values for the e2e path).
3. `docker build` the image locally and `k3d image import` it.
4. `helm install` with e2e-scoped values (no real domain, pullPolicy=Never).
5. Waits for the Deployment rollout + `/readyz`.
6. Runs `acceptance.sh`: PUT → GET → served content → probes → DELETE.
7. Tears down the cluster (always, even on failure).

## What `acceptance.sh` does

1. `PUT /api/v1/sites/demo` with a tiny SPA zip → expects 200 + `delete_token`.
2. `GET /api/v1/sites/demo` (bearer) → 200.
3. `GET /` with `Host: demo.<base>` → 200 + body contains the SPA marker.
4. `/healthz`, `/readyz`, `/metrics` → 200.
5. `DELETE` via the `X-Delete-Token` path (no bearer) → 204.

Exits non-zero on any mismatch.

## Known limitations

- Full TLS issuance via Cloudflare DNS-01 is NOT exercised here — that needs
  a real domain + API token and so belongs to staging, not k3d.
- Wildcard host resolution inside the cluster relies on Traefik's `Host` header
  routing; the script explicitly sets `Host:` on every curl.
