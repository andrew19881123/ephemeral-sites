# Step 16 — CLI bash helpers

**Master spec sections**: [§10 layout cli/](../SPEC.md)
**Status**: ✅ Complete (2026-05-07)

---

## 1. Goal

Shell wrappers around `curl` for the four common API operations: deploy, delete, list, extend. Pure bash + curl, no deps.

## 2. Deliverables

- `cli/deploy.sh` — PUT /api/v1/sites/{slug} with optional ttl + runtime_config.
- `cli/delete.sh` — DELETE /api/v1/sites/{slug}, supports bearer or X-Delete-Token.
- `cli/list.sh`   — GET /api/v1/sites (optional label filter).
- `cli/extend.sh` — PATCH /api/v1/sites/{slug} (ttl_seconds).
- `cli/README.md` — env vars + examples.

## 3. Acceptance

- All four scripts pass `bash -n` syntax check.
- Each refuses to run without required env vars (`EPHEMERAL_API`, `EPHEMERAL_TOKEN`).
- Each uses `curl --fail-with-body` so the caller sees a non-zero exit on 4xx/5xx.
