# Atlas 2.2 — PREP → roadmap crosswalk

Authoritative mapping from landed PREP packages (#159–#199) to post-unlock
production slots in
[`docs/strategy/ATLAS-2.2-EXECUTABLE-ROADMAP.md`](../../strategy/ATLAS-2.2-EXECUTABLE-ROADMAP.md).

**Status: PREP ONLY** — traceability rehearsal; not unlock or release certification.

| Gate | Value |
|---|---|
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |
| Tip audited | `5e2559f096ce6e1a906ee69ded45f8393ff2310b` / TREE `2768267fa1923a3a1108f84fff9d77eae7e794fc` |
| PREP packages mapped | **21** (+ ADV-POOL, FIXTURE-ROLLUP) |

## Legend

| Relation | Meaning |
|---|---|
| **direct** | PREP maps 1:1 to a named `AS-2.2-*-001` DAG slot |
| **feeds** | PREP is upstream input for a DAG consumer slot |
| **enabler** | PREP supports multiple slots; no dedicated DAG node yet |
| **optional** | Post-unlock slot marked optional in strategy DAG |

## Crosswalk table

| Short | PREP package | PR | Roadmap slot(s) | Relation | PREP entry |
|---|---|---|---|---|---|
| RET | `AS-2.2-RET-HYBRID-001` | [#159](https://github.com/WezzSide/project-atlas/pull/159) | `AS-2.2-RET-CTX-001` | feeds | [`AS-2.2-RET-HYBRID-001.md`](../AS-2.2-RET-HYBRID-001.md) |
| CTX | `AS-2.2-CTX-COMPILER-001` | [#161](https://github.com/WezzSide/project-atlas/pull/161) | `AS-2.2-RET-CTX-001` | feeds | [`ctx-compiler/`](../ctx-compiler/) |
| KCI | `AS-2.2-KCI-ENGINE-PREP-001` | [#160](https://github.com/WezzSide/project-atlas/pull/160) | `AS-2.2-KCI-001` | direct | [`AS-2.2-KCI-ENGINE-PREP-001.md`](../AS-2.2-KCI-ENGINE-PREP-001.md) |
| MEM | `AS-2.2-MEM-GOV-001` | [#169](https://github.com/WezzSide/project-atlas/pull/169) | *(enabler)* | enabler | [`mem-gov/`](../mem-gov/) |
| DoD | `AS-2.2-DOD-COMPILER-001` | [#170](https://github.com/WezzSide/project-atlas/pull/170) | *(enabler)* | enabler | [`dod-compiler/`](../dod-compiler/) |
| TIME | `AS-2.2-TIME-MACHINE-001` | [#168](https://github.com/WezzSide/project-atlas/pull/168) | `AS-2.2-TEMPORAL-001` | feeds | [`time-machine/`](../time-machine/) |
| REALITY-LIVE | `AS-2.2-REALITY-LIVE-001` | [#167](https://github.com/WezzSide/project-atlas/pull/167) | *(enabler)* | enabler | [`reality-live/`](../reality-live/) |
| REALITY-GAP | `AS-2.2-REALITY-GAP-PREP-001` | [#172](https://github.com/WezzSide/project-atlas/pull/172) | *(enabler)* | enabler | [`reality-gap/`](../reality-gap/) |
| RESEARCH | `AS-2.2-RESEARCH-001` | [#171](https://github.com/WezzSide/project-atlas/pull/171) | `AS-2.2-ASK2-001` *(peer)* | feeds | [`research/`](../research/) |
| CONFLICT | `AS-2.2-CONFLICT-UX-PREP-001` | [#181](https://github.com/WezzSide/project-atlas/pull/181) | `AS-2.2-CONFLICT-UX-001` | direct | [`conflict-ux/`](../conflict-ux/) |
| XPROJ | `AS-2.2-XPROJ-CONTRACT-PREP-001` | [#179](https://github.com/WezzSide/project-atlas/pull/179) | `AS-2.2-XPROJ-001` | direct | [`xproj/`](../xproj/) |
| KF2 | `AS-2.2-KF2-FABRIC-PREP-001` | [#186](https://github.com/WezzSide/project-atlas/pull/186) | `AS-2.2-KF2-FABRIC-001` | direct | [`kf2-fabric/`](../kf2-fabric/) |
| ASK2 | `AS-2.2-ASK2-DEEPEN-PREP-001` | [#188](https://github.com/WezzSide/project-atlas/pull/188) | `AS-2.2-ASK2-001` *(peer)* | direct | [`ask-atlas-2/`](../ask-atlas-2/) |
| INTEL | `AS-2.2-INTEL-SLICE-PREP-001` | [#189](https://github.com/WezzSide/project-atlas/pull/189) | `AS-2.2-INTEL-SLICE-001` | direct | [`intel-slice/`](../intel-slice/) |
| CHATGPT | `AS-2.2-CHATGPT-LIVE-PREP-001` | [#191](https://github.com/WezzSide/project-atlas/pull/191) | `AS-2.2-CHATGPT-LIVE-001` | optional | [`chatgpt-live/`](../chatgpt-live/) |
| TEMPORAL | `AS-2.2-TEMPORAL-UX-PREP-001` | [#192](https://github.com/WezzSide/project-atlas/pull/192) | `AS-2.2-TEMPORAL-001` | direct | [`temporal-ux/`](../temporal-ux/) |
| COMPAT-PIN | `AS-2.2-COMPAT-PIN-PREP-001` | [#196](https://github.com/WezzSide/project-atlas/pull/196) | `AS-2.2-COMPAT-PIN-001` | direct | [`compat-pin/`](../compat-pin/) |
| ESTATE-OPS | `AS-2.2-ESTATE-OPS-PREP-001` | [#197](https://github.com/WezzSide/project-atlas/pull/197) | `AS-2.2-ESTATE-OPS-001` | direct | [`estate-ops/`](../estate-ops/) |
| DOC-CHARTER | `AS-2.2-DOC-CHARTER-PREP-001` | [#199](https://github.com/WezzSide/project-atlas/pull/199) | `AS-2.2-DOC-CHARTER-001` | direct | [`doc-charter/`](../doc-charter/) |

## DAG coverage notes

### Named DAG slots with landed PREP

| Roadmap slot | Upstream PREP |
|---|---|
| `AS-2.2-DOC-CHARTER-001` | DOC-CHARTER PREP |
| `AS-2.2-COMPAT-PIN-001` | COMPAT-PIN PREP |
| `AS-2.2-KF2-FABRIC-001` | KF2-FABRIC PREP |
| `AS-2.2-RET-CTX-001` | RET-HYBRID + CTX-COMPILER PREP |
| `AS-2.2-TEMPORAL-001` | TEMPORAL-UX + TIME-MACHINE PREP |
| `AS-2.2-CONFLICT-UX-001` | CONFLICT-UX PREP |
| `AS-2.2-XPROJ-001` | XPROJ PREP |
| `AS-2.2-ESTATE-OPS-001` | ESTATE-OPS PREP (also consumes XPROJ) |
| `AS-2.2-INTEL-SLICE-001` | INTEL-SLICE PREP (consumes RET-CTX, KF2, TEMPORAL, CONFLICT) |
| `AS-2.2-KCI-001` | KCI-ENGINE PREP |
| `AS-2.2-CHATGPT-LIVE-001` | CHATGPT-LIVE PREP *(optional)* |

### Peer slots (named in PREP; not drawn in DAG v1)

| Peer slot | PREP | Notes |
|---|---|---|
| `AS-2.2-ASK2-001` | RESEARCH + ASK2-DEEPEN | Ask Atlas 2 deepen; consumes RET-CTX citations |

### Enabler PREP (no dedicated DAG node)

| PREP | Supports |
|---|---|
| MEM-GOV | Governed agent memory contracts across intelligence surfaces |
| DoD | Definition-of-done compiler; KCI / intel rehearsal |
| REALITY-LIVE | Live reality projection contracts |
| REALITY-GAP | Estate gap register deepening 2.0 reality-gap catalog |

## Integration indexes (not crosswalk rows)

| Package | PR | Role |
|---|---|---|
| `AS-2.2-PREP-STATUS-001` | [#203](https://github.com/WezzSide/project-atlas/pull/203) | Status snapshot — see [`../PREP-STATUS.md`](../PREP-STATUS.md) |
| `AS-2.2-PREP-FIXTURE-ROLLUP-001` | [#242](https://github.com/WezzSide/project-atlas/pull/242) | Fixture-plan + contract-stub rollup — see [`../AS-2.2-PREP-FIXTURE-ROLLUP-001.md`](../AS-2.2-PREP-FIXTURE-ROLLUP-001.md) |
| `AS-2.2-ADV-POOL-001` | [#243](https://github.com/WezzSide/project-atlas/pull/243) | ADV threat-matrix pool — see [`../adv-pool/`](../adv-pool/) |
| README index lanes | [#173](https://github.com/WezzSide/project-atlas/pull/173) · [#195](https://github.com/WezzSide/project-atlas/pull/195) · [#198](https://github.com/WezzSide/project-atlas/pull/198) · [#202](https://github.com/WezzSide/project-atlas/pull/202) · [#244](https://github.com/WezzSide/project-atlas/pull/244) (INDEX-013) · [#245](https://github.com/WezzSide/project-atlas/pull/245) (INDEX-014) | Multi-package restore / rollup+ADV index — see [`../README.md`](../README.md) |

## Sync note (AS-2.2-ROADMAP-CROSSWALK-SYNC-001)

Tip refreshed to `5e2559f096ce6e1a906ee69ded45f8393ff2310b` / TREE `2768267fa1923a3a1108f84fff9d77eae7e794fc` after merge of fixture rollup (#242), ADV-POOL (#243), and README INDEX-013/014 (#244/#245). Product crosswalk row count remains **19**; rollup and ADV-POOL are integration indexes only (not new DAG product slots / not SEMIDX).

## Explicit non-claims

- Not unlock / not release certification
- Enabler rows do not invent new roadmap slots
- Peer slots remain outside DAG v1 until strategy refresh post-unlock
- Machine-readable stub: [`fixtures/crosswalk.fixture.json`](fixtures/crosswalk.fixture.json)
