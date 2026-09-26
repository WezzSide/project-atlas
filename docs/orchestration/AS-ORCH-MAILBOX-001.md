# AS-ORCH-MAILBOX-001 — Agent Inbox candidate

Status: implemented in an isolated candidate carrier; not merged, not owner-approved, and not a production integration. The mailbox is transport state only: `MESSAGE != AUTHORITY`, `RECEIPT != AUTHORITY`, `TASK_DIRECTIVE != EXECUTION`, and `PASS != MERGE AUTHORIZATION`.

## Source binding

This candidate is based on `f6b2495a03196901a5a72c2cf3451d4504b54d5f` with tree `9c670d710ec63d36fea70c6a181c088b79294336`, in isolated branch `feat/as-agent-mailbox-001`. The carrier is not the canonical source anchor. No push, merge, or canonical checkout mutation is part of this work.

Direct source inspection confirmed:

- AS-ORCH-001A (`orchestration/models.py`, `transitions.py`, `validator.py`) owns result validation/classification.
- AS-ORCH-001B (`orchestration/router.py`, `policy.py`) owns deterministic typed routing and always emits non-authoritative directives.
- AS-ORCH-001D (`orchestration/dispatcher.py`) owns governed single-hop dispatch.
- AS-ORCH-001E (`orchestration/autonomy/loop.py`) owns the persistent loop and `DispatchPort` recovery seam; `autonomy/continuation_broker.py` owns its existing single-successor continuation state.
- `atlas3/ledger.py` is an append-only normalized engineering-event ledger. `append_event` is event-ID idempotent, but the ledger has no pending/processed inbox lifecycle or attempt-correlated result consumption. Agent-event ingestion has separate provenance/policy/receipt requirements. Neither was repurposed as task authority.

## Candidate composition (current carrier state)

```text
provider/agent result
  -> AgentMailbox durable ingress and correlation
  -> InboxRouter producer-identity callback + trusted HEAD/tree check
  -> existing 001A parse/classify
  -> existing 001B route
  -> continuation-eligible TaskDirective (execution_authorized=false)
  -> MailboxGovernorBridge validates and persists a typed successor binding
  -> deterministic read-only WorkNode admission via existing governor.add_node/mark_ready
  -> 001E capability matching / lease / DispatchPort seam
```

The message contract is `AgentInboxMessage` (`schema_version=1`) and is registered as `agent-inbox-message`. Authority-bearing flags are literal `false`; an attempted true claim is rejected. `owner_required` is an untrusted producer hint and does not affect routing. Secret-shaped content is rejected before persistence; quarantine stores only the content digest and a reason code. Payload SHA-256 binds canonical JSON bytes, not producer authenticity; caller-provided identity and task-attempt binding verifiers must bind the producer to the actual transport identity and the task/run/dispatch/attempt to current governed state. Without those verifiers, routing quarantines the message.

Storage is one digest-protected JSON snapshot per project under `.atlas/orchestration/inbox/<project_id>/state.json`. Writes use a cross-process project lock, fsynced temporary file, atomic replace, and directory fsync on POSIX. Corrupt state fails closed. Messages are ordered by `(created_at, message_id)`. Exact message replay is idempotent; changed content under the same ID is quarantined. Idempotency-key replay with identical operation binding is suppressed; key reuse with changed content is quarantined. Identical result payloads for the same task/run/dispatch are recorded as duplicates and are not classified again. Incident IDs correlate producer observations by project, task, task class, trusted HEAD/tree, and normalized reason code; raw failure prose and producer identity do not affect the correlation key.

The mailbox currently supports `PENDING`, `PROCESSED`, and `QUARANTINED`. A single locked consumer persists the pure routing result before returning. `MailboxGovernorBridge` validates the persisted 001B directive, source pins, injected authority verification, allowed read-only task types, and the narrowly recognized pre-start recovery facts. It writes a deterministic successor binding and non-authoritative `WorkNode` before idempotently materializing the node through the existing governor. It never leases, selects an agent, or calls `DispatchPort`. Existing 001E remains the only selection/lease/dispatch owner. No second scheduler, task queue, dispatcher, or authority ledger is introduced.

## Launcher-failure acceptance fixture

For `LOCAL_EXECUTOR_SETUP_REFRESH_FAILED`, 001A now returns the existing `AUTONOMOUS_RECONCILE` transition only when the result is `BLOCKED`, contains exactly that blocker, has a valid receipt binding, `target_moved=false`, `unauthorized_mutations=0`, and structured `retryable=true` / `process_started=false` facts. 001B maps that to the existing `PROGRAM_RECONCILIATION` TaskDirective. The directive remains non-authoritative. Missing or conflicting facts remain `BLOCKED`; a sender's `OWNER_REQUIRED` hint cannot create an owner gate. A genuine 001A owner-gated state still routes to `OWNER_REQUIRED`.

This is bounded admission, not execution authority. The bridge admits only the exact known pre-start setup-refresh blocker as `PROGRAM_RECONCILIATION`; candidate verification/recertification additionally requires a callback confirming exact candidate HEAD/tree identity. Unknown task types, owner-gated transitions, mutating permissions, stale source pins, missing authority verification, and duplicate/corrupt bindings fail closed. The resulting WorkNode has an empty mutation surface, `EXTERNAL_AGENT` host class, explicit `DISCOVER` or `VERIFY` capability, and all authority flags false. Governor admission makes it READY; only a separately configured 001E `DispatchPort` can lease and dispatch it. The carrier does not configure a production authority verifier or concrete provider DispatchPort.

`VERIFICATION_RESULT` remains held at `WAIT_EXTERNAL`: this bridge does not yet ingest a verifier receipt bound to the active dispatch, lease, implementer, verifier, immutable candidate HEAD/tree, and independently computed result digest. Admission-time candidate validation does not certify a later worker result. A typed `PASS` or role label is not verification evidence.

## 001E safety corrections and remaining seam

The loop now selects only an available agent whose registered capabilities cover the WorkNode requirements. If none exists, it raises `CAPABILITY_UNAVAILABLE` before creating a lease or calling the dispatch port. A failed worker/process result transitions to `BLOCKED`; it cannot be misrepresented as an independent-review failure or consume an IV remediation cycle. A successful result for a node with `certification_required=true` routes an independent verifier identity and leaves the node `VERIFYING`; it does not call `complete_verification(..., passed=True)`. If no independent verifier is available, that node is marked `BLOCKED` and its attempt is finalized so the single loop slot is not wedged. Non-IV nodes retain the existing completion path.

This is not a complete verifier execution path. Current 001E state does not durably bind/dispatch a separate verifier attempt or accept a verifier result with immutable candidate identity, lease/attempt identity, and independently computed payload digest. The shipped `run_governor_loop_tick` also does not provide a concrete 001D `DispatchPort`. Therefore the implementation proves WorkNode admission and capability-safe selection through the existing seam, not an end-to-end provider execution or independent-verification cycle.

## State transitions and recovery

| Event | Persisted state | Behavior |
|---|---|---|
| valid first message | `PENDING` | store message, payload digest, and deterministic incident ID |
| exact replay | unchanged | return existing receipt; no second classification |
| same ID, altered content | `QUARANTINED` receipt | preserve only digest/reason; original record remains |
| malformed/cross-project/authority claim | `QUARANTINED` receipt | no delivery or authority effect |
| accepted deterministic route | `PROCESSED` | persist 001A decision and 001B route atomically |
| stale source pin / unverified producer | `QUARANTINED` | no route |
| process interruption before replace | old snapshot remains | retry reads prior durable state; no external effect occurred |
| restart after commit | snapshot rehydrates | processed message is not reclassified; pending message is handled once under lock |

This store does not claim a transaction with the autonomous loop, provider process, or continuation broker. Recovery of actual process dispatch remains with 001D/001E. There is no exactly-once external execution claim.

## Validation and limits

The focused mailbox/governor/loop/dispatcher/schema tests and the orchestration/autonomy test selection were run in the isolated editable `.[dev]` environment; exact outcomes are recorded in the current worklog. `pip check`, dependency import smoke, Ruff, strict mypy, and diff checks were run. These tests prove deterministic local contracts and fake `DispatchPort` behavior only; they are not live provider evidence. The isolated venv install established that project metadata supplies `referencing` transitively through `jsonschema`; no runtime dependency change was needed.

The Atlas documentation doctor returned `{"ok": false, "checks": {"canonical_skill": "PASS", "skill_hash": "PASS", "adapters": "FAIL", "project_configuration": "PASS", "vault_identity": "PASS", "spool": "PASS"}}`. No normalization, canonical vault update, or receipt is claimed. This note is a candidate source document, not a synchronized Atlas record.
