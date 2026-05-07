# Step 15 — Helm chart

**Master spec sections**: [§8 Ingress/DNS/cert-manager](../SPEC.md), [§9 values.yaml](../SPEC.md), [§10 layout chart](../SPEC.md), [§12 install/upgrade](../SPEC.md)
**Roadmap entry**: [§16.1 step 15](../SPEC.md)
**Status**: ⏳ Draft

---

## 1. Goal

Ship a Helm chart at `charts/ephemeral-sites/` that deploys the API (1 replica Deployment with API + static server containers), the PVC, the Service, two Ingresses (API + wildcard), the cert-manager Certificate, and the cleanup CronJob. Values follow master spec §9 verbatim.

Tests are chart-level: `helm lint` + `helm template` produce no warnings.

---

## 2. Scope

### 2.1 Included

- `Chart.yaml` (apiVersion v2, appVersion, description)
- `values.yaml` (master spec §9 literal)
- `values-production.example.yaml` (operator override template)
- Templates:
  - `_helpers.tpl` — name / labels / selectors
  - `deployment.yaml` — 1 replica, 2 containers (api, static) in same pod, sharing PVC
  - `service.yaml` — 2 services (api: port 8000, static: port 8001)
  - `ingress-api.yaml` — host=apiHost
  - `ingress-wildcard.yaml` — host=wildcardHost
  - `certificate.yaml` — cert-manager wildcard DNS-01
  - `pvc.yaml`
  - `configmap.yaml` — app limits / allowed_extensions as env
  - `cronjob-cleanup.yaml` — invokes `python -m ephemeral_sites.cleanup`
  - `servicemonitor.yaml` — optional, behind `metrics.serviceMonitor.enabled`

### 2.2 Deferred (v1.1 per spec §13 OP-7)

- Horizontal Pod Autoscaler.
- NetworkPolicy.
- Pod Disruption Budget (single replica — PDB moot).

---

## 3. Acceptance

1. `helm lint charts/ephemeral-sites` → 0 errors, 0 warnings.
2. `helm template charts/ephemeral-sites -f values.yaml` → valid YAML, all placeholders interpolated.
3. `helm template` output includes: Deployment, Service (api + static), 2 Ingresses, Certificate, PVC, ConfigMap, CronJob.
4. `securityContext` on the Deployment matches spec §7.3 (runAsNonRoot, runAsUser=10001, allowPrivilegeEscalation=false, readOnlyRootFilesystem, capabilities drop all).
5. CronJob schedule defaults to `*/5 * * * *`.
6. `resources.limits/requests` present on each container (app + cleanup).
7. `existingSecret: ephemeral-sites-auth` is mounted as env `EPHEMERAL_API_KEYS`.

---

## 4. Test List

Chart-only tests (shell-level, not pytest): `helm lint` and `helm template | yq`. For CI, add a `charts/Makefile` target or a simple script. We integrate them via a new `make helm-check` target in the top-level Makefile.

Marker on the integration side (tests/integration/test_helm_chart.py, `@pytest.mark.helm`, skipped if `helm` CLI absent):

- [ ] `test_helm_lint_passes` — shells out to `helm lint charts/ephemeral-sites`.
- [ ] `test_helm_template_renders_all_kinds` — shells out to `helm template` and asserts presence of expected `kind:` lines.
- [ ] `test_helm_template_honours_securitycontext` — greps for `runAsNonRoot: true`.

---

## 5. Done When

- [ ] Chart directory populated, `helm lint` clean.
- [ ] `helm template` emits all expected kinds.
- [ ] `make helm-check` target added.
- [ ] CLAUDE.md + this file → ✅.
