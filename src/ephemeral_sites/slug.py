"""Slug generator and validator for ephemeral-sites.

See ``docs/steps/step-3-slug-generator.md`` for the full public contract.

This module is pure: no I/O, no DB access, no logging. The ``is_taken``
predicate is injected by the API/DB layer (step 4+), so the retry loop
can be tested with simple callables without a running database.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable

from .slug_words import DEFAULT_ADJECTIVES, DEFAULT_NOUNS

__all__ = [
    "SLUG_REGEX",
    "InvalidSlugError",
    "SlugCollisionError",
    "generate_slug",
    "generate_unique_slug",
    "validate_slug",
]

# Master spec §5.2 path regex. Compile once at import time.
SLUG_REGEX: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


class InvalidSlugError(ValueError):
    """Slug does not match the path regex. Maps to HTTP 400."""


class SlugCollisionError(RuntimeError):
    """:func:`generate_unique_slug` exhausted its retry budget. Maps to HTTP 500."""


def validate_slug(slug: str) -> None:
    """Raise :class:`InvalidSlugError` if ``slug`` does not match ``SLUG_REGEX``.

    ``slug`` must be a ``str`` (``bytes`` or other types fail fast).
    """
    if not isinstance(slug, str) or SLUG_REGEX.match(slug) is None:
        raise InvalidSlugError(f"invalid slug: {slug!r}")


def generate_slug(
    *,
    rng: Callable[[int], bytes] = secrets.token_bytes,
    adjectives: tuple[str, ...] = DEFAULT_ADJECTIVES,
    nouns: tuple[str, ...] = DEFAULT_NOUNS,
) -> str:
    """Produce a ``{adjective}-{noun}-{4hex}`` slug.

    ``rng`` is a callable ``Callable[[int], bytes]`` returning ``n``
    cryptographically-random bytes. Default: :func:`secrets.token_bytes`.

    The 4 bytes consumed are: ``[0]`` adjective index, ``[1]`` noun index,
    ``[2:4]`` rendered as 4 lowercase hex characters.

    The result is guaranteed to match :data:`SLUG_REGEX`.
    """
    blob = rng(4)
    adj = adjectives[blob[0] % len(adjectives)]
    noun = nouns[blob[1] % len(nouns)]
    hex4 = blob[2:4].hex()
    return f"{adj}-{noun}-{hex4}"


def generate_unique_slug(
    is_taken: Callable[[str], bool],
    *,
    max_attempts: int = 5,
    rng: Callable[[int], bytes] = secrets.token_bytes,
    adjectives: tuple[str, ...] = DEFAULT_ADJECTIVES,
    nouns: tuple[str, ...] = DEFAULT_NOUNS,
) -> str:
    """Return the first generated slug for which ``is_taken(slug)`` is ``False``.

    Calls :func:`generate_slug` up to ``max_attempts`` times, stopping as
    soon as a candidate passes the uniqueness predicate.

    Raises:
        SlugCollisionError: after ``max_attempts`` consecutive collisions.
    """
    for _ in range(max_attempts):
        candidate = generate_slug(rng=rng, adjectives=adjectives, nouns=nouns)
        if not is_taken(candidate):
            return candidate
    raise SlugCollisionError(f"could not find an unused slug after {max_attempts} attempts")
