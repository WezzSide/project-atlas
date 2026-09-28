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
| positive IV evidence is the exact record the authority bound (`iv_binding`: comment id + author + body sha256 + updated_at; optional `trusted_iv_authors` allowlist), unedited since binding and bound before the decision | `IV_BINDING_MISSING` / `IV_EVIDENCE_NOT_FOUND` / `IV_AUTHOR_MISMATCH` / `IV_AUTHOR_UNTRUSTED` / `IV_BODY_HASH_MISMATCH` / `IV_EDITED` / `IV_EDITED_AFTER_AUTHORITY` / `IV_BINDING_NOT_BEFORE_AUTHORITY` |
| that record names HEAD+TREE and its **canonical record lines** say `IV_VERDICT=PASS`, `BLOCKING_P0=0`, `BLOCKING_P1=0` (bodies are CRLF-normalised, then fenced/indented code, HTML comments/blocks and CommonMark code spans of any backtick-run length are replaced by a placeholder — never bare whitespace — so quotes, URLs, examples and text sharing a line with stripped context never form a whole-line record; duplicate/contradictory or malformed record lines never count) | `IV_NOT_BOUND_TO_CANDIDATE` / `IV_NOT_PASS` |
| `required_checks` present, non-empty, well-formed | `NO_REQUIRED_CHECKS` / `MALFORMED_REQUIRED_CHECKS` |
| malformed timestamps / unknown review states / collector failures | `MALFORMED_EVIDENCE` / `EVIDENCE_COLLECTION_FAILED` (DENY, never crash-to-allow) |
| no blocking evidence (P0/P1>0, IV FAIL, REJECTED, CHANGES_REQUESTED, security) whose **effective, edit-aware** timestamp (`updated_at`) is at or after (`>=`) the authority decision | `NEWER_BLOCKING_EVIDENCE` |
| every required CI run's newest exact-head run chosen by validated timezone-aware timestamps (malformed → DENY) | `CI_NOT_TERMINAL_SUCCESS` / `MALFORMED_EVIDENCE` |
| authority bound to this PR, not consumed, snapshot newer than the decision | `PR_MISMATCH` / `AUTHORITY_ALREADY_CONSUMED` / `SNAPSHOT_OLDER_THAN_AUTHORITY` |

Every decision is a receipt (`atlas-merge-gate-receipt/v1`) carrying the authority binding,
the observed identity, required CI run ids, the latest IV id and the newest evidence id, so
the merge log can prove which evidence was current at the mutation instant.

```
python -m controller.merge_gate --pr 1026 --authority authority.json --receipt receipt.json
# exit 0 = ALLOW, 2 = DENY. authority.json: {"pr","head","tree","base","decided_at",
#   "required_checks":["ci","atlas-runner-ci"],
#   "iv_binding":{"iv_evidence_id","iv_author","iv_updated_at","iv_body_sha256"},
#   "trusted_iv_authors"?: [...], "rebind_base"?: sha, "consumed": false}
```

`tests/test_merge_gate_authenticity.py` proves IV source authenticity (bound id/author/hash/updated_at, allowlist), edit freshness (post-authority edits invalidate), canonical record context (fence/indented/HTML-comment/code-span/URL/quoted tokens never count, CRLF bodies included) and temporal CI ordering. `tests/test_merge_gate_successor.py` closes the fail-open surfaces found by the independent IV of the first revision (canonical verdict parsing, mandatory `required_checks`, same-second boundary, `--paginate --slurp` collection, SHA case normalization, fail-closed malformed evidence). `tests/test_merge_gate.py` replays the #1025 race deterministically (blocking verdict after
admission, before merge) and covers drift, stale CI, unbound IV, consumed authority and the
read-only collector. Merging this hardening change itself requires separate authority.