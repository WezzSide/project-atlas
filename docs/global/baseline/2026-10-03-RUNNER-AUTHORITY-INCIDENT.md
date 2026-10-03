# Runner Transport-Authority Incident and Hardening Backlog — 2026-10-03

| Field | Value |
|---|---|
| Token | `RUNNER_TRANSPORT_GRANT_EXPIRY_INCIDENT_RECORDED` |
| Code observed at | `main` `b413aa1a7a781390ec13d326554c24895c2dbe50`, TREE `fd21eafd63f3de54c37cd19accb219538e272e9a`. All `file:line` references are at this identity |
| Written at | 2026-10-03T07:36Z |
| Companions | [2026-10-02-AUTONOMY-FRONTIER.md](./2026-10-02-AUTONOMY-FRONTIER.md), [2026-10-02-OPERATIONAL-SLICE-AND-F2-BRIEF.md](./2026-10-02-OPERATIONAL-SLICE-AND-F2-BRIEF.md), [2026-10-02-AUTHORITY-COMPATIBILITY.md](./2026-10-02-AUTHORITY-COMPATIBILITY.md) |
| Evidence vocabulary | [Operating contract §4](../ATLAS-GLOBAL-OPERATING-CONTRACT.md): `OBSERVED` / `PROVEN` / `INFERRED` / `PLANNED` / `UNKNOWN` |
| Status | Record and analysis only. It grants nothing, decides nothing, and changes no runtime behaviour. Every backlog item is `PLANNED` |

Two evidence sources are kept apart throughout:

- **Code** — read by this session from the repository at the identity above. Labelled
  `OBSERVED`.
- **Relayed** — the incident narrative as reported by the owner. This session had no access
  to VPS2, its journal, its state database or its configuration. Relayed statements are
  marked `RELAYED` and carry the label `UNKNOWN` for direct observation; they are not
  rounded up.

## 1. Incident summary

| # | Statement | Source | Label |
|---|---|---|---|
| 1 | On 2026-10-02 the E2 execution for `ATLAS-DEVQ-0002` (workflow `atlas-agent-execute`, run `37023482722`, job `110892156175`) stayed queued | RELAYED | `UNKNOWN` (not observed by this session) |
| 2 | Cause: the previous standing VPS transport admission grant had expired | RELAYED | `UNKNOWN`; consistent with code (§2.2 row "expired") |
| 3 | The controller failed closed: no worker was started for the queued job | RELAYED | `UNKNOWN` for the host; the fail-closed branch itself is `OBSERVED` in code (`controller.py:189-196`) |
| 4 | The expiry refusal was not visible in the normal journal or health surfaces | RELAYED | **Confirmed against code**, `OBSERVED` (§2.4): the refusal is written only to the SQLite `audit_log` table |
| 5 | Recovery: a temporary replacement grant `grant-github-transport-e2-20261002`, reported expiry `2026-10-03T15:00:00Z`, reported repository-wide for queued executor jobs | RELAYED | `UNKNOWN` (§5) |
| 6 | Recovery included exactly one controller restart, after which the same queued run executed | RELAYED | `UNKNOWN` for the host; the need for a restart is explained by code, `INFERRED` (§3.4) |
| 7 | Infrastructure recovery only: no extra dispatch and no extra implementation attempt | RELAYED | `UNKNOWN`; consistent with code, which keeps no state row for a refused job (§2.3), so the original queued job is re-evaluated as new |

Net effect: the safety property held (fail closed) and the observability property failed
(the operator could not see *why* from the surfaces the runbooks point at).

## 2. How the controller handles the transport grant today

All statements in this section are `OBSERVED` in code unless marked otherwise. Paths are
relative to `infra/atlas-runner/`.

### 2.1 Where the grant is configured, loaded and validated

| Step | Location | What happens |
|---|---|---|
| Config field | `controller/config.py:105-113` | `transport_grant_id: str \| None = None`, `queued_transport_enabled: bool = False`. Both default to fail closed |
| Config load | `controller/cli.py:31-34` (`_build_context`) | The TOML is parsed once per process. `cmd_run` (`cli.py:75-81`) never re-reads it |
| Registry attach | `controller/cli.py:49-53`, `cli.py:78` | `GrantStore` opens the **same SQLite file** as the state store |
| Grant record | `controller/grants.py:51-65` | Table `grants`: `grant_id`, `scope_json`, `repository`, `base_revision`, `executor_type`, `status`, `expires_at`, `budget`, `consumed`, timestamps |
| Validation call | `controller/controller.py:188-190` | Runs for **every queued job on every poll**: `self.grants.validate(transport, repository=repository)` |
| Validation rules | `controller/grants.py:175-232` (`validate_row`) | Order: missing id, unknown id, `status != active`, expired, budget exhausted, column bindings, scope bindings |
| Poll cadence | `controller/controller.py:362-368`, `config.py:99` | `poll_interval_seconds`, default `10.0` |

The grant row is read from SQLite on each validation (`grants.py:137-141`), so the *content*
of the registry is live. The *identity* of the grant the controller asks for is the config
value, fixed at process start.

The transport call passes only `repository`. `task_id`, `base_revision`, `executor_type`,
`action_type` and `execution_hash` are left `None`, and `require_complete_bindings` is
`False`:

```python
# controller/controller.py:188-190
repository = f"{self.config.github.owner}/{self.config.github.repo}"
try:
    self.grants.validate(transport, repository=repository)
```

The grant is never consumed on this path (`controller.py:154-156`, and
`docs/ATLAS-INTERFACE.md:132-133`: "validated per admission but NOT consumed per job").

### 2.2 Outcome per grant condition

| Condition | Branch | Audit event / `detail.reason` | Job outcome |
|---|---|---|---|
| `queued_transport_enabled = false` | `controller.py:160-166` | `blocked_authority` / `queued_transport_disabled` | Skipped, stays queued in GitHub |
| `transport_grant_id` unset | `controller.py:175-181` | `blocked_authority` / `no_transport_grant_configured` | Skipped |
| No registry attached | `controller.py:182-187` | `blocked_authority` / `grant_registry_required` | Skipped |
| Grant id not in registry | `grants.py:192-193` → `controller.py:191-196` | `blocked_authority` / `transport_grant_invalid: unknown grant '<id>'` | Skipped |
| Status not `active` (revoked) | `grants.py:195-196` | `blocked_authority` / `transport_grant_invalid: grant '<id>' status=<status>` | Skipped |
| **Expired** (`now >= expires_at`) | `grants.py:197-198` | `blocked_authority` / `transport_grant_invalid: grant '<id>' expired` | Skipped |
| Budget exhausted (`consumed >= budget`) | `grants.py:199-200` | `blocked_authority` / `transport_grant_invalid: grant '<id>' budget exhausted` | Skipped |
| Repository mismatch | `grants.py:201-212` | `blocked_authority` / `transport_grant_invalid: grant '<id>' binds repository=…` | Skipped |
| Grant bound to `base_revision`, `executor_type`, or scope `task_id` / `action_type` / `execution_hash` | `grants.py:201-231` | `blocked_authority` / `transport_grant_invalid: … scope mismatch` | Skipped: the transport call supplies none of these, so any such binding can never match |
| Any other exception (for example a SQLite error) | `controller.py:191` (`except Exception`) | Same `transport_grant_invalid: <text>` | Skipped; indistinguishable from an authority refusal |
| Valid | `controller.py:212-217` | `transport_grant_validated` / `{"grant_id": <id>}` | Proceeds to capacity check and execution |

The expiry branch in full:

```python
# controller/grants.py:197-198
if row["expires_at"] is not None and now >= row["expires_at"]:
    raise GrantExpiredError(f"grant {grant_id!r} expired")
```

```python
# controller/controller.py:191-196
except Exception as exc:
    self.store.audit(
        "blocked_authority", task_id=task_id,
        detail={"reason": f"transport_grant_invalid: {exc}"},
    )
    continue
```

Three consequences follow from the code:

- **Fail closed is real.** Every refusal ends in `continue` before `submit_task` and
  `_start_execution`. No worker is provisioned.
- **A refused job leaves no task or execution row.** The `continue` precedes
  `store.submit_task` (`controller.py:218`). The job remains queued on GitHub and is
  re-evaluated on the next poll under the identity `gh-<run_id>-<run_attempt>-<job_id>`
  (`controller.py:150`). Once a valid grant is in force, the same queued job is admitted
  with no new dispatch.
- **"Exhausted" is nearly unreachable for a standing transport grant.** Nothing on the
  transport path increments `consumed`. It only occurs if the grant was issued with
  `budget <= 0` or the same id is also consumed on the `submit` path.

### 2.3 What is logged, and at what level

- The controller package has **no logging at all** on the admission path. There is no
  `import logging` in `controller/controller.py`, `grants.py`, `state.py`, `health.py` or
  `reconcile.py`, and no `print` in the daemon loop. The only `print` calls are in
  `cli.py`, on one-shot subcommands (`cli.py:89-379`).
- `StateStore.audit` is a single SQLite insert and nothing else:

```python
# controller/state.py:157-176 (abridged)
def audit(self, event, *, task_id=None, execution_id=None, detail=None) -> None:
    with self._conn:
        self._conn.execute(
            "INSERT INTO audit_log(ts, task_id, execution_id, event, detail_json)"
            " VALUES (?,?,?,?,?)", ...
```

- The systemd unit runs `atlas-runner … run` as a `Type=simple` service
  (`systemd/atlas-runner-controller.service:9-17`). The journal therefore carries only what
  the process writes to stdout or stderr. On the refusal path that is nothing. There is no
  log level because there is no log line.
- A second silent path sits next to it: a GitHub polling failure returns an empty list with
  no audit row and no output (`controller.py:141-144`).

### 2.4 What health and status report

- `health` (`controller/health.py:26-168`) computes exactly these checks: `state_db`
  (heartbeat age), `docker`, `worker_image`, `state_dir_writable`, `config`,
  `free_memory_mb`, `free_disk_mb`, `workers`, `github_api`. **None reads the `grants`
  table, the configured `transport_grant_id`, or the `audit_log`.**
- The heartbeat is written at the top of every poll, before admission
  (`controller.py:357-360`). A controller that refuses every job still heartbeats, so
  `state_db` stays `ok` and the verdict stays `healthy` (exit 0).
- `status` (`cli.py:115-139`) reports only `active_workers` and active executions. A refused
  job has no execution row, so it does not appear.
- `fabric-state` (`cli.py:157-177`) derives from executions and containers and reports
  `IDLE_READY` while every queued job is being refused.
- No CLI subcommand reads `audit_log`. The only references to the table in the controller
  package are the schema and `INSERT` statements (`state.py:72-79`, `state.py:167`). Reading
  it requires direct SQLite access to the state database on the host.

### 2.5 Audit and evidence written

- **Audit:** one `blocked_authority` row per refused job **per poll**. At the default
  10-second interval that is about 8 640 rows per day for each queued job, with no
  de-duplication and no pruning of `audit_log` in the controller package. The `detail`
  carries the exception text, which includes the grant id (an identifier, not a secret) and
  the reason phrase. It carries no stable machine-readable reason code: the phrase is
  free text built from `str(exc)`.
- **Evidence:** none. Execution evidence is produced by the worker lifecycle
  (`controller/evidence.py`, `controller/worker.py`), which a refused job never enters. There
  is no evidence artifact, receipt or GitHub-visible signal for a refusal. The GitHub job
  simply stays `queued`.

### 2.6 Verdict on "the expiry refusal is invisible"

**Confirmed, `OBSERVED` in code.** An expired transport grant produces:

| Surface | Result |
|---|---|
| systemd journal | Nothing |
| `atlas-runner health` | `healthy`, exit 0 (given the other checks pass) |
| `atlas-runner status` / `fabric-state` | No active work, `IDLE_READY` |
| GitHub | Job stays `queued` with no annotation |
| SQLite `audit_log` | `blocked_authority` rows with a free-text reason. Not reachable through any CLI subcommand |

The refusal is recorded, so the statement "no trace at all" would be false. It is recorded
only in a place that the documented operator path (`docs/OPERATIONS.md`,
`docs/RECOVERY.md`: journal, `health`, `status`, `reconcile`) never reads. The existing
tests cover expiry at registry level (`tests/test_integration_hardening.py:52`, `:102`,
`:202`) and the disabled-transport refusal (`tests/test_authority_path_repairs.py:58`,
`tests/test_overnight_admission_mission.py:49`). No test asserts any operator-visible
signal for a transport-grant refusal.

## 3. F-RUNNER-3 — controller reconcile semantic drift

### 3.1 Question

`controller/controller.py:367` carried the comment
`# reconcile is run by the CLI on startup and periodically here`. Does reconciliation run
periodically, or only at startup?

### 3.2 Answer

**Startup only (plus manual invocation). There is no periodic reconcile.** `OBSERVED`.

Every call site of `Reconciler.reconcile()` outside the tests:

| Call site | When |
|---|---|
| `controller/cli.py:79` (`cmd_run`) | Once, before `controller.run_forever()` |
| `controller/cli.py:290` (`cmd_reconcile`) | Manual `atlas-runner reconcile` |

Proof that nothing else reaches it:

- The loop body is `poll_once()` then sleep (`controller.py:362-368`). `poll_once`
  (`controller.py:357-360`) calls `store.heartbeat` and `admit_queued_jobs` only.
- `controller/controller.py` does not import `controller.reconcile` (imports at
  `controller.py:12-23`). The `Controller` class holds no `Reconciler`.
- `cmd_once` (`cli.py:84-90`), the documented cron/timer fallback, does not reconcile
  either.
- `systemd/` contains two units, `atlas-runner-controller.service` and
  `atlas-runner-firewall.service`. There is no `.timer` unit and no `OnCalendar` anywhere
  under `infra/`.
- No script under `scripts/` and no workflow under `.github/workflows/` invokes
  `atlas-runner reconcile`.
- Tests call `Reconciler(...).reconcile()` directly (`tests/test_reconcile.py`,
  `tests/test_failure_matrix.py:223`, `tests/test_overnight_admission_mission.py:265`). None
  drives it through `run_forever`.

This agrees with the earlier baselines, which already recorded "startup only"
([frontier §4](./2026-10-02-AUTONOMY-FRONTIER.md),
[G1–G15 baseline](./2026-10-02-G1-G15-BASELINE.md)).

### 3.3 Disposition

The comment was provably stale. It was corrected in a separate, comment-only commit to state
the actual behaviour. No scheduling semantics changed.

The same drift exists in four other places, left untouched by this record because the
allowed change was the single comment:

| Location | Stale text |
|---|---|
| `controller/controller.py:4-5` (module docstring) | "reconcile on startup and periodically" |
| `controller/controller.py:358` (`poll_once` docstring) | "One poll cycle: reconcile leftovers, admit, heartbeat" |
| `controller/reconcile.py:3` (module docstring) | "on startup and periodically" |
| `docs/ARCHITECTURE.md:20` | "Startup + periodic stale-worker cleanup" |

Four independent statements of "periodic" make it likely that periodic reconcile was the
**design intent** and was never implemented (`INFERRED`). It is therefore proposed as a
WorkItem rather than resolved by rewording alone.

**WorkItem proposal — `F-RUNNER-3` `CONTROLLER_PERIODIC_RECONCILE`** (`PLANNED`)

| Field | Value |
|---|---|
| Outcome | Reality and documentation agree. Either (a) the daemon runs the existing `Reconciler.reconcile()` on a bounded interval, or (b) every statement of "periodic" is removed and startup-only is the documented contract |
| Why it matters | A worker lost mid-run, a stale worker past its timeout, a stuck `REQUESTED`/`ADMITTED` admission (`reconcile.py:56-89`, including its grant refund) and stale workspaces are repaired only at the next restart or manual run. On a long-lived daemon that is unbounded |
| Scope for (a) | `controller/controller.py` (`Controller.__init__`, `run_forever`), `controller/cli.py` (`cmd_run` wiring), `controller/config.py` plus `schemas/controller-config.schema.json` (one interval key, fail-closed parse), `tests/test_reconcile.py`, `tests/test_controller.py`, the five stale statements above |
| Constraints | Reconcile must not run concurrently with `_start_execution` (the loop is single-threaded and `run_execution` is synchronous, so interleaving between polls satisfies this). Interval is counted with the injected `clock`. A reconcile exception must not kill the loop and must be surfaced (F-RUNNER-1 surface). `RECONCILED != VERIFIED` is preserved |
| Acceptance tests | `test_run_forever_reconciles_on_interval`, `test_run_forever_does_not_reconcile_every_poll`, `test_periodic_reconcile_failure_does_not_stop_polling`, `test_periodic_reconcile_never_touches_running_execution`, `test_stuck_admission_refund_is_exactly_once_across_periodic_passes` |
| Non-goals | Node-loss detection, multi-host leases, any change to reconcile rules, any change to grant semantics |
| Decision needed | Choice between (a) and (b), and the interval for (a). This changes runtime scheduling on a production host, so it goes through the normal authorize → implement → IV → owner merge lifecycle |

### 3.4 Does startup-only reconcile explain the restart in the recovery?

**No. The restart is explained by configuration loading, not by reconcile.** `INFERRED`
from code; the actual operator steps on the host are `UNKNOWN`.

- Reconcile does not read grants and does not act on queued GitHub jobs
  (`reconcile.py:91-126`). A refused job has no execution row for it to repair (§2.2).
- Grants are **not** loaded only at startup. The registry row is re-read on every
  validation (`grants.py:137-141`, `controller.py:190`). A grant inserted while the daemon
  runs is visible on the next poll.
- The grant **id** the controller asks for is loaded only at startup
  (`cli.py:34`, `controller.py:174`).
- Grant records are immutable by id. Re-issuing an existing id with a different
  `expires_at` raises `GrantConflictError` (`grants.py:109-120`), and re-issuing identical
  content does not reactivate it (`tests/test_integration_hardening.py:102-112`). An expired
  grant can therefore not be extended in place through `GrantStore.issue`.

Combined: recovery from expiry requires a grant under a **new id**
(`grant-github-transport-e2-20261002` is a new id, consistent with this), which requires
changing `transport_grant_id` in the TOML, which takes effect only when the process starts
again. One restart is the minimum the current design allows.

One further gap sits on the same path: `GrantStore.issue` has **no production caller**.
There is no `atlas-runner` subcommand and no script that issues, lists or revokes a grant;
the only callers are tests. How the replacement grant was written into the VPS2 registry is
`UNKNOWN` to this session, and no runbook in `docs/` describes it.

## 4. Ranked hardening backlog

All items are `PLANNED`. Rank orders by risk reduced per unit of work, with fail-closed
behaviour preserved in every item. "Agent-doable" means implementable inside a normal
authorized WorkItem with independent verification and owner merge; it never means
self-authorized.

| Rank | ID | Outcome | Risk if left | Authority needed | Evidence for the gap |
|---|---|---|---|---|---|
| 1 | **F-RUNNER-1** `TRANSPORT_GRANT_EXPIRY_OBSERVABILITY` | Every transport refusal carries a stable non-secret reason code in journal, health and audit, still failing closed | The next expiry looks identical to an idle healthy runner. Diagnosis again needs host-level SQLite access and owner relay | Agent-doable | `OBSERVED` (§2.3–§2.6) |
| 2 | **F-RUNNER-4** `TRANSPORT_GRANT_PRE_EXPIRY_WARNING` | Health degrades and the journal warns before the grant expires | Expiry is discovered only by a stalled job, as on 2026-10-02 | Agent-doable; warning threshold is an operations default | `OBSERVED`: no code reads `expires_at` except the refusal branch (`grants.py:197`) |
| 3 | **F-RUNNER-2** `TRANSPORT_GRANT_SCOPE_BINDING` | The transport grant can be narrowed to a task, execution, workflow and budget | A repository-wide standing grant admits every queued job whose labels match, for its whole validity window | **Owner decision** on authority semantics, then agent-doable | `OBSERVED` (§2.1, §4.3) |
| 4 | **F-RUNNER-5** `TRANSPORT_GRANT_OPERATOR_PATH` | Issue / list / revoke and rotate a transport grant through an audited CLI and a runbook, without a restart | Rotation stays an undocumented manual database edit plus config edit plus restart; each step is a chance to widen authority by mistake | **Owner decision** (who may issue; whether rotation without restart is acceptable), then agent-doable | `OBSERVED` (§3.4): no production caller of `GrantStore.issue`; config read once |
| 5 | **F-RUNNER-3** `CONTROLLER_PERIODIC_RECONCILE` | Reconcile cadence matches its documentation | Lost or stale workers and stuck admissions wait for a restart | Agent-doable after the (a)/(b) choice | `OBSERVED` (§3.2) |
| 6 | **F-RUNNER-6** `REFUSAL_AUDIT_DEDUPLICATION` | One audit row per refusal state change, not one per poll | About 8 640 rows per day per queued job during an outage; unbounded `audit_log` growth on a disk-gated host | Agent-doable | `OBSERVED` (§2.5) |
| 7 | **F-RUNNER-7** `ADMISSION_ERROR_CLASSIFICATION` | Infrastructure errors are distinguished from authority refusals; GitHub poll failures are surfaced | A database fault is reported as an authority problem; a GitHub outage is reported as nothing | Agent-doable | `OBSERVED` (`controller.py:141-144`, `controller.py:191`) |

F-RUNNER-1 and F-RUNNER-4 share an insertion point and can ship as one slice. They are
listed separately because F-RUNNER-1 alone closes the incident's observability gap.

### 4.1 F-RUNNER-1 — `TRANSPORT_GRANT_EXPIRY_OBSERVABILITY`

**Outcome.** When the configured transport grant is absent, expired, exhausted or otherwise
invalid, the controller exposes one explicit, non-secret reason code in three places — the
journal, the health/status surface and the audit log — and continues to fail closed. No
grant content beyond the grant id, and no secret, appears in any of them.

**Proposed reason-code vocabulary.** Stable identifiers, one per refusal class, replacing
the free-text `transport_grant_invalid: <exception text>`:

| Code | Raised from | Meaning |
|---|---|---|
| `TRANSPORT_DISABLED` | `controller.py:160` | `queued_transport_enabled` is false |
| `TRANSPORT_GRANT_NOT_CONFIGURED` | `controller.py:175` | `transport_grant_id` unset |
| `TRANSPORT_GRANT_REGISTRY_UNAVAILABLE` | `controller.py:182`, and non-`GrantError` exceptions | No registry, or the registry could not be read |
| `TRANSPORT_GRANT_UNKNOWN` | `GrantUnknownError` | Configured id has no registry record |
| `TRANSPORT_GRANT_REVOKED` | `GrantConsumedError`, status branch (`grants.py:195`) | Status is not `active` |
| `TRANSPORT_GRANT_EXPIRED` | `GrantExpiredError` | Validity window passed |
| `TRANSPORT_GRANT_EXHAUSTED` | `GrantConsumedError`, budget branch (`grants.py:199`) | `consumed >= budget` |
| `TRANSPORT_GRANT_SCOPE_MISMATCH` | `GrantScopeError` | A binding does not match the job |
| `TRANSPORT_GRANT_OK` | success | Health surface only |

Today "revoked" and "exhausted" raise the same exception class with different text. The
code must come from a discriminator on the exception (for example a `code` attribute set at
the raise site), never from parsing the message.

**Insertion points.**

| File : function | Change |
|---|---|
| `controller/grants.py` : exception classes and `GrantStore.validate_row` | Attach a stable `code` to each `GrantError` at the raise site |
| `controller/controller.py` : `Controller.admit_queued_jobs` | Map each refusal branch to a reason code; write it as `detail.reason_code` beside the existing `detail.reason` (kept for compatibility); emit one journal line on stderr |
| `controller/controller.py` : new helper, for example `Controller.transport_admission_state()` | Pure read: returns `{enabled, grant_id, code, expires_at}` from config plus registry, with no side effects |
| `controller/health.py` : `run_health` | New check `transport_admission` built from the same pure read. `ok` is `True` only for `TRANSPORT_GRANT_OK`, `None` when transport is disabled by configuration, `False` otherwise |
| `controller/cli.py` : `cmd_health`, `cmd_status` | Open the registry read-only and pass it in; `status --json` gains a `transport_admission` object |
| `docs/OPERATIONS.md`, `docs/RECOVERY.md`, `docs/ATLAS-INTERFACE.md` | Document the codes and the triage step |

Journal line shape (single line, stderr, captured by systemd):

```text
atlas-runner admission_refused reason_code=TRANSPORT_GRANT_EXPIRED grant_id=<id> task_id=gh-<run>-<attempt>-<job>
```

It is emitted on a **change** of reason code and then at a bounded repeat interval, not on
every poll, so the journal stays readable during a long refusal. That rate limit is local
to the journal line; audit de-duplication is F-RUNNER-6.

One design point needs care: whether an invalid transport grant makes `health` exit `2`
(blocked) or `1` (degraded). `deploy-release.sh` uses the health command as a deploy gate
(`scripts/atlas-runner-health.sh`), so exit `2` on an expired grant would make a deploy
roll back for a reason unrelated to the release. The proposal is `degraded` (exit `1`),
treated like `github_api` in the verdict (`health.py:147-162`), with the exact handling
confirmed during implementation review.

**Acceptance tests** (in `infra/atlas-runner/tests/`, new module
`test_transport_grant_observability.py`):

- `test_expired_transport_grant_refuses_with_reason_code_expired`
- `test_unknown_transport_grant_refuses_with_reason_code_unknown`
- `test_revoked_transport_grant_refuses_with_reason_code_revoked`
- `test_exhausted_transport_grant_refuses_with_reason_code_exhausted`
- `test_repository_mismatch_refuses_with_reason_code_scope_mismatch`
- `test_unconfigured_transport_grant_refuses_with_reason_code_not_configured`
- `test_registry_error_is_not_reported_as_authority_refusal`
- `test_refusal_still_fails_closed_no_task_no_execution_no_worker`
- `test_refusal_emits_one_journal_line_per_reason_change`
- `test_health_reports_transport_admission_expired_and_is_not_healthy`
- `test_health_transport_admission_ok_when_grant_valid`
- `test_health_transport_admission_neutral_when_transport_disabled`
- `test_status_json_includes_transport_admission`
- `test_refusal_surfaces_contain_no_scope_json_and_no_token_values`
- `test_reason_code_derives_from_exception_code_not_message_text`

**Allowed paths.** `infra/atlas-runner/controller/controller.py`, `grants.py`, `health.py`,
`cli.py`; `infra/atlas-runner/tests/`; `infra/atlas-runner/docs/OPERATIONS.md`,
`RECOVERY.md`, `ATLAS-INTERFACE.md`.

**Non-goals.** No change to which jobs are admitted. No change to grant schema, validity,
consumption or scope. No automatic renewal, extension or replacement of a grant. No new
network surface. No change to `merge_gate.py`. No audit de-duplication (F-RUNNER-6).

### 4.2 F-RUNNER-4 — `TRANSPORT_GRANT_PRE_EXPIRY_WARNING`

**Outcome.** While the grant is still valid but expires within a configured window, the
`transport_admission` health check reports the remaining time and the verdict becomes
`degraded`; the journal carries one `TRANSPORT_GRANT_EXPIRING` line per threshold crossing.
Admission is unchanged until actual expiry.

**Insertion points.** The pure read from F-RUNNER-1 already returns `expires_at`;
`health.py:run_health` compares it with `now`. One config key (for example
`transport_grant_warn_seconds`) in `config.py` and `controller-config.schema.json`.

**Acceptance tests.** `test_health_degraded_when_transport_grant_expires_within_window`,
`test_health_healthy_when_transport_grant_expiry_is_far`,
`test_grant_without_expiry_reports_no_expiry_and_no_warning`,
`test_pre_expiry_warning_does_not_block_admission`.

**Rationale for rank 2.** It converts the failure from "discovered by a stalled execution"
to "visible hours earlier on the surface operators already poll", at very low cost once
F-RUNNER-1 exists.

### 4.3 F-RUNNER-2 — `TRANSPORT_GRANT_SCOPE_BINDING` (design only)

> **Authority semantics. Not to be implemented without an owner decision.** This section is
> a design comparison. It defines no grant, issues nothing and changes no admission rule.

**Current schema versus a narrower binding.**

| Binding | In grant schema today (`grants.py:51-65`) | Enforced on the transport path today (`controller.py:190`) | Available to the controller at admission |
|---|---|---|---|
| Repository | Column `repository` | **Yes**, if the column is set. A `NULL` column matches any repository | Yes (config) |
| `task_id` | `scope_json.task_id` | No. If set, validation always fails because the call passes `None` | No. The queued job carries no Atlas task id; the local id is `gh-<run>-<attempt>-<job>` |
| `execution_id` | Not in schema (only `scope_json.execution_hash`) | No | No |
| Workflow | Not in schema | No. Admission is by label subset only (`github.py:170-190`) | Partly: `job_name` is captured; workflow path and name are not (`github.py:80-85`) |
| Base revision | Column `base_revision` | No. If set, validation always fails | No. Run `head_sha` is not captured |
| Work seal / inputs digest | `scope_json.execution_hash` exists for the `submit` path | No | No. `workflow_dispatch` inputs are not read by the controller |
| Executor type | Column `executor_type` | No. If set, validation always fails | No |
| Dispatch / admission budget | Columns `budget`, `consumed` | Checked, **never consumed** on the transport path | Yes |
| Expiry | Column `expires_at` | **Yes** | Yes |

So today a transport grant can express exactly: *this repository, until this time*. Every
other binding the schema offers either is ignored or makes the grant unusable for
transport. The relayed description of the temporary grant ("repository-wide for queued
executor jobs during its validity window") is the only shape the current code accepts.

The `submit` path is already narrow: `admit_atlas_task` validates with
`require_complete_bindings=True` against task, repository, base revision, executor type,
action type and execution hash, and consumes budget in the same transaction
(`state.py:284-300`). The asymmetry is between the two admission paths, not a missing
capability in the registry.

**Design sketch.** A transport grant gains an explicit, versioned binding set evaluated
against facts the controller observes from GitHub for the queued job:

| Binding | Source of the observed value | Match rule |
|---|---|---|
| `repository` | Config | Exact, required |
| `workflow` | Run `path` (workflow file) from the Actions API | Exact, from an allow-list |
| `run_id` or `execution_id` | Run id; or a `run-name` carrying the execution id (see frontier item F3) | Exact when bound |
| `task_id` | A dispatch input or `run-name` convention | Exact when bound |
| `base_revision` | Run `head_sha` | Exact when bound |
| `inputs_digest` / work seal | Digest of the dispatch inputs, computed by the dispatcher and by the controller from the same canonical form | Exact when bound |
| `admission_budget` | Registry `budget` / `consumed` | Consumed once per admitted `(run_id, run_attempt, job_id)`, in the same transaction that records the task, with the existing exactly-once refund on start failure |
| `expires_at` | Registry | As today |

Two shapes then coexist: a **standing** grant (repository + workflow allow-list + expiry,
unconsumed, as today but explicit) and a **bound** grant (one execution, budget 1). The
grant record would declare which shape it is, so that an unbound field is a deliberate
"any" and not an accident of a `NULL` column.

**Owner decisions required before implementation.**

| # | Decision |
|---|---|
| R1 | Is a repository-wide **standing** transport grant an accepted steady state, or should each queued execution require a **bound** grant? |
| R2 | If both exist: which workflows may run under a standing grant (`atlas-agent-execute`, acceptance, smoke, verify), and which require a bound grant? |
| R3 | Who issues transport grants, from which host, and how is issuance verifiable? This must line up with open decision D1/D5 in the [authority-compatibility note](./2026-10-02-AUTHORITY-COMPATIBILITY.md) (verifiable issuance, key held off every agent host) |
| R4 | Does a transport admission **consume** budget? If yes: per job, per run, or per run attempt, and does a GitHub re-run need a new grant? |
| R5 | Which identity binds a queued run to its sealed WorkItem: `run-name`, a dispatch input, or an inputs digest? Is the dispatcher trusted to state it, or must the controller recompute it? |
| R6 | Is `base_revision` bound to the run's `head_sha`, to the sealed package's base, or both? |
| R7 | Maximum validity window and whether a transport grant may ever be issued without `expires_at` (today `NULL` means "never expires", `grants.py:197`) |
| R8 | Relationship to `ONE_WORKFLOW_DISPATCH_GRANT` in the DEVQ contracts: are dispatch authority and transport admission one grant with two checks, or two grants (§6)? |
| R9 | Migration: how the current standing grant is retired when bound grants are introduced, and what the controller does with a grant record of the old shape |

**Risk if left.** For the whole validity window, any queued job in the repository whose
labels are a subset of the runner label set is admitted. The remaining controls are who can
dispatch workflows in the repository (GitHub permission) and capacity
(`max_concurrent_jobs`). The transport grant itself adds a time bound and nothing else.

**Non-goals.** No change to GitHub token scopes. No change to the `submit` path. No change
to merge authority or to `merge_gate.py`.

### 4.4 F-RUNNER-5 — `TRANSPORT_GRANT_OPERATOR_PATH`

**Outcome.** A documented, audited operator path for the grant registry: `grant list`
(ids, bindings, status, expiry, consumed/budget; never more), `grant issue`, `grant revoke`,
each writing an audit row; and a defined way for a running controller to pick up a rotated
transport grant.

**Options for rotation without restart** (owner decision, because each changes how
authority reaches a running process):

- (a) keep restart-required, and document it as the contract;
- (b) re-read `transport_grant_id` from the config file on `SIGHUP`, with the reload audited
  and journaled;
- (c) resolve the transport grant by a stable **role** (for example the single active grant
  marked as the repository's transport grant) instead of a config-pinned id.

Option (c) moves authority selection from a root-owned config file into the database and is
the widest change. Option (a) costs nothing and is safe once F-RUNNER-1 and F-RUNNER-4 make
expiry visible.

**Risk if left.** `INFERRED`: each rotation is a manual database write plus a config edit
plus a restart, with no runbook. The restart itself is safe for running work only because
startup reconcile repairs it; a restart during an active execution turns that execution
into `worker_lost` or a refunded stuck admission.

### 4.5 F-RUNNER-6 — `REFUSAL_AUDIT_DEDUPLICATION`

**Outcome.** A refusal writes an audit row when the `(task_id, reason_code)` pair first
appears or changes, plus a bounded periodic "still refused" row with a count. The same
applies to `transport_grant_validated`, which is also written on every poll for every
matching queued job, including jobs later suppressed as duplicates (`controller.py:212-217`
runs before the duplicate check at `controller.py:218-228`).

**Insertion point.** `controller/controller.py:Controller.admit_queued_jobs`, with a small
in-memory last-state map; no schema change.

**Acceptance tests.** `test_repeated_refusal_writes_one_audit_row_per_state_change`,
`test_still_refused_summary_row_carries_count`,
`test_refusal_audit_resumes_after_reason_changes`.

### 4.6 F-RUNNER-7 — `ADMISSION_ERROR_CLASSIFICATION`

**Outcome.** `except Exception` at `controller.py:191` is narrowed: `GrantError` subclasses
are authority refusals; anything else is an infrastructure fault with its own code
(`TRANSPORT_GRANT_REGISTRY_UNAVAILABLE`) and is still fail closed. The swallowed
`GitHubError` at `controller.py:143-144` produces an audit row and a rate-limited journal
line.

**Acceptance tests.** `test_sqlite_error_during_grant_validation_is_infrastructure_fault`,
`test_github_poll_failure_is_audited_and_journaled`,
`test_infrastructure_fault_still_admits_nothing`.

## 5. The temporary grant

| Item | Value | Label |
|---|---|---|
| Grant id | `grant-github-transport-e2-20261002` | RELAYED; `UNKNOWN` by direct observation |
| Reported expiry | `2026-10-03T15:00:00Z` | RELAYED; `UNKNOWN` |
| Reported scope | Repository-wide for queued executor jobs during its validity window | RELAYED; `UNKNOWN`. Consistent with §4.3: it is the only shape the transport path accepts |
| Actual registry row (bindings, `status`, `budget`, `consumed`, `expires_at`) | Not read | `UNKNOWN` |
| Whether VPS2 `transport_grant_id` currently names this grant | Not read | `UNKNOWN` |
| Whether the controller is running, and on which release | Not read | `UNKNOWN` |

**This session could not read VPS2 state.** It had no access to the host, its journal, its
state database or its configuration. Nothing in this record is a direct observation of the
grant or of the controller on VPS2.

**Remaining exposure window.** As of writing (2026-10-03T07:36Z), if the reported expiry is
accurate, the grant remains valid for about **7 hours 24 minutes**. During that window, by
the code at this identity, every queued job in the repository whose labels are a subset of
the runner label set is admissible (§4.3 "Risk if left"). `INFERRED` from code plus relayed
values.

**Constraints on the temporary grant.**

- It **must not be extended**. The registry does not allow it by id in any case
  (`grants.py:109-120`); extension by direct database edit would bypass that immutability.
- It **must not be replaced** by another standing grant as a convenience. A further grant is
  a new owner authority decision (§4.3, R1 and R7).
- It **must not be consumed for unrelated jobs**. It was issued for infrastructure recovery
  of one queued execution. No workflow should be dispatched to take advantage of the open
  window.
- After expiry the controller will again refuse every queued job, silently, exactly as on
  2026-10-02, until F-RUNNER-1 ships. Anyone dispatching after `15:00:00Z` should expect a
  job that stays `queued` with a `healthy` controller.

**Cleanup is an owner / operations action.** After expiry, `transport_grant_id` on VPS2
still names an expired grant. Removing or replacing that config value, deciding whether
`queued_transport_enabled` stays `true`, restarting or reloading the controller, and
optionally marking the grant `revoked` in the registry are host operations that require
VPS2 access and an authority decision. They are outside this record and outside agent
authority. Until done, the state is fail closed, which is the safe direction.

## 6. Authority layers are distinct

Four separate things were involved. Holding one never implies another. Collapsing them is
how an infrastructure recovery could be mistaken for a new authorization.

| Layer | What it authorizes | Where it lives | Issued or held by | What it does **not** authorize |
|---|---|---|---|---|
| **Task / execution dispatch authority** | That a specific sealed WorkItem may be executed: one workflow dispatch for one package | DEVQ contracts (`grant_required: ONE_WORKFLOW_DISPATCH_GRANT`), owner mission directives, `GOVERNANCE.md` lifecycle | Owner | Admission on the VPS; any second dispatch or repair attempt; merge |
| **VPS transport admission authority** | That the runner fabric may pick up queued GitHub jobs for this repository at all | `transport_grant_id` + `queued_transport_enabled` in the controller config; the `grants` table on the runner host | Owner / operations on the host | Which task runs; how many attempts; what the job may write; merge |
| **GitHub token capability** | What the API technically permits a credential to do: read queued runs, mint a JIT runner config, push a dedicated branch | The controller's token in its environment file; the workflow's `GITHUB_TOKEN` and its `permissions:` block | GitHub App / token configuration | Anything. Capability is not permission: a token that *can* dispatch or push is not thereby *allowed* to |
| **Classifier permission** | What an agent session's harness lets that session do with its tools (run a command, call an API, push) | The agent host's permission system | The session's user | Any authority in the three layers above. A permitted tool call is not an owner grant |

Applied to this incident:

- Dispatch authority for the `ATLAS-DEVQ-0002` E2 run was already spent on the original
  dispatch. The recovery spent **none**: the same queued run executed (RELAYED), and the
  code confirms that a refused job is re-evaluated without a new dispatch (§2.2).
- The replacement grant restored **transport admission** only. It is not a new task
  authorization, not a retry budget, and not evidence that the execution was correct.
  `EXECUTOR_SUCCESS != VERIFIED` still applies to whatever the run produced.
- The expired grant did not reduce any **token capability**. The controller could still
  reach the API (`github_api` would stay `ok`). That is precisely why health stayed green.
- No **classifier permission** in any agent session can substitute for either grant, and
  none was used to do so in producing this record: this session read code, wrote this
  document and corrected one comment.

## 7. Frontier

| Item | State | Held by |
|---|---|---|
| Expiry refusal invisible in journal and health | Confirmed in code; F-RUNNER-1 `PLANNED` | Next authorized WorkItem (agent-doable) |
| Reconcile comment drift | Comment corrected; four further stale statements and the periodic/startup choice open | F-RUNNER-3 decision |
| Transport grant scope | Design only; R1–R9 open | Owner |
| Temporary grant | `UNKNOWN` by direct observation; reported expiry `2026-10-03T15:00:00Z` | Owner / operations (cleanup after expiry) |
| VPS2 actual state | `UNKNOWN` | Owner / operations |
