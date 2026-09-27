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
| `atlas-runner status [--json]` | Controller status: admitted/active executions, last poll, capacity. |
| `atlas-runner health` | Health report JSON; exit 0 healthy / 1 degraded / 2 blocked. Deploy gate fails on blocked. |
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

1. `atlas-runner health` — exit 2/blocked explains itself.
2. Check job labels are exactly a subset of
   `[self-hosted, linux, x64, atlas, executor]` (GitHub matches all labels).
3. Check capacity: `status --json` — `capacity:max_concurrent_jobs` means a
   worker is already active (`max_concurrent_jobs = 1`).
4. Check the token: GitHub API errors appear in journalctl as `GitHubError`.

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
