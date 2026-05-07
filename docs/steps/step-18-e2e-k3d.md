# Step 18 — End-to-end on k3d

**Master spec sections**: [§11.1 piramide test](../SPEC.md), [§12.2 procedura install](../SPEC.md)
**Roadmap entry**: [§16.1 step 18](../SPEC.md)
**Status**: ✅ Complete (2026-05-07) — scaffolding + runbook committed; operator runs against real cluster.

---

## 1. Goal

Validate the full deployment path on a local k3d cluster before any production
install: build the image, install cert-manager + a staging issuer, deploy the
chart, exercise the HTTP API with `curl`, verify the served content comes back
through Traefik, and check `/readyz` + `/metrics`.

E2E tests are **not** part of the pytest suite (master spec §11.1 piramide:
≤ 5% of tests, manual / CI-only). They live as a reproducible shell script
under `e2e/` with a manual runbook.

---

## 2. Deliverables

- `e2e/run.sh` — end-to-end script: create cluster, install cert-manager,
  build + import image, `helm install`, run acceptance curls, teardown.
- `e2e/README.md` — runbook explaining prerequisites (k3d, docker, helm, kubectl),
  environment variables, expected output.
- `e2e/acceptance.sh` — the curl-based acceptance suite (PUT / GET / DELETE
  lifecycle on the served subdomain) — callable from `run.sh` or standalone
  against a production install.

## 3. Why not inside pytest

- Spinning up k3d inside pytest would bloat the unit/integration suite from
  <15s to >2min per run.
- The infrastructure under test (cert-manager, Traefik, Cloudflare DNS-01)
  is not reproducible without a real DNS zone or extensive fakes.
- Running `e2e/run.sh` in CI is a separate job gated by a manual trigger.

## 4. Acceptance checklist (manual on each release)

- [ ] `e2e/run.sh` succeeds end-to-end (creates cluster, deploys, runs
      `acceptance.sh`, teardown).
- [ ] `acceptance.sh` against a production install passes (PUT returns 200,
      GET returns SPA content, DELETE returns 204).
- [ ] `/healthz` and `/readyz` both 200 under `kubectl port-forward`.
- [ ] `/metrics` exposes all master-spec counters.

## 5. Done when

- [x] `e2e/run.sh` + `e2e/acceptance.sh` + `e2e/README.md` committed.
- [x] Scripts pass `bash -n` syntax check.
- [x] Runbook cross-referenced from `README.md` and `docs/installation.md`.
