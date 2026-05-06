# Step 3 — Slug generator

**Master spec sections**: [§5.2 path param regex](../SPEC.md), [§5.3 POST /api/v1/sites](../SPEC.md), [§11.3 test_slug](../SPEC.md)
**Roadmap entry**: [§16.1 step 3](../SPEC.md)
**Status**: ✅ Complete (2026-05-06, commit `d0a3026`)
**Owner**: Andrea Veronesi

---

## 1. Goal

Deliver two small, testable, pure functions + a curated word list:

1. **`generate_slug()`** — produce a memorable slug in the format `{adjective}-{noun}-{4hex}`, e.g. `happy-fox-a3f2`. Used by `POST /api/v1/sites` when the client does not specify a slug in the path (master spec §5.3).
2. **`validate_slug(slug)`** — enforce the path regex `^[a-z0-9][a-z0-9-]{2,62}$` (master spec §5.2). Used for user-supplied slugs in `PUT /api/v1/sites/{slug}`.
3. **`generate_unique_slug(is_taken, ...)`** — small wrapper that calls `generate_slug()` in a loop against an "is this taken?" predicate, retrying up to 5 times, then raising `SlugCollisionError`. The predicate is injected so the DB layer can supply it in step 4+ without the slug module ever touching SQLite.

The module is **pure**: no I/O, no DB access, no logging side effects. Randomness and the word lists are injectable so every test is deterministic.

---

## 2. Public API / Contract

### 2.1 Module layout

- `src/ephemeral_sites/slug.py` — the three public functions + errors.
- `src/ephemeral_sites/slug_words.py` — the curated adjective and noun lists.
- `tests/unit/test_slug.py` — unit tests.

No new runtime dependencies; uses stdlib `secrets`, `re`, `typing`.

### 2.2 Exceptions

```python
class InvalidSlugError(ValueError):
    """Slug does not match the path regex. Maps to HTTP 400."""


class SlugCollisionError(RuntimeError):
    """generate_unique_slug() exhausted its retries. Maps to HTTP 500."""
```

### 2.3 Functions

```python
SLUG_REGEX: re.Pattern[str]  # compiled ^[a-z0-9][a-z0-9-]{2,62}$

def validate_slug(slug: str) -> None:
    """Validate a user-supplied slug. No return value on success.

    Raises:
        InvalidSlugError: when ``slug`` does not match SLUG_REGEX.
    """


def generate_slug(
    *,
    rng: Callable[[int], bytes] = secrets.token_bytes,
    adjectives: tuple[str, ...] = DEFAULT_ADJECTIVES,
    nouns: tuple[str, ...] = DEFAULT_NOUNS,
) -> str:
    """Produce a ``{adjective}-{noun}-{4hex}`` slug.

    ``rng(n)`` must return n cryptographically-random bytes. Injectable so
    tests can pin the output. The first ~1-2 bytes pick the adjective
    index (`b[0] % len(adjectives)`), the next 1-2 the noun
    (`b[1] % len(nouns)`), the last 2 bytes render as 4 lowercase hex
    characters.

    The output is guaranteed to match SLUG_REGEX.
    """


def generate_unique_slug(
    is_taken: Callable[[str], bool],
    *,
    max_attempts: int = 5,
    rng: Callable[[int], bytes] = secrets.token_bytes,
    adjectives: tuple[str, ...] = DEFAULT_ADJECTIVES,
    nouns: tuple[str, ...] = DEFAULT_NOUNS,
) -> str:
    """Return the first slug for which ``is_taken(slug) == False``.

    Retries up to ``max_attempts`` times.

    Raises:
        SlugCollisionError: after ``max_attempts`` consecutive collisions.
    """
```

### 2.4 Word lists

Stored as module-level tuples in `slug_words.py`:

- `DEFAULT_ADJECTIVES: tuple[str, ...]` — ≥ 64 curated entries.
- `DEFAULT_NOUNS: tuple[str, ...]` — ≥ 64 curated entries.

Constraints on every word in both lists:

- Lowercase ASCII letters only (`^[a-z]+$`).
- Length 3–10 characters.
- SFW / non-political / non-offensive.
- No word appears in both lists.

Total slug-space size with defaults: `|A| · |N| · 2^16 ≥ 64·64·65536 ≈ 268M`, which is orders of magnitude above any plausible collision rate for this single-user service (profile in master spec §1.3: ≤ 50 deploys/day, ≤ 100 simultaneous sites).

### 2.5 Length bound proof

A slug `{adj}-{noun}-{4hex}` with adj ≤ 10, noun ≤ 10, hex = 4 → `10 + 1 + 10 + 1 + 4 = 26`. The regex allows 3–63 chars, so defaults are comfortably under the cap even for the longest possible words.

---

## 3. Acceptance Criteria

1. `generate_slug()` always returns a string matching `SLUG_REGEX`.
2. `generate_slug()` has the form `{adjective}-{noun}-{4 hex chars}`.
3. `generate_slug()` is deterministic when `rng` is a function returning fixed bytes.
4. `generate_slug()` produces different outputs for different `rng` outputs.
5. `generate_unique_slug()` returns on the first try when `is_taken` returns `False` immediately.
6. `generate_unique_slug()` retries through a sequence of `True, True, True, False` and returns on the 4th call.
7. `generate_unique_slug()` raises `SlugCollisionError` when `is_taken` returns `True` for all 5 default attempts.
8. `generate_unique_slug()` respects a custom `max_attempts` parameter.
9. `validate_slug` accepts a minimally-valid slug (`"a-b"` or any 3-char slug matching the regex).
10. `validate_slug` accepts a slug at the upper length bound (63 chars).
11. `validate_slug` rejects an empty string (`InvalidSlugError`).
12. `validate_slug` rejects a slug shorter than 3 chars.
13. `validate_slug` rejects a slug longer than 63 chars.
14. `validate_slug` rejects a slug starting with `-` (first char must be `[a-z0-9]`).
15. `validate_slug` rejects uppercase characters.
16. `validate_slug` rejects underscores.
17. `validate_slug` rejects non-ASCII characters.
18. `validate_slug` rejects dots, slashes, spaces.
19. `DEFAULT_ADJECTIVES` and `DEFAULT_NOUNS` each contain ≥ 64 entries.
20. Every word in both lists matches `^[a-z]{3,10}$`.
21. The two lists are disjoint (no word appears in both).
22. `InvalidSlugError` is a subclass of `ValueError` (so callers that catch `ValueError` also catch it).
23. `SlugCollisionError` is a subclass of `RuntimeError`.

---

## 4. Test List

All in `tests/unit/test_slug.py`.

### 4.1 generate_slug

- [ ] `test_generate_slug_matches_regex`
- [ ] `test_generate_slug_has_three_hyphen_separated_parts`
- [ ] `test_generate_slug_last_part_is_4_hex_lowercase`
- [ ] `test_generate_slug_is_deterministic_given_rng`
- [ ] `test_generate_slug_varies_with_different_rng_output`
- [ ] `test_generate_slug_picks_adjective_from_list`
- [ ] `test_generate_slug_picks_noun_from_list`

### 4.2 generate_unique_slug

- [ ] `test_generate_unique_slug_first_try_not_taken`
- [ ] `test_generate_unique_slug_retries_then_succeeds`
- [ ] `test_generate_unique_slug_raises_after_5_collisions`
- [ ] `test_generate_unique_slug_raises_after_custom_max_attempts`
- [ ] `test_generate_unique_slug_passes_each_candidate_to_is_taken`

### 4.3 validate_slug

- [ ] `test_validate_slug_accepts_minimal`
- [ ] `test_validate_slug_accepts_max_length`
- [ ] `test_validate_slug_accepts_digits`
- [ ] `test_validate_slug_accepts_typical_generated_slug`
- [ ] `test_validate_slug_rejects_empty`
- [ ] `test_validate_slug_rejects_too_short`
- [ ] `test_validate_slug_rejects_too_long`
- [ ] `test_validate_slug_rejects_leading_dash`
- [ ] `test_validate_slug_rejects_uppercase`
- [ ] `test_validate_slug_rejects_underscore`
- [ ] `test_validate_slug_rejects_non_ascii`
- [ ] `test_validate_slug_rejects_dots_and_slashes`

### 4.4 Word lists

- [ ] `test_wordlists_minimum_size`
- [ ] `test_wordlists_all_lowercase_alpha_3_to_10_chars`
- [ ] `test_wordlists_are_disjoint`

### 4.5 Contract

- [ ] `test_invalid_slug_error_is_value_error`
- [ ] `test_slug_collision_error_is_runtime_error`

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- Empty word list passed explicitly: out of scope (caller's bug; stdlib indexing will raise). Document in the docstring, do not defend.
- `rng` returning fewer bytes than asked: out of scope (contract breach).

### 5.2 Deferred

- **Wiring `is_taken` to SQLite**: step 4+ (DB module). This module only provides the retry loop skeleton.
- **Reserved slug blocklist** (e.g. `api`, `www`, `admin`): not in master spec. If we add it later, extend `validate_slug`. For now the wildcard is only `*.preview.<domain>`, and `api.preview.<domain>` is the API host — a site slug of `api` would not actually collide because Traefik routes based on hostname, not subdomain presence. Still, blocking it would avoid user confusion. **Deferred** until operational experience suggests it.

### 5.3 Explicitly non-goal

- **Profanity filter on the random combinations** — with ≥64 curated safe adjectives × 64 curated safe nouns, no pair can read as profanity. Word lists are the line of defense, not a runtime filter.

---

## 6. Open Questions

(None — mini-spec approved.)

~~Q1: Should `generate_slug` take the rng as a callable `Callable[[int], bytes]` or as a raw seed/byte buffer?~~
→ Callable. That's how `secrets.token_bytes` is exposed and it lets the test pass a deterministic stub (`lambda n: b"\x00" * n`). Taking raw bytes forces the caller to know how many are needed, leaking implementation detail.

~~Q2: `max_attempts=5` per the master spec, but should `generate_unique_slug` count the initial attempt as 1 or 0?~~
→ Count it as 1. So `max_attempts=5` means up to 5 calls to `is_taken`, 5 distinct slug candidates considered. That matches master spec §5.3 "retry automatico fino a 5 volte, poi 500" naturally.

~~Q3: Should we ship a Dockerfile-friendly pre-compiled regex or compile at import?~~
→ Compile at import, module-level. Cost is negligible; no reason to complicate.

---

## 7. Done When

- [ ] All 29 tests in §4 committed and green on CI.
- [ ] Coverage ≥ 95% on `slug.py` (small pure module; should be trivially near 100%).
- [ ] Ruff clean on `slug.py`, `slug_words.py`, and `test_slug.py`.
- [ ] `make check` green locally.
- [ ] Roadmap table in [`CLAUDE.md`](../../CLAUDE.md) §8 updated (Step 3 → ✅).
- [ ] This file's Status flipped to ✅.
