#!/usr/bin/env bash
# ephemeral-sites: delete a site.
# Usage:
#   EPHEMERAL_API=... EPHEMERAL_TOKEN=... ./delete.sh <slug>
# OR
#   EPHEMERAL_API=... EPHEMERAL_DELETE_TOKEN=dt_xxx ./delete.sh <slug>

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <slug>" >&2
    exit 2
fi

: "${EPHEMERAL_API:?set EPHEMERAL_API}"

slug="$1"
args=(
    --fail-with-body
    --show-error
    --silent
    -X DELETE
)

if [[ -n "${EPHEMERAL_TOKEN:-}" ]]; then
    args+=(-H "Authorization: Bearer ${EPHEMERAL_TOKEN}")
elif [[ -n "${EPHEMERAL_DELETE_TOKEN:-}" ]]; then
    args+=(-H "X-Delete-Token: ${EPHEMERAL_DELETE_TOKEN}")
else
    echo "set EPHEMERAL_TOKEN (bearer) or EPHEMERAL_DELETE_TOKEN" >&2
    exit 2
fi

curl "${args[@]}" "${EPHEMERAL_API}/api/v1/sites/${slug}"
