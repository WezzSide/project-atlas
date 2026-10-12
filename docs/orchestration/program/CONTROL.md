# The control contract — what Atlas Studio consumes

Studio observes; it does not drive.

```
UI          != CANONICAL TRUTH
OBSERVATION != CONTROL
REQUEST     != AUTHORIZATION
```

There is no write path from a UI into program state. Everything is either a
projection of durable state or one of four requests routed through the same
governance a command-line operator goes through.

## Reading

```bash
python -m project_atlas.orchestration.program.cli program control \
  --program PROGRAM.json --state-root ~/atlas-state
```

One versioned JSON object:

| Key | What it carries |
| --- | --- |
| `contract_id`, `contract_version` | `atlas.program.control`, currently `1` |
| `read_only` | always `true` for the view |
| `program` | id, objective, approver, digest, base pin, complete/paused/cancelled, last stop reason |
| `supervisor` | the service identity and whether it is genuinely alive (PID **and** start identity) |
| `tasks` | per task: state, agent, adapter, attempts, launches, failure class, owner gate, what it waits on, its last attempt, and the acceptance conditions themselves |
| `ownership` | active leases: task, agent, lease id, authorized paths |
| `needs_reconciliation` | attempts with an unknown outcome |
| `status` | the same compact status `program status` prints |
| `limits` | declared limits, launches used and remaining, estimated cost with its caveat |
| `actions` | what is supported, and what is **not**, each with a reason |
| `recent_events` | the tail of the durable event log |
| `agents` | the enrolled roster |

### Stability

Fields are added, never renamed or repurposed. A consumer that does not
recognise a field ignores it; a consumer pinned to version 1 keeps working. If
a field's meaning would have to change, a new field appears beside it and the
old one is deprecated in place.

## Acting

```bash
python -m ...program.cli program control --program P --state-root R \
  --action pause|resume|cancel|reconcile --requested-by wesley [--attempt-id A]
```

| Action | Effect |
| --- | --- |
| `pause` | stop starting new work; workers already running are left to finish. Reversible |
| `resume` | clear a pause. A running service picks it up without restarting |
| `cancel` | stop, and terminate any worker currently running. **Not** reversible from here |
| `reconcile` | inspect interrupted attempts; with `--attempt-id`, record an operator's judgement that one did not land |

### Pause is not cancel

Interrupting a running worker turns a reversible operator decision into a set
of uncertain outcomes that need reconciling — which is not what anyone means by
"pause". A pause therefore lets in-flight workers finish and only withholds new
dispatch. Cancel is the one that interrupts, and its outcomes are recorded as
`UNCERTAIN` rather than assumed either way.

## What no interface can do

Named in the contract itself, so a UI can render honestly instead of implying
capabilities nobody has:

| Refused | Why |
| --- | --- |
| `grant_owner_gate` | an owner gate is granted outside this system or not at all |
| `raise_limits` | limits come from the approved program file; changing them is a new approval, not a control action |
| `merge` | this package never merges |
| `adopt_running_process` | a session enters a program only through an explicit handoff of a **stored** session |
| `edit_task` | the approved program is immutable while it runs; editing it is detected as `PROGRAM_DIGEST_DRIFT` and refused |

The contract also cannot reach `enroll`, `assign` or `handoff`. Those are
operator acts with their own recorded provenance, not things a viewer should be
able to trigger.

## Closing Studio does not stop work

It cannot. The supervisor is a separate process, its state is on disk, and the
whole read path writes nothing — asserted by a test that reads the view five
times and checks `state.json` and `events.jsonl` are byte-identical afterwards,
and that no stop file appeared.
