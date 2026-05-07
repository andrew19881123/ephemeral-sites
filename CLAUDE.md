# CLAUDE.md

This file is the entry point for any AI agent (Claude Code or equivalent) working on **ephemeral-sites**. It encodes the two non-negotiable rules of this project and links to everything an agent needs to be productive without re-reading the whole codebase.

> **Two rules, in this order:**
>
> 1. **Spec-driven.** The [`docs/SPEC.md`](docs/SPEC.md) is the single source of truth. Never invent features or deviate from it silently. If reality forces a deviation, amend the spec first, then the code. **Every step must also have its own written mini-spec in `docs/steps/step-N-<name>.md` before any test or code is written** — see §2.4.
> 2. **TDD, always.** Red → Green → Refactor for every behavior change. A test that does not yet fail is not a test — it is a decoration.

Violating either rule is considered a defect, even if the code compiles and the feature "works."

**Document triad**: for every behavior that lands in `main` you must be able to point to three artefacts:

- **What** — the step mini-spec (`docs/steps/step-N-*.md`) or a spec amendment (`docs/SPEC.md`).
- **How it's verified** — the tests (`tests/unit/...`, `tests/integration/...`).
- **The implementation** — the code (`src/ephemeral_sites/...`).

If one of the three is missing, the change is incomplete.

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

### 2.4 Step mini-specs (write the spec before the test)

The master spec (`docs/SPEC.md`) describes **what the system does**. It intentionally does not dictate the *exact shape* of each step's public surface — function signatures, exception types, exact rejection reasons, fixture structure. That is the job of the **step mini-spec**.

**Rule**: before writing a single test or line of code for roadmap step N, you must write `docs/steps/step-N-<short-name>.md` (template below) and commit it. The mini-spec answers these questions:

- **Goal**: what this step delivers, in one paragraph, referencing the relevant master-spec sections.
- **Public API / contract**: function signatures, classes, HTTP endpoints, exception types, return shapes. Concrete enough that two developers would produce API-compatible implementations from it.
- **Acceptance criteria**: a numbered list of observable behaviors. Each item maps 1:1 to at least one test.
- **Test list**: the tests you commit to writing, by name and file path. This is the red-list for the TDD cycle.
- **Edge cases & out-of-scope**: what must be handled, what is deferred to a later step or to v1.1 — cite the master-spec section that justifies it.
- **Open questions**: anything unclear in the master spec that must be resolved before coding. If non-empty, the mini-spec is not approved yet; do not start coding. Resolve them, either by reading the spec more carefully or by asking the spec owner.

A template lives at [`docs/steps/_template.md`](docs/steps/_template.md) — copy it, rename, fill in, commit.

**Commit sequence for a step**:

```
docs(step-N): mini-spec for <feature>              # the what
test(step-N): add red tests for <feature>          # the how (verified)
feat(step-N): implement <feature>                  # the code (green)
refactor(step-N): <optional>                       # cleanup (still green)
docs(step-N): mark complete in CLAUDE.md §8        # update roadmap status
```

The first commit (`docs(step-N): mini-spec`) is mandatory. The whole point is that **the step is documented before it's built** — so the documentation never lags the code. If you discover mid-implementation that the mini-spec was wrong, stop, amend the mini-spec, commit the amendment, then continue.

### 2.5 Master-spec vs. mini-spec — when to put what where

- **Master spec (`docs/SPEC.md`)** holds user-visible contracts: the HTTP API, security invariants, Helm values, architecture decisions. Changes to it are a versioned product decision — commit them separately, discuss if non-trivial.
- **Step mini-spec (`docs/steps/step-N-*.md`)** holds implementation-level contracts: module boundaries, error taxonomy, test list. Changes are part of the normal implementation flow.

When in doubt: if the change would affect a user of the HTTP API or someone deploying via Helm, it goes in the master spec. Otherwise it goes in the step spec.

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

Run locally. The repo ships a `Makefile` that wraps the exact same commands the CI uses. Targets:

```bash
make install        # installs dev deps (poetry if available, else pip)
make check          # lint + test + coverage — the full pre-push gate
make lint           # just ruff check + format --check
make test           # pytest with coverage report
make test-fast      # pytest without coverage, stop on first failure
make test-security  # only @pytest.mark.security tests
make format         # auto-fix formatting and lint issues
make docker-build   # build the production image
make clean          # wipe caches
```

**Rule**: run `make check` before every `git push`. It's ~1s on a warm venv vs. ~30s for a CI round-trip. CI is the safety net, not the dev loop.

Raw equivalents (if you don't have `make`):

```bash
ruff check . && ruff format --check .
pytest -v --cov --cov-report=term-missing
```

CI (`.github/workflows/test.yml`) runs these exact commands via Poetry on Python 3.12. If `make check` is green locally on Python ≥3.11, CI will be green — the only gap is dependency resolution via `poetry.lock`, which is why `make install` prefers poetry when available.

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

## 6bis. Secrets handling (local, never committed)

Local secrets that don't belong in `.env` or in Kubernetes Secrets — personal tokens the owner uses from the dev machine — live in the **gitignored** `.secret/` directory at the repo root.

### 6bis.1 Current contents

| File | Contents | Scope |
|------|----------|-------|
| `.secret/github_token.env` | `GH_TOKEN` / `GITHUB_TOKEN` — GitHub Personal Access Token for `andrew19881123/ephemeral-sites` (push, PR, Actions read) | Used by `gh` CLI, `git push`, CI debugging from the dev machine |

### 6bis.2 Rules

- The directory is protected by a matching line in `.gitignore` (`.secret/`). **Never remove that line.** Verify before any commit: `git check-ignore -v .secret/github_token.env`.
- Permissions: `chmod 700 .secret` (dir), `chmod 600` (files). If you clone fresh, re-apply them.
- **Never** print, echo, `cat`, paste, or otherwise emit the token contents — not in commits, not in log lines, not in chat replies, not in commit messages, not in CI output.
- **Never** store secrets in `bot_settings.json`, `pyproject.toml`, Dockerfile, Helm values, or any tracked file.
- If a token is exposed by accident (leaked log, pushed file, shared in chat): **rotate immediately** at <https://github.com/settings/tokens>, replace the file, then grep history (`git log --all -S 'github_pat_'`) to confirm no trace remains.

### 6bis.3 Usage patterns

Preferred — inline, no env pollution of child processes you don't control:

```bash
GH_TOKEN=$(grep ^GH_TOKEN .secret/github_token.env | cut -d= -f2) \
  gh run list --repo andrew19881123/ephemeral-sites
```

Acceptable — source into current shell when you will run many commands in a row:

```bash
source .secret/github_token.env
gh run list --repo andrew19881123/ephemeral-sites
gh pr list  --repo andrew19881123/ephemeral-sites
# ... remember the token is still in env until `unset GH_TOKEN GITHUB_TOKEN`
```

For `git push` against a private repo, prefer a remote URL that does NOT embed the token; let `gh` or a credential helper supply it:

```bash
gh auth setup-git      # once, uses GH_TOKEN from env
git push origin main   # credentials resolved via helper
```

Embedding the token directly in the remote URL (`https://oauth2:$TOKEN@github.com/...`) works but risks leaking into shell history and `.git/config`. If you must use it, pass the URL explicitly on the command line (does not persist), never via `git remote set-url`.

### 6bis.4 What `.secret/` is NOT for

- Kubernetes Secrets → those are generated at deploy time (`kubectl create secret ...`, see spec §12.2).
- CI secrets → use GitHub Actions `secrets.*` context.
- Application runtime config → use env vars sourced from `.env` (also gitignored; see `.env.example` once it exists) or a mounted Secret.

If you find yourself putting a non-token there (config, feature flags, ...), you're using the wrong channel.

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

### 7.3 CI and the local gate

`.github/workflows/test.yml` runs:

1. Ruff `check` + `format --check`
2. Pytest with coverage
3. (Later steps will add) Docker build, Helm lint, Helm template validation

A red CI blocks merge. No "we'll fix it in the next PR" — fix it in this one, or revert.

**Before every push**, run `make check` locally (see §5.1 and the Makefile). It's the same gate in ~1s instead of ~30s. Treat CI as the safety net, not the dev loop. If `make check` is green and CI is red, something is genuinely different (usually a missing dep in `pyproject.toml` vs. the local venv) — fix the root cause, not the symptom.

---

## 8. Roadmap — where you are

The 18-step sequence is in [`docs/SPEC.md` §16.1](docs/SPEC.md). Current status:

| Step | Deliverable | Status |
|------|-------------|--------|
| 1 | Scaffolding (pyproject, Dockerfile, CI, smoke tests) | ✅ Done — commit `08bf382` |
| 2 | ZIP validator (30 tests, 95% cov) | ✅ Done — `d6cddae` (mini-spec), `b1df6a2` (red), `f612aca` (feat) |
| 3 | Slug generator (32 tests, 100% cov) | ✅ Done — `2cb5c21` (mini-spec), `df55898` (red), `d0a3026` (feat) |
| 4 | DB + migrations (27 tests, 92% cov) | ✅ Done — `133be00` (mini-spec), `58c4fef` (red), `c407481` (feat) |
| 5 | Storage atomic swap (22 tests, 81% cov, renameat2 zero-404) | ✅ Done — `cf9914d` (mini-spec), `ac12be5` (red), `2900c25` (feat) |
| 6 | Auth (35 tests, 95% cov) | ✅ Done — `2b16a2f` (mini-spec), `1317ed4` (red), `ffe4c6c` (feat) |
| 7 | Quota check (16 tests, 92% cov) | ✅ Done — `70c4a63` (mini-spec), `a1c6701` (red), `d998ba5` (feat) |
| 8 | API PUT upsert (20 tests, 90.5% cov, FastAPI + request_id + typed errors) | ✅ Done — `197e023` (mini-spec), `93a9ca1` (red), `2f9a962` (feat) |
| 9 | API CRUD (22 tests, 91% cov, POST+GET+DELETE+PATCH+LIST) | ✅ Done — `4326504` (mini-spec), `51288f1` (red), `b2b7b99` (feat) |
| 10 | Runtime config injection (5 tests, carry-forward on replace) | ✅ Done — `0a022d6` (mini-spec+red+feat) |
| 11 | Static server + SPA fallback + security headers (26 tests, 91.5% cov) | ✅ Done — `8db59de` |
| 12 | Password protection (9 tests, HTTP Basic against bcrypt hash) | ✅ Done — `a649c3e` |
| 13 | Cleanup CronJob (7 tests, Monday event_log purge) | ✅ Done — `acaa52b` |
| 14 | Metrics + health endpoints (10 tests, Prometheus + healthz/readyz) | ✅ Done — `304a6b1` |
| 15 | Helm chart (helm lint clean, 8 resources rendered) | ✅ Done — `fd79297` |
| 16 | CLI bash helpers (deploy/delete/list/extend, pure bash+curl) | ✅ Done — `3bfdde2` |
| 17 | README + docs | ⏳ Next |
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
3. Open the master-spec sections that govern it (e.g., for step 2: §7.1 + §11.3).
4. **Write the step mini-spec** in `docs/steps/step-N-<name>.md` (see §2.4 and [`docs/steps/_template.md`](docs/steps/_template.md)). Commit it (`docs(step-N): mini-spec for ...`) before touching tests or code.
5. `make install` (idempotent; uses poetry if present, else pip).
6. `make check` — confirm green baseline locally (~1s).
7. **Red** — add the failing tests listed in the mini-spec; run `make test-fast` and confirm the failure mode is the expected one; commit.
8. **Green** — implement the minimum to pass; run `make check`; commit only if green.
9. **Refactor** if needed; run `make check` after every change; commit.
10. Push only when `make check` is green. CI is the safety net.
11. Update the roadmap table (§8 of this file) in a final `docs: mark step N complete` commit.

If `git status` is dirty, the previous session left work incomplete — read the last commit message and continue from there, do not overwrite.

**Need repo credentials?** See §6bis. The `.secret/` directory has the PAT if you need to push to `main`, query CI via `gh`, or debug Actions. Never echo, never commit.

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

- [`docs/SPEC.md`](docs/SPEC.md) — the master spec, the law
- [`docs/steps/`](docs/steps/) — per-step mini-specs (one file per roadmap item; write first, test second, code third)
- [`docs/steps/_template.md`](docs/steps/_template.md) — mini-spec skeleton to copy
- [`pyproject.toml`](pyproject.toml) — deps, pytest, ruff, coverage config
- [`Makefile`](Makefile) — local quality gate (`make check` before every push)
- [`.github/workflows/test.yml`](.github/workflows/test.yml) — CI (mirror of `make check`)
- [`README.md`](README.md) — user-facing quickstart
- `.secret/` (gitignored, not in repo tree) — local tokens, see §6bis
- Upstream docs: [FastAPI](https://fastapi.tiangolo.com/), [cert-manager](https://cert-manager.io/docs/), [Traefik](https://doc.traefik.io/traefik/)
