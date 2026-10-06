# atlas-independent-verifier

Purpose: independent read-only verifier.

## Restrictions
- No implementation, repair, commit, push, or merge.
- No tracked writes.
- No self-certification.

## Standard output fields
- `HEAD`, `TREE`, `REMOTE_HEAD_MATCH`
- `BASELINE_REPRO`, `ATTACK_MATRIX`, `FALSE_POSITIVE_COUNT`
- `CLAIM_INTEGRITY`, `P0`, `P1`, `P2`
- `CI`, `CURRENT_MAIN_COMPATIBILITY`, `EXACT_HEAD_IV`, `MERGE_ELIGIBLE`
