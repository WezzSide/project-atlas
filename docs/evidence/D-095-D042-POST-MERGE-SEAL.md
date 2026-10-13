# D-095 — D-042 exact-main post-merge seal and governance reconciliation

> Current governance state is recorded in
> `docs/evidence/D-096-D042-POST-HOC-OWNER-GOVERNANCE-RATIFICATION.md`
> under `D-PROJECT-ATLAS-OWNER-D042-D096-GOVERNANCE-RATIFICATION`:
> `POST_HOC_OWNER_RATIFICATION = GRANTED`,
> `PRE_MERGE_AUTHORIZATION_PROVENANCE = UNVERIFIED`,
> `D042_STATE = CLOSED`.
> The CASE B findings below are **not rewritten**. They remain the
> historical record that pre-merge authorization provenance is
> UNVERIFIED and that `#353` merged while frozen text said
> `MERGE_AUTHORIZATION = NOT_GRANTED`.

DIRECTIVE: `D-PROJECT-ATLAS-CLOUD-D042-D095-POST-MERGE-SEAL-AND-GOVERNANCE-RECONCILIATION`
VALIDATOR: Cloud/Local D-042 lane (Windows)
PACKAGE: `AS-CODER-ALPHA-CONVERSATIONAL-CAPTURE-001` (CAPTURE-002 / D-042)
RECORDED: 2026-08-15

This packet is **evidence / governance only**.
`POST_MERGE_CLOSURE_PRODUCTION_CHANGES = 0`.
PR #354 was not touched. `#353` was not amended.

This is an **independent** reconciliation. It does **not** copy the prior
Local D-095 `CLOSED` verdict. It re-reads live git/GitHub and applies
this directive's CASE A/B.

Relationship to the earlier owner-held receipt
`docs/evidence/D-095-D042-POST-MERGE-RATIFICATION-AND-SEAL.md`
(docs branch `docs/d042-d095-post-merge-seal`, draft PR `#360`):
that receipt remains historical Local evidence under
`D-PROJECT-ATLAS-OWNER-D042-D095-POST-MERGE-RATIFICATION-AND-CLOSURE`.
Its technical SHA/tree/CI findings are consistent with this re-read.
Its `D042_STATE = CLOSED` is **not** the current governance state.

D-091 through D-094 receipts are not rewritten.

---

## Live merge (re-read 2026-08-15)

```
PR                             = 353
PR_353_MERGED                  = YES
mergedAt                       = 2026-08-14T20:36:07Z
merged_by                      = cursor[bot]
MERGE_COMMIT                   = 9441b0c576dc54bc43a92a62a4e972889424c21f
MERGE_TREE                     = ed78a92e941d88ac1aa198c311b0120b4c9ce7ef
PARENT_1                       = c282f2c1eb2dde24f997e480c37d083fda906e54
PARENT_2                       = 822a6d82fa81df8afa1f4de759f3d2dc2a8b93fb
PREVIOUS_MAIN                  = c282f2c1eb2dde24f997e480c37d083fda906e54
AUTHORIZED_PRODUCTION_HEAD     = 9ec65c7662f1ed8e18805a9496df8ded19d2c65e
AUTHORIZED_PRODUCTION_TREE     = 97e56303ec7642bb86c9799cd2dbd79bfa1eaf08
FINAL_PR_HEAD                  = 822a6d82fa81df8afa1f4de759f3d2dc2a8b93fb
FINAL_PR_TREE                  = ed78a92e941d88ac1aa198c311b0120b4c9ce7ef
POST_MERGE_MAIN                = 9441b0c576dc54bc43a92a62a4e972889424c21f
EXPECTED_POST_MERGE_MAIN       = 9441b0c576dc54bc43a92a62a4e972889424c21f
origin/main                    = 9441b0c576dc54bc43a92a62a4e972889424c21f
MAIN_MOVED_AFTER_D042_MERGE    = NO
MERGE_METHOD                   = GITHUB_MERGE_COMMIT
SQUASH                         = NO
REBASE                         = NO
TREE_EQUALITY                  = PASS
LINEAGE                        = PASS
POST_MERGE_SEAL                = PASS
```

`git fetch origin` then `git rev-parse origin/main` equaled the merge
commit. `git log -1 --format=%T` on that commit equaled `ed78a92e`.
Parents matched. Subject is `Merge pull request #353 from
B0LK13/cursor/d042-conversational-capture-6f85`.
`git diff --stat 822a6d82 origin/main` is empty.
`git rev-list --count origin/main ^9441b0c` = `0`.
D-091 freeze `9ec65c7` is an ancestor of both the PR tip and exact main.

---

## Authorized payload

```
AUTHORIZED_PAYLOAD_PRESENT     = YES
PRODUCTION_SEMANTIC_DRIFT      = 0
UNRELATED_PRODUCTION_CHANGE    = 0
```

Exact main contains D-091 production freeze semantics:

- `atlas.conversation-capture.v1` (`conversation_capture.py` + schema)
- CLI `atlas capture conversation` and `atlas capture review`
- API `POST /v1/captures/conversation`
- Knowledge Inbox quarantine (`build_knowledge_inbox_receipt`)
- human-readable non-authoritative projection
- agent-context label `Conversation capture — non-authoritative`
- Web Knowledge conversation-capture panel
- review lifecycle (`captured` / `reviewed` / `rejected`; REVIEWED ≠ promotion)
- provider-neutral structured submission (`PROVIDER_RE`; extra tokens accepted)
- transcript extraction rejected (`TRANSCRIPT_EXTRACTION_NOT_IMPLEMENTED`)
- MCP remains read-only (no capture write tool)

Post-D-091-freeze path delta (`9ec65c7` → `9441b0c`) is evidence /
governance only (`WORKLOG.md`, `docs/backlog.md`, `docs/evidence/D-091*`
through `D-094*`). No `src/`, `apps/`, or production-test path changed
after the freeze.

---

## Exact-main CI

Re-read GitHub run `31838651156` (not invented; not rerun).

```
EXACT_MAIN_CI                  = PASS
POST_MERGE_GITHUB_CI           = PASS
POST_MERGE_CI_RUN              = 31838651156
event                          = push
headBranch                     = main
headSha                        = 9441b0c576dc54bc43a92a62a4e972889424c21f
workflowName                   = ci
conclusion                     = success
```

Jobs (all `success`):

- `control-plane` (`94890535994`)
- `quality (ubuntu-latest, 3.12, full)` (`94890535977`)
- `quality (ubuntu-latest, 3.13, compat)` (`94890536551`)
- `quality (windows-latest, 3.12, windows)` (`94890535928`)

PR-head CI `31837034472` on `822a6d82` remains a separate pre-merge
observation and is not labeled as merge-main CI.

---

## Exact-main bounded validation

Prior D-095 worktree `D:\atlas-acceptance-d060\d095-atlas-src` no longer
matched HEAD/TREE (it had moved to this docs branch at `8c48f85`).
Suites were **re-run** on a fresh detached worktree.

```
VALIDATION_WORKTREE            = D:\atlas-acceptance-d060\d095-recon-src
VALIDATION_HEAD                = 9441b0c576dc54bc43a92a62a4e972889424c21f
VALIDATION_TREE                = ed78a92e941d88ac1aa198c311b0120b4c9ce7ef
STALE_GLOBAL_ATLAS_USED        = NO
PYTHON                         = py -3.12.10 worktree venv
atlas module                   = D:\atlas-acceptance-d060\d095-recon-src\src\project_atlas
```

| Gate | Result | Evidence |
| --- | --- | --- |
| D042_EXACT_MAIN_SUITE | PASS | `test_as_coder_alpha_042_conversation_capture.py` — 7 passed |
| SESSION_CAPTURE | PASS | `test_as_coder_alpha_capture_001.py` — 5 passed |
| KNOWLEDGE_INBOX | PASS | `test_as_2_0_inbox_sched_sec_001.py` — 7 passed |
| AGENT_HANDOFF | PASS | `test_as_coder_alpha_context_handoff_001.py` — 2 passed |
| API_ADV | PASS | `test_as_2_1_api_adv_deepen_001.py` — 22 passed (one isolated rerun flake, then pass) |
| SECURITY | PASS | `test_as_sec_009_api_auth.py` — 7 passed |
| MCP | PASS | `test_as_2_0_mcp_001.py` + `test_as_2_1_mcp_adv_001.py` — 13 passed |
| IDENTITY_CONNECT | PASS | `test_as_coder_alpha_connect_001.py` — 8 passed |
| SOURCE_LINEAGE | PASS | `test_source_identity.py` — 17 passed |
| D049_REGRESSION | PASS | focused D-049 files; 102 passed, 1 skipped. No full `D:\` estate discovery |
| CONTROL_PLANE | PASS | `atlas-vault-documentation/tests --no-cov` exit 0; exact-main CI `control-plane` success |
| RUFF | PASS | `python -m ruff check .` — All checks passed |
| MYPY | PASS | exact-main CI ubuntu 3.12 mypy success; see residual |
| WEB_TYPECHECK | PASS | `npx tsc -b` exit 0 |
| WEB_BUILD | PASS | `npm run build` (`tsc -b && vite build`) exit 0 |

Local `python -m mypy src` with mypy 2.3.1 reported `unused-ignore` at
`src/project_atlas/yaml_structured.py:222`. That file is byte-identical
to previous main `c282f2c` (not in the D-042 production delta).
Applicable CI mypy on `9441b0c` passed.

No authentic Dark Factory recapture. No `D:\` estate discovery.

---

## Local campaign classification

```
LOCAL_EVIDENCE_CLASS           = OWNER-SUPPLIED LOCAL EVIDENCE
CLOUD_OBSERVED                 = NO
D092A_ONBOARDING               = PASS
D092_AUTHENTIC_OWNER_ROUND_TRIP = PASS
D092B_ROUTING_SUPPLEMENT       = PASS
D092_RECONCILED_RESULT         = PASS
D091_LOCAL_ACCEPTANCE          = PASS
LOCAL_D042_CAMPAIGN_STATE      = SEALED_PASS
DOGFOOD_PROJECT_ID             = dark-factory-02ee94d0
DOGFOOD_PROJECT_UUID           = c440d169-bb43-4e97-a175-0d3f62177d8f
DOGFOOD_BASELINE_READY         = YES
DOGFOOD_SNAPSHOT               = CREATED
LOCAL_D092_APPLICABLE_TO_MERGED_MAIN = YES
```

References (not rewritten): `D:\atlas-acceptance-d060\d092c-final\`,
`d092-run`, `d092b-routing`, `d092a-onboarding`, and the prior Local
freeze `D:\atlas-acceptance-d060\d095-seal\FINAL_REPORT.md`.

---

## Authority hard gates

From sealed Local D-092 / D-092B plus the exact-main D-042 suite.
Authentic Dark Factory capture was not rerun.

```
CAPTURE_AUTO_PROMOTIONS        = 0
TRUTH_CORE_MUTATIONS_FROM_CAPTURE = 0
PROJECT_IDENTITIES_MINTED_BY_CAPTURE = 0
PROJECT_IDENTITIES_MERGED_BY_CAPTURE = 0
DISCOVERY_SIDE_EFFECTS         = 0
CONNECT_SIDE_EFFECTS_FROM_CAPTURE = 0
INGEST_SIDE_EFFECTS_FROM_CAPTURE = 0
SECRET_ECHO                    = 0
PROMPT_INJECTION_EXECUTIONS    = 0
CROSS_PROJECT_LEAKS            = 0
REPLAY_DUPLICATES              = 0
NEW_SECURITY_HIGH              = 0
NEW_HIGH                       = 0
HIGH_OPEN                      = 0
```

Historical `AS-CODER-ALPHA-044-HIGH` remains a separate open package.

---

## Post-merge review-comment triage

Four Copilot review comments appeared **after** merge
(`created_at` 2026-08-14T20:40:31–32Z; `mergedAt` 2026-08-14T20:36:07Z)
on tip `822a6d82`. Triaged against production code on exact main
`9441b0c` (same tree as `822a6d82`).

### COMMENT A — `set_conversation_review_state` vault path

Source: https://github.com/WezzSide/project-atlas/pull/353#discussion_r3787029613

`capture_conversation()` and `list_conversation_captures()` raise
`VAULT_NOT_FOUND` when `vault` is not a directory.
`set_conversation_review_state()` resolves the path and, if the capture
JSON is absent, raises `UNMATCHED_CAPTURE`.

```
COMMENT_A_VALID                = YES
SEVERITY                       = LOW
USER_IMPACT                    = inconsistent error code on a missing/non-directory vault for review-state only
AUTHORITY_IMPACT               = none (no review write occurs when the capture file is absent; no promotion path)
CLOSURE_BLOCKING               = NO
```

### COMMENT B/C — `ALLOWED_PROVIDERS` / no-op branch

Sources:
https://github.com/WezzSide/project-atlas/pull/353#discussion_r3787029640
https://github.com/WezzSide/project-atlas/pull/353#discussion_r3787029656

`_normalize_provider()` requires `PROVIDER_RE`. Tokens matching the
regex that are not in `ALLOWED_PROVIDERS` hit an explicit `pass`
("Opaque extra tokens remain provider-neutral; no authority branch").
This is the intended provider-neutral contract. The constant and
no-op branch are misleading / dead-code cleanup, not a semantic defect.
Do not change acceptance semantics for naming aesthetics.

```
PROVIDER_COMMENT_VALID         = YES
SEMANTIC_DEFECT                = NO
CLOSURE_BLOCKING               = NO
```

### COMMENT D — schema required fields

Source: https://github.com/WezzSide/project-atlas/pull/353#discussion_r3787029683

Stored-record schema defines `inbox`, `projection`, and `idempotency`
but does not list them in `required`. The producer always writes them.
Consumers use `.get()` / `or {}` and do not treat absence as authority.
Authority-bearing fields (`authority.level`, `authority.classification`,
`honesty.*`, `review_state`) **are** required and locked
(`NON_CANONICAL`, `quarantined-evidence`, `promoted_to_authority: false`).
A hand-crafted stored record missing the derived trio can validate;
list/review fail-soft; replay overwrites `idempotency`. This is schema
tightness, not an authority/replay safety break.

```
SCHEMA_REQUIRED_FIELDS_DEFECT  = YES
SEVERITY                       = LOW
CLOSURE_BLOCKING               = NO
```

```
POST_MERGE_REVIEW_COMMENTS_TRIAGED = YES
POST_MERGE_BLOCKING_DEFECTS    = 0
LOW_MEDIUM_RESIDUALS           = COMMENT_A error-code inconsistency (LOW); COMMENT_B/C ALLOWED_PROVIDERS naming/dead-code (LOW, not semantic); COMMENT_D optional derived inbox/projection/idempotency (LOW)
```

No HIGH/CRITICAL. No new remediation PR opened (bounded fixes are not
clearly required to protect authority). Residuals remain tracked here.

`GOVERNANCE_RULE_FOR_LOW_MEDIUM_RESIDUALS = NOT_FOUND`
after search of `docs/backlog.md`, `docs/adr/`, `docs/evidence/`,
`atlas-vault-documentation/skill/SKILL.md`, and control-plane docs.
Therefore this lane does **not** close solely on tracked LOW residuals.

---

## Merge-authorization reconciliation (independent of technical correctness)

Search performed:

- this conversation / owner directives (transcript
  `c035d4e1-e0f4-458b-ba3c-b5230167e80b`)
- repository evidence (`docs/evidence/D-091*`, `D-093*`, `D-094*`,
  `D-089*`, WORKLOG, backlog)
- PR `#353` body, reviews, issue comments, merge actor
- ADRs / SKILL / control-plane governance records

Findings:

- Frozen `#353` body recorded `MERGE_AUTHORIZATION = NOT_GRANTED`.
- D-091, D-093, D-094, and the D-094 owner merge packet all recorded
  `MERGE_AUTHORIZATION = NOT_GRANTED`. The packet said it was **not**
  owner authorization.
- PR `#353` issue comments: none. The only review is Copilot, submitted
  **after** merge.
- Merge actor: `cursor[bot]` at `2026-08-14T20:36:07Z`.
- No preserved owner receipt granting merge **before** that timestamp.

Candidate post-hoc directive (not hidden):
`D-PROJECT-ATLAS-OWNER-D042-D095-POST-MERGE-RATIFICATION-AND-CLOSURE`
in this conversation at 2026-08-15 08:34 UTC+2 granted
`OWNER_POST_MERGE_RATIFICATION = GRANTED_CONDITIONALLY` **after** the
merge, if integrity gates pass. That is accepted as a chat owner
directive receipt for **post-hoc ratification only**. It is **not**
pre-merge authorization and does **not** make
`MERGE_AUTHORIZATION_PROVENANCE = VERIFIED` for the merge act.

```
MERGE_AUTHORIZATION_PROVENANCE = UNVERIFIED
PREMERGE_OWNER_AUTHORIZATION   = UNVERIFIED
MERGE_EXECUTION_PRECEDED_VERIFIED_OWNER_AUTHORIZATION = YES
POST_HOC_OWNER_RATIFICATION    = CONDITIONAL
OWNER_GOVERNANCE_RATIFICATION_REQUIRED = YES
MERGE_OCCURRED                 = YES
POST_MERGE_TECHNICAL_SEAL      = PASS
MERGE_AUTHORIZATION            = (not written as VALID)
```

CASE B applies. Do not write `MERGE_AUTHORIZATION = VALID`.
Do not claim pre-merge authorization.

---

## Closure decision

```
D042_POST_MERGE_ACCEPTANCE     = PASS (technical)
D042_FINAL_ACCEPTANCE          = PENDING
D042_STATE                     = MERGED — TECHNICALLY VERIFIED — GOVERNANCE RATIFICATION PENDING
CONVERSATIONAL_CAPTURE         = PRODUCTION_ON_MAIN — GOVERNANCE RATIFICATION PENDING
POST_MERGE_CLOSURE_PRODUCTION_CHANGES = 0
CLOSURE_EVIDENCE_PATH          = docs/evidence/D-095-D042-POST-MERGE-SEAL.md
CLOSURE_PR                     = 360
PR_354_MUTATED                 = NO
NEW_UNRELATED_WORK             = NO
```

Return to owner. Do not merge `#360` without separate explicit
authorization. Do not merge any remediation without separate
authorization.

Local recon freeze copy:
`D:\atlas-acceptance-d060\d095-recon\FINAL_REPORT.md`
(does not overwrite `d095-seal`).
