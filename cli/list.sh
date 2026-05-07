#!/usr/bin/env bash
# ephemeral-sites: list sites (optionally filter by label).
# Usage:
#   EPHEMERAL_API=... EPHEMERAL_TOKEN=... ./list.sh [label]

set -euo pipefail

: "${EPHEMERAL_API:?set EPHEMERAL_API}"
: "${EPHEMERAL_TOKEN:?set EPHEMERAL_TOKEN}"

label="${1:-}"
query=""
[[ -n "$label" ]] && query="?label=${label}"

curl \
    --fail-with-body --show-error --silent \
    -H "Authorization: Bearer ${EPHEMERAL_TOKEN}" \
    "${EPHEMERAL_API}/api/v1/sites${query}"
echo
