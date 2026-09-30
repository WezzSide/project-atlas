# Project Atlas Copilot Instructions

Use `AGENTS.md` and `CLAUDE.md` as the primary implementation contracts. This file encodes Atlas governance shorthand and merge-discipline defaults for Copilot-enabled flows.

## Atlas governance vocabulary and hard boundaries

- `EXACT_HEAD` means exact commit identity (`HEAD`) and matching tree identity (`HEAD^{tree}`).
- Any `HEAD` movement invalidates prior exact-head certification receipts.
- `CI PASS != IV PASS`; independent verification is a separate gate.
- `implementer != independent verifier`; no self-certification.
- `MERGE_ELIGIBLE != MERGED`; `MERGED != SEALED`.
- `MODEL OUTPUT != AUTHORITY`; repository truth and receipts win.
- `PREP != IMPLEMENTED`; never over-claim package maturity.
- `UI != CANONICAL TRUTH`; validate against source and contracts.

## Operational requirements for governed changes

- Pin `HEAD`/`TREE` at session start and re-check before decisions.
- Keep claim boundaries explicit; fail closed on ambiguity.
- For merge decisions, re-read evidence at decision instant; stale evidence is invalid.
- No stale verdict reuse is allowed across head/tree movement.
- Treat `P0` and `P1` findings as merge gates.
- Do not infer owner policy; use explicit repository policy only.
- Keep unrelated lanes isolated; do not mutate paused/owner-gated lanes.
- Waiting is lane-local and never a global stop.
- Preserve evidence lineage: predecessor/successor links must be explicit.

## Tooling and MCP boundaries

- Prefer local-first, reproducible tools.
- Do not place PATs or long-lived secrets in tracked files or MCP config.
- Use runtime auth indirection where possible (for example wrapper scripts reading `gh auth token`).
- Treat Codebase Memory as derived intelligence; never canonical merge evidence.

## Atlas role profiles

Role profiles are maintained in `docs/agent-skills/`:

- `atlas-governor`
- `atlas-independent-verifier`
- `atlas-implementer`
- `atlas-merge-guardian`
- `atlas-evidence-bundle`
- `atlas-frontier-coordinator`
- `atlas-successor-discovery`
- `atlas-postmerge-seal`
- `atlas-security-review`
- `atlas-design-review`
