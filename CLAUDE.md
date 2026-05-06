# CLAUDE.md

This file is the entry point for any AI agent (Claude Code or equivalent) working on **ephemeral-sites**. It encodes the two non-negotiable rules of this project and links to everything an agent needs to be productive without re-reading the whole codebase.

> **Two rules, in this order:**
>
> 1. **Spec-driven.** The [`docs/SPEC.md`](docs/SPEC.md) is the single source of truth. Never invent features or deviate from it silently. If reality forces a deviation, amend the spec first, then the code.
> 2. **TDD, always.** Red → Green → Refactor for every behavior change. A test that does not yet fail is not a test — it is a decoration.

Violating either rule is considered a defect, even if the code compiles and the feature "works."

---

## 1. Project overview

`ephemeral-sites` is a self-hosted **single-user** service for Kubernetes (target: k3s on GCP) that publishes temporary static SPA sites from a ZIP upload. An owner sends a ZIP to `PUT /api/v1/sites/{slug}` and gets back a public HTTPS URL on a wildcard subdomain that expires after a configurable TTL.

- **Stack**: Python 3.12, FastAPI, SQLite (WAL), PVC RWO, Traefik, cert-manager + Let's Encrypt wildcard (Cloudflare DNS-01)
- **Deploy unit**: Helm chart → 1 replica Deployment (API + static server in same pod) + CronJob cleanup
- **Threat model, limits, endpoint list, data model, values.yaml, roadmap**: all in [`docs/SPEC.md`](docs/SPEC.md)

You do not need to memorize the spec. You need to open it, find the relevant section, and follow it exactly.

---

## 2. Spec-driven development (Rule #1)

### 2.1 Before writing any code

1. Identify the roadmap step you are implementing — see [`docs/SPEC.md` §16.1](docs/SPEC.md).
2. Read the spec sections that govern that step (e.g., for the validator: §7.1 + §11.3).
3. List the acceptance criteria as a TODO before touching code.
4. If something in the spec is ambiguous or wrong: **stop and ask**. Do not guess.

### 2.2 Deviations

If implementation reveals that a spec requirement is wrong, impossible, or incomplete:

1. Stop coding.
2. Open the spec file, amend the relevant section with the correction (as a git change, not a comment in code).
3. Commit the spec amendment **separately** from the code change, with a message like `docs(spec): clarify ZIP flattening behavior for nested top-level`.
4. Then implement.

The spec file in-repo is versioned exactly like code. Reviewers review both.

### 2.3 Placeholders that must NOT leak into code

The spec's §13 "Open Points" lists four placeholders still unresolved: domain name, GCP static IP, owner email for Let's Encrypt, GitHub org. Keep them as Helm values (already templated in `values.yaml`) or env vars — **never hard-coded** in Python, Dockerfile, or chart templates.

---

## 3. TDD discipline (Rule #2)

### 3.1 The cycle

**Red** — write the test first. Run it. Confirm it fails for the right reason (usually `ImportError`, `AttributeError`, or a clean assertion failure; a `SyntaxError` means the test itself is broken, fix it before proceeding).

**Green** — write the minimum production code that makes the failing test pass. No extra functionality, no premature optimization, no refactors yet.

**Refactor** — with tests green, clean up: extract, rename, deduplicate. Run tests after every micro-change. Green must stay green.

### 3.2 Order of work inside a step

For every roadmap step (§16.1), the commit sequence looks like:

```
test: add red tests for <feature>         # tests fail, CI red (expected)
feat: implement <feature>                 # tests green, CI green
refactor: <optional cleanup>              # tests still green
```

Small steps are preferred — one behavior per red-green cycle. "Add validator" is not one step; "reject path traversal", "reject symlink", "reject zip bomb" are three.

### 3.3 When the test is hard to write

If you find yourself building elaborate mocks for something simple, the production code is probably too coupled. Refactor **first**:

- Inject dependencies instead of importing them inside the function.
- Make time and randomness injectable (parameters with sensible defaults).
- Extract pure functions from effectful ones.

### 3.4 What is NOT excused from TDD

- "Just a config file" — if `values.yaml` gains a field that changes behavior, write a helm `template` rendering test or a config-loading test.
- "It's just glue code" — the glue is where race conditions live.
- "It's only logging" — if the log line is a compliance/audit artifact, assert on it.

### 3.5 Acceptable TDD exceptions (document them in the commit)

- **Exploratory spike** — throwaway code in a feature branch that never reaches `main` without tests.
- **Docs-only change** — `README.md`, `CLAUDE.md`, `docs/*`, docstrings.
- **Dependency pin bump** with no behavior change.

---

## 4. Testing strategy

Full strategy: [`docs/SPEC.md` §11](docs/SPEC.md). Summary:

| Level | Location | Speed | Uses |
|-------|----------|-------|------|
| Unit | `tests/unit/` | <100ms each | Mocks, pure functions, no network |
| Integration | `tests/integration/` | 100ms-2s | Real tempdir + real SQLite (`tmp_path` fixture), TestClient for FastAPI |
| E2E | manual / `helm test` | minutes | Real k3d cluster, real curl |

### 4.1 Markers

Declared in `pyproject.toml` `[tool.pytest.ini_options].markers`:

- `@pytest.mark.unit`
- `@pytest.mark.integration`
- `@pytest.mark.e2e` (excluded from normal runs — opt-in)
- `@pytest.mark.security` (zip bombs, path traversal, symlink — run these on every push)

### 4.2 Coverage gates

From `pyproject.toml`:

- Overall floor: **80%** (enforced by `--cov-fail-under=80` in CI, via `fail_under = 80` in `tool.coverage.report`).
- Business logic (`validator`, `auth`, `storage`, `slug`, `quota`): aim **≥90%**.

Never lower the gate to make CI green. Fix the tests or the code.

### 4.3 Fixtures

Test fixtures (ZIP files used across many tests) live in `tests/fixtures/`:

- `valid_spa.zip` — minimal SPA with `index.html` at root
- `valid_spa_with_subfolder.zip` — content inside a single top-level dir (validator must flatten)
- `zip_bomb.zip` — a small ratio-bomb (NOT the canonical 42.zip, to keep repo size sane)
- `path_traversal.zip` — contains `../../etc/passwd`
- `symlink.zip` — contains a symlink to `/etc/shadow`
- `no_index.zip` — valid ZIP without `index.html`

**Generation**: whenever possible, generate fixtures programmatically inside the test (small, explicit, reviewable) rather than committing opaque binary blobs. Commit a blob only when the crafted bytes (e.g., compression ratio for a bomb) cannot be produced readably from code.

---

## 5. Code conventions

### 5.1 Language & tooling

- **Python 3.12** exactly — newer `match`, newer typing syntax OK; nothing that would break on 3.12.
- **Poetry** for deps (`pyproject.toml` is the source of truth).
- **Ruff** for lint + format (config in `pyproject.toml`, line length 100).
- **pytest** for tests, `pytest-asyncio` for async.
- **No `mypy`** in v1 unless it pays for itself — typing hints as documentation, not enforced.

Run locally:

```bash
poetry install --with dev
poetry run pytest -v
poetry run ruff check .
poetry run ruff format --check .
```

CI mirrors this exactly (`.github/workflows/test.yml`).

### 5.2 Style nits

- f-strings over `%`/`.format()`.
- Explicit `return` at function end when mixing return/no-return branches.
- No bare `except:` — always `except SpecificError` or `except Exception as e`.
- Log at `INFO` for state changes, `DEBUG` for traces, `WARNING` for recoverable failures, `ERROR` for unexpected. Never log secrets (see §6).
- No emojis in code, strings, or log lines. Reserve them for user-facing docs and chat replies (if any).

### 5.3 Module layout

The code tree is fixed by [`docs/SPEC.md` §10](docs/SPEC.md). Do not invent new top-level modules. If something genuinely does not fit, amend the spec first.

### 5.4 Error handling in the API

- Public error response shape: `{"error": "<slug>", "detail": "<human string>", "request_id": "<uuid>"}`.
- Never expose filesystem paths, stack traces, or internal IDs in `detail`.
- Correlate with logs via `request_id` middleware (see spec §5 and §7.6).

---

## 6. Security-first mindset

The spec §7 is not optional reading. Key invariants that MUST be reflected in tests:

1. **ZIP validator rejects, never sanitizes silently.** Anything unexpected (path traversal, symlink, zip bomb, non-whitelisted extension, absolute path, Windows drive letter) → HTTP 400. A validator that "fixes" bad input is a validator that drops its guard.
2. **Bcrypt for all hashes** (API keys, delete tokens, passwords). Cost=12. Timing-safe comparison via `bcrypt.checkpw`.
3. **No secrets in logs.** Filter middleware strips `Authorization`, `X-Delete-Token`, `password` form field. Add a unit test that asserts a request with those fields produces a log line that does NOT contain the secret.
4. **No secrets in error responses.** Verified by a test that intentionally triggers each error path and greps the response body for the secret string.
5. **Atomic writes only.** Directory swap via `flock` + `rename`. The test `test_overwrite_no_404_window` exists for a reason — never weaken it.
6. **Container hardening** (spec §7.3) is enforced by the Helm chart. Any change to `securityContext` needs a matching `helm template | yq` test.

---

## 7. Git workflow

### 7.1 Commits

- **Conventional-commits prefix**: `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`, `ci:`, `build:`.
- Subject line ≤ 72 chars, imperative mood ("add validator", not "added validator").
- Body explains **why**, not what (the diff shows what).
- Every behavior-changing commit references the spec section and/or roadmap step: `feat: reject path traversal in ZIP (spec §7.1, step 2)`.
- Never amend a commit that has been pushed to `main`. Never force-push `main`.

### 7.2 Branches & PRs

- `main` is protected, green CI required.
- Feature branches: `step-N-<slug>` (e.g., `step-2-validator`).
- PRs: scope = one roadmap step, one red-green-refactor cycle per commit inside.
- Self-review the diff before asking for review: "would I accept this from someone else?"

### 7.3 CI

`.github/workflows/test.yml` runs:

1. Ruff `check` + `format --check`
2. Pytest with coverage
3. (Later steps will add) Docker build, Helm lint, Helm template validation

A red CI blocks merge. No "we'll fix it in the next PR" — fix it in this one, or revert.

---

## 8. Roadmap — where you are

The 18-step sequence is in [`docs/SPEC.md` §16.1](docs/SPEC.md). Current status:

| Step | Deliverable | Status |
|------|-------------|--------|
| 1 | Scaffolding (pyproject, Dockerfile, CI, smoke tests) | ✅ Done — commit `08bf382` |
| 2 | ZIP validator | ⏳ Next |
| 3 | Slug generator | Pending |
| 4 | DB + migrations | Pending |
| 5 | Storage atomic swap | Pending |
| 6 | Auth (bcrypt, API key, delete token) | Pending |
| 7 | Quota check | Pending |
| 8 | API PUT upsert | Pending |
| 9 | API CRUD (GET/DELETE/PATCH/POST/LIST) | Pending |
| 10 | Runtime config injection | Pending |
| 11 | Static server + SPA fallback + security headers | Pending |
| 12 | Password protection | Pending |
| 13 | Cleanup CronJob | Pending |
| 14 | Metrics + health endpoints | Pending |
| 15 | Helm chart | Pending |
| 16 | CLI bash helpers | Pending |
| 17 | README + docs | Pending |
| 18 | E2E on k3d | Pending |

**Update this table after every step.** Out-of-date status is misinformation — worse than no status.

### 8.1 Step acceptance checklist

Before marking a step ✅:

- [ ] All spec-mandated tests for the step are present and green (spec §11.3).
- [ ] Coverage floor (80% overall, 90% for the new business module) holds.
- [ ] `ruff check` + `ruff format --check` clean.
- [ ] CI is green on `main`.
- [ ] Spec sections touched by this step are still accurate; if not, spec commit first.
- [ ] The table above is updated in the same PR.

---

## 9. How to start a work session

New Claude session, fresh context, user says "continue ephemeral-sites"? Do this:

1. Read this file (done — you're here).
2. Open [`docs/SPEC.md`](docs/SPEC.md), jump to §16.1 roadmap, find the next `⏳` step.
3. Open the spec sections that govern it (e.g., for step 2: §7.1 + §11.3).
4. `poetry install --with dev` (idempotent).
5. `poetry run pytest -v` — confirm green baseline.
6. Start the red-green-refactor cycle for the first sub-behavior of the step.
7. Commit (see §7.1), push, watch CI.

If `git status` is dirty, the previous session left work incomplete — read the last commit message and continue from there, do not overwrite.

---

## 10. What this project is NOT

Hard "no"s from [`docs/SPEC.md` §14](docs/SPEC.md). Do not propose or implement any of these, even if a user asks:

- SSR / Node.js runtime
- Built-in build step (Webpack, Vite, ...)
- Database for hosted sites
- Multi-tenancy / workspaces / RBAC
- Git integration (push → deploy)
- Custom domains per site (only wildcard subdomain)
- OpenTelemetry tracing
- Inbound webhooks
- Web dashboard in v1 (deferred to v1.1 — see spec §13 OP-7)

For those use cases, users go to Netlify / Vercel / Kubero. `ephemeral-sites` does **one thing: serve static ephemeral SPA sites via API**.

---

## 11. References

- [`docs/SPEC.md`](docs/SPEC.md) — the spec, the law
- [`pyproject.toml`](pyproject.toml) — deps, pytest, ruff, coverage config
- [`.github/workflows/test.yml`](.github/workflows/test.yml) — CI
- [`README.md`](README.md) — user-facing quickstart
- Upstream docs: [FastAPI](https://fastapi.tiangolo.com/), [cert-manager](https://cert-manager.io/docs/), [Traefik](https://doc.traefik.io/traefik/)
