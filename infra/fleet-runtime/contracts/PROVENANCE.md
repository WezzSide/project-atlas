# Node contract documents: classification and provenance

Four contract documents are versioned in this directory, imported unchanged from the release
copy of the node contracts in the operator's fleet bundle. Their presence in the repository
does not itself deploy them or establish that every node has loaded them. At revision
`a66f8ec01d632d747d398f0e9ab68e6016a59504`, the supervisor reads the operating contract and
goals listed below, and includes the continuous-goals file when present. No runtime reader
for `AUTONOMY-POLICY.json` was found. Node availability depends on separate deployment and
configuration.

Nor does presence here establish that the runtime reads a file or that anything enforces
it. The README section
[Node contracts](../README.md#node-contracts) defines the terms used below.

| File | sha256 | Class | Runtime use at this revision |
|---|---|---|---|
| `GLOBAL-AUTONOMOUS-OPERATING-CONTRACT.md` | `b4e9027006fead151ecd606fe74bccf43bf28fc68d1e3056ed78dfa1d32a5373` | stable node contract | runtime read by the mission supervisor (required), placed in every cycle prompt as prompt context; not enforced by code |
| `GLOBAL-GOALS.json` | `dceb2b376b76fec7ef2049238ce952d44c69aec5dc9b72d83c7975fd2bff49bf` | stable node contract | runtime read by the mission supervisor (required), placed in every cycle prompt as prompt context; not enforced by code |
| `GLOBAL-CONTINUOUS-GOALS.json` | `408fa6862fc0cf7bf6baf2b9c96c0a8ddae6d9ccd150324c321c52094cff9db8` | owner-issued contract, dated 2026-09-29 | runtime read by the mission supervisor only when present, placed in every cycle prompt as prompt context; not enforced by code |
| `AUTONOMY-POLICY.json` | `b4ab61e448cd81aa77ca3d4310c9cdfd7f10617fe88297bd5fe9ff100375cd79` | stable node contract (vocabulary) | none: no reader was found in the imported fleet runtime source (`node/`, `control/`) at this revision, so its presence here does not by itself establish runtime use or enforcement |

`AUTONOMY-POLICY.json` is not the task-authoring policy. That policy is a separate,
operator-held document. It is the one the queue service enforces on autonomous task
submissions (see the README).

`tests/unit/test_fleet_runtime_contracts.py` pins these digests. That proves the repository
files still match the recorded bytes, so a changed contract shows up as a new file or a
deliberate digest change, not a silent edit. It is a repository check. The runtime does not
compare these digests before use. Each cycle receipt records `contract_sha256` and
`goals_sha256`, which are the digests of the files on disk when the receipt is written,
after the executor run. The prompt actually used is bound by `prompt_sha256`.

## Classification

Each file was read in full and checked against five exclusion classes.

| Exclusion class | Result for all four |
|---|---|
| Secret-bearing configuration | No. They contain no secret, token, key or credential |
| Machine-local state | No. The content names no address, host path or port. It names node roles, and `GLOBAL-CONTINUOUS-GOALS.json` refers to one node by name (VPS3) as part of the issued text. Whether nodes hold identical copies is a separate, dated observation (Provenance below) |
| Ephemeral deployment state | No, with one caveat below |
| Credentials | No |
| Private operator configuration | No. They contain no address, account, host path or port |

Caveat, recorded and not edited away: `GLOBAL-CONTINUOUS-GOALS.json` carries content that
was current when the owner issued it and can age: a list of frontier candidates, a hold
status for three authority requests, and mission goals dated 2026-09-29. It is imported
as the issued text. It is a dated snapshot of owner direction, not a live status surface;
read current status elsewhere.

Not imported, and not part of this directory: the task-authoring policy document
(owner-issued authority, kept as local operator configuration), per-node role, mission and
authority files, and anything secret-bearing.

## Provenance

| Claim | Status | Label |
|---|---|---|
| Origin | The operator's local fleet bundle, release copy of the node contracts | `OBSERVED` by the importing session |
| Bytes here versus the bundle | Identical (the digests above are the bundle files' digests) | `OBSERVED` |
| Bytes here versus the nodes | On 2026-10-06 the copies on the three fleet nodes inspected that day had these same digests | `OBSERVED` on that date, read-only. A dated observation, not a standing claim about current deployment state |
| Bytes on the nodes now | Not established by this repository | `UNKNOWN` |
| Who issued them | The owner; `GLOBAL-CONTINUOUS-GOALS.json` states its issue date | `RECORDED` in the file; not independently established |
| Future deployments | Unchanged by this import. Nothing here deploys or updates a node | — |

The observations about the bundle and the nodes rest on evidence outside this repository.
The dated fleet records in `docs/global/baseline/` do not list these contract digests.

Four identities are kept apart:

| Identity | What establishes it |
|---|---|
| Repository identity | These files at an exact Git revision; the pinned digests above |
| Historical bundle identity | The `OBSERVED` match between these bytes and the bundle copies |
| Historical node observation | The 2026-10-06 read-only observation above. It holds for that date and those nodes only |
| Current live deployment identity | Not established here. A repository import is not a deployment |

## Relationship to `docs/global/`

`docs/global/` holds the repository's canonical operating contract and goals for work on
this repository. The documents here are the texts given to fleet nodes. They overlap in
intent and in several goal names, but neither is derived from the other in any way this
import established, and nothing here redefines a canonical ID. Where they differ,
`docs/global/` governs repository work. These record the texts the bundle gives nodes. The
supervisor places the contract and goals in the executor's prompt, plus the continuous goals
when present, as described above. Reconciling the two is an owner decision.

## Meaning preserved

No wording, field or value was changed for publication. The texts mention node roles and a
queue command by name; those are part of the contract and were left as issued.
