"""Unit tests for the slug generator.

Derived 1:1 from docs/steps/step-3-slug-generator.md §4 (Test List).
Each test maps to at least one acceptance criterion in §3 of that
mini-spec.

Written before the implementation — running this module without
src/ephemeral_sites/slug.py must fail with ImportError. That is the
expected red signal.
"""

from __future__ import annotations

import re
import string

import pytest

from ephemeral_sites import slug
from ephemeral_sites.slug_words import DEFAULT_ADJECTIVES, DEFAULT_NOUNS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches the master-spec path regex (§5.2).
_PATH_REGEX = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


def _fixed_rng(payload: bytes):
    """Return a Callable[[int], bytes] that always returns the first n bytes
    of ``payload`` (cycling if payload is shorter)."""

    def _rng(n: int) -> bytes:
        out = bytearray()
        i = 0
        while len(out) < n:
            out.append(payload[i % len(payload)])
            i += 1
        return bytes(out)

    return _rng


# ---------------------------------------------------------------------------
# §4.1 generate_slug
# ---------------------------------------------------------------------------


def test_generate_slug_matches_regex():
    for _ in range(100):
        s = slug.generate_slug()
        assert _PATH_REGEX.match(s), f"slug {s!r} does not match master-spec regex"


def test_generate_slug_has_three_hyphen_separated_parts():
    s = slug.generate_slug()
    parts = s.split("-")
    assert len(parts) == 3, f"expected 3 parts in {s!r}, got {parts}"


def test_generate_slug_last_part_is_4_hex_lowercase():
    for _ in range(50):
        s = slug.generate_slug()
        last = s.rsplit("-", 1)[-1]
        assert len(last) == 4, f"hex suffix wrong length in {s!r}"
        assert all(c in "0123456789abcdef" for c in last), f"non-hex suffix in {s!r}"


def test_generate_slug_is_deterministic_given_rng():
    rng = _fixed_rng(b"\x00" * 32)
    a = slug.generate_slug(rng=rng)
    b = slug.generate_slug(rng=rng)
    assert a == b


def test_generate_slug_varies_with_different_rng_output():
    a = slug.generate_slug(rng=_fixed_rng(b"\x00" * 32))
    b = slug.generate_slug(rng=_fixed_rng(b"\xff" * 32))
    assert a != b


def test_generate_slug_picks_adjective_from_list():
    s = slug.generate_slug()
    adj = s.split("-", 1)[0]
    assert adj in DEFAULT_ADJECTIVES, f"adjective {adj!r} not in DEFAULT_ADJECTIVES"


def test_generate_slug_picks_noun_from_list():
    s = slug.generate_slug()
    # Middle segment: everything between the first and the last "-"
    middle = s.split("-")[1]
    assert middle in DEFAULT_NOUNS, f"noun {middle!r} not in DEFAULT_NOUNS"


# ---------------------------------------------------------------------------
# §4.2 generate_unique_slug
# ---------------------------------------------------------------------------


def test_generate_unique_slug_first_try_not_taken():
    calls: list[str] = []

    def is_taken(s: str) -> bool:
        calls.append(s)
        return False

    result = slug.generate_unique_slug(is_taken)
    assert result not in (None, "")
    assert len(calls) == 1
    assert calls[0] == result


def test_generate_unique_slug_retries_then_succeeds():
    responses = iter([True, True, True, False])
    calls: list[str] = []

    def is_taken(s: str) -> bool:
        calls.append(s)
        return next(responses)

    result = slug.generate_unique_slug(is_taken)
    assert result == calls[-1]
    assert len(calls) == 4


def test_generate_unique_slug_raises_after_5_collisions():
    calls: list[str] = []

    def is_taken(s: str) -> bool:
        calls.append(s)
        return True

    with pytest.raises(slug.SlugCollisionError):
        slug.generate_unique_slug(is_taken)
    assert len(calls) == 5


def test_generate_unique_slug_raises_after_custom_max_attempts():
    calls: list[str] = []

    def is_taken(s: str) -> bool:
        calls.append(s)
        return True

    with pytest.raises(slug.SlugCollisionError):
        slug.generate_unique_slug(is_taken, max_attempts=2)
    assert len(calls) == 2


def test_generate_unique_slug_passes_each_candidate_to_is_taken():
    seen: list[str] = []

    def is_taken(s: str) -> bool:
        seen.append(s)
        return False

    returned = slug.generate_unique_slug(is_taken)
    assert returned in seen
    # Every candidate must match the master-spec regex.
    for s in seen:
        assert _PATH_REGEX.match(s), f"candidate {s!r} violates the path regex"


# ---------------------------------------------------------------------------
# §4.3 validate_slug
# ---------------------------------------------------------------------------


def test_validate_slug_accepts_minimal():
    # Regex allows length 3. Pick one that definitely complies.
    slug.validate_slug("abc")


def test_validate_slug_accepts_max_length():
    # 63 chars: one leading alphanum + 62 more in [a-z0-9-]
    s = "a" + ("b" * 62)
    assert len(s) == 63
    slug.validate_slug(s)


def test_validate_slug_accepts_digits():
    slug.validate_slug("0-fox-a3f2")


def test_validate_slug_accepts_typical_generated_slug():
    slug.validate_slug("happy-fox-a3f2")


def test_validate_slug_rejects_empty():
    with pytest.raises(slug.InvalidSlugError):
        slug.validate_slug("")


def test_validate_slug_rejects_too_short():
    with pytest.raises(slug.InvalidSlugError):
        slug.validate_slug("ab")


def test_validate_slug_rejects_too_long():
    with pytest.raises(slug.InvalidSlugError):
        slug.validate_slug("a" + ("b" * 63))  # 64 chars


def test_validate_slug_rejects_leading_dash():
    with pytest.raises(slug.InvalidSlugError):
        slug.validate_slug("-happy-fox")


def test_validate_slug_rejects_uppercase():
    with pytest.raises(slug.InvalidSlugError):
        slug.validate_slug("Happy-fox")


def test_validate_slug_rejects_underscore():
    with pytest.raises(slug.InvalidSlugError):
        slug.validate_slug("happy_fox")


def test_validate_slug_rejects_non_ascii():
    with pytest.raises(slug.InvalidSlugError):
        slug.validate_slug("caffè-demo")


@pytest.mark.parametrize("bad", ["happy.fox", "happy/fox", "happy fox", "happy\tfox"])
def test_validate_slug_rejects_dots_and_slashes(bad: str):
    with pytest.raises(slug.InvalidSlugError):
        slug.validate_slug(bad)


# ---------------------------------------------------------------------------
# §4.4 Word lists
# ---------------------------------------------------------------------------


def test_wordlists_minimum_size():
    assert len(DEFAULT_ADJECTIVES) >= 64, (
        f"DEFAULT_ADJECTIVES has {len(DEFAULT_ADJECTIVES)} entries, expected ≥ 64"
    )
    assert len(DEFAULT_NOUNS) >= 64, (
        f"DEFAULT_NOUNS has {len(DEFAULT_NOUNS)} entries, expected ≥ 64"
    )


@pytest.mark.parametrize(
    "wordlist, name",
    [(DEFAULT_ADJECTIVES, "DEFAULT_ADJECTIVES"), (DEFAULT_NOUNS, "DEFAULT_NOUNS")],
)
def test_wordlists_all_lowercase_alpha_3_to_10_chars(wordlist, name):
    pattern = re.compile(r"^[a-z]{3,10}$")
    offenders = [w for w in wordlist if not pattern.match(w)]
    assert not offenders, f"{name} contains invalid words: {offenders}"


def test_wordlists_are_disjoint():
    overlap = set(DEFAULT_ADJECTIVES) & set(DEFAULT_NOUNS)
    assert not overlap, f"adjective/noun overlap: {sorted(overlap)}"


def test_wordlists_have_no_duplicates():
    # Defensive: a typo that duplicates a word still passes other checks.
    assert len(set(DEFAULT_ADJECTIVES)) == len(DEFAULT_ADJECTIVES)
    assert len(set(DEFAULT_NOUNS)) == len(DEFAULT_NOUNS)


def test_wordlists_use_only_ascii():
    # Hedge against accidental unicode homoglyphs (e.g. Cyrillic 'а').
    allowed = set(string.ascii_lowercase)
    for w in list(DEFAULT_ADJECTIVES) + list(DEFAULT_NOUNS):
        assert set(w) <= allowed, f"non-ASCII letter in {w!r}"


# ---------------------------------------------------------------------------
# §4.5 Contract
# ---------------------------------------------------------------------------


def test_invalid_slug_error_is_value_error():
    assert issubclass(slug.InvalidSlugError, ValueError)


def test_slug_collision_error_is_runtime_error():
    assert issubclass(slug.SlugCollisionError, RuntimeError)
