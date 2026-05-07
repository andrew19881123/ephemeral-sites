#!/usr/bin/env bash
# Full end-to-end test on a local k3d cluster.
# Prerequisites: docker, k3d, helm, kubectl, curl, jq.
#
# Runs:
#   1. Create disposable k3d cluster (deleted at end / on error)
#   2. Install cert-manager (staging ClusterIssuer, HTTP-01 fallback)
#   3. Build the app image locally and import into k3d
#   4. helm install the chart with a staging values file
#   5. Wait for the Deployment to be ready + /readyz OK
#   6. Run ./acceptance.sh against the cluster
#   7. Teardown

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-es-e2e}"
IMAGE="${IMAGE:-ephemeral-sites:e2e}"
CHART_DIR="$(cd "$(dirname "$0")/.." && pwd)/charts/ephemeral-sites"
NS="${NS:-ephemeral-sites}"

cleanup() {
    echo ">>> tearing down cluster $CLUSTER_NAME"
    k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo ">>> creating k3d cluster $CLUSTER_NAME"
k3d cluster create "$CLUSTER_NAME" \
    --agents 1 \
    --port "8080:80@loadbalancer" \
    --port "8443:443@loadbalancer" \
    --wait

echo ">>> installing cert-manager"
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.1/cert-manager.yaml
kubectl -n cert-manager rollout status deploy/cert-manager --timeout=180s
kubectl -n cert-manager rollout status deploy/cert-manager-webhook --timeout=180s

echo ">>> building image $IMAGE"
cd "$(dirname "$0")/.."
docker build -t "$IMAGE" .
k3d image import "$IMAGE" -c "$CLUSTER_NAME"

echo ">>> creating namespace + auth Secret"
kubectl create ns "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create secret generic ephemeral-sites-auth \
    --from-literal=API_KEYS="main:e2e-test-key" \
    --dry-run=client -o yaml | kubectl apply -f -

echo ">>> helm install"
helm upgrade --install ephemeral-sites "$CHART_DIR" \
    -n "$NS" \
    --set app.image.repository="ephemeral-sites" \
    --set app.image.tag="e2e" \
    --set app.image.pullPolicy=Never \
    --set ingress.tls.enabled=false \
    --set domain=e2e.local \
    --set wildcardHost="*.e2e.local" \
    --set apiHost=api.e2e.local \
    --set app.baseDomain=e2e.local

echo ">>> waiting for Deployment ready"
kubectl -n "$NS" rollout status deploy/ephemeral-sites --timeout=180s

echo ">>> acceptance"
export EPHEMERAL_API="http://localhost:8080"
export EPHEMERAL_TOKEN="e2e-test-key"
export HOST_HEADER_API="api.e2e.local"
export HOST_HEADER_SITE="demo.e2e.local"
bash "$(dirname "$0")/acceptance.sh"

echo ">>> all acceptance checks passed"
