# Troubleshooting

## Pod not ready

```bash
kubectl -n ephemeral-sites describe pod <pod>
kubectl -n ephemeral-sites logs <pod> -c api
kubectl -n ephemeral-sites exec <pod> -c api -- curl -s localhost:8000/readyz
```

Common causes:

- `readyz` returns 503 `sites_root not writable` → PVC not mounted or wrong
  fsGroup. Check `securityContext.fsGroup=10001` and the PVC's `accessMode`.
- Postgres-style errors on first boot → SQLite migrations run automatically,
  so this usually means a corrupt DB file. Back up `/data/db/*` and delete to
  reset.
- `readyz` 503 with `db: ...` → inspect `kubectl logs` for the full exception.

## Certificate stuck `Ready=False`

```bash
kubectl -n ephemeral-sites describe certificate
kubectl -n cert-manager logs deploy/cert-manager | grep <your-domain>
```

- DNS-01 failing → verify the Cloudflare API token on the `ClusterIssuer` has
  Zone:DNS:Edit and the right zone.
- Rate-limited by Let's Encrypt → switch `ingress.tls.certManager.clusterIssuer`
  to `letsencrypt-staging` while debugging.

## 507 on upload, quota not actually full

`ephemeral_sites_storage_bytes` (gauge) is the source of truth. Scrape it:

```bash
kubectl -n ephemeral-sites port-forward svc/ephemeral-sites-api 8000
curl -s localhost:8000/metrics | grep ephemeral_sites_storage_bytes
```

Compare with `limits.maxTotalStorageBytes`. If the gauge is higher than the
actual `/data/sites` disk usage, you may have orphan DB rows (e.g. from a
crash during a swap) — run the cleanup job manually:

```bash
kubectl -n ephemeral-sites create job --from=cronjob/ephemeral-sites-cleanup cleanup-manual
```

## Served site 404 on subroutes

- Check `spa_mode` on the site: `curl https://api.<domain>/api/v1/sites/<slug>`.
- SPA mode serves `index.html` for unknown paths **only when the path is not
  asset-like** (see `server/spa.py`). `/dashboard/42` → SPA. `/static/foo.js` → 404.

## 401 on served content after setting a password

`Authorization: Basic base64(any:password)`. The username is ignored; only
the password is verified against the bcrypt hash. If you forgot the password,
PATCH with `{"password": null}` then re-set it.

## Rolling back a bad deploy

Keep the `delete_token` from the PUT response — even without a bearer key you
can remove a bad build:

```bash
curl -X DELETE https://api.<domain>/api/v1/sites/demo \
  -H "X-Delete-Token: dt_abc123..."
```

## Corrupt DB

```bash
kubectl -n ephemeral-sites exec -it deploy/ephemeral-sites -c api -- \
  sqlite3 /data/db/ephemeral-sites.db "PRAGMA integrity_check;"
```

If it reports issues, restore from `/data/db/*.backup-v{N}` (created before
each migration).
