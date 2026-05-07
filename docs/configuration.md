# Configuration

All runtime knobs live in Helm `values.yaml` and are propagated to the
container as `EPHEMERAL_*` environment variables. Full reference:
[docs/SPEC.md §9](SPEC.md).

## Most important knobs

| values.yaml | env | Default | Meaning |
|---|---|---|---|
| `limits.maxZipSize` | `EPHEMERAL_MAX_ZIP_SIZE` | 500 MiB | Max upload body size (413 beyond) |
| `limits.maxFilesPerSite` | `EPHEMERAL_MAX_FILES_PER_SITE` | 5000 | Max ZIP entries |
| `limits.maxTotalStorageBytes` | `EPHEMERAL_MAX_TOTAL_STORAGE_BYTES` | 40 GiB | Global quota (507 beyond) |
| `limits.defaultTtlSeconds` | `EPHEMERAL_DEFAULT_TTL_SECONDS` | 86400 (1d) | TTL when client omits the field |
| `limits.maxTtlSeconds` | `EPHEMERAL_MAX_TTL_SECONDS` | 31536000 (1y) | Upper bound |
| `limits.allowPermanent` | `EPHEMERAL_ALLOW_PERMANENT` | true | Accept `ttl_seconds=-1` |
| `limits.maxDecompressionRatio` | `EPHEMERAL_MAX_DECOMPRESSION_RATIO` | 100 | ZIP-bomb ratio guard |
| `app.baseDomain` | `EPHEMERAL_BASE_DOMAIN` | — | Wildcard domain for served sites |
| `auth.existingSecret` | `EPHEMERAL_API_KEYS` (via secretRef) | `ephemeral-sites-auth` | Secret holding the comma-list of keys |

## API-key format

```
API_KEYS="name1:plainkey1,name2:plainkey2"
```

- Names are free-form labels used ONLY in logs.
- All keys have the same power in v1 (no RBAC).
- Parsed and bcrypt-hashed at startup; plaintexts never retained.

## Per-site config (passed via API, not env)

| Field | Per-site default | Where |
|---|---|---|
| `ttl_seconds` | `limits.defaultTtlSeconds` | PUT/POST body |
| `spa_mode` | true | PUT/POST body |
| `allow_indexing` | false | PUT/POST body |
| `password` | null | PUT/POST body |
| `runtime_config` | null | PUT/POST body — written as `/config.json` |
| `labels` | null | PUT/POST body |

## Resource limits

Defaults in `values.yaml` assume a small cluster:

```yaml
app:
  resources:
    requests: { cpu: 100m, memory: 128Mi }
    limits:   { cpu: 1000m, memory: 512Mi }
cleanup:
  resources:
    requests: { cpu: 20m, memory: 32Mi }
    limits:   { cpu: 200m, memory: 128Mi }
```

Bump `app.resources` if you see OOMKills on large uploads (validator holds the
ZIP fully streamed to tmpfs).

## Logging

```yaml
logging:
  level: INFO
  format: json
```

JSON lines to stdout — harvest with `kubectl logs` or a sidecar.
