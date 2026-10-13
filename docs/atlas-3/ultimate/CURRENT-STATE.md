# Ultimate Atlas — Current state on `origin/main` (ULT-00 Step A)

Everything below was re-derived on 2026-09-08 from a detached read-only
worktree at `origin/main`. File references are `path:line` at that tree.
Nothing was inferred from the Ultimate package's own module list.

## 1. Exact object

```text
ORIGIN_MAIN            = 9972d16448a9cefd3444363b59eae2c0dbed7f20
ORIGIN_MAIN_TREE       = 2067f1253d411e3bef9fd20ad534b29316b6e953
TIP_COMMIT             = Merge pull request #737 (AS-OBSIDIAN-CAPTURE-001 F6/F7 seal)
PACKAGE_OBSERVED_SHA   = e264d5991eb19e3f197c26a604a7cebcbd4f731e  (ancestor; 9 commits behind; docs/seal only)
WORKING_CHECKOUT       = feat/linux-filesystem-portability @ 8bf7d4de (NOT main; one untracked docs/architecture/ file)
```

## 2. Gate and validation evidence at the exact head

Source: GitHub Actions run `34267660838` on commit `9972d164` (CI workflow
`.github/workflows/ci.yml`). Figures are read from that run's logs, not from a
local re-run; a local full-suite run from this checkout would resolve
subprocess tests through the editable install of the *feature* branch and is
therefore not a valid figure for `main`.

| Job | Result |
|---|---|
| `quality (ubuntu-latest, 3.12, full)` | ruff `All checks passed!`; mypy `Success: no issues found in 405 source files`; pytest **5682 passed, 8 skipped, 4 xfailed** |
| `quality (ubuntu-latest, 3.13, compat)` | 5682 passed, 8 skipped, 4 xfailed |
| `quality (windows-latest, 3.12, windows)` | 5626 passed, 61 skipped, 3 deselected, 4 xfailed; product-perf 3 passed |
| `control-plane` | success |

Static inventory at the tree: 514 test files under `tests/` (5172 `test_`
functions), 71 of them `tests/unit/test_atlas3_*`; control plane
`atlas-vault-documentation/tests/`: 13 files, 177 tests, outside the main
ruff/mypy scope by design (`pyproject.toml:55` includes only `src/**` and
`tests/**`).

Governance state (unchanged by this wave):

```text
FULL_LIVE_DEMO_READY                 = NO
ATLAS_3_CERTIFIED_SURFACE_MUTATION   = DENY   (tests/unit/test_atlas3_demo_isolation_001.py, DENY list of 12 paths)
MERGE_AUTHORIZATION                  = NOT_GRANTED
ATLAS_OPT_WAKE_GATE                  = CLOSED (constant, src/project_atlas/opt_gate.py:39)
PACKAGE-MATURITY.json                = 62 implementation-unlocked, 1 prep-frozen (chatgpt-live-2x)
cli.py additive-only guard           = any diff to src/project_atlas/cli.py must be the two atlas3 hook insertions and remove nothing
```

Runtime footprint: `src/project_atlas/atlas3/` has 46 modules plus
`memory/` (D-192); `src/project_atlas/orchestration/` has `autonomy/`,
`origination/`, `sdk/` and 9 top-level modules; `src/atlas_contracts/` has 7
modules. Package version `2.0.0`, Python `>=3.12`, runtime deps `pydantic`,
`PyYAML`, `jsonschema` only; no Python lock file (`deps/integrity.json` says so).

## 3. Open pull requests that overlap the Ultimate package (all OPEN, none merged)

`OPEN_PR != LANDED`. These are recorded so that no ULT wave re-invents them,
and so that no ULT wave *assumes* them.

| PR | What it prototypes | Where | Overlaps ULT wave |
|---|---|---|---|
| #720 | `atlas-dag` read-only coordinator MVP: `ATLAS_EVENT_V1`, `ATLAS_IV_RECEIPT_V1` (head+tree bound), verifier pool parsed from issue #719, `session_id` on events/receipts | `scripts/atlas_dag/`, `schemas/` | 01 (identity), 07 (verifier) |
| #723 | evidence cache with proof-gated reuse (D-009); lane-scoped ownership | `scripts/atlas_dag/evidence.py` | 01 |
| #725 | `ATLAS_AGENT_REGISTRY_V1`: agent profiles, capability enum, `write_scopes`, prohibitions; `satisfy_formal_iv` unconditionally denied | `scripts/atlas_dag/agents.py`, `registry/agents.json` | 04, 05 |
| #727 | agent-aware *work* router (not model routing) | `scripts/atlas_dag/router.py` | 05 |
| #730 | stacked-PR DAG from live ancestry | `scripts/atlas_dag/stack.py` | 11 |
| #732 | git-object resolution for events; actor/session bound from registry; TOCTOU re-resolve | `scripts/atlas_dag/emitter.py` | 01 |
| #733 | `ATLAS_VERIFIER_POOL_V1` + principal registry; `AUTHOR_CONFLICT_BLOCKED`, `SESSION_CONFLICT` | `scripts/atlas_dag/verifiers.py`, `registry/verifiers.json` | 07 |
| #734 | CI/IV dispatch planner; IV lane blocked while pool unbound | `scripts/atlas_dag/dispatch.py` | 07 |
| #735 | evidence dependency/invalidation graph: `DEP_HEAD, DEP_TREE, DEP_PARENT_HEAD, DEP_MAIN_HEAD, DEP_PATH_SET, DEP_SUBSYSTEM, DEP_PLATFORM, DEP_TEST_SET, DEP_TOOLCHAIN, DEP_VERIFIER_PRINCIPAL, DEP_VERIFIER_SESSION`; `REUSABLE_BY_PROVEN_EQUIVALENCE` | `scripts/atlas_dag/evidence_graph.py` | 01, 07 |
| #736 | `ATLAS_HANDOFF_V1` generation with `truth_fingerprint` | `scripts/atlas_dag/handoff.py` | 09, 12 |
| #738 | `ATLAS_POSTMERGE_PLAN_V1` seal planning; `MERGE_ELIGIBLE != MERGED != SEALED` | `scripts/atlas_dag/seal_plan.py` | 07 |
| #739 | deterministic frontier scoring; `PRIORITY != AUTHORITY` | `scripts/atlas_dag/score.py` | 10 |
| #705 / #709 | ten role skills as Markdown, `development-tooling-manifest.json` (unhashed), MCP client config + token indirection; #709 duplicates #705's files with different content | `docs/agent-skills/`, `scripts/` | 04, 05 |
| #711 | Windows resident-driver PID contract (`write_host_identity`) | `orchestration/sdk/resident_windows.py` | 01 (OS identity only) |
| #703 | additive `atlas validate-report` via the atlas3 seam | `atlas3/validate_report.py` | none |

Facts that matter for reconciliation:

- #720→#739 is one linear stack (each PR based on its predecessor's branch).
- All twelve live **outside `src/`** and therefore outside the ruff/mypy
  scope; their event bus is a GitHub issue body; their state is a gitignored
  `.atlas-runtime/dag.json`.
- Zero of the twenty open PRs touch a DENY-listed file or `src/project_atlas/cli.py`.
- Only #703, #711 and #694 touch anything under `src/`.

## 4. Subsystem classification (ULT-00 required inventory)

Vocabulary: `LANDED_COMPLETE` · `LANDED_PARTIAL` · `ISOLATED_RUNTIME` (in
`atlas3/`, declared/fixture input, no certified-surface mutation) ·
`CONTRACT_ONLY` · `OPEN_PR_CANDIDATE` (exists only in an unmerged PR) ·
`ROADMAP_HORIZON` · `MISSING` · `SUPERSEDED`.

| # | Ultimate subsystem (package §) | Classification | What actually exists on `main` |
|---|---|---|---|
| 1 | Truth Kernel (§6.2) | LANDED_COMPLETE | `knowledge_compiler.py` (claims, authority, conflicts, UNKNOWN), `lineage.py`, `source_identity.py`, `bitemporal.py`/`kdiff`; all frozen by the DENY list |
| 2 | Evidence plane / event ledger (§6.1, §41.2) | LANDED_PARTIAL | AT3-003/014: `atlas3/events.py` 26-field envelope, 21 event types, content-bound `event_id`; `atlas3/ledger.py` JSONL under `generated/ops/atlas3/ledger/`, idempotent replay, per-row re-verification, `EVENT_ID_COLLISION`. **No inter-row chaining** (deletion/truncation undetectable), O(n) re-read per append, no lock |
| 3 | Universal Execution Identity (§7) | MISSING as contract; primitives PARTIAL | `orchestration/dispatcher.py:690-695` *observes* `base_main`/`candidate_head`/`candidate_tree` via `git rev-parse`; `evaluate_identity_binding` (`dispatcher.py:736-782`) strict-compares them on the result envelope, but they travel as untyped strings in `observations.extras`; **`base_tree` absent**; repository identity only as the constant `CANONICAL_REPOSITORY_IDENTITY` (`orchestration/autonomy/models.py:64`); environment / tools / model identity absent everywhere; canonical digest exists (`orchestration/router.py:56-67`) but is re-implemented in 10+ modules with an `ensure_ascii` fork (`atlas_contracts/event_package.py:94` vs `agent_control/authority.py:28`) |
| 4 | Proof v2 / evidence attestation / proof DAG (§26) | LANDED_PARTIAL (v1 presence-only) | AT3-050 `atlas3/proof.py`: 8-stage flat vector, evidence accepted if `dict` with truthy `evidence_ref` (`proof.py:52-66`); no digest, no head/tree, no producer, no DAG; overwrites `generated/ops/atlas3/proof/<task>.json` per call; **2 tests**, no negative control beyond the model-claim flag. AT3-051/052 `iv_bind.py`/`adv_bind.py`: bind four 40-hex SHAs, `TARGET_MOVED`, independence by a 4-word denylist, **observed SHAs are parameters**, no repo id, no hashing. Content-addressed `EvidenceBundle.payload_sha256` exists in `orchestration/autonomy/models.py:943`. Proof-DAG shape exists only in `autonomy/models.py` (`DagEdge`, `ExecutionPlan`) and in open PR #735 |
| 5 | Program Intelligence Graph (§8) | ISOLATED_RUNTIME (declared only); extractor MISSING | Twin vocabulary landed: 19 nodes, 15 relationships (`atlas3/domain.py:15-57`), `GRAPH_REUSE` aliases to AS-GRAPH-003. `file_graph.py`, `inventory.py`, `impact.py`, `truth_graph.py` read `generated/ops/atlas3/<area>/<project>/declared.json` and refuse rows without `evidence_refs`. **Nothing in `src/` imports `ast`, `tree_sitter`, `libcst`, or references SCIP/LSP**; `symbol` is a declared row `{name, file_path, kind}`. No blob/tree-keyed cache (`compile_cache.py` takes caller-supplied fingerprints) |
| 6 | Context Compiler v2 / Start v2 (§9) | LANDED_PARTIAL with recorded defects | AT3-030 `atlas3/start.py`: 11 sections, deterministic, fail-closed on missing budget/freshness. Defects: budget measured in **characters** (`start.py:42-47`) but named `token_budget`/`tokens_remaining`; sections drop provenance to a count string (`start.py:127`); budget exhaustion is emitted as `UNKNOWN`, indistinguishable from a real UNKNOWN (`start.py:43-56`); no tree/snapshot binding; `CURRENT` stale-refusal is bypassed when a state lens exists (`start.py:104-107`). The 2.x compiler `runtime_22.py` already has `_reason_included` (`:343-359`), overflow `dropped_count` (`:762-766`), per-entry provenance (`:627`) and `compat_snapshot_id` (`:727`) — the pattern exists and was not applied. AT3-054/055 memory context compiler is consume-only |
| 7 | Epistemic / freshness annotation (§10) | LANDED_PARTIAL | Freshness states in `atlas3/memory/freshness.py`; but `classify_freshness` (`:42-59`) only reaches `CURRENT`/`STALE` for PostgreSQL version text (fixture logic in a general module). `OBSERVED/DERIVED/INFERRED/CONTESTED` epistemic states as a first-class field: MISSING (`authority_class` on events has `derived/observed/non-canonical`) |
| 8 | Typed Skill Registry (§11) | LANDED_PARTIAL (integrity layer) / MISSING (registry, eval) | Two skill systems with **incompatible manifest schemas**: `atlas-vault-documentation/skill/skill-manifest.yaml` (`skill.file`) and `skills/*/skill.yaml` (`skill.entrypoint`); `skill_loader.py` hashes `SKILL.md` bytes and ignores both fields; `readiness.py` promotion requires an HMAC authority grant (`authority.py`); `agent-readiness.yaml` authorizes only the `skills/atlas-governed-work` hash; `receipt_gate.validate` applies its extra checks only when `skill.id == "atlas-governed-work"` (`receipt_gate.py:27`). No registry index, no skill eval/performance record, `version_at_least` drops non-numeric segments (`skill_loader.py:19-22`). `SkillBinding` = `{id, version, sha256}` (`atlas_contracts/agent_event.py:41-48`). Prose role skills in open PR #705/#709 |
| 9 | Agent Genome compiler (§12) | MISSING | Three unsynchronized adapter registries (`adapter_registry.py` 5 entries, `config/agent-registry.yaml` 3, `config/agent-readiness.yaml` 2); `AgentRecord`/`AgentLease` in `orchestration/autonomy/models.py`; agent registry with capability enum in open PR #725 (scripts) |
| 10 | Model Router (§13) | MISSING | `orchestration/policy.py` routes *work to roles* (`RUNTIME AUTOMATIC ROUTING NOT IMPLEMENTED`, `policy.py:16-17`); `provider_adapters.py` is a registry of stubs hard-coded `enabled: False`; `DEFAULT_MODEL` hardcoded in `orchestration/sdk/models.py:26` and `openai_responses_poc.py:34`; budgets exist in `sdk/cost_guard.py` (not wired into `run_dispatch_once`) |
| 11 | Capability Broker (§14) | LANDED_PARTIAL, fragmented | Three unlinked capability models: `atlas3/capabilities.py` (8-field semantic taxonomy, `SECURITY_CLASSES` declared at `:20` and never read, process-global mutable `REGISTRY` at `:50`), `authz.py:32-47` (14 literals, enforced, gates MCP), `agent_control/capability.py:13` (self-declared levels 0–3). Closest grant analogue: `AgentLease` (`autonomy/models.py:396-414`: `base_pin`, `authorized_paths`, `forbidden_paths`, `capabilities`, `expiry_or_terminal_condition`) + `grant_lease` fail-closed on scope expansion (`autonomy/leases.py:32-74`) + `DirectivePermissions` six `Literal[False]` flags (`models.py:322-346`). No token, no principal registry in `src/` |
| 12 | MCP gateway / untrusted-output boundary (§15.1, §17) | LANDED_PARTIAL | `mcp_server.py`: 16 read-only zero-argument tools, `authz` `mcp.read` gate, tool-id sanitizer, 16 forbidden request keys; `mcp_registry.py` structurally denies `vault-write`/`estate-scan` (`provider-generate` not in that guard, `:202-205`). **No output scanning, no egress policy, no size cap, no audit log** on the read path (`mcp_server.py:200-207`) |
| 13 | ACP / A2A adapters (§15.2–15.3) | MISSING (vocabulary only) | `atlas3/surface.py` lists `a2a` as a surface name; no ACP anywhere |
| 14 | Execution backends / brain-hands (§16) | LANDED_PARTIAL (T0/T1) | `orchestration/local_process_transport.py`: disabled by default, argv-only, env allowlist of 7 vars, **authority measured by git diff before/after** against `authorized_paths`/`forbidden_paths`, `baseline_sha`; explicitly "not a sandbox" (`:30-34`). `orchestration/autonomy/local_dispatch_port.py`: per-attempt `git worktree` from the lease's `base_pin`, supervisor integrity digest before/after, "GIT-level isolation, never OS-level" (`:428-435`). `SubprocessProcessRunner` in `agent_transport.py`. No container, gVisor, microVM, or egress control |
| 15 | Prompt-injection defence (§17) | LANDED_PARTIAL | `trusted_dispatch_prompt` interpolates only validated digests/pins (`dispatcher.py:442-477`); result envelope extracted by framing markers, stdout prose never authority; `_FORBIDDEN_EXTRA_KEYS` reject smuggled authority; conversation quarantine + `scan_text` at ingestion. Trust labels on context items (`source_class`, `instruction_authority`): MISSING |
| 16 | Proof-carrying trajectories / summaries (§18, §19) | MISSING | Closest: `agent_handoff.py` (content-addressed pack, live freshness recompute on resume, `:802-869`), `session_capture.py`; `checkpoint` exists only for sync/lease bookkeeping. No trajectory store, no tournament, no early-stop |
| 17 | Trajectory Critic (§20) | MISSING | — |
| 18 | Adversarial Breaker (§21) | LANDED_PARTIAL (process) | ADV is a process role with exact-object binding (AT3-052) and ADV receipts under `docs/evidence/`; no Breaker runtime role, no strategy catalog |
| 19 | Independent Verifier Mesh (§22) | LANDED_PARTIAL | `GOVERNANCE.md` roles + AT3-051 binder; verifier classes MISSING; principal registry + author/session conflict only in open PR #733 |
| 20 | Verification ladder V0–V12 (§23) | MISSING as policy | Practised in WORKLOG (targeted / affected / full / negative controls / IV rounds / exact-head CI) but not encoded |
| 21 | Advanced testing (§24) | MISSING libraries; practice PARTIAL | No `hypothesis`, `mutmut`, `atheris` (dev deps: pytest, pytest-cov, ruff, mypy, types-PyYAML). One hand-rolled deterministic fuzz harness (`tests/unit/test_quarantine_fuzz.py`). Mutation-style negative controls are run **manually** per package (e.g. F5–F7 seals) and recorded in prose. No `--cov-fail-under` |
| 22 | Change-impact engine (§25) | MISSING | `impact.py` is declared rows; no test selector |
| 23 | Outcome / eval platform (§27) | LANDED_PARTIAL (opt-gate) | `opt_gate.py`: 9 `REQUIRED_HARD_GATES` precede score (`decide_promotion`, `:897-955`), forbidden config keys reject caller-supplied verdicts, sealed envelopes; `scoring_broker.py` keeps holdouts out-of-process with an attempt budget; `eval_substrate.py` roles; `agent_eval_shadow.py`. No `EvalCase/EvalSuite/EvalRun` types, no `OutcomeRecord`, no `RouteDecision` |
| 24 | Governed AutoLab (§28) | CONTRACT_ONLY | Wake gate is a constant `CLOSED`; promotion path exists and is the right template; no loop |
| 25 | Governed multi-hop orchestration (§34, ULT-11) | LANDED single-hop, multi-hop correctly NOT_IMPLEMENTED | `MAX_ACTIVE_DISPATCHES = 1` (`dispatcher.py:77`); only `CANDIDATE_VERIFICATION`/`RECERTIFICATION` dispatchable, mutating types refused with `CAPABILITY_REQUIRED` (`:85-90`); leases, owner gates, result envelopes typed `extra="forbid"` |
| 26 | Memory architecture (§29) | LANDED_PARTIAL | Evidence/Truth/Temporal/Procedural(skill)/Decision/Provider-memory classes exist and are kept apart; episodic (trajectory) memory MISSING; failure memory MISSING |
| 27 | Knowledge retrieval / explainability (§30) | LANDED_PARTIAL | `ask2.py` + `runtime_22.py` hybrid retrieval with reasons and overflow; graph-guided retrieval MISSING |
| 28 | Failure memory (§31) | MISSING | Residual registers live in WORKLOG prose |
| 29 | Observability / OTel (§32) | MISSING (OTel); PARTIAL (health lenses) | `ledger_obs.py`, `twin_health.py`, `provider_sync.py`; no traces |
| 30 | Durable long-horizon state (§33) | LANDED_PARTIAL | Dispatcher records/receipts under `.atlas/orchestration/dispatcher/`, lease projection + recovery; `TaskSpec`/phase model MISSING |
| 31 | Storage ports (§41.1) | MISSING (not urgent) | Files + JSONL only |
| 32 | Content-addressed derived cache (§42) | LANDED_PARTIAL | `compile_cache.py` invalidation key over caller-supplied fingerprints; nothing keyed by git blob/tree |
| 33 | Supply-chain provenance (§43) | MISSING | `deps/integrity.json` pins three manifests; no SBOM/lock |
| 34 | Causality (§44) | ISOLATED_RUNTIME | AT3-060 declared `CAUSED_BY`, provenance required, no winner |
| 35 | Ecosystem / enterprise (§46, §48) | ROADMAP_HORIZON | AT3-120…122 `NOT_STARTED` |

Determinism is airtight across `atlas3/`: zero wall-clock hits in the package,
canonical sorted JSON, content-addressed events. Every atlas3 module states
its own limits as machine-checkable output keys (`walked_host_tree`,
`invented_from_git`, `graph_is_authority`, `promoted_to_truth_core`). That
discipline is a reuse asset for every ULT wave and must not be diluted.
