#!/usr/bin/env bash
# ephemeral-sites: deploy (PUT) a site.
# Usage:
#   EPHEMERAL_API=https://api.preview.example.com \
#   EPHEMERAL_TOKEN=main:plainkey_xxxxx \
#     ./deploy.sh <slug> <zipfile> [ttl_seconds] [runtime_config_json]
#
# Exits non-zero on HTTP >= 400. Prints the JSON response on stdout.

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <slug> <zipfile> [ttl_seconds] [runtime_config_json]" >&2
    exit 2
fi

: "${EPHEMERAL_API:?set EPHEMERAL_API (e.g. https://api.preview.example.com)}"
: "${EPHEMERAL_TOKEN:?set EPHEMERAL_TOKEN (e.g. plainkey_xxxxx)}"

slug="$1"
zipfile="$2"
ttl="${3:-}"
runtime_config="${4:-}"

if [[ ! -f "$zipfile" ]]; then
    echo "zipfile not found: $zipfile" >&2
    exit 2
fi

curl_args=(
    --fail-with-body
    --show-error
    --silent
    -X PUT
    -H "Authorization: Bearer ${EPHEMERAL_TOKEN}"
    -F "file=@${zipfile}"
)
[[ -n "$ttl" ]] && curl_args+=(-F "ttl_seconds=${ttl}")
[[ -n "$runtime_config" ]] && curl_args+=(-F "runtime_config=${runtime_config}")

curl "${curl_args[@]}" "${EPHEMERAL_API}/api/v1/sites/${slug}"
echo
