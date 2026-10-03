# ATLAS Global Foundation

The canonical, cross-host, cross-session operating foundation for Project Atlas work.
Directives and WorkItems **inherit** these documents by ID and state only their deltas
(see the [directive template](./ATLAS-DIRECTIVE-TEMPLATE.md)).

| Canonical ID | Doc | Role |
|---|---|---|
| `ATLAS_GLOBAL_AUTONOMOUS_OPERATING_CONTRACT` | [ATLAS-GLOBAL-OPERATING-CONTRACT.md](./ATLAS-GLOBAL-OPERATING-CONTRACT.md) | How agents act inside granted authority (OC-A…OC-K), decision priority, default owner boundaries, evidence vocabulary |
| `ATLAS_GLOBAL_STYLE_MISSION` | [ATLAS-GLOBAL-STYLE-MISSION.md](./ATLAS-GLOBAL-STYLE-MISSION.md) | Truthful, semantically separated, premium operator surfaces |
| `ATLAS_GLOBAL_GOALS` | [ATLAS-GLOBAL-GOALS.md](./ATLAS-GLOBAL-GOALS.md) | Global Goals `ATLAS-GOAL-G01`…`G15` |
| `ATLAS_DIRECTIVE_TEMPLATE` | [ATLAS-DIRECTIVE-TEMPLATE.md](./ATLAS-DIRECTIVE-TEMPLATE.md) | INHERITS / OUTCOME / SCOPE / INVARIANTS / AUTHORITY DELTA / SUCCESS / OPERATING EXPECTATION |

Dated baselines are evidence snapshots. They are not doctrine and are never rewritten:

| Baseline | Doc |
|---|---|
| G1–G15 current-state matrix (2026-10-02) | [baseline/2026-10-02-G1-G15-BASELINE.md](./baseline/2026-10-02-G1-G15-BASELINE.md) |
| Correction 1 to that matrix: evidence scope and DEVQ-0001 history | [baseline/2026-10-02-G1-G15-BASELINE-CORRECTION-1.md](./baseline/2026-10-02-G1-G15-BASELINE-CORRECTION-1.md) |
| Authority-compatibility note and open owner decisions D1–D5 | [baseline/2026-10-02-AUTHORITY-COMPATIBILITY.md](./baseline/2026-10-02-AUTHORITY-COMPATIBILITY.md) |
| Operational slice, F2 decision brief, E2 runbook | [baseline/2026-10-02-OPERATIONAL-SLICE-AND-F2-BRIEF.md](./baseline/2026-10-02-OPERATIONAL-SLICE-AND-F2-BRIEF.md) |
| Autonomy and fleet frontier, DEVQ-0002 record (2026-10-02) | [baseline/2026-10-02-AUTONOMY-FRONTIER.md](./baseline/2026-10-02-AUTONOMY-FRONTIER.md) |
| Studio / Mission Control style audit (2026-10-02) | [baseline/2026-10-02-STYLE-AUDIT.md](./baseline/2026-10-02-STYLE-AUDIT.md) |
| G1–G15 delta against that matrix: DEVQ-0002-E2 evidence, no status change (2026-10-03) | [baseline/2026-10-03-G1-G15-DELTA.md](./baseline/2026-10-03-G1-G15-DELTA.md) |

## Where this sits

These documents add a global layer. They do not supersede the existing authority below,
which stays canonical in its own scope:

| Existing authority | Relationship |
|---|---|
| [`autonomy/policy.md`](../../autonomy/policy.md) (owner-pinned by `policy_sha`) | More specific authority for the governed RSI loop. It wins inside the loop's scope. Not edited by this foundation |
| [`GOVERNANCE.md`](../../GOVERNANCE.md) | Roles, IV separation, lifecycle and stop boundaries remain binding. The contract cites them |
| [`docs/product/CODER-ALPHA-NORTH-STAR.md`](../product/CODER-ALPHA-NORTH-STAR.md) (D-037) | Product direction and *document* precedence (§11). The contract's priority order is a *decision* order |
| [`docs/atlas-3/NORTH-STAR.md`](../atlas-3/NORTH-STAR.md), [`docs/atlas-3/FOUNDATION.md`](../atlas-3/FOUNDATION.md) | Program north star. Autonomy is the top layer and never self-merges, consistent with the contract |
| ADR-008, ADR-009, ADR-010 ([`docs/adr/`](../adr/)) and [`apps/web/README.md`](../../apps/web/README.md) | UI stack, tokens and invariants. The style mission generalizes `UI != canonical` and `Unknown != healthy` |
| [`docs/adr/ADR-032-derived-intelligence-is-not-authority.md`](../adr/ADR-032-derived-intelligence-is-not-authority.md) | Consistent with `MODEL OUTPUT != AUTHORITY` |
| [`autonomy/instruments/directive-template.md`](../../autonomy/instruments/directive-template.md) | Loop iteration template. Unchanged; the global template is for owner directives |

## Inheritance

Agent-facing entry points ([`AGENTS.md`](../../AGENTS.md), [`CLAUDE.md`](../../CLAUDE.md),
[`README.md`](../../README.md)), the product north star and
[`apps/web/README.md`](../../apps/web/README.md) link here. A future directive declares:

```text
INHERITS:
  ATLAS_GLOBAL_AUTONOMOUS_OPERATING_CONTRACT
  ATLAS_GLOBAL_STYLE_MISSION
  ATLAS_GLOBAL_GOALS
```

`tests/unit/test_atlas_global_foundation_docs_001.py` checks that these documents exist,
that their IDs are stable and defined only here, that links resolve, and that the entry
points link back.
