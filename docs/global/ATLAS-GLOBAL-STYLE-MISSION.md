# ATLAS Global Style Mission Directive

| Field | Value |
|---|---|
| Canonical ID | `ATLAS_GLOBAL_STYLE_MISSION` |
| Version | 1 |
| Directive | `ATLAS-GLOBALIZE-AUTONOMY-2026-10-02` |
| Status | PROPOSED — binding once the owner merges it to `main` |
| Scope | Atlas Studio, Mission Control, operator surfaces, dashboards, execution, verification, fleet and evidence views |
| Builds on | ADR-008 (web app), ADR-009 (design tokens), ADR-010 (web UX), ADR-032 (derived intelligence ≠ authority), [`apps/web/README.md`](../../apps/web/README.md) invariants, [`docs/atlas-3/PRODUCT-EXPERIENCE.md`](../atlas-3/PRODUCT-EXPERIENCE.md) |
| Index | [README.md](./README.md) |

## 1. Character

Atlas surfaces should feel **premium, modern, technical, precise, calm, high-information
and AI-native**.

| Prefer | Avoid |
|---|---|
| Strong information hierarchy | Generic SaaS dashboard look |
| Restrained visual language and excellent typography | Decorative status noise |
| Dense but comprehensible information | Fabricated or vanity metrics |
| Consistent spacing (`--atlas-*` tokens, ADR-009) | Giant empty cards and redundant chrome |
| Precise status terminology | Ambiguous state colours |
| Subtle motion where it carries meaning | Effects for their own sake |
| Accessibility (never colour alone) | UI that claims authority it does not have |
| Evidence and provenance one step away | Distinct routes that look like distinct products |

## 2. SM-TRUTH — Truthful UI

UI is a projection of Atlas truth. It is never an authority source
(`UI != CANONICAL TRUTH`).

Never fabricate live state, health, freshness, objectives, execution, history, evidence,
verification or authorization.

- `UNKNOWN` stays `UNKNOWN`.
- `STALE` stays `STALE`.
- `BLOCKED` stays `BLOCKED`.
- Missing data renders as missing, never as a default success (`UNKNOWN != HEALTHY`).
- Fixture or sample data is labelled as such wherever it is shown
  (`DEMO_FIXTURE != AUTHENTIC_PILOT`).

## 3. SM-SEPARATION — Semantic separation

These dimensions are different facts. Never collapse them into one generic "green":

| Dimension | Question it answers |
|---|---|
| `connectivity` | Can we reach the source at all? |
| `freshness` | How old is the data we hold? (reuse `CURRENT` / `ALLOW_STALE_HISTORICAL` / `UNKNOWN` from PRODUCT-EXPERIENCE) |
| `liveness` | Is the producer still running and reporting? |
| `health` | Is the producer functioning correctly by its own contract? |
| `execution` | Did the task run, and what was its terminal state? |
| `implementation_success` | Did the run produce the intended change? |
| `verification` | Did an independent verifier confirm it at an exact identity? |
| `certification` | Has the governed certification gate been passed? |
| `authorization` | Has the owner granted the next authority step? |

A reachable source is not healthy. A completed run is not verified. A verified candidate is
not authorized. Each dimension needs its own label and colour role; a positive state on
one never implies a positive state on another.

## 4. SM-PROVENANCE — Provenance transparency

Every status or metric shown carries, or links to, its source, its as-of time, and the exact
identity it describes (commit, run, artifact, node). If that cannot be shown, the value is
shown as `UNKNOWN`.

## 5. SM-SYSTEM — One system

Routes may be distinct, but they share one token set, one status vocabulary and one
navigation model. New status colours or terms are added to the shared system, not defined
locally in a view.

## 6. Change control

Only the owner amends this directive. Section IDs (`SM-TRUTH`, `SM-SEPARATION`,
`SM-PROVENANCE`, `SM-SYSTEM`) are stable. Implementation guidance stays in ADRs and
`apps/web`; this directive states intent and invariants.
