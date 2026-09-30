# ATLAS DAG Coordination (`atlas-dag`)

Read-only autonomous DAG coordinator for Windows Main. Implements directives
D-002 (GitHub event bus), D-006 (`atlas-dag` CLI), D-007 (`ATLAS_IV_RECEIPT_V1`),
and D-008 (read-only Merge Guardian). MVP prohibitions hold: **no automatic merge,
no product runtime changes.**

## Components

- `schemas/atlas_event_v1.schema.json` — append-only transition events.
- `schemas/atlas_iv_receipt_v1.schema.json` — exact-head formal IV receipts.
- `schemas/dag_snapshot_v1.schema.json` — disposable snapshot shape.
- `scripts/atlas-dag.py` + `scripts/atlas_dag/` — the CLI and its modules.
- Runtime state: `.atlas-runtime/dag.json` (gitignored, disposable).

## Event bus (D-002)

One GitHub issue named `Atlas Autonomous DAG Control`. Events are fenced
` ```json ` blocks inside append-only comments. Rules:

1. Repository/GitHub truth overrides comments.
2. Only `ATLAS_EVENT_V1` transitions; stable-state noise (`NO_CHANGE`,
   `HEAD_UNCHANGED`, `CI_STILL_RUNNING`, `STILL_WAITING`, `STILL_FROZEN`) is rejected.
3. Duplicate `event_id` is idempotent; events are canonically ordered by
   `(timestamp_utc, event_id)`, so reordering cannot change reconstruction.
4. A verifier pool is declared in the issue body as an
   `ATLAS_VERIFIER_POOL_V1` fenced block. Entries bind a verifier to a trusted
   principal — the GitHub login that may actually author receipts:

   ```json
   {"schema": "ATLAS_VERIFIER_POOL_V1", "verifiers": [
     {"verifier_id": "IV-A", "principal": "github:some-login"},
     {"verifier_id": "IV-B", "principal": "github:other-login"}
   ]}
   ```

   Bare-string entries remain parseable as `DECLARED_BUT_UNBOUND` but never
   satisfy formal IV. No pool, no binding, or a receipt comment authored by a
   different login => fail-closed (`VERIFIER_POOL_UNDEFINED`,
   `VERIFIER_IDENTITY_UNBOUND`, `PRINCIPAL_MISMATCH`).

5. The event bus is append-only; `gh api --paginate` consumes every comment
   page. Stale-head events (their `head` differs from the live PR head) are
   history only and never affect current claim/freeze/ownership state.

## CLI

```bash
python scripts/atlas-dag.py --repo B0LK13/project-atlas snapshot   # writes .atlas-runtime/dag.json
python scripts/atlas-dag.py frontier
python scripts/atlas-dag.py inspect 709
python scripts/atlas-dag.py events
python scripts/atlas-dag.py owners
python scripts/atlas-dag.py gate 709    # exit 0 = PASS, 1 = FAIL, 2 = unknown PR
```

Fail-closed: unavailable GitHub data => `UNKNOWN`; ownership ambiguity =>
`AMBIGUOUS` (never last-writer-wins, never write authorization).

## Merge Guardian identity invariant (D-008)

`REMOTE PR HEAD = CI HEAD = IV HEAD = MERGE CANDIDATE HEAD`.

An open PR's prospective `merge_commit_sha` is never an actual merge receipt; a
`MERGED`/`SEALED` event while the PR is open fails the gate with
`MERGE_RECEIPT_UNPROVEN`. Head movement demotes prior exact-head CI and
whole-candidate IV to predecessor evidence.

## Tests

`tests/unit/test_atlas_dag.py` — offline adversarial suite (fake `gh` runner)
covering the acceptance matrix: DAG-001…005, OWN-001/002, IV-001…004,
CI-001…003, MG-001, EV-001…003, plus snapshot determinism and schema conformance.
