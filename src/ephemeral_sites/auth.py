"""Auth primitives: bcrypt hashing, API-key parsing, delete tokens.

See ``docs/steps/step-6-auth.md`` for the full public contract and
rationale.

The module stays small and boring by design — three concerns (hashing,
API keys, delete tokens) and a handful of exceptions. No HTTP routing
here; the API layer (step 8+) will consume these helpers via FastAPI
dependencies.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import bcrypt

__all__ = [
    "DEFAULT_BCRYPT_ROUNDS",
    "DELETE_TOKEN_PREFIX",
    "ApiKey",
    "AuthError",
    "DisabledApiKey",
    "InvalidApiKey",
    "InvalidApiKeysEnv",
    "InvalidAuthHeader",
    "authenticate",
    "generate_delete_token",
    "hash_secret",
    "parse_api_keys_env",
    "parse_bearer_header",
    "verify_delete_token",
    "verify_secret",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BCRYPT_ROUNDS: int = 12  # master spec §7.6
DELETE_TOKEN_PREFIX: str = "dt_"  # master spec §5.2 example


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base class for auth-time failures. Subclasses map to HTTP status codes."""


class InvalidAuthHeader(AuthError):
    """Authorization header missing, malformed, or wrong scheme. Maps to 401."""


class InvalidApiKey(AuthError):
    """Bearer value does not match any known key hash. Maps to 401."""


class DisabledApiKey(AuthError):
    """Bearer value matches a key flagged disabled. Maps to 403."""


class InvalidApiKeysEnv(ValueError):
    """API_KEYS env value is malformed. Raised at startup; fail-fast."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiKey:
    """An API key loaded from the ``API_KEYS`` secret.

    ``name`` is free-form (labels like "main", "ci"); it has no
    authorization meaning in v1 (master spec §5.1).
    """

    name: str
    hashed: bytes
    disabled: bool = False


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def hash_secret(plaintext: str, *, rounds: int = DEFAULT_BCRYPT_ROUNDS) -> bytes:
    """Return a fresh-salt bcrypt hash of ``plaintext``.

    ``rounds`` is the cost parameter (2^rounds iterations). Production
    uses 12 (~250 ms per hash); tests pass 4 to stay under a second.
    """
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(plaintext.encode("utf-8"), salt)


def verify_secret(plaintext: str, hashed: bytes) -> bool:
    """Constant-time plaintext/hash comparison via :func:`bcrypt.checkpw`.

    Returns ``False`` (never raises) if ``hashed`` is not a valid bcrypt
    blob — a corrupted or pre-migration row shows up as "no match",
    which is the same outcome as a genuine mismatch for callers.
    """
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed)
    except (ValueError, TypeError):
        # bcrypt.checkpw raises ValueError on malformed hash input.
        return False


# ---------------------------------------------------------------------------
# API-key parsing
# ---------------------------------------------------------------------------


def parse_api_keys_env(
    env_value: str,
    *,
    rounds: int = DEFAULT_BCRYPT_ROUNDS,
) -> tuple[ApiKey, ...]:
    """Parse the ``API_KEYS`` secret into a tuple of hashed :class:`ApiKey`.

    Format (master spec §5.1):
        "main:plainkey1,ci:plainkey2"

    Plaintext keys are hashed immediately (cost = ``rounds``); the
    plaintext itself never escapes this function.

    Raises:
        InvalidApiKeysEnv: on empty input, duplicate names, malformed
            entries (missing colon, empty name, empty secret).
    """
    if not env_value or not env_value.strip():
        raise InvalidApiKeysEnv("API_KEYS is empty")

    entries = env_value.split(",")
    parsed: list[ApiKey] = []
    seen_names: set[str] = set()

    for raw in entries:
        entry = raw.strip()
        if not entry:
            raise InvalidApiKeysEnv("API_KEYS contains an empty entry (check for trailing commas)")
        if ":" not in entry:
            # Note: we do NOT include the offending entry in the error
            # message — it might be the plaintext of a misconfigured line.
            raise InvalidApiKeysEnv(
                "API_KEYS entry is missing ':' separator between name and secret"
            )
        name_raw, secret_raw = entry.split(":", 1)
        name = name_raw.strip()
        secret = secret_raw.strip()
        if not name:
            raise InvalidApiKeysEnv("API_KEYS entry has an empty name")
        if not secret:
            raise InvalidApiKeysEnv(f"API_KEYS entry {name!r} has an empty secret")
        if name in seen_names:
            raise InvalidApiKeysEnv(f"API_KEYS has duplicate name {name!r}")
        seen_names.add(name)
        parsed.append(ApiKey(name=name, hashed=hash_secret(secret, rounds=rounds)))

    if not parsed:
        raise InvalidApiKeysEnv("API_KEYS contains no valid entries")

    return tuple(parsed)


# ---------------------------------------------------------------------------
# Authorization header parsing
# ---------------------------------------------------------------------------


def parse_bearer_header(header_value: str | None) -> str:
    """Return the token from ``Authorization: Bearer <token>``.

    Scheme check is case-insensitive per RFC 7235 §2.1.

    Raises:
        InvalidAuthHeader: missing, empty, non-Bearer scheme, or empty
            token. The error message intentionally never echoes the
            header content (log hygiene; we may be looking at a token
            submitted under a wrong scheme by accident).
    """
    if header_value is None or not header_value:
        raise InvalidAuthHeader("Authorization header missing or empty")

    parts = header_value.split(None, 1)
    if len(parts) != 2:
        raise InvalidAuthHeader("Authorization header malformed")
    scheme, token = parts
    if scheme.lower() != "bearer":
        raise InvalidAuthHeader("Authorization scheme must be Bearer")
    token = token.strip()
    if not token:
        raise InvalidAuthHeader("Authorization bearer token is empty")
    return token


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def authenticate(
    presented_plaintext: str,
    known_keys: Iterable[ApiKey],
) -> ApiKey:
    """Authenticate ``presented_plaintext`` against ``known_keys``.

    Scans EVERY key (no early exit on mismatch) so the total runtime is
    independent of *which* key matched — no side-channel for timing
    attackers to learn which slot a valid key lives in. bcrypt.checkpw
    is itself constant-time per comparison, so the aggregate wall time
    only depends on the number of keys, not on the outcome.

    If multiple keys match the same plaintext (common during rotation),
    the first ENABLED match wins.

    Raises:
        InvalidApiKey: when no hash matches (including empty input).
        DisabledApiKey: when the only matches are disabled keys.
    """
    enabled_match: ApiKey | None = None
    disabled_match: ApiKey | None = None

    for key in known_keys:
        if verify_secret(presented_plaintext, key.hashed):
            if key.disabled:
                if disabled_match is None:
                    disabled_match = key
            else:
                if enabled_match is None:
                    enabled_match = key

    if enabled_match is not None:
        return enabled_match
    if disabled_match is not None:
        raise DisabledApiKey(f"API key {disabled_match.name!r} is disabled")
    raise InvalidApiKey("unknown API key")


# ---------------------------------------------------------------------------
# Delete tokens
# ---------------------------------------------------------------------------


def generate_delete_token(
    *,
    rng: Callable[[int], str] = secrets.token_urlsafe,
    rounds: int = DEFAULT_BCRYPT_ROUNDS,
) -> tuple[str, bytes]:
    """Produce a fresh delete token as ``(plaintext, hashed)``.

    The plaintext starts with :data:`DELETE_TOKEN_PREFIX` (``"dt_"``)
    followed by 24 random bytes rendered URL-safe base64 — 192 bits of
    entropy, more than enough for an ephemeral single-user service.
    """
    body = rng(24)
    plaintext = f"{DELETE_TOKEN_PREFIX}{body}"
    hashed = hash_secret(plaintext, rounds=rounds)
    return plaintext, hashed


def verify_delete_token(presented: str, hashed: bytes) -> bool:
    """True iff ``presented`` is the plaintext that produced ``hashed``."""
    return verify_secret(presented, hashed)
