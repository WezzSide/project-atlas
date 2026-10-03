# Atlas Runner Fabric — Operations (AS-RUNNER-FABRIC-001)

Operator guide for the VPS-02 runner fabric. All commands assume the
deployed wrapper (`/opt/atlas-runner/current/bin/atlas-runner`) or a dev
checkout with `PYTHONPATH=src`. Exit codes: 0 ok, 1 operational failure,
2 usage/config error. HEALTH != VERIFIED; these commands report executor
state, never certification.

## Command reference

| Command | Purpose |
| --- | --- |
| `atlas-runner run` | Daemon poll loop (graceful SIGTERM). This is what systemd runs. |
| `atlas-runner once` | Single poll/admission cycle (cron/systemd timer fallback). |
| `atlas-runner submit <task.json>` | Submit a local task definition (validated against the worker-task schema). |
| `atlas-runner status [--json]` | Controller status: `transport_admission` state, active workers and executions. |
| `atlas-runner health` | Health report JSON; exit 0 healthy / 1 degraded / 2 blocked. The deploy gate (`deploy-release.sh`) rolls back on ANY non-zero exit. Includes the informational `transport_admission` check and `advisories`. |
| `atlas-runner version` | Controller version + release identity. |
| `atlas-runner reconcile` | Crash recovery / stale cleanup: state DB vs Docker reality. |
| `atlas-runner list-workers` | Active worker containers with lifecycle states. |
| `atlas-runner show <execution_id>` | One execution record incl. evidence pointer. |

## Logs

```bash
journalctl -u atlas-runner-controller.service -f          # follow
journalctl -u atlas-runner-controller.service --since -1h # recent window
journalctl -u atlas-runner-controller.service -p err      # errors only
```

Controller logs: `/var/log/atlas-runner/`. Per-job worker stdout/stderr:
`docker logs <worker_container>` while it exists, plus
`/var/lib/atlas-runner/jobs/<execution_id>/workspace/worker.log` (retention:
24 h workspace, 30 d evidence — `[retention]` config).

## Common diagnostics

**Queued job never picked up**

1. `atlas-runner health` — exit 2/blocked explains itself. Then read
   `checks.transport_admission` and `advisories` even when the exit code is 0:
   an invalid transport grant refuses every queued job while the controller
   is otherwise healthy (see "Transport admission" below).
2. `journalctl -u atlas-runner-controller.service | grep -E
   'admission_refused|transport_admission'` — the refusal reason code.
3. Check job labels are exactly a subset of
   `[self-hosted, linux, x64, atlas, executor]` (GitHub matches all labels).
4. Check capacity: `status --json` — `capacity:max_concurrent_jobs` means a
   worker is already active (`max_concurrent_jobs = 1`).
5. Check the token: GitHub API errors appear in journalctl as `GitHubError`.

**Worker stuck**

1. `atlas-runner list-workers` — find the worker and its lifecycle state.
2. `atlas-runner reconcile` — forces state-DB vs Docker reconciliation and
   cleans stale containers.
3. If the state is `CLEANUP_REQUIRED`, deregistration or destruction failed:
   remove the stale container manually (`docker rm -f <id>`), then
   `reconcile` again. Investigate the journal for the underlying cause.

**Evidence lookup**

- By execution id: `atlas-runner show <execution_id>` prints the record and
  the evidence path
  (`/var/lib/atlas-runner/jobs/<execution_id>/evidence.json`).
- By GitHub run id: `atlas-runner status --json` lists executions with
  `github_run_id`; match and then `show`.

**Health states**

- 0 healthy: docker reachable, state DB heartbeating, dirs writable, config
  valid.
- 1 degraded: a non-fatal check failed (e.g. GitHub API unreachable) — the
  controller keeps running but admission may stall.
- 2 blocked: a fatal check failed (docker daemon down, state dir not
  writable). Jobs stay queued in GitHub until their own timeout; the
  controller re-evaluates after the daemon returns.

## Transport admission (reason codes, journal, health, status)

Specification and incident background:
`docs/global/baseline/2026-10-03-RUNNER-AUTHORITY-INCIDENT.md` §4.1 (F-RUNNER-1)
and §4.2 (F-RUNNER-4). OBSERVABILITY != AUTHORITY: these surfaces describe a
refusal; they never issue, renew, extend or widen a grant, and admission is
exactly as fail-closed as before.

**Reason codes** (stable, non-secret):

| Code | Meaning | `state` |
| --- | --- | --- |
| `TRANSPORT_GRANT_OK` | Configured grant is valid for this repository | `ok` |
| `TRANSPORT_DISABLED` | `queued_transport_enabled = false` (by design, not an error) | `disabled` |
| `TRANSPORT_GRANT_NOT_CONFIGURED` | Transport enabled but `transport_grant_id` unset | `refusing` |
| `TRANSPORT_GRANT_UNKNOWN` | Configured id has no registry record | `refusing` |
| `TRANSPORT_GRANT_REVOKED` | Grant status is not `active` | `refusing` |
| `TRANSPORT_GRANT_EXPIRED` | Validity window passed | `refusing` |
| `TRANSPORT_GRANT_EXHAUSTED` | `consumed >= budget` | `refusing` |
| `TRANSPORT_GRANT_SCOPE_MISMATCH` | A grant binding does not match (e.g. repository) | `refusing` |
| `TRANSPORT_GRANT_INVALID` | Defensive fallback for an unclassified grant error (not expected) | `refusing` |
| `TRANSPORT_GRANT_REGISTRY_UNAVAILABLE` | No registry, or it could not be read (infrastructure fault, NOT an authority refusal). Admission still fails closed; whether the grant is valid is **not known** | `unknown` |
| `TRANSPORT_GRANT_EXPIRING` | Warning only: grant valid but expires within `transport_grant_warn_seconds` (default 86400; `0` disables). Nothing is blocked early | `ok` + `warning` |

**Journal** (stderr, captured by systemd). Single lines, only `key=value`
fields; never grant contents, exception text or secrets:

```text
atlas-runner admission_refused reason_code=TRANSPORT_GRANT_EXPIRED grant_id=<id> task_id=gh-<run>-<attempt>-<job>
atlas-runner transport_admission state=refusing reason_code=TRANSPORT_GRANT_EXPIRED grant_id=<id> expires_at=<UTC>
atlas-runner transport_admission state=expiring reason_code=TRANSPORT_GRANT_EXPIRING grant_id=<id> expires_at=<UTC> remaining_seconds=<n>
atlas-runner transport_admission state=ok reason_code=TRANSPORT_GRANT_OK grant_id=<id>
```

- `admission_refused` is written when a queued job is refused;
  `transport_admission` is written from every poll, so an expired or
  soon-to-expire grant is visible even when nothing is queued.
- Rate limit: a line is written when its state changes (reason code or grant
  id), and otherwise at most once per 15 minutes (`expiring`: once per hour;
  `ok`: once, only after a non-ok state). Suppressed lines are counted in
  `repeats=<n>` on the next line. The limiter is in-memory: a restart, or each
  `atlas-runner once` invocation, logs again.
- The journal rate limit does not apply to the audit log: `audit_log` still
  gets one `blocked_authority` row per refused job per poll, now with
  `detail.reason_code` (and `detail.grant_id`) beside the unchanged
  `detail.reason`.

**Health.** `checks.transport_admission` carries `ok` (`true` only for
`TRANSPORT_GRANT_OK`, `null` when transport is disabled, `false` otherwise,
including `unknown`), `state`, `code`, `grant_id`, `expires_at`,
`expires_in_seconds`, `warning`. The top-level `advisories` list repeats the
code, e.g. `["transport_admission:TRANSPORT_GRANT_EXPIRED"]`.

This check is **informational**: it never changes `status` or the exit code.
`deploy-release.sh` rolls back on any non-zero health exit and the trusted
deploy workflow fails its post-deploy step the same way, so letting an expired
grant turn health `degraded` would roll back deploys unrelated to it. A
`healthy` / exit 0 report with a non-empty `advisories` list therefore means
"host is fine, queued jobs are being refused (or soon will be)". Whether
transport state should ever affect the exit code is an open owner decision
(it would need the deploy gate to distinguish exit 1 from exit 2 first).

**Status.** `atlas-runner status --json` has a `transport_admission` object
(`enabled`, `grant_id`, `code`, `state`, `expires_at`, `expires_in_seconds`,
`warning`); the text form prints one `transport_admission:` line.
`fabric-state` is unchanged (it describes workers, not authority).

`health` and `status` open the grant registry read-only; they cannot create,
consume or revoke a grant.

## Capacity tuning

Defaults below are placeholders PENDING preflight on real VPS-02
resources (preflight is `NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY` — VPS-02 was
unreachable from the build network at documentation time). Re-measure before
raising any limit.

| Knob (config key) | Default | Rationale (placeholder) |
| --- | --- | --- |
| `max_concurrent_jobs` | 1 | VPS-02 is a small shared host; one worker at a time bounds memory and blast radius. Raise only after measured headroom. |
| `worker.cpus` | 2.0 | Sufficient for controller tests + smoke workload. |
| `worker.memory_mb` | 4096 | Runner + toolchain baseline; leave `min_free_memory_mb` (1024) headroom. |
| `worker.pids` | 512 | Runner spawns many short-lived helpers; 512 prevents fork storms. |
| `worker.timeout_seconds` | 1800 (30 min) | One job, bounded; aligns with workflow job timeouts (15–20 min) plus margin. |
| `worker.storage_mb` | 10240 | Workspace + apt/pip caches for one job. |
| `poll_interval_seconds` | 10 | Fast enough admission without hammering the API. |
| `min_free_disk_mb` | 5120 | Protects evidence retention and image layers. |

Edit `/etc/atlas-runner/config/atlas-runner.toml` (mode 0600), then
`systemctl restart atlas-runner-controller.service`. Config errors fail
closed: the service refuses to start on an invalid config.
