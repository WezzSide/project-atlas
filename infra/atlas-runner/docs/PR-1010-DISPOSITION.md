# PR #1010 disposition (Band J triage)

PR #1010 ("ATLAS-RUNNER-EVIDENCE-REVISION-001: preserve controller revision
provenance", head f815fe21) was reviewed against current main and this
integration-hardening candidate. Merge-tree: no textual conflicts.

| Surface | Classification |
|---|---|
| controller/worker.py (+38, `_resolve_revision` base/result enrichment) | STILL_REQUIRED — closes the long-disclosed residual "controller-side evidence.json base/result_revision null" |
| entrypoint.sh (+30, fragment revision emission) | STILL_REQUIRED |
| tests/test_worker_evidence_revisions.py (+208) | STILL_REQUIRED |
| tests/test_entrypoint_fragment.py (+90) | STILL_REQUIRED |
| docs/EVIDENCE.md (+18) | STILL_REQUIRED |
| .github/workflows/atlas-runner-acceptance.yml (+3) | STILL_REQUIRED (evidence artifact surfacing; re-verify against the current acceptance workflow during refresh) |

No surface is ALREADY_SUPERSEDED (nothing in main or in this candidate
implements revision provenance) and none is CONFLICTING (clean merge-tree).

## Recommendation

REFRESH_AND_REVIEW — after the integration-hardening PRs merge, refresh
PR #1010 onto the new main (or port its content into a single follow-up
branch owned by that workstream). Do NOT merge as-is: stale base, zero
submitted reviews, and the acceptance workflow has drifted since. Do not
duplicate its implementation inside the hardening candidate (it touches
worker/entrypoint surfaces this candidate does not).
