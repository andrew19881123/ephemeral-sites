# Step 12 — Password protection (served sites)

**Master spec sections**: [§5.2 password field](../SPEC.md), [§7.2 serving password-protected sites](../SPEC.md), [§11.3 test_server_password](../SPEC.md)
**Roadmap entry**: [§16.1 step 12](../SPEC.md)
**Status**: ⏳ Draft

---

## 1. Goal

Replace the step-11 stub (always 401 for password-protected sites) with real HTTP Basic authentication against `sites.password_hash`. No change to the API layer (PUT/PATCH already hash-and-store the password since step 6/step 8).

---

## 2. Contract

Flow:

1. Resolve slug from Host (step 11).
2. Look up site.
3. If `password_hash IS NULL` → serve normally.
4. Else parse `Authorization: Basic base64(username:password)`:
   - Header missing → 401 + `WWW-Authenticate: Basic realm="..."`.
   - Header malformed → 401.
   - Username is ignored (master spec doesn't mandate one; convention: any value).
   - Password verified via `auth.verify_secret(plaintext, password_hash)`.
     - Mismatch → 401.
     - Match → continue serving (file / SPA / synthetic endpoints).

Constant-time: bcrypt's checkpw is already constant-time per comparison; we do exactly one bcrypt call per request (no user enumeration, no branching on "is there a hash").

---

## 3. Acceptance Criteria

1. `GET /` on protected site with no Authorization → 401 with `WWW-Authenticate`.
2. `GET /` on protected site with correct Basic creds → 200 + body = index.html.
3. `GET /` on protected site with wrong password → 401.
4. `GET /` on protected site with malformed Basic header (`Basic notbase64`) → 401.
5. `GET /` on protected site with non-Basic scheme (`Bearer xxx`) → 401.
6. `GET /_ephemeral/info` on protected site still requires auth.
7. `GET /config.json` on protected site still requires auth.
8. Unprotected site with spurious `Authorization: Basic ...` → 200 (header ignored).

---

## 4. Test List

- [ ] `tests/integration/test_server_password.py::test_password_protected_requires_auth` (spec §11.3)
- [ ] `tests/integration/test_server_password.py::test_correct_password_serves_content`
- [ ] `tests/integration/test_server_password.py::test_wrong_password_returns_401`
- [ ] `tests/integration/test_server_password.py::test_malformed_basic_header_returns_401`
- [ ] `tests/integration/test_server_password.py::test_non_basic_scheme_returns_401`
- [ ] `tests/integration/test_server_password.py::test_protected_site_gates_ephemeral_info`
- [ ] `tests/integration/test_server_password.py::test_protected_site_gates_config_json`
- [ ] `tests/integration/test_server_password.py::test_unprotected_site_ignores_auth_header`

---

## 5. Edge Cases

- Username in the Basic header is ignored (convention in v1; spec doesn't constrain).
- Password with colons: split on the FIRST `:` so passwords can contain them.

---

## 6. Open Questions

(None.)

---

## 7. Done When

- [ ] 8 tests green; previous 239 still green.
- [ ] `make check` clean.
- [ ] CLAUDE.md updated.
