# ephemeral-sites CLI helpers

Thin shell wrappers around `curl` for the HTTP API. Pure bash + curl, no extra deps.

## Environment variables

| Var | Required by | Meaning |
|---|---|---|
| `EPHEMERAL_API` | all | Base URL, e.g. `https://api.preview.example.com` |
| `EPHEMERAL_TOKEN` | deploy, list, extend, delete (bearer path) | API key plaintext (`main:plainkey_xxxxx` format NOT needed — only the plainkey) |
| `EPHEMERAL_DELETE_TOKEN` | delete (token path, alternative to bearer) | The `dt_...` string returned by the create response |

## Scripts

| Script | Purpose | Usage |
|---|---|---|
| `deploy.sh` | PUT upsert | `deploy.sh <slug> <zip> [ttl] [runtime_config_json]` |
| `delete.sh` | DELETE | `delete.sh <slug>` (uses bearer or X-Delete-Token) |
| `list.sh`   | GET list | `list.sh [label]` |
| `extend.sh` | PATCH ttl | `extend.sh <slug> <ttl_seconds>` |

## Examples

```bash
export EPHEMERAL_API=https://api.preview.example.com
export EPHEMERAL_TOKEN=plainkey_from_secret

# Deploy a site for 1 hour
./deploy.sh demo ./dist.zip 3600

# Deploy permanent
./deploy.sh docs ./build.zip -1

# List all sites tagged 'experiment'
./list.sh experiment

# Extend demo by 24 hours
./extend.sh demo 86400

# Delete via bearer
./delete.sh demo

# Delete via one-shot token (no bearer needed)
unset EPHEMERAL_TOKEN
export EPHEMERAL_DELETE_TOKEN=dt_abc123...
./delete.sh demo
```

## Exit codes

- 0: success.
- 2: usage error (missing args / missing env).
- other non-zero: curl failure (HTTP >= 400); the response body is printed by `--fail-with-body`.
