#!/usr/bin/env bash
# ephemeral-sites: extend a site's TTL via PATCH.
# Usage:
#   EPHEMERAL_API=... EPHEMERAL_TOKEN=... ./extend.sh <slug> <ttl_seconds>

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <slug> <ttl_seconds>" >&2
    exit 2
fi

: "${EPHEMERAL_API:?set EPHEMERAL_API}"
: "${EPHEMERAL_TOKEN:?set EPHEMERAL_TOKEN}"

slug="$1"
ttl="$2"

curl \
    --fail-with-body --show-error --silent \
    -X PATCH \
    -H "Authorization: Bearer ${EPHEMERAL_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"ttl_seconds\": ${ttl}}" \
    "${EPHEMERAL_API}/api/v1/sites/${slug}"
echo
