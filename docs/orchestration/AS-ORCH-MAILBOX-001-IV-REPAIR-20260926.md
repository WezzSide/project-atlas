# AS-ORCH-MAILBOX-001 — IV repair record

Repair baseline: `b8ffd36f75a9b8793a50d2bc1c506a01dac18229`

Baseline tree: `8ad4fa5e050c7ef35fd567c8a91c5eee00b1badc`
Independent review: exact-baseline findings F1, F2 and F3, each P1. The
historical review remains a FAIL for those findings; this document records a
new repair candidate and does not rewrite that verdict.

## Finding dispositions

| Finding | Root cause | Repair | Regression evidence |
| --- | --- | --- | --- |
| F1 — rejected message suppresses valid result | Logical-result dedupe scanned every stored record before contextual validation; cross-message idempotency also trusted unvalidated records. | Exact `message_id` replay remains transport-idempotent. Cross-message idempotency and logical-result dedupe now use only processed, non-quarantined, non-duplicate records. Result equivalence binds kind, project-local task/run/dispatch/attempt, requester, reason, trusted HEAD/tree and canonical payload digest. | stale pins followed by valid pins; invalid identity/cross-project input followed by valid result; exact replay; validated equivalent results; reopen/restart behavior. |
| F2 — stale lock takeover permits concurrent writers | A lockfile older than 30 seconds could be unlinked while its owner remained alive; atomic replace did not prevent an old snapshot overwriting a newer one. | Mailbox transactions use a persistent lock file and kernel-owned exclusive file lock (`flock` on POSIX, `msvcrt.locking` on Windows). Elapsed time does not revoke ownership; OS releases the lock on process death. The existing general `ProjectIdentityLock` is unchanged. | old mtime/live-owner exclusion, idempotent old-owner release, terminated-process release, and simultaneous distinct-message writes. |
| F3 — one incident can create multiple successors / restart can reactivate completion | Successor identity included route/source observation details and the journal had no incident-wide lifecycle claim; reconcile recreated absent nodes as READY regardless of prior completion. | Persisted successor identity is incident+generation scoped. The store atomically allows at most one active successor per incident, coalesces compatible route observations, and requires a terminal predecessor plus explicit allowed retry/supersession transition for a later generation. Schema v3 stores lifecycle/generation/supersession; v1/v2 successor records migrate to `WAIT_RECONCILIATION`. Reconcile never recreates missing non-PREPARED nodes or terminal nodes as READY. | different producer/attempt/message, concurrent same-incident admission, distinct incident, explicit terminal supersession, schema migration, and completed/missing-node restart. |

## Recovery and lifecycle

`PREPARED` is the only state from which absence of a governor node permits
creation. Before governor mutation the record advances to `MATERIALIZING`.
An absent node for any later state becomes `WAIT_RECONCILIATION`; operators must
reconcile the actual governor/process evidence before dispatch. Observed
`CERTIFIED`, `MERGED`, `SUPERSEDED` and `CLOSED` states persist as `TERMINAL`.
`BLOCKED`, `OWNER_HELD` and `MERGE_ELIGIBLE` are not inferred terminal.

The mailbox continues to grant no authority and never calls DispatchPort.
Inbox messages/results, route directives, successor bindings and receipts
remain non-authoritative.

## Repair validation

- Focused mailbox/router/transition/autonomy/dispatcher/schema suite:
  **207 passed**.
- Broader `pytest -k "orchestration or autonomy"`: **340 passed, 3 skipped,
  4151 deselected**.
- Ruff check and format check: **PASS**; strict scoped mypy on the three
  changed source modules: **PASS**; `git diff --check`: **PASS**.
- Runtime: isolated local Python 3.14.5 venv. The POSIX `flock` behavior was
  exercised here. The Windows `msvcrt` locking branch is implemented and
  type-checked but **NOT_RUN on native Windows**.
- These are local deterministic tests with the real mailbox/governor code and
  a test governor. No live provider or real-agent E2E, remote CI, independent
  re-review, merge, or deployment is established.
- Initial repair code commit `c2807a10872dfcde8f8b83b4f16fb54f3b1481ac` was
  created from the frozen baseline. The configured signing attempt failed
  because the session's `.gnupg` directory is read-only and no gpg-agent was
  available; the local candidate is unsigned. Do not treat unsigned status as
  review or merge approval.

## Remaining limitations

- No production `001D` provider DispatchPort is connected.
- The existing `001E` CLI path still creates a loop without a provider port.
- `PROGRAM_RECONCILIATION` remains incompatible with the `001D` read-only task allowlist.
- Production authority/candidate identity callbacks are not configured or bound.
- No durable production verifier-attempt/receipt binds immutable candidate,
  lease, attempt, dispatch, verifier identity and independently computed digest.
- No automatic Inbox + real provider + result + independent-verification E2E.
- SDK result digest and pre-start durable dispatch identity remain separate residuals.

No remote provider, VPS, service, or production configuration is exercised by
these local tests. A green local suite does not authorize merge or deployment.

## Follow-on repair: exact 2dc1a6a5 findings N1–N4

This section records a separate repair against immutable baseline
`2dc1a6a5eed691b938d2bd2ba70a1119788efe0d` / tree
`cb2aef215f1658a6c9f95ff3d1cec1da6d10b22d`. The historical review remains
unchanged: N1, N2, N3 and N4 were P1 findings on that exact baseline; F2's
POSIX and Windows review results remain PASS. This is not an independent
re-review of the new candidate.

- **N1 — routing-context dedupe:** cross-message logical result dedupe now
  occurs after routing validation. Equivalence binds the normalized routing
  outcome, task/run/dispatch/attempt/requester, authority reference, trusted
  pins, retry/owner flags, producer role, and normalized execution facts.
  Incomplete `WAIT_EXTERNAL` observations do not consume corrected facts;
  exact `message_id` replay remains idempotent.
- **N2 — current admission checks:** the duplicate path resolves its validated
  source route but passes through the same current source-pin, authority,
  candidate, owner-gate, task-type, and permission checks as first admission.
  Historical admission/deduplication is not treated as current authority.
- **N3 — materialization CAS:** `PREPARED → MATERIALIZING` is an atomic
  compare-and-set on successor generation and lifecycle revision, with a
  unique owner token. Only that token/revision may finalize. Stale claimants
  lose; restart with an unresolved `MATERIALIZING` record waits for
  reconciliation rather than creating a second WorkNode. The existing F2
  POSIX/Windows file-lock implementation was not changed.
- **N4 — replay vs retry:** each admission remains bound to its original
  successor generation, including after terminal completion. Replay never
  creates a generation. A new generation requires a distinct `retry_id`,
  prior successor/generation binding, current admission checks and an
  explicit retry-verifier approval; retry identity is persisted with the
  successor and replay of that retry resolves the same successor.
- Mailbox snapshot schema advances to v4. Legacy lifecycle states migrate
  conservatively: ambiguous in-progress materialization becomes
  `WAIT_RECONCILIATION`; terminal and safely prepared v3 states are preserved.

### Follow-on regression evidence

- N1 targeted regressions: **5 passed**; N2: **5 passed**; N3: **4 passed**;
  N4: **4 passed**. Complete mailbox module: **75 passed**. Existing F2 lock
  tests: **4 passed**; legacy v2/v3 migration tests: **2 passed**.
- Mailbox/router/transitions/autonomy/autonomy-loop/pin-retarget/schema set:
  **218 passed**. Broader `pytest -k "orchestration or autonomy"`:
  **359 passed, 3 skipped, 4151 deselected** (Python 3.14.5, local venv).
- Ruff check, Ruff format check, strict mypy on the three mailbox source
  modules, and `git diff --check`: **PASS**. POSIX tests ran locally; native
  Windows runtime CI has not run for this candidate.
- Tests are local deterministic mailbox/governor tests. No real provider,
  VPS, production callback, independent verification, remote CI, or full
  mailbox/provider/verifier E2E is established. F2's earlier native-Windows
  result remains historical evidence, not a new run on this candidate.

The next candidate requires a fresh exact-HEAD independent review. The
production `DispatchPort`, provider E2E, independently bound verifier receipt,
SDK digest verification, dispatch-start identity window, and
`PROGRAM_RECONCILIATION`/001D mismatch remain separate residuals. No merge or
deployment authorization is implied.
