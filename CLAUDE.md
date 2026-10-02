# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**Coder Alpha north star:** persistent brain for AI-native projects — see
`docs/product/CODER-ALPHA-NORTH-STAR.md` (D-037). Historical roadmap priority
is not current owner priority. Global operating foundation (operating
contract, style mission, Global Goals, directive template): `docs/global/`.

Project Atlas is a local-first "project knowledge compiler": it scans approved
documentation sources and generates a structured Obsidian vault following an
internal Open Knowledge Format (OKF) profile — evidence-backed, offline,
deterministic. See `AGENTS.md` for the full principles and core narrative
(no claim without a traceable source, three-layer vault model, human-edit
preservation, no subjective trust scores) — this file focuses on commands and
architecture, not repeating that narrative.

The Core pipeline is implemented: `discover` → `ingest` → `build-indexes` →
`build-portfolio` → `validate`, with read-only read/query lenses layered on
top (`query`, `ask2`, `kdiff`, `overview`, `state`) plus `connect`, `doctor`,
`snapshot`/`restore`, and a read-only LIVE_API (`live api-serve`). The
repository also contains shared Atlas contracts (`src/atlas_contracts/`) and
the governed agent control-plane sibling deliverable
(`atlas-vault-documentation/`). Check `WORKLOG.md` (tail) for the current
work-package status and `docs/backlog.md` for what's checked off. Atlas 2.2 capabilities are unlocked per-capability
(`docs/atlas-2.2/PACKAGE-MATURITY.json`: `prep-frozen` vs
`implementation-unlocked`). Product maturity truth: Atlas 1.0 complete,
Atlas 2.0 release-certified, Atlas 2.1 live productization layer (including
read-only MCP/ChatGPT bridge surfaces), Atlas 2.2 no longer PREP-only overall.
Sealed Golden Demo pin (ancestor of current `main`, not always HEAD):
`754bb266fa2d2ff39089c4e587c9b90eacd841fd`
(`c481c1aa6ba408a16b176d5326f209d6a76b6c42`), with
`ATLAS_DEMO_2_2_PORTABLE_CANDIDATE=PASS`, `WINDOWS_DEMO_SEAL=PASS`,
`ATLAS_DEMO_2_2_WORKING=YES`, `WINDOWS_STRANGER_PHASE_C=PASS`.
AS-OPT-GATE-001 is merged (`#321` / `project_atlas.opt_gate`);
`ATLAS_OPT_WAKE_GATE = CLOSED`; `EVALUATOR_STABLE = YES` (post-merge reassessment); wake remains `CLOSED` / governance `OPEN_ELIGIBLE` only.

Truth boundaries (must remain explicit): `PREP != IMPLEMENTED`,
`DEMO_FIXTURE != AUTHENTIC_PILOT`, `DEMO != RELEASE`,
`UI != CANONICAL TRUTH`, `MODEL OUTPUT != AUTHORITY`,
`PROMOTE_ELIGIBLE != MERGED/DEPLOYED/AUTHORITATIVE`,
`CODEX_VALIDATED = NO`, `EXTERNAL_SECURITY_REVALIDATION_REQUIRED = YES`.
`ATLAS_DEMO_2_2_WORKING = YES` means the Golden Product Vertical Slice passed
portable + Windows stranger validation only; it is not a claim that
`AUTHENTIC_PILOT = PASS`, `EXTERNAL_SECURITY_CERTIFICATION = PASS`, or
`COMMERCIAL_GA = YES`.

## Commands

```bash
pip install -e ".[dev]"                                   # editable install with dev deps

python -m pytest                                          # full test suite
python -m pytest tests/unit/test_scaffold.py::test_name    # single test
python -m pytest -k pattern                                 # by keyword
python -m pytest -m integration                             # integration-marked tests only

python -m ruff check .                                      # lint (src/ and tests/ only)
python -m mypy src                                           # strict type check

atlas --help
atlas version
atlas init --output <dir> [--dry-run]                       # create a vault scaffold
atlas discover --source <project-root> --output <manifest.json>
atlas ingest --manifest <manifest.json> --vault <vault-dir> --source <project-root>
atlas build-indexes --vault <vault-dir>
atlas build-portfolio --vault <vault-dir>                   # derived portfolio + bitemporal catalog
atlas validate --vault <vault-dir>
atlas connect [source] [--vault <dir>]                      # Coder Alpha bind+compile
atlas overview --vault <vault-dir> [--project <id>]         # Project Overview lens
atlas state --vault <vault-dir> [--project <id>]            # Current State lens
atlas changed --vault <vault-dir> [--project <id>]          # What Changed lens
atlas decisions --vault <vault-dir> [--project <id>]        # Decision memory lens
atlas unknown --vault <vault-dir> [--project <id>]          # Unknown/conflict lens
atlas brief --vault <vault-dir> [--project <id>]            # Unified project brief
atlas context --vault <vault-dir> --project <id>            # Agent context export
atlas handoff create|resume --vault <vault-dir> ...         # Cross-agent handoff
atlas capture record|list --vault <vault-dir> ...           # Session capture defaults
atlas obsidian project --vault <vault-dir> [--project <id>] # Living Obsidian projection
atlas review decide --vault <vault-dir> --project <id> ...  # Human decisions -> Truth Core

atlas doctor [--vault <vault-dir>] [--json]                 # environment/vault diagnostics
atlas ask2 --vault <dir> --project <p> --question "..."     # Ask Atlas 2 (read-only)
atlas kdiff --vault <dir> --project <p> [--as-of T | --from T1 --to T2]   # Knowledge Diff / Time Machine
atlas snapshot ... / atlas restore ...                       # backup & recovery bundles
atlas live api-serve                                         # read-only LIVE_API (127.0.0.1)
```

`.github/workflows/ci.yml` is the authoritative gate sequence: ruff → mypy →
pytest → CLI smoke test (`atlas init --dry-run`, then a real `atlas init`,
asserting `index.md` and `00-system/vault-charter.md` exist).

## Architecture

**Package layout** (`src/project_atlas/`, src-layout):

- `cli.py` — argparse-based `atlas` entry point; dispatches all commands.
  Exit codes: `0` success, `1` operational error, `2` argparse usage error.
- `config.py` — TOML config loading via stdlib `tomllib`, precedence
  defaults → `[tool.atlas]` in `pyproject.toml` → explicit `--config` file.
  All fields default safely; the CLI works with zero configuration.
- `scaffold.py` — `atlas init` (FR-001/AT-001): builds the vault skeleton
  described in `docs/plan.md` §3. **The directory structure this module
  writes (`00-system/`, `projects/`, `sources/`, etc.) is the tool's
  *output*, not this repository's own layout** — a common point of
  confusion.
- `discovery.py` — `atlas discover` (FR-002): recursive, streaming SHA-256
  inventory with default exclusions, configured include/exclude globs,
  sensitive filename protection, and `.atlas-project.yaml` markers.
- `ingestion.py` — `atlas ingest`: manifest validation, deterministic
  document classification, protected-region preservation, agent-event
  quarantine, and atomic canonical writes via a single `_promote(write_plan)`
  boundary. A project's first ingest also allocates durable project identity
  (AS-ID-001 genesis: a fresh, random UUIDv4, once) into its `--source`
  project marker (`.atlas-project.yaml` / `.atlas/project.yaml`) when that
  marker has no `project_uuid` yet -- the one write this command makes
  outside `--vault`. `_append_marker_project_uuid` appends the field as a
  single new line rather than re-serializing the whole marker, so unrelated
  human-authored formatting (list style, blank lines, quoting) is preserved;
  the CLI reports the allocation on stdout (`identity allocated for: ...`).
- `indexes.py` — `atlas build-indexes` (FR-010): deterministic lexical
  indexes under `generated/indexes/`, rejects obsolete `indexes/` directory.
- `validation.py` — `atlas validate` (FR-012): link resolution, OKF
  frontmatter/schema checking, provenance hash validation, generated-index
  drift rejection.
- `retrieval.py` — read-only exact/prefix lookup over generated lexical
  indexes (AS-RET-001); never mutates the Vault.
- `portfolio.py` — `atlas build-portfolio`: derived portfolio intelligence
  under `generated/portfolio/` (AS-MVP-001); also triggers the bitemporal
  validity-catalog derivation.
- `doctor.py` — `atlas doctor`: environment and optional Vault diagnostics,
  with a `--json` machine-readable report (PROD-DOCTOR-001).
- `ask2.py` — `atlas ask2` (AS-2.2-ASK2-001): read-only answer lens over
  project-scoped hybrid retrieval + a p2 read-only context compiler; returns
  known/unknown/conflict honestly; UNKNOWN stays UNKNOWN; model ≠ authority;
  never writes.
- `knowledge_diff.py` — `atlas kdiff` (AS-2.2-KDIFF-001): read-only as-of reads
  and T1→T2 diffs over document-declared valid-time; graph ≠ authority; no
  canonical writes.
- `runtime_22.py` — AS-2.2 runtime package: hybrid retrieval and context
  compiler runtime primitives used by Ask Atlas 2 and related read paths.
- `bitemporal.py` / `bitemporal_catalog.py` — AS-2.0-TEMPORAL-001 validity-window
  evaluation and the catalog writer. `bitemporal_catalog.py` derives the
  `generated/ops/bitemporal/` catalog (from persisted claims + document-declared
  valid-time) that the `kdiff` reader consumes, and is rebuilt by
  `build-portfolio`.
- `backup.py` — `atlas snapshot`/`restore` (AS-BACKUP-001): byte-complete
  recovery bundles; operational durability ≠ project authority.
- `vault_identity.py` — canonical Vault identity bootstrap/preservation
  (`.atlas/vault.json`): `atlas init` establishes identity; `snapshot` remains
  non-minting and `restore` preserves identity. Linux uses the POSIX dirfd-safe
  path and Windows uses the platform-specific atomic path introduced by `#320`.
- `api_server.py` / `app_service.py` / `web_api/` — read-only LIVE_API
  (`atlas live api-serve`, 127.0.0.1) including the `/v1/conflicts` and
  `/v1/kdiff` projections (`web_api/conflicts.py`); a Web `#/time-machine` page
  lives under `apps/web`.
- `mcp_server.py` / `mcp_registry.py` — AS-2.1 read-only MCP bridge surface:
  allow-listed read tools only; unknown/write/path-traversal requests fail
  closed.
- `chatgpt_bridge.py` / `chatgpt_capture.py` — AS-2.1 ChatGPT bridge surface:
  export/capture into quarantine with explicit truth boundary
  (`LLM output != authority`).
- `knowledge_compiler.py` — deterministic claim extraction, authority
  precedence, conflict detection, review queue generation, and lifecycle
  transition enforcement (AS-CORE-003).
- `semantic_compiler.py` — project-record compilation and OKF Markdown
  rendering with protected human regions (AS-CORE-002).
- `lineage.py` — durable source-lineage identity, v1→v2 registry migration,
  and ambiguity fail-closed handling (AS-ID-001).
- `source_identity.py` — UUIDv4 project genesis, canonical path rules, and
  project identity locks.
- `okf_renderer.py` — OKF concept-note rendering helpers.
- `secrets.py` — conservative content-based secret scanning; returns
  metadata only, never matched content (NFR-004).
- `schema.py` — JSON Schema loading and validation via `importlib.resources`.
- `logging.py` — structured logging to **stderr only** (so vault content
  printed to stdout is never interleaved with logs); `console` or `json`
  formatter, loggers namespaced `project_atlas.<module>`.
- `domain/` — Pydantic v2 models and controlled vocabularies. Import from
  the package root (`from project_atlas.domain import ...`), not submodules
  — enforced by `domain/__init__.py`'s docstring and `__all__`.
- `schemas/` — JSON Schemas for domain records and contracts, shipped as
  package data so validation works from an installed wheel without a repo
  checkout (ADR-001).

**Conventions that carry across modules** (established in `scaffold.py`,
expected to hold for future `discover`/`ingest`/etc.):

- Deterministic output — no wall-clock timestamps in generated content
  (NFR-001); metadata records `generated.by` only, never `generated.at`
  (ADR-001 §2).
- Fail-closed path safety — output paths are resolved and rejected up front
  (filesystem root, home dir, existing file, non-empty dir), and every
  individual write is re-checked with `is_relative_to(resolved)` before
  touching disk (defence in depth against path traversal, AT-013).
- Atomic writes — temp file in the target directory, then `os.replace`.
- Docs-as-spec traceability — code and tests reference requirement/backlog
  IDs (`FR-xxx`, `NFR-xxx`, `AT-xxx`, `B-xxx`, ...) defined in `docs/`
  (`plan.md`, `prp.md`, `acceptance-test.md`, `backlog.md`,
  `implementation-roadmap.md`, `adr/`). Update `docs/backlog.md` checkboxes
  as work completes; log each work package's plan/commands/results in
  `WORKLOG.md`.

**Out of scope for the above commands**: `atlas-vault-documentation/` and
`AGENT-BOOTSTRAP.md` implement a separate "governed agent control plane"
(session receipts, skill hashing, `atlas_agent.py doctor`/`run`) with its
own tooling. It is a sibling deliverable, not part of `project_atlas`.

`src/atlas_contracts/` are shared subsystem contracts (agent events, event
packages, provenance, receipts, identity, versions) used by both Core and
the control plane; they are part of the `project-atlas` package data.
