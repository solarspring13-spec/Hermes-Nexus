# .maintainer/ — Maintainer-Only Assets

> **This directory is for project maintainers.**
> Contents are NOT part of the public release surface.

## Structure

| Directory | Purpose |
|-----------|---------|
| `sync/` | Sync scripts for downstream consumer repos (e.g. Wiki, CI templates) |
| `audits/` | Security audit records and token rotation logs |
| `private_raw/` | Pre-desensitization design documents, internal drafts (gitignored) |

## Conventions

1. **`private_raw/`** is gitignored — never commit raw design docs or token-bearing files.
2. **`audits/`** stores sanitized audit reports only (no live tokens).
3. **`sync/`** scripts are committed but not exposed in public README.

## Governance

This directory is governed by the Hermes-Nexus Governance Blueprint.
CTO approval required for structural changes to `.maintainer/`.

---

*Last updated: 2026-05-23 — P2 Directory Restructuring*
