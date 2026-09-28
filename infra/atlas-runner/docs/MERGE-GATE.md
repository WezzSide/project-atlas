# Merge gate — evidence-freshness recheck at the merge instant

Closes the governance defect observed on PR #1025 (2026-09-28): a blocking independent
verification (IV) comment (`5867783462`, 10:05:00Z) existed before the merge (10:05:38Z),
yet the merge proceeded because the decision relied on an earlier admission snapshot.

`controller/merge_gate.py` is a read-only, fail-closed gate that must be evaluated
immediately before any merge mutation. It never merges. Required invariant:

| check | DENY reason |
|---|---|
| candidate HEAD / TREE exactly as bound by the authority | `HEAD_DRIFT` / `TREE_DRIFT` |
| base exact, or explicitly rebound (`rebind_base`) | `BASE_DRIFT` |
| every required CI run terminal SUCCESS on the exact head, newest run wins | `CI_MISSING` / `CI_NOT_TERMINAL_SUCCESS` |
| latest IV record bound to HEAD+TREE, verdict PASS, `BLOCKING_P0=0`, `BLOCKING_P1=0` | `IV_MISSING` / `IV_NOT_BOUND_TO_CANDIDATE` / `IV_NOT_PASS` |
| no blocking evidence (P0/P1>0, IV FAIL, REJECTED, CHANGES_REQUESTED, security) newer than the authority decision | `NEWER_BLOCKING_EVIDENCE` |
| authority bound to this PR, not consumed, snapshot newer than the decision | `PR_MISMATCH` / `AUTHORITY_ALREADY_CONSUMED` / `SNAPSHOT_OLDER_THAN_AUTHORITY` |

Every decision is a receipt (`atlas-merge-gate-receipt/v1`) carrying the authority binding,
the observed identity, required CI run ids, the latest IV id and the newest evidence id, so
the merge log can prove which evidence was current at the mutation instant.

```
python -m controller.merge_gate --pr 1026 --authority authority.json --receipt receipt.json
# exit 0 = ALLOW, 2 = DENY. authority.json: {"pr","head","tree","base","decided_at",
#   "required_checks":["ci","atlas-runner-ci"], "rebind_base"?: sha, "consumed": false}
```

`tests/test_merge_gate.py` replays the #1025 race deterministically (blocking verdict after
admission, before merge) and covers drift, stale CI, unbound IV, consumed authority and the
read-only collector. Merging this hardening change itself requires separate authority.