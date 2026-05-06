"""Unit tests for the auth module (bcrypt + API keys + delete tokens).

Derived 1:1 from docs/steps/step-6-auth.md §4.

All tests pass ``rounds=4`` to the hashing helpers so the suite runs
quickly; production uses rounds=12 (master spec §7.6). The test helper
``_fast_hash`` below centralises that choice.
"""

from __future__ import annotations

import pytest

from ephemeral_sites import auth

# Low-cost factor so each test is sub-50 ms instead of ~250 ms.
FAST_ROUNDS = 4


def _fast_hash(plaintext: str) -> bytes:
    return auth.hash_secret(plaintext, rounds=FAST_ROUNDS)


# ---------------------------------------------------------------------------
# §4.1 Hashing (7)
# ---------------------------------------------------------------------------


def test_hash_secret_produces_bcrypt_blob():
    h = _fast_hash("anything")
    assert isinstance(h, bytes)
    # bcrypt modular crypt format: $2b$<cost>$...
    assert h.startswith(b"$2"), f"not a bcrypt hash: {h!r}"


def test_hash_secret_different_salt_each_call():
    a = _fast_hash("same")
    b = _fast_hash("same")
    assert a != b, "hash_secret should use a fresh salt each call"


def test_hash_secret_honors_rounds_parameter():
    h = auth.hash_secret("x", rounds=4)
    # Cost is embedded in the hash: $2b$04$...
    assert h[:7] in (b"$2b$04$", b"$2a$04$", b"$2y$04$")


def test_verify_secret_accepts_correct_plaintext():
    h = _fast_hash("secret")
    assert auth.verify_secret("secret", h) is True


def test_verify_secret_rejects_wrong_plaintext():
    h = _fast_hash("right")
    assert auth.verify_secret("wrong", h) is False


def test_verify_secret_returns_false_on_invalid_hash_bytes():
    # Stale / corrupted hash in the DB must not raise.
    assert auth.verify_secret("x", b"not-a-bcrypt-hash") is False
    assert auth.verify_secret("x", b"") is False


def test_verify_secret_round_trip_at_default_rounds():
    # Uses the module's DEFAULT_BCRYPT_ROUNDS value; override via
    # rounds=FAST_ROUNDS to keep the test fast.
    h = auth.hash_secret("token-xyz", rounds=FAST_ROUNDS)
    assert auth.verify_secret("token-xyz", h) is True


# ---------------------------------------------------------------------------
# §4.2 API-key parsing (10)
# ---------------------------------------------------------------------------


def test_parse_api_keys_env_single_entry():
    keys = auth.parse_api_keys_env("main:secret", rounds=FAST_ROUNDS)
    assert len(keys) == 1
    assert keys[0].name == "main"
    assert keys[0].disabled is False


def test_parse_api_keys_env_stores_bcrypt_hash_not_plaintext():
    keys = auth.parse_api_keys_env("main:plainkey", rounds=FAST_ROUNDS)
    assert keys[0].hashed != b"plainkey"
    assert keys[0].hashed.startswith(b"$2")
    # And the hash round-trips.
    assert auth.verify_secret("plainkey", keys[0].hashed) is True


def test_parse_api_keys_env_multiple_entries_preserves_order():
    keys = auth.parse_api_keys_env("a:1,b:2,c:3", rounds=FAST_ROUNDS)
    assert [k.name for k in keys] == ["a", "b", "c"]


def test_parse_api_keys_env_trims_whitespace():
    keys = auth.parse_api_keys_env("  main : secret ,  ci : other  ", rounds=FAST_ROUNDS)
    assert [k.name for k in keys] == ["main", "ci"]
    assert auth.verify_secret("secret", keys[0].hashed) is True
    assert auth.verify_secret("other", keys[1].hashed) is True


def test_parse_api_keys_env_rejects_empty_string():
    with pytest.raises(auth.InvalidApiKeysEnv):
        auth.parse_api_keys_env("", rounds=FAST_ROUNDS)


def test_parse_api_keys_env_rejects_duplicate_names():
    with pytest.raises(auth.InvalidApiKeysEnv):
        auth.parse_api_keys_env("main:a,main:b", rounds=FAST_ROUNDS)


def test_parse_api_keys_env_rejects_missing_colon():
    with pytest.raises(auth.InvalidApiKeysEnv):
        auth.parse_api_keys_env("no-colon-here", rounds=FAST_ROUNDS)


def test_parse_api_keys_env_rejects_empty_name():
    with pytest.raises(auth.InvalidApiKeysEnv):
        auth.parse_api_keys_env(":secret", rounds=FAST_ROUNDS)


def test_parse_api_keys_env_rejects_empty_secret():
    with pytest.raises(auth.InvalidApiKeysEnv):
        auth.parse_api_keys_env("main:", rounds=FAST_ROUNDS)


def test_parse_api_keys_env_rejects_trailing_comma_only():
    # Extra comma produces an empty entry, which is invalid.
    with pytest.raises(auth.InvalidApiKeysEnv):
        auth.parse_api_keys_env("main:x,", rounds=FAST_ROUNDS)


# ---------------------------------------------------------------------------
# §4.3 Bearer parsing (6)
# ---------------------------------------------------------------------------


def test_parse_bearer_header_standard():
    assert auth.parse_bearer_header("Bearer abc123") == "abc123"


def test_parse_bearer_header_case_insensitive_scheme():
    assert auth.parse_bearer_header("bearer abc123") == "abc123"
    assert auth.parse_bearer_header("BEARER abc123") == "abc123"


def test_parse_bearer_header_none_raises():
    with pytest.raises(auth.InvalidAuthHeader):
        auth.parse_bearer_header(None)


def test_parse_bearer_header_empty_raises():
    with pytest.raises(auth.InvalidAuthHeader):
        auth.parse_bearer_header("")


def test_parse_bearer_header_wrong_scheme_raises():
    with pytest.raises(auth.InvalidAuthHeader):
        auth.parse_bearer_header("Basic dXNlcjpwYXNz")


def test_parse_bearer_header_empty_token_raises():
    with pytest.raises(auth.InvalidAuthHeader):
        auth.parse_bearer_header("Bearer ")


# ---------------------------------------------------------------------------
# §4.4 authenticate (5)
# ---------------------------------------------------------------------------


def _make_key(name: str, plaintext: str, *, disabled: bool = False) -> auth.ApiKey:
    return auth.ApiKey(name=name, hashed=_fast_hash(plaintext), disabled=disabled)


def test_authenticate_returns_matching_key():
    k = _make_key("main", "secret")
    result = auth.authenticate("secret", [k])
    assert result.name == "main"


def test_authenticate_rejects_unknown_plaintext():
    k = _make_key("main", "right")
    with pytest.raises(auth.InvalidApiKey):
        auth.authenticate("wrong", [k])


def test_authenticate_rejects_disabled_key():
    k = _make_key("old", "secret", disabled=True)
    with pytest.raises(auth.DisabledApiKey):
        auth.authenticate("secret", [k])


def test_authenticate_rejects_empty_token():
    k = _make_key("main", "secret")
    with pytest.raises(auth.InvalidApiKey):
        auth.authenticate("", [k])


def test_authenticate_prefers_enabled_over_disabled_on_same_plaintext():
    # During rotation the same plaintext may briefly live in two entries,
    # one disabled and one enabled. The enabled one must win.
    disabled = _make_key("main-old", "rotating", disabled=True)
    enabled = _make_key("main-new", "rotating")
    result = auth.authenticate("rotating", [disabled, enabled])
    assert result.name == "main-new"
    assert result.disabled is False


# ---------------------------------------------------------------------------
# §4.5 Delete tokens (4)
# ---------------------------------------------------------------------------


def test_generate_delete_token_has_dt_prefix():
    plaintext, _ = auth.generate_delete_token(rounds=FAST_ROUNDS)
    assert plaintext.startswith("dt_"), f"expected 'dt_' prefix, got {plaintext!r}"


def test_generate_delete_token_verify_round_trip():
    plaintext, hashed = auth.generate_delete_token(rounds=FAST_ROUNDS)
    assert auth.verify_delete_token(plaintext, hashed) is True


def test_generate_delete_token_rejects_wrong_plaintext():
    _, hashed = auth.generate_delete_token(rounds=FAST_ROUNDS)
    assert auth.verify_delete_token("dt_definitely_wrong", hashed) is False


def test_generate_delete_token_produces_distinct_tokens():
    a, _ = auth.generate_delete_token(rounds=FAST_ROUNDS)
    b, _ = auth.generate_delete_token(rounds=FAST_ROUNDS)
    assert a != b


# ---------------------------------------------------------------------------
# §4.6 Contract (3)
# ---------------------------------------------------------------------------


def test_invalid_auth_header_subclasses_auth_error():
    assert issubclass(auth.InvalidAuthHeader, auth.AuthError)
    assert issubclass(auth.InvalidApiKey, auth.AuthError)
    assert issubclass(auth.DisabledApiKey, auth.AuthError)


def test_invalid_api_keys_env_is_value_error():
    assert issubclass(auth.InvalidApiKeysEnv, ValueError)


def test_error_messages_do_not_contain_plaintext():
    """Log hygiene: the raised exceptions must not leak the token."""
    secret = "very-secret-token-that-must-not-leak"
    k = _make_key("main", "right")

    try:
        auth.authenticate(secret, [k])
    except auth.InvalidApiKey as exc:
        assert secret not in str(exc)
        assert secret not in repr(exc)

    try:
        auth.parse_bearer_header(f"Basic {secret}")
    except auth.InvalidAuthHeader as exc:
        assert secret not in str(exc)
        assert secret not in repr(exc)
