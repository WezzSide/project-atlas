# Node contract documents: classification and provenance

Four contract documents that every fleet node receives. They are imported here unchanged,
as versioned documentation of what the nodes are given.

| File | sha256 | Class | Read by |
|---|---|---|---|
| `GLOBAL-AUTONOMOUS-OPERATING-CONTRACT.md` | `b4e9027006fead151ecd606fe74bccf43bf28fc68d1e3056ed78dfa1d32a5373` | stable node contract | the mission supervisor, into every cycle prompt |
| `GLOBAL-GOALS.json` | `dceb2b376b76fec7ef2049238ce952d44c69aec5dc9b72d83c7975fd2bff49bf` | stable node contract | the mission supervisor, into every cycle prompt |
| `GLOBAL-CONTINUOUS-GOALS.json` | `408fa6862fc0cf7bf6baf2b9c96c0a8ddae6d9ccd150324c321c52094cff9db8` | owner-issued contract, dated 2026-09-29 | the mission supervisor, into every cycle prompt when present |
| `AUTONOMY-POLICY.json` | `b4ab61e448cd81aa77ca3d4310c9cdfd7f10617fe88297bd5fe9ff100375cd79` | stable node contract (vocabulary) | no reader was found in the imported runtime source |

`tests/unit/test_fleet_runtime_contracts.py` pins these digests. A changed contract is a new
file or a deliberate digest change, not a silent edit.

## Classification

Each file was read in full and checked against five exclusion classes.

| Exclusion class | Result for all four |
|---|---|
| Secret-bearing configuration | No. They contain no secret, token, key or credential |
| Machine-local state | No. The same bytes are on every node |
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
| Bytes here versus the nodes | On 2026-10-06 the copies on all three fleet nodes had these same digests | `OBSERVED` on that date, read-only; not a standing fact |
| Who issued them | The owner; `GLOBAL-CONTINUOUS-GOALS.json` states its issue date | `RECORDED` in the file; not independently established |
| Future deployments | Unchanged by this import. Nothing here deploys or updates a node | — |

The observations about the bundle and the nodes rest on evidence outside this repository.

## Relationship to `docs/global/`

`docs/global/` holds the repository's canonical operating contract and goals for work on
this repository. The documents here are the texts given to fleet nodes. They overlap in
intent and in several goal names, but neither is derived from the other in any way this
import established, and nothing here redefines a canonical ID. Where they differ,
`docs/global/` governs repository work and these govern what a node was told. Reconciling
the two is an owner decision.

## Meaning preserved

No wording, field or value was changed for publication. The texts mention node roles and a
queue command by name; those are part of the contract and were left as issued.
