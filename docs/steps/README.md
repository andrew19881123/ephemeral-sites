# Step mini-specs

One file per roadmap step (see [`../SPEC.md` §16.1](../SPEC.md) for the master list). Each file is written **before** the corresponding test and code — see [`../../CLAUDE.md` §2.4](../../CLAUDE.md) for the rule and rationale.

## Naming

`step-N-<short-kebab-name>.md` — e.g. `step-2-validator.md`, `step-8-api-put-upsert.md`.

## Template

Copy [`_template.md`](_template.md), rename, fill every section, commit as:

```
docs(step-N): mini-spec for <name>
```

## Index (updated after each step)

| # | File | Status |
|---|------|--------|
| 1 | *(scaffolding — no mini-spec; behavior was trivial, covered by spec §16.1)* | ✅ |
| 2 | [`step-2-validator.md`](step-2-validator.md) | ✅ Done |
| 3 | [`step-3-slug-generator.md`](step-3-slug-generator.md) | ✅ Done |
| 4 | [`step-4-db-migrations.md`](step-4-db-migrations.md) | ✅ Done |
| 5 | [`step-5-storage.md`](step-5-storage.md) | ✅ Done |
| 6 | [`step-6-auth.md`](step-6-auth.md) | ✅ Done |
| 7 | [`step-7-quota.md`](step-7-quota.md) | 🟡 In progress |
| 8+ | — | Pending |
