# Ultimate Atlas — Conflicts with `main` and boundary decisions (ULT-00 Step C)

Rule applied: where `main` is more advanced or more precise than the package,
`CURRENT MAIN WINS` and the target is adapted. Where a proposal would weaken a
stable invariant, or asserts something the repository cannot support,
`STOP THAT PROPOSAL` (re-shape or defer). Nothing here weakens a gate.

## A. CURRENT MAIN WINS — package statements corrected by repository truth

| # | Package says | `main` truth | Consequence |
|---|---|---|---|
| A1 | `main` included `e264d599` | `main` is `9972d164`, nine commits later (docs/seal only) | All figures in this reconciliation are bound to `9972d164` / tree `2067f125` |
| A2 | Atlas 3 commands are reached as `atlas atlas3 …` (implied by §35.2 / §68 module naming) | `register_atlas3_parsers` registers on the **top-level** subparser (`cli.py:3406-3409`): `atlas pulse`, `atlas start`, `atlas proof`, `atlas iv-bind`, … (35 commands, `atlas3/cli.py:105-561`) | Any new ULT command is a top-level `atlas <cmd>` registered in `atlas3/cli.py`; root `cli.py` stays untouched (additive-only guard) |
| A3 | "Atlas already has a canonical executable governance skill" (§11) | There are **two** skill systems with incompatible manifest schemas; readiness authorizes only `skills/atlas-governed-work`; `receipt_gate` applies its strongest checks only to that id | ULT-04 cannot start from "the" skill; the owner must first name the canonical one (owner gate) |
| A4 | Capability registry AT3-004 "should become the vocabulary layer for a real capability-control system" (§14) | AT3-004 is a semantic taxonomy with no enforcement (`SECURITY_CLASSES` never read; process-global mutable `REGISTRY`). The enforceable grant substrate already exists as `AgentLease` + `grant_lease` scope-expansion refusal + `DirectivePermissions` in `orchestration/autonomy` | ULT-05 builds `CapabilityGrant` as a typed projection of a lease bound to an execution identity; AT3-004 remains definition-only |
| A5 | Verifier pool, principal registry, evidence invalidation graph, handoff generation, seal planning are "New" for ULT-07/09 | All five are prototyped in the open, unmerged `atlas-dag` stack #720–#739 (`scripts/atlas_dag/`, outside `src/` and outside lint/type scope, GitHub-issue event bus) | Reconciliation records them as `OPEN_PR_CANDIDATE`. ULT-07 must not re-invent them **and** must not assume them. The owner decides compose-vs-supersede before ULT-07 starts. ULT-01a adopts #735's dependency-class names (`DEP_HEAD`, `DEP_TREE`, `DEP_TOOLCHAIN`, `DEP_PLATFORM`, …) as the invalidation vocabulary so the two converge if #735 lands |
| A6 | AutoLab promotion loop sketch (§28) | `opt_gate.decide_promotion` already encodes hard-gates-precede-score, forbidden caller-supplied verdict keys, sealed envelopes and an out-of-process holdout broker with an attempt budget | ULT-10/15 adopt `opt_gate` as the template; no new promotion engine |
| A7 | Exact-object binding "at minimum repository / commit / tree" (§4.5) | `iv_bind`/`adv_bind` bind head+tree only; repository identity exists only as the constant `CANONICAL_REPOSITORY_IDENTITY` (`autonomy/models.py:64`); `base_tree` is absent from the dispatcher's pins | ULT-01a adds `repository` and `base_tree` to the identity and binds IV/ADV/proof to it |
| A8 | "Atlas Start … explicit-budget philosophy; upgrade to a real tokenizer" (§9.3) | `start.py` counts characters and labels them tokens; truncation is emitted as `UNKNOWN`; provenance is dropped; the CURRENT refusal is bypassed when a state lens exists | These are honesty defects, not v2 features. ULT-03a fixes them **before** any tokenizer work |
| A9 | Package's atlas3 module list (§3.1) | Matches `main` except `__init__.py`; `orchestration/` also contains `autonomy/`, `origination/`, `sdk/` which hold the real leases, DAG, budgets and process ports | Reuse map points at those subpackages, not only at `orchestration/dispatcher.py` |
| A10 | Ledger "append-only" (§6.1, §41.2) | Alteration is detected per row; **deletion/truncation is not** (no inter-row chain); O(n) re-read per append; no lock | Recorded as a ledger residual for a later package; not an ULT-01a concern |

## B. STOP THAT PROPOSAL — re-shaped or deferred to protect invariants

| # | Package proposal | Why it must not land as written | Re-shaped form |
|---|---|---|---|
| B1 | UEI `environment.runtime_image_digest`, `network.egress_policy_hash`, `cpu_profile`, `memory_limit` as identity fields (§7) | No container, sandbox, or egress control exists on `main`. Populating these would fabricate an environment claim (`INFERRED → OBSERVED` collapse) | Identity v1 carries only what is observable now: OS, arch, Python version, declared tool versions. Every unobservable dimension is an explicit `UNKNOWN`, and evidence bound to an `UNKNOWN` environment is marked `environment_bound: false` |
| B2 | "Use a real tokenizer / model-aware budget" (§9.3) | A tokenizer is a dependency addition (§61 justification: offline, Windows, license, size) and a model-vendor coupling in the core (§4.7) | Honest unit first (`budget_unit: "chars"`), then an optional offline tokenizer **port** with a stdlib fallback; never a required download |
| B3 | `CapabilityGrant` built on `atlas3.capabilities.REGISTRY` (§14) | That registry is process-global, mutable, unauthenticated and unpersisted; using it as an authorization store would create a second authority system | Build on `AgentLease`; see A4 |
| B4 | "Verifier mesh" runtime roles that emit `certified_for_merge` or promote proof (§22) | `VERIFIER RESULT != MERGE AUTHORITY`; AT3-051/052 already hardcode `certified_for_merge: False`; GOVERNANCE forbids the same actor implementing and verifying | Any verifier-role runtime stays declaration-only until a principal registry exists in `src/`; output can never carry a merge or certification field other than `False` |
| B5 | SLSA/in-toto provenance (§26.3, §43) | No lock file, no SBOM, no signing keys; a compliance claim would be false | Reuse the *concepts* (subject, builder, materials) in `EvidenceAttestation` field names; state `SLSA_LEVEL = NOT_CLAIMED` |
| B6 | Multi-hop, test-time scaling, critic early-stop (§19–20, ULT-09/11) | `MAX_ACTIVE_DISPATCHES = 1` and read-only-only dispatch are deliberate gates; the prerequisites the package itself lists (grants, identity, backends, verifier mesh, evals, budgets) are all `MISSING` or `PARTIAL` | Unchanged: last waves. No ULT-00 proposal touches `orchestration/dispatcher.py` |
| B7 | Property/mutation/fuzz libraries "now" (§24) | Adds dev dependencies and CI time on three runners incl. Windows; owner decision under §61 | Owner gate; until then keep the manual negative-control discipline the WORKLOG seals already use |
| B8 | New planning files `NORTH-STAR-EXTENSION.md`, `TARGET-ARCHITECTURE.md`, … in ULT-00 | Writing target architecture before the owner accepts the reconciliation would let a roadmap outrank re-derived truth (precedence §0.2) | Only the four ULT-00 files exist; the rest are per-wave deliverables |
| B9 | `atlas agent run`, `atlas proof verify`, `atlas route explain`, … CLI aliases (§35.2) | `atlas proof` exists; adding parallel verbs risks the "no alias proliferation" rule (AT3-072) and the cli.py additive-only guard | Extend `atlas proof` (`--attestations`, `--identity`) in ULT-01a; no new top-level verbs in ULT-00/01a |

## C. Wave-order adjustments (dependency edges re-derived)

```text
ULT-00  reconcile (this)                                   ── done, owner-gated acceptance
ULT-01a ExecutionIdentity v1 + EvidenceAttestation v1       ── no prerequisites; isolated atlas3/ + atlas_contracts/
        + object-bound proof v2 (v1 compat)
ULT-01b proof DAG + observation adapter (reuse dispatcher    ── after 01a
        observe_binding_pins; IV/ADV consume identity)
ULT-03a Start honesty (unit, truncation, provenance,        ── after 01a (binds base_tree); before any "Start v2"
        CURRENT bypass)
ULT-02a ProgramIntelligencePort + stdlib-ast extractor       ── after 01a (blob/tree keys); independent of 03a
ULT-05a CapabilityGrant = lease × identity projection        ── after 01a; independent of 02/03
ULT-05b MCP response scanning + untrusted label              ── independent; small
ULT-04a single SkillManifest schema                          ── owner names the canonical skill first
ULT-06a ExecutionBackend port over the two existing backends ── after 05a
ULT-07  verifier mesh                                        ── after 01b; owner decision on #733/#735 first
ULT-08a hypothesis decision + first properties               ── owner dep decision; after 01a
ULT-10a eval/outcome types                                   ── after 01a (environment hash)
ULT-09 / 11 / 12 / 13 / 14 / 15 / 16 / 17                    ── unchanged from the package; all after 07 and 10
```

Edges that the package drew and `main` does not support: ULT-04 → ULT-05
(genome before broker). On `main` the grant substrate (leases) exists and the
genome does not, so ULT-05a precedes ULT-04 and does not depend on it.
