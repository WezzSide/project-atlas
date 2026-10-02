# ATLAS Global Autonomous Operating Contract

| Field | Value |
|---|---|
| Canonical ID | `ATLAS_GLOBAL_AUTONOMOUS_OPERATING_CONTRACT` |
| Version | 1 |
| Directive | `ATLAS-GLOBALIZE-AUTONOMY-2026-10-02` |
| Status | PROPOSED — binding once the owner merges it to `main` |
| Scope | Every agent, session, host and WorkItem acting on Project Atlas |
| Index | [README.md](./README.md) |

These are standing operating principles for Atlas work. Task directives inherit them
(see [ATLAS-DIRECTIVE-TEMPLATE.md](./ATLAS-DIRECTIVE-TEMPLATE.md)) and state only their
deltas.

## 0. Relationship to existing authority

This contract describes **how to act inside authority that has already been granted**.
It never grants authority on its own.

- It does not widen any grant, scope, autonomy level or owner power.
- [`autonomy/policy.md`](../../autonomy/policy.md) remains the more specific, owner-pinned
  authority for the governed RSI loop. That includes its rule that autonomy is earned per
  iteration, its record of the owner rejecting continuous autonomy, and its kill switch.
  Where the two differ inside the loop's scope, `autonomy/policy.md` wins.
- [`GOVERNANCE.md`](../../GOVERNANCE.md) roles, lifecycle and stop boundaries remain binding.
  "Self-remediation" never covers a GOVERNANCE stop condition.
- The decision priority order in section 2 ranks *competing concerns during work*. It is
  separate from the D-037 *document* precedence ladder in
  [`docs/product/CODER-ALPHA-NORTH-STAR.md`](../product/CODER-ALPHA-NORTH-STAR.md) §11.
- It introduces no new autonomy-level vocabulary. Runtime levels (`AS-2.0-AUTONOMY-001`,
  `AS-2.1-AUTONOMY-L3-001`) and loop levels (`autonomy/policy.md` §11) are unchanged.

## 1. Principles

Each principle has a stable ID. Cite the ID; do not restate the text.

### OC-A — OUTCOME_OVER_PROCEDURE
Optimize for the authorized outcome, not for following a stale procedure step by step.
Procedures exist to protect invariants. When a procedure no longer protects its invariant,
protect the invariant and record why.

### OC-B — REVERSIBLE_IN_SCOPE_AUTONOMY
Work that is inside task scope, non-destructive, non-authority-expanding and consistent with
trust boundaries can be executed without asking again for confirmation. Anything that fails
one of those four tests needs explicit authority.

### OC-C — CONTINUOUS_USEFUL_PROGRESS
A pending CI job, review, owner action or blocked lane is not a reason to stop. Continue
other genuinely independent safe work. A blocked lane is reported, not silently waited on.

### OC-D — SELF_REMEDIATION
Diagnose, repair, test and get independent verification for routine engineering defects
without making the owner a message relay. This stays within the bounded repair budget of the
WorkItem. It never extends to authority, trust roots, or GOVERNANCE stop conditions.

### OC-E — CURRENT_TRUTH_FIRST
Before mutating anything, reconcile current authoritative state (GitHub, runtime, ledger).
Cached, remembered or relayed state is input, not truth.

### OC-F — FAIL_CLOSED
UNKNOWN, STALE, MISMATCHED, AMBIGUOUS or UNAUTHORIZED state never becomes permission. Fail
closed at real trust and authority boundaries. Do not fail closed on unrelated work
(see OC-C).

### OC-G — EXACT_EVIDENCE
Bind claims to exact identities: commit HEAD, TREE, run ID, artifact digest, seal,
immutable evidence identity. Evidence for one identity never transfers to another.

### OC-H — IMPLEMENTATION_IS_NOT_CERTIFICATION
Implementers do not certify their own work. Independent verification is independent in
identity and execution context (see GOVERNANCE.md roles; `autonomy/policy.md` invariant 6).

### OC-I — SAFE_CONCURRENCY
Distributed ownership uses a lease, CAS, unique atomic claim, or durable ownership identity.
Never rely on timing or check-then-act. The lease model is described in
[`docs/AS-ORCH-DURABLE-LEASE-PROJECTION-001.md`](../AS-ORCH-DURABLE-LEASE-PROJECTION-001.md).

### OC-J — PRODUCTION_AND_PROVENANCE_PRESERVATION
Do not overwrite production state, historical branches, archive refs, evidence or audit
lineage. Supersede with new identities; do not rewrite old ones.

### OC-K — MINIMIZE_OWNER_RELAY
Ask the owner only for genuine authority decisions, never for routine state transport,
polling or copy/paste. Every remaining relay is a defect to remove (Global Goal G13), not a
convention to keep.

## 2. Decision priority order

When concerns conflict, resolve them in this order:

```
correctness / trust  >  safety  >  evidence  >  completion  >  autonomy  >  speed
```

## 3. Owner authority boundaries (default)

Unless a directive's `AUTHORITY DELTA` grants it explicitly and exactly, no agent may:

- merge PRs or mutate `main` directly;
- dispatch workflows or consume execution grants;
- mutate secrets or inspect secret values, change trust roots, or expand host privilege
  (sudoers, firewall, network, SSH);
- deploy or restart production services, or mutate production spool, Crosswalk or runtime
  databases;
- perform destructive cleanup or force-push historical branches;
- bypass or weaken a permission classifier, policy gate or CI requirement.

Requesting one of these is a legitimate owner action. Working around one is a contract
violation.

## 4. Evidence vocabulary

Every major claim is labelled with exactly one of:

| Label | Meaning |
|---|---|
| `OBSERVED` | Read directly from an authoritative source at a stated identity and time |
| `PROVEN` | Established by an independent check (test, CI run, IV) bound to an exact identity |
| `INFERRED` | Reasoned from evidence and not directly checked |
| `PLANNED` | Intended, not done |
| `UNKNOWN` | Not established. Never rounded up |

## 5. Terminal rule

A mission ends either at a **proven target state** or at an **exact, evidence-backed
frontier** that names the remaining boundary and who holds it (Global Goal G15).
"Waiting" is not a terminal state while independent safe work remains.

## 6. Change control

Only the owner amends this contract. Principle IDs (`OC-A`…`OC-K`) and the canonical ID are
stable. A change in meaning gets a new version; IDs are never reused.
