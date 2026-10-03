# Atlas Runner Fabric — Recovery (AS-RUNNER-FABRIC-001)

Failure and recovery runbook for the VPS-02 runner fabric. Recovery actions
are executor-side; nothing here is certification (`EXECUTOR_SUCCESS !=
VERIFIED`).

## Controller crash

- **State durability:** the state DB is SQLite in WAL mode
  (`PRAGMA journal_mode=WAL`); every mutator runs in an immediate
  transaction, so a crash mid-write cannot produce a half-written execution
  record.
- **Restart:** systemd restarts the controller (unit restart policy).
  On startup the controller runs reconcile before polling: executions whose
  workers no longer exist in Docker are driven to a terminal state, stale
  containers are removed, and non-terminal work is re-evaluated.
- **Recovery check:**
  `systemctl status atlas-runner-controller.service` then
  `journalctl -u atlas-runner-controller.service --since -10m`.

## Docker daemon failure

- `health` reports exit 2 (blocked) with `docker` check detail; the
  controller keeps polling GitHub but admits nothing.
- Jobs already queued in GitHub stay queued until their own workflow/job
  timeout — no work is lost silently on the fabric side.
- When the daemon returns, the next health cycle unblocks admission; no
  manual re-queue is needed. Re-run `atlas-runner reconcile` if any worker
  was mid-flight during the outage.

## Stale runner / worker cleanup

1. `atlas-runner list-workers` — identify stale entries.
2. `atlas-runner reconcile` — automated cleanup first.
3. Manual fallback: `docker ps -a --filter name=atlas-worker`, remove stale
   containers (`docker rm -f <id>`), then `reconcile` again.
4. Stale **GitHub-side runner registrations** (e.g. after a hard host
   failure): remove via the repository's Actions settings or
   `gh api -X DELETE /repos/<owner>/<repo>/actions/runners/<id>` using the
   PAT. Ephemeral registration means strays should be rare — one
   registration per worker, auto-removed on deregistration.

## CLEANUP_REQUIRED executions

`CLEANUP_REQUIRED` means deregistration or destruction did not complete.
Follow the stale-cleanup procedure above, then inspect the journal for the
root cause before admitting new work at scale.

## GitHub outage

- Poll errors surface as `GitHubError` in the journal and a degraded health
  state. The controller retries on its fixed poll interval; queued runs
  remain in GitHub and are re-evaluated after service restoration.
- No new behavior is implemented for backoff in v1: the documented behavior
  is fixed-interval retry plus operator-visible degradation. A bounded
  exponential backoff is a candidate follow-up if outage noise becomes real
  (measure first).

## Transport grant expired or invalid (queued jobs stay queued)

Symptom: GitHub jobs stay `queued`, `health` exits 0, no worker starts.

1. `atlas-runner health` — read `checks.transport_admission.code` and
   `advisories`. `atlas-runner status` shows the same on one line.
2. `journalctl -u atlas-runner-controller.service | grep transport_admission`
   — when the state changed and the reason code.
3. Interpret the code with the table in `docs/OPERATIONS.md` ("Transport
   admission"). `TRANSPORT_GRANT_REGISTRY_UNAVAILABLE` is a state-database
   problem, not an authority problem: fix the database access first.
   `TRANSPORT_GRANT_EXPIRING` is a warning; jobs are still admitted.

The controller stays fail closed in every one of these states, and a refused
job leaves no task or execution row: once a valid grant is in force the same
queued job is admitted with no new dispatch. Restoring admission (a grant
under a new id, the `transport_grant_id` config value, the restart that makes
the controller read it) is an **owner / operations authority decision**, not a
recovery step an agent or this runbook may take on its own; there is no
automatic renewal. Background and open decisions:
`docs/global/baseline/2026-10-03-RUNNER-AUTHORITY-INCIDENT.md`.

## Partial release / mixed versions

- Activation is atomic: `deploy-release.sh` swaps the `current` symlink in
  one `mv -T` after staging and host-side tests pass, so the running
  controller always points at exactly one release. There is no window in
  which a mixed-version controller is active.
- A release directory is immutable once staged (`git archive` of an exact
  revision); re-deploying the same revision is a no-op swap.

## Rollback runbook

See DEPLOYMENT.md. Summary: deploy-release.sh auto-rolls-back on failed
health; manual rollback restores the `current` symlink to the last
known-good release and restarts the unit, then verifies `health` exits 0.

## Reboot considerations

- VPS-02 reboot: systemd brings the controller back (unit enabled);
  reconcile runs before the first poll. Docker must be running first —
  if the unit starts before Docker, the controller degrades (health 1)
  and self-recovers once Docker is up; no operator action needed beyond
  watching `journalctl`.
- Evidence and state survive reboot (persistent directories). In-flight
  jobs do not survive a worker container stop; their GitHub runs fail and
  can be re-dispatched.
