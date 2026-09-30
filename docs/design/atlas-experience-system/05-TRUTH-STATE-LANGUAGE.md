# 05 — Cross-Surface Truth-State Language

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`
**Status:** **IMPLEMENTED** in this lane (`AX-002`, `AX-003`) — see `11-HANDOFF.md`
**Fixes:** audit findings A-4 (vocabulary is not a system) and A-5 (SC 4.1.3 unimplemented)

This is the load-bearing element of the Evidence Desk direction (`04` §3). It is one
vocabulary, used identically by web, CLI, TUI, desktop and mobile.

---

## 1. The three rules

**Rule 1 — Every state carries a glyph, a label and a token.** Colour is never the only
carrier. This follows R-5's semantic-colour rule ("if your app becomes unusable when
stripped of colour, your design is broken") and it is also what makes the states legible
in a CLI with `NO_COLOR` set, in a monochrome TUI, and to a screen reader.

**Rule 2 — Absence renders as `UNKNOWN`, never as a healthy state.** There is no code path
that maps missing evidence to `OK`. This is `unknown != healthy` expressed as a default
rather than as a review rule.

**Rule 3 — A state change that alters what the data *means* must be announced.** A
LIVE→DEMO fallback is assertive (`role="alert"`); a load completing is polite
(`role="status"`). This is the fix for A-5, and it is a truth-boundary requirement, not
only a compliance one — see `01` R-6.

## 2. The vocabulary

Thirteen states. Every one is either already computed by Atlas or directly derivable from
an existing endpoint; none is invented for the UI.

| State | Glyph | Label | Means | Atlas source |
|---|:--:|---|---|---|
| `OK` | `●` | OK | Evidence present, single authority, in-validity | claim compile |
| `LIVE` | `◆` | LIVE | Read from LIVE_API against a real vault | `data_source=live_api` |
| `DEMO` | `◇` | DEMO FIXTURE | Isolated sample data — **not** a live vault | `data_source=demo_stub` |
| `FIXTURE` | `◇` | FIXTURE | Deterministic test data | fixture ingest |
| `UNKNOWN` | `?` | UNKNOWN | No traceable source. Not an error, not healthy | `/v1/unknown-status` |
| `UNRESOLVED` | `~` | UNRESOLVED | Question open, resolution possible | review queue |
| `CONTESTED` | `⇄` | CONTESTED | Sources disagree; no display winner | `/v1/conflicts` |
| `STALE` | `⧗` | STALE | Outside declared valid-time | bitemporal catalog |
| `BLOCKED` | `⊘` | BLOCKED | Cannot proceed; dependency unmet | attention queue |
| `OWNER_REQUIRED` | `⚿` | OWNER REQUIRED | Only the owner may decide. **Never actionable in UI** | `/v1/authz`, opt gate |
| `READY` | `▷` | READY | Eligible to proceed. `PROMOTE_ELIGIBLE != MERGED` | `/v1/next-status` |
| `RUNNING` | `◌` | RUNNING | Work in progress | `/v1/actions/recent` |
| `FAILED` | `✕` | FAILED | Attempted and failed | receipts |

`OWNER_REQUIRED`, `RUNNING`, `READY` and `BLOCKED` were measured at 0–1 occurrences in the
web layer (audit §3). Atlas computes states its UI currently has no way to say; this table
closes that gap.

### Deliberate constraints

- **`CONTESTED` never collapses.** No surface may pick a winner for display. Both values
  render, both sources cited, neither styled as preferred. Resolution is
  `atlas review decide` — a human act.
- **`OWNER_REQUIRED` never renders as an enabled control.** It is a *label on a fact*, not
  a button. Rendering it as an actionable affordance would breach
  `READ_ONLY_UI != EXECUTION_AUTHORITY`.
- **`DEMO`/`FIXTURE` are ambient, not per-value.** Plane belongs in persistent chrome
  (Evidence Desk status strip) so it cannot be scrolled past.
- **`UNKNOWN` is not an error state.** It must not be styled like `FAILED`, and in the CLI
  it must not produce a non-zero exit code (see `07` §4).

## 3. Colour tokens — contrast verified

Values below were computed against the real theme backgrounds, not estimated. Light is
checked against `--atlas-panel: #fffdf8` and `--atlas-paper: #f5f0e8`; dark against
`#1c1917` and `#0c0a09`.

| State | Light | Ratio (panel/paper) | Dark | Ratio (panel/paper) |
|---|---|---|---|---|
| `OK` / `LIVE` / `READY` | `#15653f` | 6.97 / 6.24 | `#4ade80` | 10.04 / 11.34 |
| `UNKNOWN` | `#8a5300` | 6.23 / 5.58 | `#fbbf24` | 10.48 / 11.83 |
| `CONTESTED` / `UNRESOLVED` | `#9a3412` | 7.19 / 6.44 | `#fb923c` | 7.73 / 8.73 |
| `STALE` | `#5b5563` | 7.06 / 6.33 | `#a8a29e` | 6.93 / 7.83 |
| `BLOCKED` | `#8f2d2d` | 8.00 / 7.17 | `#f87171` | 6.32 / 7.14 |
| `OWNER_REQUIRED` | `#6d3faf` | 6.93 / 6.21 | `#c4a2f5` | 8.19 / 9.25 |
| `RUNNING` | `#0f5f8f` | 6.76 / 6.06 | `#7dd3fc` | 10.49 / 11.85 |
| `DEMO` / `FIXTURE` | `#7a4a00` | 7.36 / 6.59 | `#fcd34d` | 12.13 / 13.70 |
| `FAILED` | `#a01b1b` | 7.74 / 6.94 | `#f87171` | 6.32 / 7.14 |

**All 26 combinations meet WCAG 2.2 AA for normal text (≥ 4.5:1). Lowest observed: 5.58:1**
(`UNKNOWN` on light paper). Verification is automated — `apps/web/scripts/test-truth-state.mjs`
recomputes every ratio from the token values and fails the suite if any drops below 4.5.
The check is on the *tokens*, so it cannot drift as themes change.

Two deliberate notes:

- `UNKNOWN` amber is intentionally darkened from the existing `--atlas-unknown: #b45309`
  (which measures 4.99 on panel but only **4.47** on `#f5f0e8` paper — a marginal AA
  failure at normal weight). This is a real, if small, pre-existing defect the token
  system now prevents.
- `BLOCKED` and `FAILED` share a hue in dark theme. They are separated by glyph (`⊘` vs
  `✕`) and label, per Rule 1, so this is acceptable — but it is recorded rather than hidden.

## 4. Rendering per surface

### Web — `TruthChip`

```
◆ LIVE        ? UNKNOWN        ⇄ CONTESTED        ⚿ OWNER REQUIRED
```

Glyph is `aria-hidden` (decorative — the label carries the meaning). The chip exposes
`data-truth-state` for testability. `title` gives the long meaning. Non-interactive by
default: a chip is a statement, not a control.

### Web — `TruthAnnouncer` (the A-5 fix)

Mounted **unconditionally** by `ProdShell`, because R-6 establishes that a live region must
already exist in the DOM before content is inserted — a conditionally rendered banner does
not reliably announce.

```
<div role="status" aria-live="polite" aria-atomic="true">   ← routine transitions
<div role="alert"  aria-live="assertive" aria-atomic="true"> ← meaning-changing transitions
```

Severity mapping:

| Transition | Region | Announcement |
|---|---|---|
| loading → loaded | polite | "Loaded. Reading LIVE vault." |
| LIVE → DEMO fallback | **assertive** | "Now showing DEMO FIXTURE data — live vault unreachable. This is not live vault data." |
| load → error | assertive | "Read failed. <reason>" |
| rollup → `UNKNOWN` | polite | "Health rollup is UNKNOWN. Unknown is not healthy." |
| answer → `CONTESTED` | polite | "Sources disagree. N competing sources." |

The wording is deliberately explicit because R-6 found **no established accessible pattern
for communicating provenance or uncertainty** — there is no convention a screen-reader user
could be expected to already know, so the text states the epistemic situation in full.

### CLI

Glyph + label, honouring `NO_COLOR`:

```
◆ LIVE       vault=atlas-main
? UNKNOWN    no traceable source for "deployment target"
⇄ CONTESTED  2 competing sources — atlas review decide --project P
⚿ OWNER REQUIRED  wake gate CLOSED — owner decision required
```

Never colour-only, and never colour-dependent. See `07` §4 for exit-code semantics.

### TUI

Same glyph + label in the persistent provenance line (`08` §3). Because the glyph carries
the state, the TUI remains correct in a monochrome terminal — which R-5 treats as the test
of whether a TUI design is sound.

### Mobile / desktop

Identical chips. On narrow viewports the chip keeps its label and drops to
`font-size: 0.7rem` with the glyph retained; it must never degrade to a bare colour dot,
because that would violate Rule 1 exactly where screen real estate tempts it most.

## 5. The type-level guarantee

```ts
export type TruthState =
  | "ok" | "live" | "demo" | "fixture" | "unknown" | "unresolved"
  | "contested" | "stale" | "blocked" | "owner_required"
  | "ready" | "running" | "failed";
```

Two functions carry the invariants:

- `truthStateFor(value)` — maps `null` / `undefined` / `""` / absent evidence to
  `"unknown"`. **Rule 2 becomes a default rather than a discipline**: to render absent
  evidence as healthy, a developer would have to bypass the helper deliberately.
- `isOwnerGated(state)` — returns true for `owner_required`, letting callers assert that no
  interactive control is attached.

This is the structural answer to A-4: the invariant `unknown != healthy` moves out of
reviewer vigilance and into a function with a test.
