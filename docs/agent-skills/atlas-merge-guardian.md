# atlas-merge-guardian

Purpose: merge-gate evaluator consuming CI/IV/claim receipts.

## Required inputs
- `REMOTE_HEAD_MATCH`
- `EXACT_HEAD_CI`
- `EXACT_HEAD_IV`
- `CLAIM_INTEGRITY`
- `P0`, `P1`
- `CURRENT_MAIN_COMPATIBILITY`
- `MERGEABLE`

## Decision rule
- Re-read evidence at merge instant.
- If any evidence changes, set `MERGE_AUTHORIZATION = REVOKED`.

