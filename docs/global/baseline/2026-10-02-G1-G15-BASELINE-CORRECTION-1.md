# Correction 1 to the G1–G15 Baseline of 2026-10-02

| Field | Value |
|---|---|
| Corrects | [2026-10-02-G1-G15-BASELINE.md](./2026-10-02-G1-G15-BASELINE.md). The original is preserved; this file supersedes the specific wording below and nothing else |
| Observed at | `main` `9e22fdfc8ea45b582598ad3823bad868c212ea00` (merge of #1043, 2026-10-02T12:12:23Z), GitHub read-only |
| Why | Several negative claims were broader than the evidence that supported them, and the successful DEVQ-0001 history was under-stated |

## 1. Evidence dimensions and verdict vocabularies

These are separate dimensions. A result in one says nothing about another.

| Dimension | Vocabulary | Where it lives |
|---|---|---|
| **V1: automatic fabric verification** (`atlas-runner-verify.yml`) | `VERIFIED` / `REJECTED` / `UNESTABLISHED` | `atlas-verification-report` artifact. It checks the executor evidence fragment and runner identity, not code semantics |
| **V2: GitHub CI** (`ci.yml`) | success / failure / cancelled | Check runs on an exact head |
| **V3: dev-loop contract verdict** | `PASS` / `FAIL` (`VerdictRecord`, `dev_contracts.py:82,253`); planner phase `INTEGRATION_READY` | In-process or caller-supplied Crosswalk path. **No persisted instance found** |
| **V4: independent session-based candidate review** | P0/P1/P2 findings, text | GitHub comments or PR bodies |

Other dimensions:
- **Locally replayed Crosswalk state:** none found in the repository or `~/cowork-global`.
- **Durable production lineage:** not inspected; live service directories were deliberately not entered.

Separately: prepared ≠ independently reviewed ≠ merged ≠ runtime-wired ≠ deployed ≠
exercised live ≠ operating without manual relay.

## 2. Corrected statements

| Original wording | Evidence scope | Corrected wording |
|---|---|---|
| "No dev-loop result has ever received a `VERIFIED` verdict"; "the 10 most recent atlas-runner-verify.yml runs all concluded failure" | All 14 `atlas-runner-verify.yml` runs and their report artifacts. Window: 2026-09-27 → 2026-10-01. **V1 only** | Under V1, none of the 10 `atlas-agent-execute.yml` runs (36754123746 … 36905529693) received `VERIFIED`. Each verify run on them (36754274155 … 36906255665) reported `REJECTED` on a single **precondition**, `evidence_fragment_present=FAIL`, because the executor never uploaded its evidence fragment. **V1 never examined any candidate's content**, so this is not a judgement on any code. V1 *did* return `VERIFIED` (7/7) for three `atlas-runner-acceptance` runs on 2026-09-27. The DEVQ-0001 E6 candidate was never submitted to V1; its assurance is V2 plus V4 (section 3). (OBSERVED) |
| "The loop has never run live as a loop"; "No non-test caller constructs `Planner` or `FabricAdapter`" | `git grep` over `src scripts autonomy .github` at `9e22fdfc`; `pyproject` entrypoints | No non-test code constructs `Planner`, `FabricAdapter`, `Crosswalk`, `SpoolTransport` or `GitHubRestPort`. So the closed planner → adapter → verifier → verdict → repair cycle has **not run as one automated process** (OBSERVED). This does **not** mean nothing ran live: DEVQ-0001-E6 was a live, owner-dispatched execution of a sealed dev-loop package that produced a real, merged implementation (section 3). The loop components were not in that path |
| "`autonomy/ledger.jsonl` is empty" | That file only; the repository and `~/cowork-global` searched for Crosswalk, spool or ledger JSONL | `autonomy/ledger.jsonl` is the **governed RSI loop** ledger (`policy.md` §8), a separate system from the dev loop, and it is empty. **That proves only that this file is empty.** No dev-loop Crosswalk or spool instance was found in the repository or `~/cowork-global`. Live service state was **not inspected** (UNKNOWN). DEVQ-0001 lineage is recorded outside any machine ledger (section 3) |
| "DEVQ-0001 reached main only through owner dispatch, a manual byte rescue and an owner merge"; "#1040 merged without a recorded IV artifact" | PR #1040 reviews, comments, timeline and CI; issue #757; branch `atlas/agent-36865220709-1` | See section 3. Owner dispatch, recovery and merge were owner relays (OBSERVED). V4 IV of the exact candidate **is** recorded, as text in the #757 closure comment, written 93 minutes after the merge. There is no GitHub review, no `docs/evidence` receipt and no V1 verdict for #1040 |
| G06/G11: "IV for several merged PRs exists only as PR body text" | Comments and reviews on #1032, #1039, #1040, #1041, #1042, #1043, #1045 | Only #1043 has an IV report as a standalone PR comment. #1040's IV is in the #757 closure comment. #1042's IV is a claim in its own PR body (exact head `4a4131b3`). #1041 cites IV on `d3363e0e` and relies on a byte-identity transfer that is not separately recorded (INFERRED). #1032, #1039 and #1045 have no IV record on GitHub. None is a repository artifact or a V1/V3 verdict |
| G14 / envelope row: "#1043 not merged", "READY_FOR_OWNER_MERGE" | GitHub, 2026-10-02T12:12Z | **Superseded by events.** #1043 was owner-merged as `9e22fdfc`. Its parents are `7373092` and `cea7ede8`, and its tree `fec888ed` is byte-identical to the IV'd candidate. **Merged ≠ exercised live:** the salvage path has not run on a real envelope rejection |

Status changes from this correction: no goal changes status. G06 stays `PARTIAL`, with the
evidence restated above. G12 stays `PARTIAL`. G14 stays `PARTIAL`: #1043 is merged but not
yet exercised live.

## 3. DEVQ-0001 historical evidence (preserved; not an autonomous-runtime proof)

| Item | Identity |
|---|---|
| Package | `docs/autonomy/first-run/ATLAS-DEVQ-0001.package.json`, execution `ATLAS-DEVQ-0001-E6`, `work_seal` `3eea9b4d1b95e22e1593861266230debbf7e287843f9d40a01770fb68e9a0d48`, base `01f04329283dfe2088a80d602f99c4b29856081f`, authority `OWNER-DIRECTIVE-2026-09-30-AUTONOMOUS-LOOP-FIRST-TASK` |
| Implementation | Run 36865220709 (2026-10-01T12:56Z, owner dispatch). The agent step succeeded (`is_error=false`, 19 turns). The post-agent infra-test gate then failed for an environment reason, so push was skipped (fixed later by #1039) |
| Recovery | `eff0e0929b23197059ed8d9df6a71fb0313e0554`, tree `83d6455f…`, on `atlas/agent-36865220709-1`. Recovered byte-for-byte from the VPS2 workspace capture (tarball sha256 `86b7b46d…354b`, recorded in the commit trailer). No model re-run |
| Acceptance | 5/7 new F14 tests fail on the sealed base; 7/7 pass on the candidate (#1040 body, #757 closure comment) |
| CI (V2) | Run 36877979202 on head `eff0e092`: 4/4 success |
| Independent review (V4) | "P0=0 / P1=0, each guard mutation-checked": [#757 closure comment](https://github.com/WezzSide/project-atlas/issues/757#issuecomment-5936065975), written after the merge. Whether the reviewer was a separate session from the recovering session is **not recorded** (UNKNOWN) |
| Integration | PR #1040, owner-merged as `7cac4f1a9e223e44e942900ae0a80f2a4e09e4ab` (merge tree = candidate tree). Issue #757 closed `completed` |

## 4. Dispatch accounting

| Lineage | Physical dispatches | Implementation-bearing attempts | Notes |
|---|---|---|---|
| DEVQ-0001 historical epoch | 9 (runs 36754123746 … 36865220709) | 1 (E6) | Runs 1–4 failed in toolchain setup, before any task prompt rendered. Their attribution to DEVQ-0001 rests on the owner's accounting (INFERRED). Runs 5–8 (E2–E5) failed before any model usage |
| DEVQ-0002 | 1 (E1, run 36905529693) | 1 (E1) | Envelope failure (25 turns > 20). Product verdict `UNOBSERVABLE` |

**DEVQ-0002 ceiling.**
- Total ceiling: 3 implementation-bearing attempts.
- E2 would be attempt 2/3 if model execution begins.
- After that, only **one** further implementation-bearing attempt remains, unless the owner
  changes the ceiling.

## 5. Not accessible from this workspace

- **Live service directories:** the `/var/lib/atlas-*` state. Any runtime Crosswalk, spool or planner state would live there.
- **E6 working files:** the VPS2 workspace capture tarball and the E6 agent transcript.
- **The #757 IV session's own report:** reviewer identity and mutation output.
- **Per-run dispatch inputs:** the API does not return them and the workflow sets no `run-name`.
- **DEVQ-0002-E1 identities:** the package, seal and inputs hash.
