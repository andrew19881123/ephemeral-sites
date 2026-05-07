# Security notes

## Threat model (master spec §2)

- **Primary attacker**: anyone with a leaked API key. The quota, rate limit, and
  zip-bomb guards contain the blast radius.
- **Not in scope**: multi-tenancy (single-user v1), account takeover (no user
  concept), supply-chain against the uploaded SPA (you ship your own JS).

## Controls in place

### Upload pipeline

- ZIP validator rejects (never sanitizes) on path traversal, symlinks, absolute
  paths, Windows drive letters, ratio > 100, total > `maxZipSize * 10`, single
  file > `maxZipSize * 2`, extension not in whitelist, and missing `index.html`.
  Every rejection → HTTP 400 with a stable `reason_code`.
- Defense in depth: storage layer re-checks each resolved path stays under
  `sites_root` before writing (mitigates a validator bypass).
- Atomic swap via `flock` + `renameat2(RENAME_EXCHANGE)` on Linux — no 404
  window during upgrades (verified by `test_put_same_slug_no_404_during_swap`).

### Authentication

- bcrypt cost 12 for API-key hashes, delete tokens, and passwords.
- Timing-safe comparison via `bcrypt.checkpw`; `authenticate()` scans ALL keys
  (no early exit) so wall-time is independent of which key matches.
- Disabled keys → 403 (distinct from 401 unknown).

### Log hygiene (master spec §7.6)

- Error `detail` strings never contain file paths, stack traces, or user-
  controlled tokens. They carry a `reason_code` plus a human string.
- `request_id` (uuid4 hex) is echoed on every response as `X-Request-ID` and
  in the JSON body — the correlation key for debugging without leaking secrets.
- `log.exception` is used inside handlers; the exception itself is logged but
  never surfaced in the response body.

### Container hardening (master spec §7.3)

All implemented in the Helm chart:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  fsGroup: 10001
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: [ALL] }
  seccompProfile: { type: RuntimeDefault }
```

The `/tmp` volume is an `emptyDir` (writable). Data lives on the RWO PVC at
`/data`.

### Served-content headers

Every 200 response from the static server carries:

```
X-Content-Type-Options: nosniff
X-Frame-Options: SAMEORIGIN
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; ...
X-Robots-Tag: noindex, nofollow, noarchive      # unless allow_indexing=true
```

Password-protected sites gate every request (including the synthetic
`/_ephemeral/info` and `/config.json`) via HTTP Basic + bcrypt.

## Secrets handling

- `API_KEYS` lives in a Kubernetes Secret (`auth.existingSecret`), mounted as
  env. The plaintext never touches a ConfigMap or application log.
- Delete tokens are shown **exactly once** in the create/replace response body,
  never retrievable again — only their bcrypt hash is stored.
- Local developer tokens belong in the gitignored `.secret/` directory; never
  in tracked files (see `CLAUDE.md §6bis`).

## Known limitations

- **No rate limiting in v1** — master spec §16.1 explicitly defers. Use
  Traefik / upstream rate limits if needed.
- **No WAF** — if the SPA you publish has a JS-level XSS, your clients see it.
  CSP helps; you own the payload.
- **Bearer tokens don't rotate automatically** — operator rotates via the
  `API_KEYS` Secret + `kubectl rollout restart deploy/ephemeral-sites`.

## Reporting vulnerabilities

Open a private GitHub security advisory on
`https://github.com/andrew19881123/ephemeral-sites/security/advisories/new`.
