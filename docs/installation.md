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
