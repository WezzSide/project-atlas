# VPS Fleet State Baseline and Drift — 2026-10-06

| Field | Value |
|---|---|
| Token | `FLEET_STATE_BASELINE_RECORDED` |
| Repository identity | `main` `9b7bc8f3cffa1ba880b24e07d730dfe3adfb2867`, TREE `70f3baa3b0dc678e2713d241f4cdbdc47de4478a` |
| Observed window | 2026-10-06, about 09:31Z to 09:42Z |
| Observer | One agent session with read-only access to each host |
| Method | Read-only probes. No privilege escalation, no service action, no package change, no deployment, no dispatch, nothing written on any host |
| Structured record | [evidence/fleet-observation-2026-10-06.public.json](./evidence/fleet-observation-2026-10-06.public.json) |
| Companions | [2026-10-02-AUTONOMY-FRONTIER.md](./2026-10-02-AUTONOMY-FRONTIER.md) §4, [2026-10-03-RUNNER-AUTHORITY-INCIDENT.md](./2026-10-03-RUNNER-AUTHORITY-INCIDENT.md) |
| Evidence vocabulary | [Operating contract §4](../ATLAS-GLOBAL-OPERATING-CONTRACT.md). This record uses `OBSERVED` / `INFERRED` / `UNKNOWN` / `PLANNED` and claims nothing as `PROVEN` |
| Status | Record only. It grants nothing, decides nothing and changes no runtime behaviour. `ATLAS_VPS_FLEET_AUTONOMOUS` is **not asserted** |

## 0. How to read this record

This is a **public projection** of a fuller observation that is kept off this repository.
Host addresses, access details, account names, port numbers, host file layout, exact
capacity figures, internal release numbers and failure counters are deliberately left out.
The projection reduces detail; it does not round any unhealthy state upward.

- `OBSERVED`: read by this session from a host or from the repository in the window above.
  A point-in-time reading, not a standing fact.
- `INFERRED`: follows from observations but was not itself read.
- `UNKNOWN`: not observed. It never means "absent" or "false".
- `PLANNED`: a gap or next step. Nothing in this record performs one.

Four rules apply to every row:

1. A release that exists on a host is not a release that should be active.
2. A component that is installed but inactive is a state, not a request to activate it.
3. A running service is not a healthy service. Liveness and progress are reported separately.
4. A reachable host is not a member of an execution fabric.

Not read at all: secrets, tokens, environment values, authority entries, journals, container
state, database and queue contents, and any state that needs elevated access.

## 1. Summary

| Question | Answer | Label |
|---|---|---|
| Are the three hosts reachable and running? | Yes, with no failed system units | `OBSERVED` |
| Do live roles match the intended mapping? | Yes: VPS1 forge / execution, VPS2 verifier and runner, VPS3 control plane | `OBSERVED` |
| Is each host supervised? | Each runs its own mission supervisor with its own mission | `OBSERVED` |
| Are the supervisors progressing? | VPS3 progressing. VPS2 degraded: its cycles have been failing for several days. VPS1 `UNKNOWN` | `OBSERVED` / `UNKNOWN` |
| Is the repository's DEVQ coordinator deployed? | Not found on any host | `OBSERVED` at the locations probed; "not deployed" is `INFERRED` |
| Can the running fleet components be tied to `main`? | Only the runner controller. The others have no source in this repository | `OBSERVED` |
| Does the fleet act as one execution fabric? | Nothing observed shows it. Three independently supervised hosts were observed | `OBSERVED` |
## 2. Per-host state

State words used below: **active** (running), **inactive** (installed, not running),
**disabled** (installed, not set to start), **degraded** (running but not completing its
work), **not found**, **unknown**.

### 2.1 VPS1 — forge / execution (`ATLAS-FORGE-01`)

| Component | State | Label |
|---|---|---|
| Mission supervisor | active; progress `UNKNOWN` (its state is not readable with the access used) | `OBSERVED` / `UNKNOWN` |
| Forge worker | active | `OBSERVED` |
| Task executor | installed, started on demand, idle in the window | `OBSERVED` |
| Execution broker | disabled | `OBSERVED` |
| Facts export (scheduled) | scheduled, idle in the window | `OBSERVED` |
| DEVQ coordinator, planner or spool from this repository | not found | `OBSERVED` |
| Agent backends available to the observer | one | `OBSERVED`; for the service identities `UNKNOWN` |
| Capacity | healthy headroom | `OBSERVED` |

The supervisor on VPS1 is a different build from the one on VPS2 and VPS3. `OBSERVED`.
Which is newer or intended is `UNKNOWN`.

### 2.2 VPS2 — verifier and runner (`ATLAS-EU-VERIFY-01`)

| Component | State | Label |
|---|---|---|
| Mission supervisor | **degraded**: running, but its cycles have failed repeatedly since 2026-09-30 and it reports an executor-failure backoff | `OBSERVED` |
| Verifier service | active, on an older release than the newest one present on the host | `OBSERVED` |
| Runner controller | active, but it has restarted repeatedly; most recently on 2026-10-06 | `OBSERVED`; cause `UNKNOWN` |
| Runner host guards (container and network policy) | applied | `OBSERVED` |
| DEVQ coordinator, planner or spool from this repository | not found | `OBSERVED` |
| Agent backends available to the observer | two launch; three others are installed but could not launch because a language runtime they need was not available | `OBSERVED` |
| Capacity | **resource-constrained** compared with the other two hosts, with disk pressure that has grown since 2026-09-28 | `OBSERVED`; what grew is `UNKNOWN` |

- Whether the older verifier release is the intended one is `UNKNOWN`. This is a drift
  observation, not an instruction to change it.
- Whether the unavailable runtime has anything to do with the supervisor's failures is
  `UNKNOWN`. Causality was not investigated and is not claimed.
- The runner controller is deployed at `main` revision
  `b2f97ff268f0e995f1f49871944fbcaedc04a514` (merge of #1036, 2026-10-01). `OBSERVED`.
  `main` is 126 commits ahead of it, and two later commits on `main` touch
  `infra/atlas-runner`. `OBSERVED`. That the deployed controller lacks them is `INFERRED`.

### 2.3 VPS3 — control plane (`ATLAS-EU-CONTROL-01`)

| Component | State | Label |
|---|---|---|
| Mission supervisor | active and **progressing**: last successful cycle on 2026-10-06, no current failures | `OBSERVED` |
| Queue service | active | `OBSERVED` |
| Owner gateway | active | `OBSERVED` |
| Control service | disabled, and no release of it was present | `OBSERVED` |
| Database server | active, local only | `OBSERVED` |
| Scheduled backup and health jobs | scheduled | `OBSERVED` |
| DEVQ coordinator, planner or spool from this repository | not found | `OBSERVED` |
| Agent backends available to the observer | five | `OBSERVED` |
| Capacity | healthy headroom; the largest host | `OBSERVED` |

Queue and database contents were not read. `UNKNOWN`.

## 3. Identities

| Kind | VPS1 | VPS2 | VPS3 | Label |
|---|---|---|---|---|
| Node role | `ATLAS-FORGE-01` | `ATLAS-EU-VERIFY-01` | `ATLAS-EU-CONTROL-01` | `OBSERVED` |
| Supervisor runs under a dedicated service identity | yes | no | no | `OBSERVED` |
| Service components run under their own identities, separate from the supervisor | yes | yes | yes | `OBSERVED` |

These are host-level identities and role strings. Whether any of them is an authenticated
executor or verifier identity in the sense the backlog asks for is `UNKNOWN`; nothing
observed establishes it. Whether the supervisor running without a dedicated identity on two
hosts is intended is `UNKNOWN`.
## 4. Drift from the local fleet record

The owner workstation holds a local fleet-state record (schema `atlas-fleet-state/v1`,
revision 34, observed 2026-09-28, sha256
`8efc2cf7a74f9672adb5aac75a357303b62fb222deb0065648a99a4153ec198a`). It is not in this
repository. This baseline does not modify it and does not adopt it as canonical.

| Subject | Local record (2026-09-28) | Live (2026-10-06) | Label |
|---|---|---|---|
| VPS1 role | A worker role in conflict, with the forge role recorded separately as bound | Forge role | `OBSERVED` |
| Mission supervisors | Not mentioned | Active on all three hosts | `OBSERVED` |
| VPS1 on-demand executor and facts export | Not mentioned | Present | `OBSERVED` |
| VPS3 queue service, owner gateway, control service, database | Not mentioned | Queue, gateway and database active; control service disabled | `OBSERVED` |
| VPS2 runner controller revision | Recorded as not observed | A `main` revision of 2026-10-01 | `OBSERVED` |
| VPS2 verifier release | Not mentioned | An older release is active | `OBSERVED` |
| VPS2 disk use | Moderate | Under pressure | `OBSERVED` |

The local record predates the supervisors, which started on 2026-09-30 and 2026-10-01.
`OBSERVED`.

## 5. Drift from repository assumptions

| Repository statement | Live observation | Label |
|---|---|---|
| [`infra/atlas-runner/README.md`](../../../infra/atlas-runner/README.md) gives the VPS-02 state as `NOT_DEPLOYED` | A runner controller release is active on VPS2. The frontier record noted this from deploy runs; this is the first host-side reading | `OBSERVED` |
| The frontier record (§4) describes "one controller on one host" and a "GitHub-hosted verifier" | VPS2 runs a runner controller and a verifier service; VPS1 runs a forge worker; VPS3 runs a queue service and an owner gateway | `OBSERVED` |
| `dev_planner.py` names the planner role as "VPS3 in the current deployment mapping" | VPS3 holds the control role. No planner or coordinator from this repository was found on it | `OBSERVED`; "not deployed" is `INFERRED` |
| [`docs/backlog.md`](../../backlog.md): no entrypoint constructs a coordinator; the fabric adapter is constructed only in tests | Consistent with the hosts | `OBSERVED` |
| Node role strings | Two of the three appear in no repository file | `OBSERVED` |
| Mission supervisor, queue service, owner gateway, verifier service, forge worker, execution broker | No source for them is in this repository. Where it is kept is `UNKNOWN` | `OBSERVED` |

## 6. Implemented in code versus deployed

Repository state and live deployment state are separate claims and are kept in separate
columns.

| Capability | In this repository | On the fleet | Tied to `main`? | Label |
|---|---|---|---|---|
| DEVQ planner, journal, store identity, coordinator tick, spool transport, executor ownership | Implemented; local tests only, no entrypoint | Not found | — | `OBSERVED` / `INFERRED` |
| Runner controller (`infra/atlas-runner`) | Implemented | Active on VPS2, behind `main` | Yes, to an exact revision | `OBSERVED` |
| Per-host mission supervisor | Not present | Active on all three: progressing on VPS3, degraded on VPS2, unknown on VPS1 | No | `OBSERVED` |
| Queue service and owner gateway | Not present | Active on VPS3 | No | `OBSERVED` |
| Verifier service | Not present | Active on VPS2, older release | No | `OBSERVED` |
| Forge worker | Not present | Active on VPS1 | No | `OBSERVED` |
| On-demand executor | Not present | Installed on VPS1, idle in the window | No | `OBSERVED` |
| Control service | Not present | Disabled on VPS3, no release present | No | `OBSERVED` |

Whether the VPS3 queue service and this repository's spool transport are meant to be the
same transport, successive ones, or unrelated is `UNKNOWN`. Nothing observed connects them.

## 7. Gaps before the fleet can act as one execution fabric

Every row is `PLANNED`. None is started by this record. "Owner" marks an authority boundary
in the operating contract (§3).

| # | Gap | Boundary |
|---|---|---|
| 1 | The DEVQ coordinator has no entrypoint and is deployed nowhere; its open activation blockers are tracked in the backlog | Engineering, then owner deployment |
| 2 | Most running fleet components have no source in this repository, so their revisions cannot be bound to a `main` identity | Owner decision on where that source lives |
| 3 | No versioned, published per-node inventory with a publisher identity (frontier §4, first row). This record is unsigned and is an input to that, not a substitute | Engineering, then owner key decision |
| 4 | VPS2's supervisor is degraded. Diagnosis needs access this session did not use | Owner access grant |
| 5 | VPS2's runner controller restarts repeatedly. Cause unread | Owner access grant |
| 6 | VPS2 is the most constrained host and carries the verifier, the runner controller and a supervisor | Owner capacity or placement decision |
| 7 | VPS1's supervisor progress cannot be read without elevated access, so its health cannot be reported | Owner access grant, or a published status surface |
| 8 | The verifier's active release and the newer releases present are unreconciled | Owner deployment decision |
| 9 | The deployed runner controller is behind `main` | Owner deployment decision |
| 10 | No cross-host lease, fencing, placement, reallocation or failover was observed in operation (frontier §4) | Engineering, then live validation under an owner grant |
| 11 | Executor and verifier identities are host-level only; authenticated identities are not established | Engineering, then owner trust-root decision |
| 12 | Liveness, progress and health are not aggregated across hosts; each host had to be read by hand | Engineering |

## 8. What this record does not show

- That any host executed, verified or coordinated a work item in the window.
- The contents of any queue, journal, database, grant or authority file.
- Why VPS2's supervisor or runner controller is failing.
- That the hosts can reach one another; only that the observer reached each.
- Independent verification. It is one reading by one session.
