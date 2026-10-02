# Authority-Compatibility Note for the Global Foundation — 2026-10-02

| Field | Value |
|---|---|
| Purpose | Before adoption: for each restriction in the [operating contract](../ATLAS-GLOBAL-OPERATING-CONTRACT.md), what is restated, what is new or broader, and what the owner must still do |
| Sources read at | `main` `9e22fdfc8ea45b582598ad3823bad868c212ea00`: `autonomy/policy.md` (§1, §2, §5, §9, §11, §12, §16), `GOVERNANCE.md` (Roles, Lifecycle, Exact-hash, Stop boundaries), `src/project_atlas/orchestration/autonomy/dev_contracts.py`, `dev_first_run.py`, `WORKLOG.md` (PR #666 and #683 entries) |
| Status | Analysis only. It grants nothing and decides nothing |

## 1. Scope of the existing authorities

| Source | Scope it actually governs |
|---|---|
| `autonomy/policy.md` | **Only the governed RSI loop** (§1): iterations, roles, grants `G-<n>`, ledger, levels. It is pinned by `policy_sha`; only the owner edits it (§16) |
| `GOVERNANCE.md` | **All governed repository work**: roles, the authorize → implement → IV → certify → owner merge lifecycle, exact-hash rules, and stop boundaries |
| DEVQ contracts (`dev_contracts.py`, `dev_first_run.py`) | **Dev-loop lineages**: sealed WorkItems, `max_attempts` (default 3), `materialize_repair`, and `grant_required: ONE_WORKFLOW_DISPATCH_GRANT` per package |
| Owner mission directives | The mission they name, including explicit "do not merge / dispatch / bypass" boundaries |

The contract's §0 subordinates it to the first two. The scope-discipline bullet in §0 keeps
loop mechanisms in loop scope.

## 2. Restriction by restriction

| Restriction | Existing source and scope | Contract restates | Contract newly defines or applies more broadly | Owner action still needed |
|---|---|---|---|---|
| Merge / mutate `main` | GOVERNANCE Roles and Lifecycle steps 5–6 (all work); policy invariants 2 and 7 (loop) | Yes | Nothing new | None |
| Dispatch / consume execution grants | DEVQ package `grant_required` (dev loop); mission directives | Yes, for dev-loop work | States it as the default boundary for **all** agent work | Confirm (D1) |
| **Owner approval vs verifiable grant issuance** | GOVERNANCE: "Owner (or Owner-backed directive)" authorizes; no channel specified. policy §5: verifiable `G-<n>` grants (loop only); signatures off at level 0 (§12.3) | — | **New distinction**: owner approval (session or PR) vs verifiable issuance, which is a signature by an owner key held off agent hosts. It does not decide whether approval lets an *agent* perform boundary actions; agents fail closed until decided | **D1**, **D5** |
| **Agent-drafted text vs authenticated owner adoption** | `policy.md` header: proposed by the executor, "binding when the owner merges it" (repository practice) | Yes: an owner merge is governance adoption | Makes explicit that an owner-account merge is **not machine-verifiable** here. OBSERVED 2026-10-02: the agent host's `gh` token is the owner account `WezzSide` with admin rights; `main` requires 0 reviews and no signatures; environment `atlas-vps02` has no protection rules | **D5** |
| **Per-iteration authority** | policy invariant 5: "granted per iteration by a verifiable grant, never standing" (**loop only**) | Yes, scoped to the loop (OC-B) | Outside the loop: no new WorkItem or dispatch without owner authority. Per-dispatch vs per-lineage granularity is **not** decided | **D3** |
| **Repair after failed IV** | GOVERNANCE stop: "validation fails → stop and escalate" (general). policy §9 REDESIGN within the retry cap (loop). `materialize_repair` within `max_attempts` (DEVQ) | Yes, each in its own scope (OC-D) | Nothing new. An earlier draft universalized the GOVERNANCE stop; corrected | **D4** (only if wanted beyond those mechanisms) |
| Classifier / gate / CI bypass | Mission directives; WORKLOG #683 precedent | Yes | **New**: non-grantable by any directive, including an owner-issued one. The owner can still change configuration directly | Accept or adjust |
| Reading secret values | Mission directives; `secrets.py` posture (metadata only) | Yes | **New**: non-grantable by directive | Accept or adjust |
| HALT / HALT-REQUEST | policy invariant 4 and §4.1 rule 1 (loop): any role creates; only the owner removes | Yes | Applies "may create" to non-loop agents as well. Creating is protective and never blocks owner action | Accept or restrict |
| Editing `policy.md` / `loop.yaml` | policy §16 (owner only) | Yes; agents may propose in a PR | Nothing new | None |
| Trust roots, host privilege, production state | Mission directives; `trust.require_trust_current_for_merge` | Yes | Collected as one default list | None |
| Platform denial precedence | WORKLOG #653 (incident) and #683 (precedent) | Yes | **New, explicit**: no signature, preflight or approval overrides a platform or classifier denial | None |

## 3. Open owner decisions

These are the points where current policy and the owner's desired bounded autonomy are not
yet reconciled. The contract does not decide them.

| ID | Decision | Current posture (fail closed) | Options |
|---|---|---|---|
| **D1** | May an agent perform a boundary action (merge, dispatch) on owner approval given in a session, when the platform permits it? | No: the agent prepares the action, the owner performs it. Consistent with #653 being recorded as an incident | (a) Keep. (b) Allow only for actions named exactly (identity, digest, count, expiry) in the approval. (c) Require verifiable issuance (F2) |
| **D2** | Who may issue machine-verifiable dispatch grants outside the loop, and where do they live? | No mechanism exists. `autonomy/grants/**` is supervisor-writable from loop level 2 (policy §4, §11), so dispatch grants there would let a supervisor mint them | (a) Owner-only path outside `autonomy/grants/**`. (b) Policy edit excluding DG grants from supervisor scope at every level. See the [F2 brief](./2026-10-02-OPERATIONAL-SLICE-AND-F2-BRIEF.md) |
| **D3** | Per-dispatch or per-lineage authority outside the loop? | Per dispatch (each DEVQ execution needs `ONE_WORKFLOW_DISPATCH_GRANT`) | (a) Keep per dispatch. (b) One grant per lineage, covering up to `max_attempts` sealed executions, each still bound to its own seal and inputs digest |
| **D4** | Bounded repair after a failed IV outside the RSI loop and DEVQ lineages? | GOVERNANCE stop and escalate | (a) Keep. (b) Allow when the owner-approved WorkItem declares a repair budget |

| **D5** | How is owner action authenticated when agents run with the owner's GitHub account? | Not distinguishable: `merged_by` = `WezzSide` either way | (a) Give agent hosts a separate, least-privilege identity (bot or fine-grained token without merge/admin). (b) Keep the shared account, and make every machine-checked grant require an owner signing key held off-host, with a pinned signer list. (c) Both |

Protections the note does **not** reopen: classifier, gate and CI integrity; secret values;
HALT removal; trust roots; `policy.md` and `loop.yaml`.
