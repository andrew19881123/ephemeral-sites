# Installation

Target: Kubernetes 1.26+ (tested on k3s). Requires Helm 3, cert-manager, and a
Cloudflare DNS zone for wildcard Let's Encrypt certificates (DNS-01 challenge).

## Prerequisites

- [ ] Kubernetes cluster (k3s/k3d/any 1.26+)
- [ ] Static public IP + DNS wildcard (`*.preview.<domain>` → cluster)
- [ ] `cert-manager` installed and a `ClusterIssuer` (`letsencrypt-prod`) ready
- [ ] Traefik (default on k3s) or any ingress controller with `IngressClassName` support
- [ ] Helm 3.x on the admin workstation

## Install (recommended — OCI registry)

Both the container image and the Helm chart are published to
`ghcr.io/andrew19881123` as OCI artifacts by the `release.yml` workflow
on every `v*.*.*` tag. You do not need to clone this repository.

```bash
# 1. Namespace
kubectl create namespace ephemeral-sites

# 2. API-key Secret — list of "name:plainkey" entries
kubectl -n ephemeral-sites create secret generic ephemeral-sites-auth \
  --from-literal=API_KEYS="main:$(openssl rand -hex 32)"

# Save the plainkey in a password manager — it is the only copy.

# 3. Prepare your values-prod.yaml with domain/hosts (see docs/configuration.md).
#    Minimum fields to change:
#      domain, wildcardHost, apiHost, app.baseDomain, dns.zone
$EDITOR values-prod.yaml

# 4. Install directly from the OCI registry — no repo clone needed.
helm install ephemeral-sites \
  oci://ghcr.io/andrew19881123/charts/ephemeral-sites \
  --version 0.1.0 \
  -n ephemeral-sites \
  -f values-prod.yaml

# 5. Verify
kubectl -n ephemeral-sites get pods,certificate,ingress
```

Upgrades: `helm upgrade ephemeral-sites oci://.../charts/ephemeral-sites --version 0.2.0 ...`.

First deploy: wildcard certificate issuance via DNS-01 takes 2–5 minutes.

### Alternative: install from a local clone

If you prefer to pin on a specific commit or customise the chart before
installing, clone the repo and point `helm install` at the local chart:

```bash
git clone https://github.com/andrew19881123/ephemeral-sites.git
cd ephemeral-sites
cp charts/ephemeral-sites/values.yaml values-prod.yaml
$EDITOR values-prod.yaml
helm install ephemeral-sites ./charts/ephemeral-sites \
  -n ephemeral-sites -f values-prod.yaml
```

## Known deploy gotchas

Lessons learned from a real k3s deploy (Traefik + Let's Encrypt HTTP-01, no
cert-manager). None of these are chart bugs — they are non-obvious interactions
between Helm values, Traefik's Kubernetes provider, and the app's env contract.
Call them out before your first install.

### 1. Traefik validates `tls.secretName` existence even when `certresolver` is set

If you use `ingress.tls.mode: existing-secret` with the annotation
`traefik.ingress.kubernetes.io/router.tls.certresolver: letsencrypt`, Traefik
still requires the referenced Secret to exist. When it is missing, Traefik logs
`Error configuring TLS: secret ... does not exist` and skips the entire router
— the HTTPS request then falls through to whichever other router matches the
SNI, typically the wildcard, producing a misleading 404 from the static
backend.

Workaround: pre-create a throwaway self-signed TLS secret with the same name.
Traefik ignores the contents once the ACME cert is issued via `certresolver`.

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout /tmp/k.pem -out /tmp/c.pem \
  -subj "/CN=placeholder"
kubectl -n ephemeral-sites create secret tls <your-existingSecret-name> \
  --cert=/tmp/c.pem --key=/tmp/k.pem
```

### 2. Wildcard Ingress beats literal Ingress on Traefik's default priority

The chart creates two Ingresses sharing the same parent domain:
`api.preview.<domain>` (literal) and `*.preview.<domain>` (wildcard). Traefik
translates the wildcard into a `HostRegexp(...)` rule that is **longer** than
the literal `Host(...)` rule. Since its default router priority is
`len(rule)`, the wildcard wins and captures every request — including the
ones intended for the API — routing them to the static container which
responds 404 `{"error":"not_found"}`.

Workaround: force the API ingress priority explicitly via
`ingress.api.annotations`:

```yaml
ingress:
  api:
    annotations:
      traefik.ingress.kubernetes.io/router.tls.certresolver: letsencrypt
      traefik.ingress.kubernetes.io/router.priority: "1000"
```

Any value well above the wildcard's regexp length (~50 on a typical nip.io
domain) works. Debug with
`kubectl -n kube-system logs deploy/traefik | grep routerName`.

### 3. `API_KEYS` must be `name:secret` comma-separated

The env var is parsed by `auth.parse_api_keys_env` (master spec §5.1). A raw
token without the `name:` prefix raises `InvalidApiKeysEnv: API_KEYS entry is
missing ':' separator between name and secret` and every authenticated request
returns 500.

Correct:

```bash
kubectl -n ephemeral-sites create secret generic ephemeral-sites-auth \
  --from-literal=API_KEYS="main:$(openssl rand -hex 32)"
# Multiple keys allowed, comma-separated:
#   API_KEYS="main:<key>,ci:<key>"
```

The bearer token sent by clients is only the `<key>` part (not the `name:`
prefix).

## Upgrade

```bash
helm upgrade ephemeral-sites ./charts/ephemeral-sites \
  -n ephemeral-sites \
  -f values-prod.yaml
```

The Deployment uses `strategy: Recreate` (RWO PVC forbids parallel pods) — expect
a ~15s serving gap during upgrades.

## Uninstall

```bash
helm uninstall ephemeral-sites -n ephemeral-sites
# The PVC is retained by default — delete manually to free storage.
kubectl -n ephemeral-sites delete pvc ephemeral-sites-data
```
