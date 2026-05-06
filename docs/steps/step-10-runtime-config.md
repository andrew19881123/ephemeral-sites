# Step 10 — Runtime config injection

**Master spec sections**: [§5.2 field `runtime_config`](../SPEC.md), [§5.9 `/config.json`](../SPEC.md), [§6.1 `sites.runtime_config` column](../SPEC.md), [§11.3 test_runtime_config](../SPEC.md)
**Roadmap entry**: [§16.1 step 10](../SPEC.md)
**Status**: ✅ Complete (2026-05-06, commit `0a022d6`)
**Owner**: Andrea Veronesi

---

## 1. Goal

Persist and re-serve the per-site `runtime_config` JSON blob. When the operator PUTs a site with `runtime_config='{"api_url":"..."}'`, the server:

1. Stores the exact JSON string in `sites.runtime_config` (DB).
2. Writes it to `{sites_root}/{slug}/config.json` at extraction time (already handled by storage in step 5).
3. After a replace-without-runtime_config, the **previously stored** config is carried forward (per master spec §5.2 — the field is optional and, when absent, means "keep existing").

Step 11 (static server) will actually *serve* the file over HTTP; step 10 only guarantees it lands on disk and in the DB every time the site is deployed.

---

## 2. Public API / Contract

### 2.1 PUT runtime_config semantics

- Field absent (no form field `runtime_config`) → preserve existing DB value and write it to disk.
- Field present with valid JSON → overwrite DB + disk.
- Field present but malformed → 400 `malformed_field` (already covered by step 8).
- Field present with empty string → treat as absent (keep existing).

### 2.2 PATCH runtime_config

Master spec §5.6 does not list `runtime_config` in the PATCH body. Confirmed non-goal for step 10. (Replace = PUT; metadata-only = PATCH; this is a data field, belongs to PUT.)

### 2.3 Module-level change

- `src/ephemeral_sites/api/routes_sites.py::put_site` — detect "field absent / empty" and look up existing `runtime_config` from DB, pass forward.

### 2.4 Tests

- `tests/integration/test_runtime_config.py::test_config_json_served_from_param` — master spec §11.3. Validates that `config.json` lands on disk with the exact bytes after PUT.
- Plus: carry-forward on replace, DB round-trip, empty-string-as-absent.

---

## 3. Acceptance Criteria

1. PUT with `runtime_config='{"a":1}'` → `{slug}/config.json` contains `{"a": 1}` on disk.
2. DB `sites.runtime_config` equals the JSON string written.
3. Second PUT to the same slug **without** `runtime_config` preserves the previous value on disk and in DB.
4. Second PUT to the same slug with a **new** `runtime_config` overwrites.
5. PUT with `runtime_config=''` → treated as absent (test 3 carry-forward triggers).
6. PUT with `runtime_config='not valid json'` → 400 (already green since step 8).

---

## 4. Test List

- [ ] `tests/integration/test_runtime_config.py::test_config_json_served_from_param`
- [ ] `tests/integration/test_runtime_config.py::test_replace_without_runtime_config_preserves_previous`
- [ ] `tests/integration/test_runtime_config.py::test_replace_with_new_runtime_config_overwrites`
- [ ] `tests/integration/test_runtime_config.py::test_empty_string_is_treated_as_absent`
- [ ] `tests/integration/test_runtime_config.py::test_db_runtime_config_matches_disk`

---

## 5. Edge Cases & Out of Scope

- Non-object JSON (`runtime_config='[1,2,3]'`): accepted. Master spec doesn't constrain shape. Step 10 persists whatever validates as JSON.
- `runtime_config` larger than some threshold: deferred to v1.1.
- Serving over HTTP with `Cache-Control: no-cache` → step 11.

---

## 6. Open Questions

(None.)

---

## 7. Done When

- [ ] 5 tests green.
- [ ] Coverage ≥ 80% overall.
- [ ] `make check` clean.
- [ ] CLAUDE.md roadmap row 10 → ✅.
- [ ] This file's Status flipped to ✅.
