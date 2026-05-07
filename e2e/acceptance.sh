#!/usr/bin/env bash
# Acceptance curls against an ephemeral-sites install.
# Requires: EPHEMERAL_API, EPHEMERAL_TOKEN, HOST_HEADER_API, HOST_HEADER_SITE.

set -euo pipefail

: "${EPHEMERAL_API:?}"
: "${EPHEMERAL_TOKEN:?}"
: "${HOST_HEADER_API:=}"
: "${HOST_HEADER_SITE:=}"

host_api=()
host_site=()
[[ -n "$HOST_HEADER_API" ]] && host_api=(-H "Host: $HOST_HEADER_API")
[[ -n "$HOST_HEADER_SITE" ]] && host_site=(-H "Host: $HOST_HEADER_SITE")

# Build a tiny SPA zip on the fly.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
(cd "$WORK" && echo "<!doctype html><title>e2e demo</title>" > index.html && zip -q spa.zip index.html)

echo "[1/5] PUT /api/v1/sites/demo"
resp="$(curl -sS -w '\n%{http_code}' "${host_api[@]}" \
    -X PUT "$EPHEMERAL_API/api/v1/sites/demo" \
    -H "Authorization: Bearer $EPHEMERAL_TOKEN" \
    -F "file=@$WORK/spa.zip")"
status="$(echo "$resp" | tail -1)"
body="$(echo "$resp" | head -n -1)"
[[ "$status" == "200" ]] || { echo "PUT failed: $status $body" >&2; exit 1; }
delete_token="$(echo "$body" | jq -r .delete_token)"
echo "  delete_token: ${delete_token:0:12}..."

echo "[2/5] GET /api/v1/sites/demo"
status="$(curl -sS -o /dev/null -w '%{http_code}' "${host_api[@]}" \
    "$EPHEMERAL_API/api/v1/sites/demo" \
    -H "Authorization: Bearer $EPHEMERAL_TOKEN")"
[[ "$status" == "200" ]] || { echo "GET failed: $status" >&2; exit 1; }

echo "[3/5] GET served content at subdomain"
status="$(curl -sS -o /tmp/body.html -w '%{http_code}' "${host_site[@]}" "$EPHEMERAL_API/")"
[[ "$status" == "200" ]] || { echo "served GET failed: $status" >&2; exit 1; }
grep -q "e2e demo" /tmp/body.html || { echo "served body mismatch" >&2; cat /tmp/body.html >&2; exit 1; }

echo "[4/5] /healthz + /readyz + /metrics"
for p in /healthz /readyz /metrics; do
    status="$(curl -sS -o /dev/null -w '%{http_code}' "${host_api[@]}" "$EPHEMERAL_API$p")"
    [[ "$status" == "200" ]] || { echo "$p failed: $status" >&2; exit 1; }
done

echo "[5/5] DELETE /api/v1/sites/demo via delete_token"
status="$(curl -sS -o /dev/null -w '%{http_code}' "${host_api[@]}" \
    -X DELETE "$EPHEMERAL_API/api/v1/sites/demo" \
    -H "X-Delete-Token: $delete_token")"
[[ "$status" == "204" ]] || { echo "DELETE failed: $status" >&2; exit 1; }

echo "OK"
