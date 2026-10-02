# Project Atlas

**Coder Alpha north star:** Project Atlas is the persistent brain for AI-native
projects — Knowledge (Obsidian/Web), Context (agents), and Truth (evidence /
provenance / conflicts / UNKNOWN). Primary promise: never explain your project
to an AI twice. See `docs/product/CODER-ALPHA-NORTH-STAR.md` (D-037).

Project Atlas is a local-first, source-backed "project knowledge compiler" that
ingests approved documentation and evidence, extracts provenance-backed
concepts, and emits a deterministic, agent- and human-readable vault in an
Open Knowledge Format (OKF) profile.

This repository contains the Core implementation (Python 3.12+, src layout),
shared contracts, tests, CI, and a sibling governed documentation/control-plane
(subproject: `atlas-vault-documentation/`) that implements the agent skill and
session control surface.

Key principles
- No claim without traceable evidence: every generated concept references its
  originating source and provenance.
- Three-layer vault model: A (source evidence), B (canonical OKF concepts), C
  (synthesized portfolio intelligence). Generated summaries must preserve
  provenance and human-edited regions.
- Determinism & offline operation: byte-identical output for repeated runs; no
  Internet required for core functionality.
- Fail-closed safety: path safety, protected-region preservation, and secret
  quarantine are enforced.

Repository status (short)
- Release and maturity reality:
  - **Atlas 1.0 complete**
  - **Atlas 2.0 release-certified**
  - **Atlas 2.1 live productization layer** (including read-only MCP and ChatGPT bridge surfaces in Core runtime boundaries)
  - **Atlas 2.2 no longer PREP-only overall**: runtime capabilities are implemented and shipped per-package (`prep-frozen` vs `implementation-unlocked` in `docs/atlas-2.2/PACKAGE-MATURITY.json`).
- Coder Alpha entry path: `atlas connect .` (bind+compile) then derived
  overview/state lenses under `generated/answers/` (lens != authority).
- Core pipeline implemented: `discover` → `ingest` → `build-indexes` →
  `build-portfolio` → `validate`, with read-only read/query lenses layered on
  top (`query`, `ask2`, `kdiff`, `overview`, `state`).
- Shipped beyond the original Core slice: `build-portfolio` (derived portfolio
  intelligence plus an AS-2.0-TEMPORAL-001 bitemporal validity catalog under
  `generated/ops/bitemporal/`), `doctor` (environment/vault diagnostics),
  Ask Atlas 2 (`ask2` — project-scoped hybrid retrieval + a read-only context
  compiler that answers known/unknown/conflict honestly and never invents
  authority), Knowledge Diff / Time Machine (`kdiff` — read-only as-of reads
  and T1→T2 diffs over document-declared valid-time), `snapshot`/`restore`
  (backup/recovery), and a read-only LIVE_API (`live api-serve`, bound to
  127.0.0.1) exposing `/v1/conflicts` and `/v1/kdiff` (with a Web
  `#/time-machine` page under `apps/web`).
- A golden demo fixture (`tests/fixtures/demo/estate/harbor-api`) carries a
  real unresolved datastore conflict (PostgreSQL 15 vs 16) and real bitemporal
  Time Machine states; it is exercised by
  `tests/integration/test_as_demo_2_2_golden_fixture.py`.
- Tests: comprehensive unit and integration suites (`tests/unit/`,
  `tests/integration`); documented passing results in
  `WORKLOG.md`.
- `atlas-vault-documentation/` is a sibling deliverable (governed agent
  control-plane) and is intentionally excluded from the main lint/type scope.
- Recovery/identity runtime contract:
  - `atlas init` establishes canonical Vault identity in `.atlas/vault.json`.
  - `snapshot` remains non-minting.
  - `restore` preserves identity (does not rotate/mint).
  - Linux uses the POSIX dirfd-safe identity write path.
  - Windows uses the platform-specific atomic identity path introduced by `#320`.
- Sealed Golden Demo pin (ancestor of current `main`, not always HEAD):
  - `FINAL_DEMO_HEAD = 754bb266fa2d2ff39089c4e587c9b90eacd841fd`
  - `FINAL_DEMO_TREE = c481c1aa6ba408a16b176d5326f209d6a76b6c42`
  - `ATLAS_DEMO_2_2_PORTABLE_CANDIDATE = PASS`
  - `WINDOWS_DEMO_SEAL = PASS`
  - `ATLAS_DEMO_2_2_WORKING = YES`
  - `WINDOWS_STRANGER_PHASE_C = PASS`
- AS-OPT-GATE-001 merged (`#321`): governed experiment/promotion boundary;
  `ATLAS_OPT_WAKE_GATE = CLOSED`; `EVALUATOR_STABLE = YES`; wake remains `CLOSED` (OPEN_ELIGIBLE governance only).

Truth boundaries (do not overclaim)
- `PREP != IMPLEMENTED`
- `DEMO_FIXTURE != AUTHENTIC_PILOT`
- `DEMO != RELEASE`
- `UI != CANONICAL TRUTH`
- `MODEL OUTPUT != AUTHORITY`
- `PROMOTE_ELIGIBLE != MERGED/DEPLOYED/AUTHORITATIVE`
- `CODEX_VALIDATED = NO`
- `EXTERNAL_SECURITY_REVALIDATION_REQUIRED = YES`
- `ATLAS_DEMO_2_2_WORKING = YES` means the Golden Product Vertical Slice
  passed portable and Windows stranger validation; it does **not** mean
  `AUTHENTIC_PILOT = PASS`, `EXTERNAL_SECURITY_CERTIFICATION = PASS`, or
  `COMMERCIAL_GA = YES`.

Quickstart (developer)
1. Create and activate Python 3.12 venv:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
```

2. Install editable package with dev dependencies:

```bash
pip install -e ".[dev]"
```

3. Run the test/lint gates locally:

```bash
python -m pytest
python -m ruff check .
python -m mypy src
```

4. Try CLI smoke commands:

```bash
atlas --help
atlas version
atlas init --output .tmp/atlas-vault --dry-run
atlas init --output .tmp/atlas-vault
```

Core CLI workflow
- atlas init --output <dir> [--dry-run]  # create deterministic vault scaffold
- atlas discover --source <project-root> --output <manifest.json>
- atlas ingest --manifest <manifest.json> --vault <vault-dir> --source <project-root>
- atlas build-indexes --vault <vault-dir>
- atlas build-portfolio --vault <vault-dir>  # derived portfolio + bitemporal validity catalog
- atlas validate --vault <vault-dir>

Read & query lenses (read-only; never mutate the Vault)
- atlas query ... / atlas ask2 --vault <dir> --project <p> --question "..."
- atlas kdiff --vault <dir> --project <p> [--as-of T | --from T1 --to T2]
- atlas doctor [--vault <dir>] [--json]      # environment/vault diagnostics
- atlas snapshot ... / atlas restore ...     # backup & recovery bundles
- atlas live api-serve                        # read-only LIVE_API on 127.0.0.1
- Run `atlas --help` for the full subcommand surface.

Development notes & conventions
- Language: Python 3.12+, packaged in `src/project_atlas`.
- Domain models: Pydantic v2 models live under `src/project_atlas/domain/`.
  Import from `project_atlas.domain`, not submodules.
- JSON Schemas: shipped as package data under `src/project_atlas/schemas/` and
  `src/atlas_contracts/schemas/`.
- Determinism: generated content must avoid wall-clock timestamps and be
  reproducible across runs.
- Protected regions: human-edited sections in generated notes are preserved
  with explicit markers; regeneration fails closed on malformed markers.
- Secrets: conservative scanning quarantines suspect sources; matched content
  is never persisted in plaintext outputs.

Repository layout (high-level)
- src/project_atlas/        — Core package (cli, scaffold, discovery, ingest,
                             indexes, validation, compilers, utils)
- src/atlas_contracts/     — Shared contract models (agent events, receipts,
                             provenance, identity)
- atlas-vault-documentation/ — Governed agent control plane (separate
                             deliverable; own tests and skill manifest)
- docs/                    — authoritative planning and acceptance documents
- WORKLOG.md               — execution log and completion evidence
- .github/workflows/ci.yml — CI gate definitions

Governance, agents, and controlled workflows
- Governed agent sessions use `atlas-vault-documentation/scripts/atlas_agent.py`
  and the canonical skill (`atlas-vault-documentation/skill/SKILL.md`).
- `AGENT-BOOTSTRAP.md` defines the bootstrap rules and `docs/global/` (the
  ATLAS global operating foundation) the evidence-first operating contract for
  autonomous agents working on this repository; agents must follow session lifecycle: bootstrap → preflight →
  session-start → work → validate → completion → postflight → receipt.
- The control plane is intentionally separate from the Core package and must
  not be imported into core runtime code.

Governance navigation
- `GOVERNANCE.md` — roles, certify/merge/baseline lifecycle, stop boundaries
- `CONTRIBUTING.md` — internal contribution and PR workflow
- `SECURITY.md` — vulnerability reporting limitations (no invented contacts)
- `SUPPORT.md` — support boundaries for this private repository
- `CODE_OF_CONDUCT.md` — conduct expectations and enforcement limitation
- `VERSIONING.md` / `RELEASING.md` — version and release authorization
- `.github/ISSUE_TEMPLATE/` — structured issue forms (security → `SECURITY.md`)
- `docs/adr/ADR-006-github-repository-governance-baseline.md` — architecture

Contributing & branch policy
- Private repository model: currently maintained by the repository owner
  (`B0LK13`) and explicitly-authorized agents. No public contribution path is
  configured.
- Branch naming: `<type>/<AS-xxx-id>-<short-description>` (e.g.
  `fix/as-mvp-001-r1-tests`); architecture branches use `architecture/...`.
- All changes land via pull-request to `main`. Do not rewrite history, force
  push, or delete evidence. For governed work, include the evidence receipt in
  the PR description per `AGENT-BOOTSTRAP.md`.
- Live GitHub settings activation (required checks, approval restoration,
  CODEOWNERS enforcement) remains deferred until separately authorized and
  verified — see `GOVERNANCE.md`.

Security & vulnerability handling
- This project has no published external private vulnerability intake.
  Do not open public issues with sensitive vulnerability details. Follow
  `SECURITY.md`.
- Confirm path-safety, secret detection, and protected-region enforcement before
  accepting changes that touch ingestion or generation code.

Where to find authoritative docs (read first)
- AGENTS.md — high-level agent guidance, architecture, and code map
- CLAUDE.md — commands, architecture summary, and developer recipes for agents
- docs/plan.md, docs/prp.md — planning, OKF profile, functional/acceptance
  requirements (PRP = product requirements prompt)
- docs/acceptance-test.md — acceptance tests (AT-001..AT-020)
- docs/backlog.md — executable backlog, Epics and checkboxes
- WORKLOG.md — completed work-package evidence and exact validation outputs
- atlas-vault-documentation/ — governed agent control surface and skill

Contact & owner
- Repository owner: B0LK13 (private maintainer). For sensitive matters follow
  the channels described in SECURITY.md.

License
- No license is published in this repository. Treat the code as private and
  follow the repository owner guidance for reuse.

—
This README synthesizes the authoritative documentation in this repository's
`docs/` and top-level governance files. For any non-trivial change follow the
global operating foundation in `docs/global/README.md` and the governed agent
bootstrap protocol in `AGENT-BOOTSTRAP.md`.
