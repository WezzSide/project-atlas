# AS-MISSION-VERTICAL-SLICE-001 — Review Remediation Evidence

Knowledge -> Development -> Recovery -> Delivery -> Measurement, end to
end. PR #780. Predecessor HEAD `2d47d647f7e4701ab37de494cb49768068c55abb`,
remediated HEAD `0323a43865dc3841ed9e0289b37203e95c079561`.

Built against fresh `origin/main`, not stacked on any open PR. Does not
depend on, import from, or modify #766/#769/#771/#775/#780/#785/#786/#776.

## Invariants

- `SELF_IV = NO`
- `MERGE_AUTHORIZATION = NOT_GRANTED`
- `FORMAL_IV_PERFORMED = NO`
- `LOCK_FACT != HOLDER_IDENTITY` (mirrors resident_driver's own law: the
  OS lease is authoritative; the checkpoint and receipt are observability)

## What this package proves, with real evidence

- **KNOWLEDGE**: real ADR/backlog/worklog retrieval, bounded I/O, content
  identity bound to the exact bytes retrieved (in-process hash, no
  separate re-read), staleness detection that actually catches a real
  file mutation.
- **DEVELOPMENT**: a real coding-agent adapter contract, a real OS-lock
  workspace lease (ownership-specific, reentrancy-safe, thread-safe), a
  real durable checkpoint written before the external effect it precedes.
- **RECOVERY**: UI-reconnect vs. worker-recovery kept genuinely distinct;
  every ambiguous outcome (mid-effect crash, corrupt checkpoint, cleanup
  that couldn't be confirmed) reads back as `UNKNOWN`/blocked, never
  silently safe.
- **DELIVERY FLOW**: idempotency enforced (not merely recorded) against a
  real effect-counting command; trusted policy enforced by the governed
  caller, independent of the adapter.
- **MEASUREMENT**: a small, honest baseline (N reported beside every
  number), command-success and handoff-success tracked separately,
  context-build and dispatch latency tracked separately, owner effort
  (0 by construction) never conflated with cost (unmeasured).

## Real defects found and fixed (not merely reasoned about)

1. Lease reentrancy: a different run_id could silently reuse another
   run's held lease in the same process. Fixed: ownership keyed by
   `(path, run_id)`.
2. Reconciliation TOCTOU: reading the checkpoint and probing the lease as
   two independent observations could straddle a real transition and
   report `SAFE_TO_RETRY` from a stale snapshot. Fixed: reconciliation
   acquires the lease before reading.
3. Timeout only killed the direct adapter process, not its descendants.
   Fixed: real process-tree containment and termination, reproduced with
   a live descendant that stops updating a liveness marker after the
   kill.
4. A corrupted checkpoint after a real effect read back as
   `NO_RUN_FOUND`/`safe_to_retry=True`. Fixed: `ABSENT` and
   `UNREADABLE`/`MALFORMED`/`INCOMPATIBLE` are no longer the same answer.
5. A completed run repeated with the same identity executed its effect
   twice. Fixed: idempotency is enforced against a stored key, not merely
   recorded.
6. `os.replace()` on the checkpoint file could transiently fail on
   Windows with a real `PermissionError` (sharing violation) triggered by
   nothing more than a benign polling reader — reproduced directly (2/30
   real runs), fixed with a bounded retry.
7. The compiled context never reached the adapter at all — only
   `base_head` leaked through into an idempotency key. Fixed:
   `context` is now a required, delivered parameter.

## Demonstration (real, not narrated)

`packet_content_equivalent()` — a real feature — exists because
`compile_mission_context()` retrieved ADR-001 §2 ("Scaffold generation
embeds no wall-clock timestamps") and applied its principle (operational
timestamps are excluded from content-equivalence) to a situation the ADR
itself never covered: comparing two compiled context packets. Run through
the real governed pipeline against this real repository checkout;
validated by a real `pytest` invocation inside that pipeline; the full
interrupt -> reconcile (`UNCERTAIN`) -> naive-retry-REFUSED ->
explicit-operator-acknowledgment -> continuation loop was exercised with
a real `SIGKILL`.

## Boundaries

Frozen and unmoved (re-confirmed at CI-completion time): #766, #769,
#771, #775, #780. Still open and actively moving, never touched: #785,
#786, #776 — this package is a parallel, independent implementation, not
a dependency of or edit to any of them. Reconciling designs with whichever
of those lands first is explicitly a follow-up for whoever holds accepted
authority over both, not this mission's own act.

## Residual, not claimed

- No native Linux CI leg exists in this repository's own `ci.yml`; Linux
  validation here is WSL/native-checkout, not GitHub-hosted Linux CI.
- Formal independent verification (a session other than the one that
  implemented this) has not occurred and is not claimed.
- No merge performed or requested.
