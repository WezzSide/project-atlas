# ATLAS-SYSTEMWIDE-CONTINUOUS-EXECUTION — mission task queue

Durable queue for the implementing session. Package
`AS-ORCH-PROGRAM-SUPERVISOR-001`, lane `feat/as-orch-program-supervisor-001`,
worktree `~/Projects/project-atlas-worktrees/program-supervisor`, base
`b87b4a22`.

Scope note (owner, 2026-09-10): one runtime and one worker are the first
validation milestone, **not** the final product scope. The objective is
continuous execution across all enrolled Atlas agents and supported runtimes.

## M1 — Prove the supervisor  ✅ COMPLETE

- [x] T01 Repository inventory + reuse map (`REUSE-MAP.md`)
- [x] T02 `models.py` — program, task, limits, acceptance, attempt records
- [x] T03 `profiles.py` — shared defaults, role profiles, narrowing-only overrides
- [x] T04 `store.py` — durable state, atomic writes, append-only fsynced event log
- [x] T05 `adapters/base.py` — adapter seam + outcome contract
- [x] T06 `adapters/local_command.py` — labelled fixture worker + fault injection
- [x] T07 `adapters/claude_code.py` — real Claude Code CLI adapter
- [x] T08 `acceptance.py` — locally observed acceptance, never worker-reported
- [x] T09 `waiting.py` — external events over `sdk.external_observers`
- [x] T10 `supervisor.py` — the cycle
- [x] T11 `cli.py` — validate / start / status / cancel / reconcile / events
- [x] T12 Acceptance program (43 tests, deterministic workers + fault injection)
- [x] T13 Regression tests for races and uncertain outcomes
- [x] T14 Real-runtime demonstration (`evidence/REAL-RUNTIME-DEMO.md`)
- [x] T15 Documentation (`README.md`, `PERMISSIONS.md`)
- [x] T16 Gates: ruff clean, mypy clean (419 files), targeted pytest green

## M2 — Support Codex and Claude  ✅ COMPLETE

- [x] T20 Inventory installed runtimes and existing adapters before adding any
      (existing: Cursor SDK `sdk/backend.py`, cursor-agent
      `sdk/cli_execution_port.py`, generic `local_process_transport.py`;
      `atlas3/memory/codex.py` is a history connector, not an execution
      adapter. No Codex execution adapter existed)
- [x] T21 Codex adapter against the verified `codex exec` interface
      (`codex-cli 0.153.4`), including streamed JSONL and thread-id recovery
- [x] T22 Capability contracts: `accepts_assigned_session` added and consulted;
      per-runtime launch, output, permission and resume behaviour encoded
- [x] T23 Controlled handoff (`program handoff`) — neither runtime attaches to
      a live session; an operator enrols a STORED session and the next dispatch
      continues it in a new supervised run
- [x] T24 `program runtimes` + `SUPPORT-MATRIX.md`; unsupported capabilities
      named, not omitted
- [x] T25 Real Codex demonstration (`evidence/REAL-RUNTIME-DEMO-CODEX.md`)
- [x] T26 31 further tests (`test_orchestration_program_runtimes.py`)

## M3 — Shared enrollment  ✅ COMPLETE

- [x] T30 `agent enroll` — one registry holding identity, role, runtime,
      workspace and narrowing-only profile overrides
- [x] T31 `agent assign` (binding proved before it is recorded) + `agent launch`
- [x] T32 Four identities separated and reported with their lifetimes
      (`agent status`); a dead supervisor releases no ownership
- [x] T33 Existing sessions enter only through `program handoff`; no live
      process is ever adopted
- [x] T34 Verification separation re-checked after enrollment substitution
- [x] T35 16 further tests (`test_orchestration_program_enrollment.py`)

## M4 — Concurrent agents  ✅ COMPLETE

- [x] T40 `max_concurrent_workers` (default 1); only `adapter.run` leaves the
      supervisor's thread, so program state is never mutated concurrently
- [x] T41 Surface-overlap gate, one-lease-per-task and one-worker-per-agent all
      hold under concurrency; exactly one DISPATCH_INTENT per task at every
      concurrency tested
- [x] T42 Owner gates unaffected by free capacity; verifier separation
      re-checked after enrollment substitution
- [x] T43 A finished task releases its lease and its dependant starts without
      a prompt; program completion is never declared with a worker running
- [x] T44 13 further tests (`test_orchestration_program_concurrency.py`)
- [x] T45 Systemwide acceptance run: Claude + Codex, separate queues, one
      supervisor, 2 concurrent workers observed
      (`evidence/SYSTEMWIDE-ACCEPTANCE.md`)

## M5 — Durable execution  ✅ COMPLETE

- [x] T50 `program service run|start|stop|status` on the repository's existing
      mechanism (detached group, log beside state, stop file, `sdk.host`
      identity). Verified by a real detached run that completed a program
- [x] T51 `program service install` writes a launcher and a systemd user unit
      as text; activation is printed, never executed
- [x] T52 Recovery vs replay documented and tested; a completed program run
      again launches nothing; the service writes its OWN identity (PR #766
      defect class)
- [x] T53 POLICY_REFUSAL for a denied-then-failed run; RUNTIME_UNAVAILABLE as a
      named blocked state; quota/credential never retried
- [x] T54 Runtime substitution authorization made durable and read at launch,
      closing a hole where `assign` refused what the supervisor would run
- [x] T55 17 further tests (`test_orchestration_program_service.py`), including
      the CLI's one-JSON-object error contract

## M6 — Central control  ✅ COMPLETE

- [x] T60 `program control` — versioned read-only contract
      (`atlas.program.control` v1): program, tasks, ownership, limits, service
      liveness, reconciliation queue, events, agents
- [x] T61 pause / resume / cancel / reconcile as governed REQUESTS; pause is
      reversible and lets running workers finish, cancel is not and does not
- [x] T62 The read path writes nothing — asserted byte-for-byte on `state.json`
      and `events.jsonl`, with no stop file created
- [x] T63 The contract names what no interface can do (owner gates, limits,
      merge, process adoption, task edits), each with its reason
- [x] T64 15 further tests (`test_orchestration_program_control.py`)

## M7 — Other runtimes  ✅ COMPLETE (with two documented blockers)

- [x] T70 All six installed runtimes inventoried with verified flag-level
      capabilities, what was NOT verified, practical demand, and any blocker
      (`program runtimes`, `SUPPORT-MATRIX.md`)
- [x] T71 Adapters NOT shipped for Cursor and Copilot, with the reason:
      both accounts are at a limit (Cursor usage limit; GitHub token rate
      limited), so their output contracts could not be verified. Raising a
      limit or switching accounts is not authorized. Flag-level capabilities
      are recorded so the work resumes as a short probe
- [x] T72 No generic subprocess adapter exists and `AdapterKind` has no member
      for one, so a program cannot name an unverified runtime
- [x] T73 5 further tests asserting the inventory stays honest

## Systemwide acceptance

- [x] Codex and Claude workers progressing through separate approved queues
      under one supervisor, with ownership protection, explicit limits, and no
      user prompt between eligible tasks
      (`evidence/SYSTEMWIDE-ACCEPTANCE.md`). Restart recovery is covered by the
      test suites rather than by that paid run
- [x] Support reported per runtime and environment (`program runtimes`,
      `SUPPORT-MATRIX.md`)

---

# ATLAS-SUPERVISOR-OPERATIONAL-INTEGRATION-002

Continued in the same lane and the same PR (#797).

- [x] I1 Reconcile the delivery: main unmoved, PR head matches local HEAD,
      ownership checked across 119 worktrees and 60 open PRs
- [x] I2 Full-suite total recovered from the run's own captured output:
      5898 passed / 8 skipped / 4 xfailed / 0 failed at `b19c2298`
- [x] I3 Capability matrix with three tiers (`atlas program capabilities`);
      the two-runtime demonstration labelled precisely
- [x] I4 Minimal additive CLI registration (23 insertions, 0 deletions);
      guard passes unwaived on its own merits
- [x] I5 Lifecycle recovery: all seven properties, against the real mechanisms
- [x] I6 Authority re-read before every dispatch (suspension, un-enrolment,
      withdrawn substitution grant), with a negative control
- [x] I7 Credential/account policy reported without reading any value
- [x] I8 Five outcomes kept distinct, with launch counts encoding retry policy
- [x] I9 Operator view v2: last meaningful progress, waiting condition, retry
      eligibility, required operator actions
- [x] I10 Reproducible operator journey through `atlas`, two real runtimes
- [x] I11 Cursor and Copilot left explicitly unavailable; no re-probing

---

# ATLAS-SUPERVISOR-OPERATIONAL-INTEGRATION-002 — terminal CI and launch handoff

- [x] C1 One bounded CI monitor; CI checkout subject recorded (merge ref
      `9dc135dc`, tree `73b1ac8a` -- **identical** to the head's tree, so CI
      tested exactly this content)
- [x] C2 Lane-owned CI failure investigated and fixed: 6 test functions
      (11 with parametrisations) on both ubuntu jobs assumed `claude`/`codex`
      were on PATH. Verified by reproducing the condition locally
- [x] C3 LAUNCH.md: install, state location, credentials, concurrency,
      budgets, and what actually survives terminal closure
- [x] C4 CONTINUATION.md: task -> next task, external waits, the five states,
      and why no interactive session can be adopted
- [x] C5 WORK-SELECTION.md: no unblocked authorized unowned task exists today,
      with the ownership evidence for each rejection
- [x] C6 `programs/first-program-TEMPLATE.json` with OPERATOR-SUPPLIED fields
- [ ] C7 Terminal CI green on the fixed head (monitor active)

