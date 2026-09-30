# 04 — Direction Comparison and Selection

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`

## 1. Scoring

Scored 1–5 against the directive's dimensions. Scores are judgements traceable to the
audit (`00`) and the research (`01`); the reasoning column carries the evidence, and the
reasoning is the substance — the numbers only summarise it.

| Dimension | Dossier | Standing Watch | Interrogation | Reasoning |
|---|:--:|:--:|:--:|---|
| **USABILITY** | 4 | 4 | 3 | Dossier and Watch are both immediately usable. Interrogation strands a user who does not know what to ask. |
| **INFORMATION_DENSITY** | 2 | 5 | 2 | Watch is built for hundreds of rows; the others show one thing at a time. Directly addresses A-3. |
| **LEARNABILITY** | 5 | 3 | 2 | A document teaches itself. A board must be learned. A palette must be *discovered*. |
| **TRUTH_CLARITY** | 4 | 4 | 5 | Interrogation makes epistemic status the headline of every answer — structurally unavoidable. Dossier's redaction is strong but only where a fact is missing; Watch's chips are per-value and can be scanned past. |
| **ACCESSIBILITY** | 5 | 3 | 3 | Dossier is semantic prose with landmarks — least to get wrong. Watch needs responsive tables + live regions. Interrogation stakes everything on a correct combobox (`aria-activedescendant` count today: 0). |
| **RESPONSIVENESS** | 4 | 2 | 5 | One input/one answer is a phone. A 15-column board is not (A-7). |
| **ATLAS_DIFFERENTIATION** | 4 | 2 | 5 | Watch looks like every ops console. Interrogation looks like nothing in the research set. |
| **IMPLEMENTATION_COMPLEXITY** (5 = cheapest) | 5 | 2 | 3 | Dossier is mostly typographic on existing tokens. Watch needs a new shell + virtualised tables. |
| **DESIGN_SYSTEM_REUSE** | 5 | 3 | 4 | Dossier reuses `tokens.css`/ADR-009 almost entirely. |
| **WEB_CLI_TUI_COHERENCE** | 3 | 5 | 4 | Watch ports directly onto the persistent multi-panel TUI pattern (R-5). Dossier has no good TUI shape. |
| **OPERATOR_TRUST** | 3 | 5 | 4 | Trust for an operator means knowing what is running and what is waiting on them (R-1, J-8). Only Watch answers that well. |
| **TOTAL** | **44** | **38** | **40** | |

## 2. Reading the scores

The totals are close enough that ranking by total would be the wrong move. What the table
actually shows is that **each direction wins a different, non-overlapping set of
dimensions**:

- **Dossier** owns *learnability, accessibility, reuse, cost* — the adoption dimensions.
- **Standing Watch** owns *density, coherence, operator trust* — the daily-use dimensions.
- **Interrogation Room** owns *truth clarity, differentiation, responsiveness* — the
  identity dimensions.

And critically, each one's weakness is a *job* the others cover:

| Direction | Fails at | Which is |
|---|---|---|
| Dossier | J-7 autonomous work, J-8 what needs me | the most under-served job per R-1 |
| Standing Watch | J-1 understanding a project | the newcomer's entire first session |
| Interrogation Room | J-6 source health, J-10 system health | the "am I reading stale input" job |

No single direction can be adopted whole without losing a job that repository capability
already supports. Picking one on aesthetics — or on total score — would delete a
capability from the product's reach. So the selection is a **synthesis**, and the
directive explicitly permits that ("select or synthesize").

## 3. Selected direction — **Evidence Desk**

> **Standing Watch's shell, Dossier's reading surface, Interrogation Room's truth model.**

### The synthesis rule

Each direction contributes the layer it won, and only that layer:

| Layer | Taken from | Why |
|---|---|---|
| **Shell & navigation** — persistent rail, ambient plane strip, 5 job-areas | Standing Watch | Wins coherence + operator trust; spatial constancy (R-5) is the right answer to A-1's flat list |
| **Home** — "Needs you" queue first (J-8) | Standing Watch | R-1: the audit plane is the product; the most under-served job |
| **Project reading surface** — document spine, inline citations, expand in place | Dossier | Wins learnability + accessibility; recovers J-1, which Watch loses |
| **Truth-state model** — epistemic status is a **typed, named state**, glyph + label + token, never colour alone | Interrogation Room + Dossier | Wins truth clarity; R-5's semantic-colour rule; fixes A-4 structurally |
| **Answer contract** — `CLAIM · EVIDENCE · STATE`, uncitable ⇒ renders `UNKNOWN` | Interrogation Room | Makes `MODEL OUTPUT != AUTHORITY` structural, not a review rule |
| **Command palette** — full 67-command surface, rows typed by authority | Interrogation Room | R-3: preserves breadth while shrinking visible nav from 15 to 5 |
| **Evidence drawer** — inline from any claim, on every surface | Dossier + R-4 | Provenance is inline, not a destination; retires `Graph` from top-level nav |
| **Density policy** — wide measure for tables, prose measure for records | Standing Watch (bounded by Dossier) | Fixes A-3 without making every page a spreadsheet |

### Why this synthesis is coherent rather than a compromise

The three directions disagree about the *unit of the product* — record, queue item,
question. A synthesis is only legitimate if those units nest rather than compete. They do:

```
a queue item (Standing Watch)  is raised because
a question (Interrogation)     is unresolved about
a record (Dossier)             that Atlas has compiled
```

That is not three products stapled together; it is one product read at three zoom levels.
The shell shows the queue, the record shows the compiled knowledge, and the truth model is
what makes both of them honest. **The truth-state system is the load-bearing element** —
it is the only piece that must be identical across all three zoom levels, and across web,
CLI and TUI. That is why it is the piece implemented in this lane (`05`, `AX-002`) rather
than left as a specification.

### Name

**Evidence Desk** — a desk is a working surface (Watch), what sits on it is a case file
(Dossier), and what you do there is establish what is actually known (Interrogation).

## 4. Why the rejected directions were rejected

Neither was rejected for being weak. Each was rejected **as a whole-product commitment**,
and each survives as the source of a layer.

**Standing Watch, as a whole product, was rejected** because it makes J-1 hard. A
newcomer's first session with Atlas would be counts and rollups with no way to read what
Atlas actually knows — and Atlas's north star is a *persistent brain*, which must be
readable, not only monitorable. It also scored lowest on differentiation (2): adopting it
whole would make Atlas look like a generic ops console, discarding the product's real
distinction. Its shell, home model and density policy are all adopted.

**Interrogation Room, as a whole product, was rejected** on discoverability. Its core
premise — you must ask — fails against a genuine Atlas use case: *the operator does not
know what they do not know*. Atlas's value includes surfacing unknowns and conflicts the
user never thought to query. A palette-only product cannot do that, and its weakest jobs
(J-6, J-10) are exactly the "am I reading stale input" jobs that `unknown != healthy`
exists to protect. It also concentrates accessibility risk into one widget in a codebase
measured at `aria-activedescendant: 0`. Its truth model, answer contract and palette are
all adopted — the palette as a *supplement* to nav rather than a replacement for it,
which removes the discoverability objection.

**Dossier, as a whole product, was not selected** either, despite the highest total (44).
Its scores are inflated by cheapness and reuse — legitimate dimensions, but they measure
*ease of building*, not *value delivered*. On the two dimensions that measure whether the
product serves its operator (density 2, operator trust 3) it is the weakest of the three,
and R-1 is unambiguous that the audit/queue view is the under-served surface. Its reading
surface, redaction instinct and accessibility discipline are adopted.

## 5. What this selection commits to, and what it does not

**Commits to:** the IA in `02`, the truth-state language in `05`, and the surface specs in
`06`–`09` being written against Evidence Desk.

**Does not commit to:** deleting any of the 15 routes, any of the 41 endpoints, or any of
the 67 commands. The palette exists so that breadth is preserved.

**Does not claim:** that Evidence Desk is implemented. This lane implements the truth-state
system and its accessibility contract (`AX-002`, `AX-003`) plus a navigable prototype of
all three directions. Everything else is `SPECIFIED`. See `11-HANDOFF.md` for the exact
implemented/prototyped/specified split.

**Owner decision that remains open:** adopting Evidence Desk as *the* production direction
is a product-architecture decision above Level 2 capability. This document recommends it
with evidence; it does not enact it. See `11-HANDOFF.md` § Owner decisions.
