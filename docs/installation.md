# Installation

Target: Kubernetes 1.26+ (tested on k3s). Requires Helm 3, cert-manager, and a
Cloudflare DNS zone for wildcard Let's Encrypt certificates (DNS-01 challenge).

## Prerequisites

- [ ] Kubernetes cluster (k3s/k3d/any 1.26+)
- [ ] Static public IP + DNS wildcard (`*.preview.<domain>` → cluster)
- [ ] `cert-manager` installed and a `ClusterIssuer` (`letsencrypt-prod`) ready
- [ ] Traefik (default on k3s) or any ingress controller with `IngressClassName` support
- [ ] Helm 3.x on the admin workstation

## Install

```bash
# 1. Namespace
kubectl create namespace ephemeral-sites

# 2. API-key Secret — list of "name:plainkey" entries
kubectl -n ephemeral-sites create secret generic ephemeral-sites-auth \
  --from-literal=API_KEYS="main:$(openssl rand -hex 32)"

# Save the plainkey in a password manager — it is the only copy.

# 3. Copy the values override and edit domain/hosts/image
cp charts/ephemeral-sites/values.yaml values-prod.yaml
$EDITOR values-prod.yaml   # domain, wildcardHost, apiHost, dns.zone, app.image.tag

# 4. Install
helm install ephemeral-sites ./charts/ephemeral-sites \
  -n ephemeral-sites \
  -f values-prod.yaml

# 5. Verify
kubectl -n ephemeral-sites get pods,certificate,ingress
```

First deploy: wildcard certificate issuance via DNS-01 takes 2–5 minutes.

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
