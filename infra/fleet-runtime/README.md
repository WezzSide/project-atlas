# Fleet runtime source

Canonical, versioned source for three fleet runtime components that previously existed only
as an unversioned bundle on an operator workstation.

| Component | Path | Runs as |
|---|---|---|
| Mission supervisor | `node/atlas_mission_supervisor.py` | Per-node mission loop; one executor cycle at a time |
| Queue client and task-policy engine | `node/atlas_queue_client.py`, `node/atlas_task_policy.py` | Imported by the supervisor; also a node-side CLI |
| Queue service | `control/queue/atlas_queue_service.py` | Control-plane pull/claim task queue |
| Owner gateway | `control/gateway/atlas_owner_gateway.py` | Outbound-only bridge between the owner interface and the queue |

The queue service imports `atlas_task_policy`; a deployment places `node/atlas_task_policy.py`
beside it or names its directory in `ATLAS_LIB_DIR`.

This directory is source only. It deploys nothing, and adding it changes no running service.

## Node contracts

Four contract texts are versioned under [`contracts/`](./contracts/) and pinned by digest;
this repository does not deploy them. At revision `a66f8ec01d632d747d398f0e9ab68e6016a59504`,
`atlas_mission_supervisor.py` reads `GLOBAL-AUTONOMOUS-OPERATING-CONTRACT.md` and
`GLOBAL-GOALS.json` from `${ATLAS_AUTONOMY_ETC}/contracts`, and includes
`GLOBAL-CONTINUOUS-GOALS.json` when present. The supervisor does not read
`AUTONOMY-POLICY.json`. Node availability therefore depends on separate operator
configuration and deployment. Classification and provenance are in
[`contracts/PROVENANCE.md`](./contracts/PROVENANCE.md).

## Provenance

| Claim | Status |
|---|---|
| Where this source came from | The operator's local fleet bundle, baseline `AUTONOMY-STARTER-r3` |
| Is it byte-identical to what currently runs on the fleet? | **No.** The bundle files were; these files were changed before publication (next section) |
| Source identity from now on | This repository at an exact Git revision |
| Deployment identity | Unchanged by this import. A component is tied to a revision of this repository only after a separately authorized deployment that records it |

## Changes made before publication

No logic was changed. Built-in defaults that described one private environment were removed,
so the values must now come from operator configuration:

- every location of a secret-bearing file (queue token, token-digest file, gateway
  configuration);
- the queue listening port;
- install and state locations, including the ones that were written into the supervisor's
  prompt text (they are now derived from the configured state directory).

A missing required value is an explicit startup failure
(`configuration error: <NAME> must be set by operator configuration`). Nothing falls back to
a private default.

Not imported: service unit files, install and deploy scripts, token and gateway
configuration, node environment files, the task-authoring policy document and the original
policy test script. Unit files and scripts are deployment artifacts; the rest is operator
or secret configuration and stays outside Git.

## Configuration contract

Required means the process exits at start without it.

| Variable | Used by | Required | Meaning |
|---|---|---|---|
| `ATLAS_AUTONOMY_ETC` | supervisor | yes | Directory holding the node's role, mission, authority and contract files |
| `ATLAS_AUTONOMY_VAR` | supervisor | yes | Directory for supervisor state, prompts, results, receipts and logs |
| `ATLAS_EXECUTOR` | supervisor | yes | Executor program run once per cycle |
| `ATLAS_WORKSPACE` | supervisor | no | Executor working directory; defaults to `work` under the state directory |
| `ATLAS_LIB_DIR` | supervisor, client | no | Directory holding the queue client and policy engine; defaults to the script's own directory |
| `ATLAS_QUEUE_URL` | client | for queue use | Queue base URL |
| `ATLAS_QUEUE_TOKEN_FILE` | client | for queue use | File holding this node's bearer token |
| `ATLAS_NODE_ENV_FILE` | client | no | Node environment file; defaults to `node.env` under `ATLAS_AUTONOMY_ETC` when that is set |
| `ATLAS_TASK_POLICY_FILE`, `ATLAS_AUTHORING_DECISIONS_DIR` | client (`author`) | for authoring | Policy document and decision-record directory |
| `ATLAS_QUEUE_DB` | queue service | yes | Queue database file |
| `ATLAS_QUEUE_PORT` | queue service | yes | Listening port |
| `ATLAS_QUEUE_TOKENS_FILE` | queue service | yes | Token-digest file (digests only; no plaintext token at rest) |
| `ATLAS_QUEUE_POLICY_FILE` | queue service | yes | Task-authoring policy document |
| `ATLAS_QUEUE_BIND` | queue service | no | Bind address; loopback by default. A wildcard bind is refused; a non-loopback bind needs `ATLAS_QUEUE_NONLOOPBACK_AUTHORIZED=1` |
| `ATLAS_GATEWAY_CONFIG`, `ATLAS_GATEWAY_STATE`, `ATLAS_GATEWAY_LOG` | owner gateway | yes | Gateway configuration (holds its keys and endpoints), state file and event log |

Tuning variables with safe numeric defaults (timeouts, lease lengths, cycle limits) are
unchanged and are documented at their point of use.

## Tying a deployment to a revision

A deployment that wants exact provenance should:

1. deploy from a commit on `main`, not from a working copy;
2. name the release after the full commit and point an "active release" link at it, as the
   runner controller already does;
3. leave operator configuration outside the release.

The read-only fleet observer publishes a revision only when the active release name is a
commit on `main`, so a deployment done this way becomes provable without further work.

## Known limits

- Imported as written: dense single-line style, no type annotations. It is outside the
  repository's lint and type-check scope, like other `infra/` components.
- The supervisor needs a POSIX host (`fcntl`).
- The task-policy engine is tested against a synthetic policy only
  (`tests/unit/test_fleet_runtime_policy_synthetic.py`); the operator policy document was
  not imported, so those tests say nothing about it.
