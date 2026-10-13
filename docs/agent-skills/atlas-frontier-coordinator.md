# atlas-frontier-coordinator

Purpose: DAG lane coordinator with local wait semantics.

## Tracked fields
- `LANE`, `PR`, `HEAD`, `TREE`, `STATE`
- `BLOCKER`, `OWNER_GATE`, `IV_GATE`, `CI_GATE`
- `DEPENDENCIES`, `SUCCESSOR`, `NEXT_ACTION`

## Key invariant
- Waiting in one lane never implies global session stop.

