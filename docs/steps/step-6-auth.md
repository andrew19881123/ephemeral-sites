# Step 6 — Auth (bcrypt + API keys + delete tokens)

**Master spec sections**: [§5.1 Autenticazione](../SPEC.md), [§5.5 DELETE auth alternatives](../SPEC.md), [§6.1 api_keys table](../SPEC.md), [§7.5 rotation](../SPEC.md), [§7.6 secrets handling](../SPEC.md), [§11.3 test_auth](../SPEC.md)
**Roadmap entry**: [§16.1 step 6](../SPEC.md)
**Status**: ✅ Complete (2026-05-06, commit `ffe4c6c`)
**Owner**: Andrea Veronesi

---

## 1. Goal

Deliver the security primitives that protect every write endpoint:

1. **bcrypt hashing** (cost=12 in prod, injectable in tests) for API keys, delete tokens, and passwords — single helper so no module rolls its own hashing.
2. **API-key authentication**: parse the `API_KEYS` secret (`"name:plainkey,name:plainkey,..."`) at startup into a tuple of `ApiKey` records with hashed material; parse `Authorization: Bearer <token>` headers; verify submitted tokens against the stored hashes with timing-safe comparison; distinguish "unknown key" (401) from "known-but-disabled key" (403).
3. **Delete tokens**: generate cryptographically random, URL-safe tokens with a `dt_` prefix (prefix is master spec §5.2 convention — makes leaked tokens recognizable in logs/search); hash for storage; expose a verify helper.

No HTTP routing in this step. The API layer (step 8+) will import this module and wire it into FastAPI dependencies.

---

## 2. Public API / Contract

### 2.1 Module layout

- `src/ephemeral_sites/auth.py` — all public helpers + error taxonomy.
- `tests/unit/test_auth.py` — tests.

Runtime dependency: `bcrypt>=4.2` (already in `pyproject.toml` since step 1).

### 2.2 Exception taxonomy

```python
class AuthError(Exception):
    """Base. Subclasses map to specific HTTP status codes."""


class InvalidAuthHeader(AuthError):
    """The Authorization header is missing, malformed, or uses a non-Bearer scheme.
    Maps to HTTP 401.
    """


class InvalidApiKey(AuthError):
    """The bearer value does not match any known key hash.
    Maps to HTTP 401.
    """


class DisabledApiKey(AuthError):
    """The bearer value matches a key whose ``disabled`` flag is True.
    Maps to HTTP 403.
    """


class InvalidApiKeysEnv(ValueError):
    """The API_KEYS env variable is malformed (empty, duplicate names,
    missing colon, etc.). Raised at startup; fail-fast on the pod.
    """
```

`AuthError` is a plain `Exception`; `InvalidApiKeysEnv` is a `ValueError` because it's a configuration error (wrong input at startup), not an auth failure.

### 2.3 Data types

```python
@dataclass(frozen=True)
class ApiKey:
    """An API key as stored in memory after parsing the secret.

    Attributes:
        name: Free-form label used only for logs ("main", "ci", ...).
            No authorization meaning in v1 (master spec §5.1).
        hashed: bcrypt hash of the plaintext. Never contains the plaintext.
        disabled: If True, verify() refuses even on hash match.
    """
    name: str
    hashed: bytes
    disabled: bool = False
```

Delete tokens are **not** a separate dataclass — they are passed around as `str` (plaintext when returning to the client, once only) or `bytes` (the bcrypt hash, stored in `sites.delete_token_hash`).

### 2.4 Hashing helpers

```python
DEFAULT_BCRYPT_ROUNDS: int = 12  # master spec §7.6

def hash_secret(plaintext: str, *, rounds: int = DEFAULT_BCRYPT_ROUNDS) -> bytes:
    """Return a bcrypt hash of ``plaintext``. Uses a fresh random salt.

    ``rounds`` is the cost parameter. Tests pass rounds=4 to avoid the
    ~250 ms-per-hash cost of the default; production uses 12.
    """


def verify_secret(plaintext: str, hashed: bytes) -> bool:
    """Constant-time check via :func:`bcrypt.checkpw`.

    Returns False (never raises) if ``hashed`` is not a valid bcrypt
    string or if the plaintext does not match. An invalid hash blob
    is treated as "no match" to keep the caller's branching simple.
    """
```

### 2.5 API-key parsing

```python
def parse_api_keys_env(
    env_value: str,
    *,
    rounds: int = DEFAULT_BCRYPT_ROUNDS,
) -> tuple[ApiKey, ...]:
    """Parse the comma-separated ``name:plainkey`` string from the
    ``API_KEYS`` Secret at startup.

    Format (master spec §5.1):
        "main:plainkey1,ci:plainkey2,readonly:plainkey3"

    Each plainkey is hashed immediately (cost=rounds) — the plaintext
    is NOT retained after this function returns.

    Whitespace around entries / separators is stripped. Empty input
    raises InvalidApiKeysEnv (no keys = no valid deployments; fail loud).

    Raises:
        InvalidApiKeysEnv: empty string, duplicate names, missing colon,
            empty name, empty plainkey.
    """
```

### 2.6 Authorization header parsing

```python
def parse_bearer_header(header_value: str | None) -> str:
    """Extract the token from ``Authorization: Bearer <token>``.

    Returns the token (non-empty) on success.

    Raises:
        InvalidAuthHeader: if ``header_value`` is None, empty, does not
            start with "Bearer " (case-insensitive), or has an empty
            token part.
    """
```

### 2.7 Authentication

```python
def authenticate(
    presented_plaintext: str,
    known_keys: Iterable[ApiKey],
) -> ApiKey:
    """Check ``presented_plaintext`` against every known key.

    The scan is exhaustive (no early exit on mismatch) so timing
    attackers cannot distinguish "wrong first entry" from "wrong last
    entry" by measuring latency. Because bcrypt.checkpw itself is
    constant-time per comparison, and we sum up the work across all
    N keys, the total wall-time is independent of which key matched.

    Returns:
        The matching :class:`ApiKey`.

    Raises:
        InvalidApiKey: no key's hash matches ``presented_plaintext``.
        DisabledApiKey: a key matched but is flagged disabled.
    """
```

### 2.8 Delete tokens

```python
DELETE_TOKEN_PREFIX: str = "dt_"  # master spec §5.2 convention

def generate_delete_token(
    *,
    rng: Callable[[int], str] = secrets.token_urlsafe,
    rounds: int = DEFAULT_BCRYPT_ROUNDS,
) -> tuple[str, bytes]:
    """Create a fresh delete token.

    Returns:
        (plaintext, hashed). Show ``plaintext`` to the client exactly
        once (in the PUT/POST response); store ``hashed`` in
        sites.delete_token_hash. Prefix ``dt_`` is applied before the
        entropy so the token is recognizable in logs.

    The plaintext body is ``DELETE_TOKEN_PREFIX + rng(nbytes=24)``,
    giving 24 random bytes base64url-encoded (~32 chars of entropy),
    for 192 bits of strength — overkill for an ephemeral service but
    free with the `secrets` module.
    """


def verify_delete_token(presented: str, hashed: bytes) -> bool:
    """True iff ``presented`` is the plaintext that produced ``hashed``.

    Thin wrapper around :func:`verify_secret`; exists for symmetry and
    so that callers don't have to know bcrypt is the underlying
    algorithm.
    """
```

---

## 3. Acceptance Criteria

### 3.1 Hashing

1. `hash_secret("x")` returns a bytes object starting with `$2b$` (bcrypt identifier).
2. Two calls to `hash_secret(same_plaintext)` produce different bytes (fresh salt each time).
3. `hash_secret("x", rounds=4)` uses cost 4 (verifiable by parsing the hash prefix `$2b$04$`).
4. `verify_secret(plaintext, hash_secret(plaintext))` is True.
5. `verify_secret("wrong", hash_secret("right"))` is False.
6. `verify_secret("x", b"not-a-bcrypt-hash")` is False (no raise).
7. `verify_secret(plaintext, hashed_with_default_rounds)` is True (cost round-trips).

### 3.2 API-key parsing

8. `parse_api_keys_env("main:x")` returns one `ApiKey` with `name="main"`, `disabled=False`.
9. Returned `ApiKey.hashed` is a bcrypt hash of `"x"`, **not** the string `"x"`.
10. `parse_api_keys_env("a:1,b:2,c:3")` returns three entries in order.
11. Whitespace is trimmed: `" main : secret "` is parsed as `name="main"`, plain=`"secret"`.
12. `parse_api_keys_env("")` raises `InvalidApiKeysEnv`.
13. `parse_api_keys_env("main:a,main:b")` raises `InvalidApiKeysEnv` (duplicate name).
14. `parse_api_keys_env("no-colon")` raises `InvalidApiKeysEnv`.
15. `parse_api_keys_env(":")` raises (empty name AND empty secret).
16. `parse_api_keys_env("a:")` raises (empty secret).
17. `parse_api_keys_env(":secret")` raises (empty name).

### 3.3 Authorization header parsing

18. `parse_bearer_header("Bearer abc123")` returns `"abc123"`.
19. `parse_bearer_header("bearer abc123")` returns `"abc123"` (case-insensitive scheme).
20. `parse_bearer_header(None)` raises `InvalidAuthHeader`.
21. `parse_bearer_header("")` raises.
22. `parse_bearer_header("Basic abc")` raises (wrong scheme).
23. `parse_bearer_header("Bearer ")` raises (empty token).

### 3.4 Authentication

24. `authenticate(plaintext, [matching_key])` returns the matching `ApiKey`.
25. `authenticate("wrong", [any_key])` raises `InvalidApiKey`.
26. `authenticate(plaintext, [disabled_key_with_matching_hash])` raises `DisabledApiKey`.
27. `authenticate("", [any_key])` raises `InvalidApiKey`.
28. When mixing enabled + disabled keys with the same plaintext (admin oversight / rotation mid-point), the enabled one wins over the disabled one (first-match-enabled-wins).

### 3.5 Delete tokens

29. `generate_delete_token()` returns `(plaintext, hashed)` where `plaintext` starts with `"dt_"`.
30. `verify_delete_token(plaintext, hashed)` is True for a pair returned by `generate_delete_token()`.
31. `verify_delete_token("dt_wrong", correct_hash)` is False.
32. Two successive calls to `generate_delete_token()` return different plaintexts (entropy).

### 3.6 Contract / taxonomy

33. `InvalidAuthHeader`, `InvalidApiKey`, `DisabledApiKey` all subclass `AuthError`.
34. `InvalidApiKeysEnv` subclasses `ValueError`.
35. `str(AuthError)` and subclasses never include the plaintext token (log hygiene — grep the message for the presented token, assert absent).

---

## 4. Test List

All in `tests/unit/test_auth.py`. Tests pass `rounds=4` everywhere so the suite runs in <1s instead of minutes.

### 4.1 Hashing (7)

- [ ] `test_hash_secret_produces_bcrypt_blob`
- [ ] `test_hash_secret_different_salt_each_call`
- [ ] `test_hash_secret_honors_rounds_parameter`
- [ ] `test_verify_secret_accepts_correct_plaintext`
- [ ] `test_verify_secret_rejects_wrong_plaintext`
- [ ] `test_verify_secret_returns_false_on_invalid_hash_bytes`
- [ ] `test_verify_secret_round_trip_at_default_rounds` (uses rounds=4 via helper)

### 4.2 API-key parsing (10)

- [ ] `test_parse_api_keys_env_single_entry`
- [ ] `test_parse_api_keys_env_stores_bcrypt_hash_not_plaintext`
- [ ] `test_parse_api_keys_env_multiple_entries_preserves_order`
- [ ] `test_parse_api_keys_env_trims_whitespace`
- [ ] `test_parse_api_keys_env_rejects_empty_string`
- [ ] `test_parse_api_keys_env_rejects_duplicate_names`
- [ ] `test_parse_api_keys_env_rejects_missing_colon`
- [ ] `test_parse_api_keys_env_rejects_empty_name`
- [ ] `test_parse_api_keys_env_rejects_empty_secret`
- [ ] `test_parse_api_keys_env_rejects_trailing_comma_only`

### 4.3 Bearer parsing (6)

- [ ] `test_parse_bearer_header_standard`
- [ ] `test_parse_bearer_header_case_insensitive_scheme`
- [ ] `test_parse_bearer_header_none_raises`
- [ ] `test_parse_bearer_header_empty_raises`
- [ ] `test_parse_bearer_header_wrong_scheme_raises`
- [ ] `test_parse_bearer_header_empty_token_raises`

### 4.4 authenticate (5)

- [ ] `test_authenticate_returns_matching_key`
- [ ] `test_authenticate_rejects_unknown_plaintext`
- [ ] `test_authenticate_rejects_disabled_key`
- [ ] `test_authenticate_rejects_empty_token`
- [ ] `test_authenticate_prefers_enabled_over_disabled_on_same_plaintext`

### 4.5 Delete tokens (4)

- [ ] `test_generate_delete_token_has_dt_prefix`
- [ ] `test_generate_delete_token_verify_round_trip`
- [ ] `test_generate_delete_token_rejects_wrong_plaintext`
- [ ] `test_generate_delete_token_produces_distinct_tokens`

### 4.6 Contract (3)

- [ ] `test_invalid_auth_header_subclasses_auth_error`
- [ ] `test_invalid_api_keys_env_is_value_error`
- [ ] `test_error_messages_do_not_contain_plaintext` (log hygiene)

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- UTF-8 plaintexts (bcrypt handles up to 72 bytes — we hash `plaintext.encode("utf-8")` internally; tokens generated by `secrets.token_urlsafe` are pure ASCII, so the limit is irrelevant for our own tokens).
- `verify_secret` on an invalid hash blob (e.g. stale row from a pre-migration state) returns False without raising — callers just see "no match".

### 5.2 Deferred

- **Per-request cache of verify results**. bcrypt cost=12 is ~250 ms per comparison; multiplied by the number of stored keys, a busy API could saturate. For single-user ≤1 req/min (master spec §1.3) this is fine; step 14 (metrics) will add a hit-cache if the histogram shows it's needed.
- **Audit log entries for auth attempts**. The `event_log` table has no `event='auth_failed'` in the v1 schema (§6.1). Log via `logging.warning` for now; structured events are a later refactor.
- **Password hashing for site-level basic auth** (master spec §5.2 `password` field) uses the same `hash_secret`/`verify_secret` helpers — no new API, just reuse. Integration happens in step 12.

### 5.3 Explicitly non-goal

- **JWT / OAuth**. Master spec §5.1 is explicit: Bearer tokens with server-side bcrypt hash comparison. No JWT.
- **Per-endpoint scopes or RBAC**. Master spec §5.1: "Tutte le keys hanno stesso potere (no admin/non-admin in v1)". Adding scopes would be a design change requiring a spec amendment.

---

## 6. Open Questions

(None — mini-spec approved.)

~~Q1: Should `authenticate` short-circuit on first match or scan all entries?~~
→ Scan all. The cost is N × 250 ms at cost=12, with N ≤ ~10 keys in any realistic deployment — a few extra seconds under attack, worth the constant-time guarantee. Master spec §7.6 "Hash bcrypt per tutto" implies we take timing seriously.

~~Q2: Prefix `dt_` or `delete_` on the delete token?~~
→ `dt_`. Master spec §5.2 example shows `"delete_token": "dt_abc123xyz"`. We follow the example literally.

~~Q3: Should `parse_api_keys_env` accept an empty value as "no keys" (degraded mode)?~~
→ No — raise. An API without keys can't authenticate anyone. Fail fast at startup so the operator sees the misconfiguration during deploy, not at the first 500.

~~Q4: Should the module expose `bcrypt` constants (version string, etc.)?~~
→ No. `bcrypt` is an implementation detail; exposing it would leak the choice into downstream modules and make future migration to scrypt/argon2 harder.

---

## 7. Done When

- [ ] All 35 tests in §4 committed and green on CI.
- [ ] Coverage ≥ 90% on `auth.py`.
- [ ] No test exceeds 500 ms (use rounds=4).
- [ ] Ruff clean.
- [ ] `make check` green locally.
- [ ] Roadmap table in [`CLAUDE.md`](../../CLAUDE.md) §8 updated (Step 6 → ✅).
- [ ] This file's Status flipped to ✅.
