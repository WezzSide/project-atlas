# ATLAS Directive Template

| Field | Value |
|---|---|
| Canonical ID | `ATLAS_DIRECTIVE_TEMPLATE` |
| Version | 1 |
| Status | PROPOSED — binding once the owner merges it to `main` |
| Use for | Owner mission directives and WorkItems |
| Not for | Governed RSI loop iterations: they keep [`autonomy/instruments/directive-template.md`](../../autonomy/instruments/directive-template.md) |
| Index | [README.md](./README.md) |

A directive inherits the global foundation by reference and states only its **deltas**.
Do not copy global doctrine into a directive. Cite principle IDs (`OC-*`, `SM-*`) and goal
IDs (`ATLAS-GOAL-Gnn`) instead.

Anything a directive does not state is inherited unchanged. In particular, if
`AUTHORITY DELTA` is empty, the directive grants **no** authority beyond the contract's
default owner boundaries (contract section 3).

---

```text
DIRECTIVE: <ID, e.g. ATLAS-<AREA>-<SLUG>-YYYY-MM-DD>

INHERITS:
  ATLAS_GLOBAL_AUTONOMOUS_OPERATING_CONTRACT   (docs/global/ATLAS-GLOBAL-OPERATING-CONTRACT.md, v1)
  ATLAS_GLOBAL_STYLE_MISSION                   (docs/global/ATLAS-GLOBAL-STYLE-MISSION.md, v1)
  ATLAS_GLOBAL_GOALS                           (docs/global/ATLAS-GLOBAL-GOALS.md, v1)
  <optional: more specific authority, e.g. autonomy/policy.md @ policy_sha>

OUTCOME:
  <the target state, stated as an observable, verifiable condition>
  <name the terminal token(s), e.g. FOO_READY_FOR_OWNER_MERGE>

SCOPE:
  in:  <repositories, paths, systems, hosts>
  out: <explicitly excluded areas, parked items>

INVARIANTS:
  <task-specific invariants beyond the global ones; cite OC-*/SM-* rather than restating them>

AUTHORITY DELTA:
  grants:   <exact, bounded authority beyond contract section 3; e.g. "one workflow_dispatch
             of atlas-agent-execute.yml with workflow_inputs_sha256=<sha>">
  revokes:  <any default authority narrowed for this task>
  expires:  <time or event>
  (empty = no additional authority)

SUCCESS:
  <evidence required: exact HEAD/TREE, CI run, IV identity, artifact digest>
  <which ATLAS-GOAL-Gnn this advances, and how the baseline status may change>

OPERATING EXPECTATION:
  <cadence, parallelism, reporting shape, stop points, what to do at each boundary>
```

---

## Rules

1. **INHERITS** names canonical IDs and versions. When a newer version exists, the directive
   names which version it inherits.
2. **AUTHORITY DELTA** is exact: specific workflow, ref, identity or digest, count, and
   expiry. "Do what is needed" is not a grant.
3. **SUCCESS** names evidence, not activity.
4. A directive may narrow a global principle for its scope. It may not silently widen
   authority. Any widening appears in `AUTHORITY DELTA`.
