# Atlas Merge Guardian Design (Preparation Only)

This document is a design and implementation plan. It does not change current branch protection, required checks, or merge policy.

## Identity invariant

Required identities at decision time:

- `PR_REMOTE_HEAD`
- `CI_HEAD`
- `IV_HEAD`
- `MERGE_CANDIDATE_HEAD`

All must resolve to the same object identity for merge authorization.

## Required merge inputs

- `REMOTE_HEAD_MATCH = YES`
- `EXACT_HEAD_CI = PASS`
- `EXACT_HEAD_IV = PASS`
- `CLAIM_INTEGRITY = PASS`
- `P0 = 0`
- `P1 = 0`
- `CURRENT_MAIN_COMPATIBILITY = PASS`
- `MERGEABLE = YES`

## Critical merge-instant rule

At merge instant, re-read all evidence receipts.  
If any receipt changed since previous observation, set:

- `MERGE_AUTHORIZATION = REVOKED`

## Negative eval suite

| Case | Scenario | Expected |
|---|---|---|
| MG01 | old-head IV PASS, new head exists | BLOCK |
| MG02 | CI PASS, IV missing | BLOCK |
| MG03 | IV FAIL arrives while CI running | BLOCK |
| MG04 | claim integrity FAIL | BLOCK |
| MG05 | P1 exists | BLOCK |
| MG06 | main moves incompatibly | BLOCK |
| MG07 | prospective merge SHA on open PR only | NOT ACTUAL MERGE |
| MG08 | verifier unavailable | BLOCK MERGE; other lanes continue |
| MG09 | conflicting same-head verifier verdicts | BLOCK until reconciled |
| MG10 | CI rerun PASS after prior cancellation | preserve both receipts |
| MG11 | head changes after merge decision | revoke authorization |
| MG12 | postmerge main tree differs from expected merge result | SEAL FAIL |

## Evidence bundle format

Path:

`.atlas/evidence/<PR-or-package>/`

Files:

- `candidate.json`
- `ci.json`
- `iv.json`
- `claims.json`
- `compatibility.json`
- `authorization.json`
- `merge-receipt.json`
- `postmerge-seal.json`

Each receipt includes:

- `repository`, `PR`, `head`, `tree`, `base`, `main_head`
- `timestamp`, `producer_identity`, `evidence_source`, `result`

## Postmerge seal workflow

1. Capture actual merge receipt (`merge commit`, `tree`, `parents`).
2. Run postmerge CI and targeted verification.
3. Assert `EXPECTED_TREE_MATCH` and `NO_UNEXPECTED_MUTATION`.
4. Only then set `INTEGRATED = YES` and `SEALED = YES`.

## Frontier/DAG preparation

Recommended lane state schema:

- `LANE`, `PR`, `HEAD`, `TREE`, `STATE`, `BLOCKER`
- `OWNER_GATE`, `IV_GATE`, `CI_GATE`, `DEPENDENCIES`
- `SUCCESSOR`, `NEXT_ACTION`

States:

- `RUNNABLE`, `RUNNING`, `WAITING_CI`, `WAITING_IV`, `OWNER_GATED`, `OWNER_PAUSED`, `BLOCKED_EXTERNAL`, `MERGE_ELIGIBLE`, `MERGED_UNSEALED`, `SEALED`, `SUPERSEDED`

Invariant:

- waiting in one lane never pauses global orchestration.
