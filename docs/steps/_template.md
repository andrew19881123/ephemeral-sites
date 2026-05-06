# Step N — &lt;short name&gt;

> Copy this file to `docs/steps/step-N-<short-name>.md`, fill every section, commit as `docs(step-N): mini-spec for <name>` **before** writing any test or code. See [`CLAUDE.md`](../../CLAUDE.md) §2.4.

**Master spec sections**: §&lt;...&gt; (link the relevant sections of [`docs/SPEC.md`](../SPEC.md))
**Roadmap entry**: [§16.1 step N](../SPEC.md)
**Status**: ⏳ Draft | 🟡 Approved, in progress | ✅ Complete
**Owner**: &lt;name&gt;

---

## 1. Goal

One paragraph. What does this step deliver, and why now? Reference the user-visible outcome or the downstream step that is blocked without it.

---

## 2. Public API / Contract

Concrete enough that two independent implementations would be API-compatible.

### 2.1 Module layout

Files created or touched:

- `src/ephemeral_sites/&lt;module&gt;.py` — &lt;responsibility&gt;
- `tests/unit/&lt;module&gt;/test_&lt;aspect&gt;.py` — &lt;what it covers&gt;

### 2.2 Function / class signatures

```python
def do_thing(input: X, *, option: Y = default) -> Z:
    """One-line summary.

    Raises:
        SomeError: when &lt;condition&gt;.
    """
```

### 2.3 Exceptions / error taxonomy

| Exception | When raised | Mapped to HTTP |
|-----------|-------------|----------------|
| `FooError` | invalid input | 400 |
| `QuotaError` | over global quota | 507 |

### 2.4 Data structures

Pydantic models, dataclasses, or TypedDicts used at the boundary.

---

## 3. Acceptance Criteria

Observable behaviors. Each line maps 1:1 to at least one test in §4.

1. Given X, when Y, then Z.
2. Given X', when Y', then Z'.
3. ...

---

## 4. Test List

The red-list for the TDD cycle. Write these first, watch them fail, then implement.

### 4.1 Unit tests

- [ ] `tests/unit/test_foo.py::test_accepts_valid_input` — happy path
- [ ] `tests/unit/test_foo.py::test_rejects_empty_input` — edge
- [ ] `tests/unit/test_foo.py::test_raises_on_X` — error path

### 4.2 Integration tests (if applicable)

- [ ] `tests/integration/test_foo_flow.py::test_end_to_end_with_db` — uses `tmp_path` and real SQLite

### 4.3 Security tests (if applicable)

Marked with `@pytest.mark.security`:

- [ ] `tests/unit/test_foo.py::test_rejects_malicious_input_A`
- [ ] `tests/unit/test_foo.py::test_rejects_malicious_input_B`

---

## 5. Edge Cases & Out of Scope

### 5.1 Must handle

- &lt;case&gt; — &lt;behavior&gt;

### 5.2 Deferred

- &lt;case&gt; → step M, or v1.1 per master spec §&lt;section&gt;.

### 5.3 Explicitly non-goal

Only if the master spec already says so. Cite the section.

---

## 6. Open Questions

If this list is non-empty, the mini-spec is NOT approved; resolve before coding.

- [ ] &lt;question&gt;

---

## 7. Done When

- [ ] All tests in §4 committed and green on CI.
- [ ] Coverage threshold holds (overall ≥ 80%, this module ≥ 90% if it's business logic).
- [ ] Ruff clean on changed files.
- [ ] Roadmap table in [`CLAUDE.md`](../../CLAUDE.md) §8 updated.
- [ ] This file's Status field flipped to ✅.
