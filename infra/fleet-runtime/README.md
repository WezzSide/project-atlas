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

The files in `contracts/` are not all used the same way. This document keeps four terms apart:

- **Repository presence**: the file is in Git. That says nothing about runtime use.
- **Runtime read**: the source code opens and parses the file. This does not by itself
  mean that anything is enforced.
- **Prompt context**: the content is placed in the prompt given to the executor. It is an
  instruction to the executor, not a machine-enforced authorization.
- **Enforcement**: code checks a rule and accepts or refuses an action because of it. This
  document uses the word only where the code does that.

What the supervisor (`node/atlas_mission_supervisor.py`) does with each file at this
revision:

| File under `$ATLAS_AUTONOMY_ETC/contracts/` | Runtime read | Prompt context | Enforcement |
|---|---|---|---|
| `GLOBAL-AUTONOMOUS-OPERATING-CONTRACT.md` | Yes, on every loop pass; required (`main`) | Yes, as `<global_contract>` (`prompt_text`) | None. If the read fails, no executor cycle runs on that pass: the supervisor sets its status to `EXECUTOR_FAILURE_BACKOFF` (frontier `SUPERVISOR_ERROR:<type>`) and retries after the tick |
| `GLOBAL-GOALS.json` | Yes, on every loop pass; required (`main`) | Yes, as `<global_goals>` (`prompt_text`) | None. Same failure behaviour as above |
| `GLOBAL-CONTINUOUS-GOALS.json` | Only when present, and only when an executor prompt is built (`continuous_goals`) | Yes, as `<global_continuous_goals>` | None. A missing or unparseable file is silently left out of the prompt |
| `AUTONOMY-POLICY.json` | No | No | None |

`AUTONOMY-POLICY.json` is versioned here as contract and vocabulary documentation. No reader
for it was found in the imported fleet runtime source (`node/`, `control/`) at this revision.
Its presence in the repository does not by itself establish that it is used or enforced.
The executor is an external program that this search does not cover. It inherits the
supervisor's environment, including `ATLAS_AUTONOMY_ETC`, minus `ATLAS_QUEUE_*`.

Each cycle receipt records `contract_sha256` and `goals_sha256`. These are the digests of the
files as they are on disk when the receipt is written, after the executor run. The text
actually used is bound by `prompt_sha256`. The supervisor does not compare any of these
digests with an expected value before use. The digests pinned in
`tests/unit/test_fleet_runtime_contracts.py` prove only that the repository files still
match the recorded bytes. They are not a runtime integrity check.

### Task-authoring policy is a separate mechanism

The task-authoring policy is a different document from `contracts/AUTONOMY-POLICY.json`. It
is operator configuration, was not imported, and is reached by three separate paths:

- **Queue service**: reads `ATLAS_QUEUE_POLICY_FILE`, which must be set at start.
  - An autonomous submission is one whose envelope carries `author_node`. The service
    evaluates it with `atlas_task_policy.evaluate` and refuses any result other than `ALLOW`
    (HTTP 422).
  - It refuses all autonomous submissions (HTTP 503) if the policy file is missing or the
    policy engine cannot be imported.
  - An identity marked `policy_enforced` may submit only autonomous envelopes (HTTP 403
    otherwise).
  - This is the authoritative enforcement point.
- **Queue client (`author`)**: reads `ATLAS_TASK_POLICY_FILE`. It evaluates the task locally
  and records the decision. It submits only on a local `ALLOW`, and the server then
  evaluates the task again.
- **Supervisor**: reads `$ATLAS_AUTONOMY_ETC/task-authoring-policy.json` when present and
  its `author_role` is this node's role. It then adds an authoring section to the prompt
  (`authoring_section`) and uses the file's digest to detect policy changes
  (`policy_state`). It does not evaluate tasks against the policy.

None of these paths reads `AUTONOMY-POLICY.json`.

## Provenance

| Claim | Status |
|---|---|
| Where this source came from | The operator's local fleet bundle, baseline `AUTONOMY-STARTER-r3` |
| Is it byte-identical to what currently runs on the fleet? | **No, by construction.** These files were changed before publication (next section). What currently runs on the fleet is not established by this repository |
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
| `ATLAS_AUTONOMY_ETC` | supervisor | yes | Directory holding the node's role, mission, authority and contract files, and the task-authoring policy when present |
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
  (`tests/unit/test_fleet_runtime_policy_synthetic.py`). The operator policy document was
  not imported. Those tests therefore do not certify the operator policy, and they do not
  establish the live fleet's effective policy configuration. An `ALLOW` in those tests is a
  test result, not an execution grant.
