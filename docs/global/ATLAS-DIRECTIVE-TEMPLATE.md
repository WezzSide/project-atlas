# ATLAS Directive Template

| Field | Value |
|---|---|
| Canonical ID | `ATLAS_DIRECTIVE_TEMPLATE` |
| Version | 2 |
| Status | ADOPTED — binding as governance on `main` by owner merge (v1: PR #1047; v2 is binding only through the owner merge that lands it) |
| Use for | Owner mission directives and WorkItems |
| Not for | Governed RSI loop iterations: they keep [`autonomy/instruments/directive-template.md`](../../autonomy/instruments/directive-template.md) |
| Index | [README.md](./README.md) |

A directive inherits the global foundation by reference and states only its **deltas**.
Do not copy global doctrine into a directive. Cite principle IDs (`OC-*`, `SM-*`) and goal
IDs (`ATLAS-GOAL-Gnn`) instead.

Anything a directive does not state is inherited unchanged. In particular, if
`AUTHORITY DELTA` is empty, the directive grants **no** authority beyond the contract's
default owner boundaries (contract section 3).

A directive states **what must be true and what may not be crossed**. It leaves **how** to the
agent (contract OC-L). The template keeps seven things apart:

| # | Concern | Field |
|---|---|---|
| 1 | Intent / objective | `OUTCOME` |
| 2 | Authorized scope envelope | `SCOPE` |
| 3 | Hard authority boundaries | `AUTHORITY DELTA` |
| 4 | Required trust / safety invariants | `INVARIANTS` |
| 5 | Acceptance evidence | `SUCCESS` |
| 6 | Autonomous execution freedom | `EXECUTION FREEDOM` |
| 7 | Owner return conditions | `OWNER RETURN CONDITIONS` |

---

```text
DIRECTIVE: <ID, e.g. ATLAS-<AREA>-<SLUG>-YYYY-MM-DD>

INHERITS:
  ATLAS_GLOBAL_AUTONOMOUS_OPERATING_CONTRACT   (docs/global/ATLAS-GLOBAL-OPERATING-CONTRACT.md, v2)
  ATLAS_GLOBAL_STYLE_MISSION                   (docs/global/ATLAS-GLOBAL-STYLE-MISSION.md, v1)
  ATLAS_GLOBAL_GOALS                           (docs/global/ATLAS-GLOBAL-GOALS.md, v1)
  <optional: more specific authority, e.g. autonomy/policy.md @ policy_sha>

OUTCOME:                      # 1. intent / objective
  <why this matters, in one or two lines>
  <the target state, stated as an observable, verifiable condition>
  <name the terminal token(s), e.g. FOO_READY_FOR_OWNER_MERGE>

SCOPE:                        # 2. authorized scope envelope
  in:  <repositories, paths, systems, hosts the agent may work in>
  out: <explicitly excluded areas, parked items>

AUTHORITY DELTA:              # 3. hard authority boundaries
  issued_by: owner @ <owner-signed grant ID | owner-adopted commit SHA>   (owner-signed = machine-verifiable; owner-adopted = governance only, and does not let an agent perform a contract §3 boundary action until D1/D5 are decided)
  grants:   <exact, bounded authority beyond contract section 3; e.g. "one workflow_dispatch
             of atlas-agent-execute.yml with workflow_inputs_sha256=<sha>">
  revokes:  <any default authority narrowed for this task>
  expires:  <time or event>
  (empty = no additional authority; every contract section 3 boundary stays in force)

INVARIANTS:                   # 4. required trust / safety invariants (MUST)
  <task-specific invariants beyond the global ones; cite OC-*/SM-* rather than restating them>
  <a mechanic belongs here only when it IS the trust, security, provenance or safety property>

SUCCESS:                      # 5. acceptance evidence
  <what must be proven, not how: exact HEAD/TREE, CI run, IV identity, artifact digest>
  <which ATLAS-GOAL-Gnn this advances, and how the baseline status may change>

EXECUTION FREEDOM:            # 6. what the agent decides without returning
  <default: everything inside SCOPE that crosses no boundary and breaks no invariant --
   investigation, design, implementation, repair, tests, CI investigation, reconciliation,
   evidence collection. State only narrowing or PREFER/SHOULD guidance here>

OWNER RETURN CONDITIONS:      # 7. when the agent comes back
  <the owner action at the end, e.g. merge decision>
  <default: acceptance satisfied and an owner action is required, or a genuine authority,
   scope, trust, provenance or safety boundary is reached (contract OC-L)>

OPERATING EXPECTATION:        # optional: reporting shape and parallelism only
  <report fields, parallel lanes; not step-by-step procedure>
```

---

## Rules

1. **INHERITS** names canonical IDs and versions. When a newer version exists, the directive
   names which version it inherits.
2. **AUTHORITY DELTA** is exact: specific workflow, ref, identity or digest, count, and
   expiry. "Do what is needed" is not a grant.
   - It is machine-verifiable only when `issued_by` names an owner signature by a key held
     off every agent host, checked against a pinned signer list (contract §3). An owner
     merge adopts text as governance, but it is not machine-verifiable while agents use the
     owner's GitHub credentials.
   - Owner approval in a session is owner authority, but it is not machine-verifiable.
     Whether it lets an agent perform boundary actions is open decision D1.
   - An agent never issues a delta to itself.
   - Some actions can never be granted by any delta (contract §3):
     - classifier, gate or CI bypass;
     - secret values;
     - edits to `autonomy/policy.md` or `loop.yaml`;
     - modifying or removing HALT files.
3. **SUCCESS** names evidence, not activity.
4. A directive may narrow a global principle for its scope. It may not silently widen
   authority. Any widening appears in `AUTHORITY DELTA`.
5. **MUST is for invariants and boundaries only.** Use MUST / MUST NOT for `INVARIANTS`,
   `AUTHORITY DELTA` and `SCOPE`. Use SHOULD / PREFER for implementation guidance, which the
   agent may depart from with a recorded reason.
6. **State the outcome, not the choreography** (contract OC-L). A directive does not
   prescribe:
   - exact command sequences or the order of internal edits;
   - step lists where an outcome and its evidence are enough;
   - pass, turn or iteration ceilings for ordinary direct implementation;
   - a return to the owner for routine technical findings, test failures, CI investigation
     or reconciliation inside the envelope.
7. **A mechanic that is the trust property is an invariant.** When ordering, a count, a
   single-use limit or a separation of sessions is what makes the result trustworthy, write
   it under `INVARIANTS` as a MUST and say which property it protects. Examples: a single-use
   dispatch, a sealed `max_attempts`, verification in a fresh session, hashing the exact
   bytes that are sent.
8. **Execution freedom never crosses a boundary.** `EXECUTION FREEDOM` cannot grant
   authority, and silence about authority means none. Merge, dispatch, deployment, secrets,
   permission changes, trust roots, destructive or external mutations, classifier denials,
   mandated independent verification and immutable evidence stay exactly as the contract
   states them.
