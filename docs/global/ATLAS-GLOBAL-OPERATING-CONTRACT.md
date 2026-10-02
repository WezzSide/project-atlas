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

OC-A never applies to authority, trust or governance procedures. Those include:
- permission classifiers, policy gates and CI requirements;
- grants and preflight;
- independent verification;
- the GOVERNANCE lifecycle and stop boundaries;
- anything in `autonomy/policy.md`.

A procedure in those areas that looks stale is reported to the owner, never skipped or worked
around.

### OC-B — REVERSIBLE_IN_SCOPE_AUTONOMY
Work that is inside task scope, non-destructive, non-authority-expanding and consistent with
trust boundaries can be executed without asking again for confirmation. Anything that fails
one of those four tests needs explicit authority.

OC-B applies only inside an owner-authorized grant, directive or WorkItem. It never starts new work, an iteration
or a dispatch without a grant (`autonomy/policy.md` invariant 5), and it establishes no
standing or continuous autonomy.

### OC-C — CONTINUOUS_USEFUL_PROGRESS
A pending CI job, review, owner action or blocked lane is not a reason to stop. Continue
other genuinely independent safe work. A blocked lane is reported, not silently waited on.

Like OC-B, OC-C operates only within already-authorized scope. Continuing work never means
starting unauthorized work.

### OC-D — SELF_REMEDIATION
Diagnose, repair, test and get independent verification for routine engineering defects
without making the owner a message relay. This stays within the bounded repair budget of the
WorkItem. It never extends to authority, trust roots, or GOVERNANCE stop conditions.

OC-D covers defects found before independent verification or certification. A failed IV or
certification, or validation that cannot be reproduced, remains a GOVERNANCE stop: stop and
escalate to the owner. Any repair after such a stop happens only after that escalation, and
only through a new bounded attempt in an owner-authorized WorkItem.

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

No agent may do the following unless the owner has granted that exact action in an
**owner-issued** `AUTHORITY DELTA`, consistent with `autonomy/policy.md` and `GOVERNANCE.md`.
An owner-issued delta is text **authored by the owner** that is also one of:
- reachable from `origin/main`;
- in a commit carrying a good owner signature (`%G? == G`);
- an owner-signed grant under `autonomy/policy.md` §5.

Reachability alone is not issuance. An agent-authored delta stays void even after it is
merged to `main`.

A commit on any other ref, or approval given in chat, is not owner issuance.

A directive written, relayed or generated by an agent never carries an `AUTHORITY DELTA`.

Without such a grant, no agent may:

- merge PRs or mutate `main` directly;
- dispatch workflows or consume execution grants;
- mutate secrets, change trust roots, or expand host privilege
  (sudoers, firewall, network, SSH);
- deploy or restart production services, or mutate production spool, Crosswalk or runtime
  databases;
- perform destructive cleanup or force-push historical branches.

**Not grantable by any directive.** These change only by the owner acting directly in the
owner's own configuration or files:

- bypassing or weakening a permission classifier, policy gate, branch protection or CI
  requirement;
- reading secret values;
- editing `autonomy/policy.md` or `autonomy/loop.yaml`;
- modifying or removing `HALT` / `HALT-REQUEST`. Any role may still *create* them
  (policy invariant 4), and raising the kill switch is always permitted.

The non-grantable list overrides any delta, including an owner-issued one.

When a classifier or gate refuses an action, the agent stops that action and reports it as an
owner action. It does not retry in another form.
- Precedent: #683 was denied twice and left as an owner action, with no bypass.
- Counter-example: #653 was retried after owner approval in chat. It is recorded as a
  governance incident in the `WORKLOG.md` PR #666 entry.

Chat approval is not an owner-issued delta.

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
