# Repository-to-Fleet Provenance Map — 2026-10-06

| Field | Value |
|---|---|
| Token | `FLEET_PROVENANCE_MAP_RECORDED` |
| Repository identity | `main` `92e833d0a6918d75b5cc3b202873aeb1f0ac0484` |
| Observed | 2026-10-06, read-only |
| Machine-readable map | [evidence/fleet-provenance-map-2026-10-06.json](./evidence/fleet-provenance-map-2026-10-06.json) |
| Schema | [evidence/fleet-provenance-map.schema.json](./evidence/fleet-provenance-map.schema.json) (`atlas-fleet-provenance-map/v1`) |
| Check | `tests/unit/test_fleet_provenance_map_20261006.py` |
| Companion | `2026-10-06-FLEET-STATE-BASELINE.md` in this directory (proposed separately; the map does not depend on it) |
| Evidence vocabulary | [Operating contract §4](../ATLAS-GLOBAL-OPERATING-CONTRACT.md). Labels used: `OBSERVED` / `INFERRED` / `UNKNOWN`, plus `RECORDED` for a statement taken from a repository document and not re-observed |
| Status | Record only. It grants nothing, decides nothing and changes no runtime behaviour. `ATLAS_VPS_FLEET_AUTONOMOUS` is **not asserted** |

## 1. Purpose

Several components running on the fleet cannot be tied to a revision of this repository.
This map states, per component, what is known and where each fact came from, so that a
coordinator or a deployment lane can read it instead of re-deriving it.

It is a **public projection**. Host access detail is left out and is not needed to use it.
It is dated: a later reading is a new file, not an edit of this one.

## 2. Fields

| Field | Meaning | Values |
|---|---|---|
| `repository.implementation` | Is there source for it here? | `IMPLEMENTED`, `NOT_IN_REPOSITORY` |
| `integration` | Is it wired to an entrypoint, workflow or deploy path here? | `INTEGRATED`, `NOT_INTEGRATED`, `UNKNOWN` |
| `instances[].deployment` | Was it found on that host? | `DEPLOYED`, `NOT_FOUND`, `NOT_APPLICABLE`, `UNKNOWN` |
| `instances[].activity` | Was it running? | `ACTIVE`, `INACTIVE`, `DISABLED`, `NOT_APPLICABLE`, `UNKNOWN` |
| `instances[].health` | Was it completing its work? | `PROGRESSING`, `DEGRADED`, `NOT_APPLICABLE`, `UNKNOWN` |
| `instances[].deployed_revision` | Exact `main` commit, only when provable | 40-hex or `null` |
| `instances[].drift_from_main` | Position against `main` | `CURRENT`, `BEHIND`, `NOT_APPLICABLE`, `UNKNOWN` |
| `instances[].runtime_identity` | Does it run under its own identity? | `DEDICATED`, `SHARED`, `NOT_APPLICABLE`, `UNKNOWN` |
| `live_validation` | Has it been exercised live? | `RECORDED_SINGLE_HOST`, `NOT_VALIDATED`, `UNKNOWN` |
| `provenance_confidence` | Can the running thing be tied to this repository? | `EXACT_REVISION`, `NONE`, `NOT_APPLICABLE` |
| `evidence[]` | Which source established which field, with a label | see the map |

`ACTIVE` is not `PROGRESSING`, `DEPLOYED` is not `ACTIVE`, and `IMPLEMENTED` is not
`DEPLOYED`. The check refuses a map that rounds any of these upward, and refuses a
revision claim without `EXACT_REVISION`.

## 3. Reading of 2026-10-06

| Component | Source here | Integrated | Host | Deployed | Active | Health | Tied to `main` |
|---|---|---|---|---|---|---|---|
| Runner controller | yes | yes | VPS2 | yes | yes | unknown; restarts repeatedly | **exact revision**, behind by 2 commits in its path |
| Agent-execute workflow | yes | yes | VPS2 workers | n/a | unknown | unknown | n/a (runs from the dispatched ref) |
| Runner-verify workflow | yes | yes | GitHub-hosted | n/a | unknown | unknown | n/a |
| DEVQ coordinator | yes | **no** | VPS1, VPS2, VPS3 | **not found** | — | — | n/a |
| Mission supervisor | **no** | unknown | VPS1 | yes | yes | unknown | none |
| Mission supervisor | **no** | unknown | VPS2 | yes | yes | **degraded** | none |
| Mission supervisor | **no** | unknown | VPS3 | yes | yes | progressing | none |
| Forge worker | **no** | unknown | VPS1 | yes | yes | unknown | none |
| On-demand executor | **no** | unknown | VPS1 | yes | idle | unknown | none |
| Execution broker | **no** | unknown | VPS1 | yes | disabled | — | none |
| Verifier service | **no** | unknown | VPS2 | yes | yes | unknown | none |
| Queue service | **no** | unknown | VPS3 | yes | yes | unknown | none |
| Owner gateway | **no** | unknown | VPS3 | yes | yes | unknown | none |
| Control service | **no** | unknown | VPS3 | no release present | disabled | — | none |

Counts: 12 components; 4 have source here; 1 can be tied to an exact `main` revision; 8
have no source here and no provable revision.

## 4. What follows

All `PLANNED`; none is started by this record.

1. **Where the eight live in source.** Until that is recorded, their drift from `main`
   cannot be computed at all. This is an owner decision, not an engineering one.
2. **A revision marker per deployed component.** The runner controller is provable only
   because its release is named by a commit. The same convention would make every row
   provable.
3. **A publisher for the map.** This file was written by hand from one reading. The useful
   form is the same schema emitted by the fleet itself, with a publisher identity
   (frontier §4, first row).
4. **The DEVQ coordinator row.** It moves only when an entrypoint exists and a deployment is
   authorized; both are tracked in the backlog.

## 5. Limits

- One reading by one session, not independently verified.
- `RECORDED` entries repeat what a repository document says; they were not re-observed.
- No cause is claimed for any degraded or restarting component.
- Nothing here says a deployed component should be changed.
