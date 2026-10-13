# atlas-postmerge-seal

Purpose: post-merge closure and seal verification.

## Required checks
- `ACTUAL_MERGE_RECEIPT`, `MERGE_COMMIT`, `MERGE_TREE`, `MERGE_PARENTS`
- Post-merge CI
- Targeted post-merge verification
- `EXPECTED_TREE_MATCH`, `NO_UNEXPECTED_MUTATION`
- Prospective PR merge SHA is never treated as merge proof.

## Result
- Set `INTEGRATED = YES` and `SEALED = YES` only after all checks pass.
