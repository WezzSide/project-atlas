# ULT-01a — ExecutionIdentity v1 + EvidenceAttestation v1 + object-bound proof v2

**Status: PROPOSAL (ULT-00 Step D). Not implemented. Not authorized.**

## Why this is the first package

Reconciliation confirms the package's expectation: the highest-leverage
missing prerequisite is the ability to say *which exact object, under which
observable conditions, a piece of evidence is about*. Every later wave (Start
binding, program-graph cache keys, capability grants, verifier mesh, eval
environment profiles, trace correlation) needs that reference, and today:

- AT3-050 accepts any `{"evidence_ref": "x"}` as a `PRESENT` stage (`atlas3/proof.py:52-66`) and computes no digest, no head/tree, no producer;
- AT3-051/052 bind head+tree but take the "observed" SHAs as **parameters** and know no repository, environment or tool identity;
- the dispatcher already *observes* `base_main`/`candidate_head`/`candidate_tree` from git and strict-compares them (`orchestration/dispatcher.py:690-695`, `:736-782`), but the pins cross the agent boundary as untyped strings in `observations.extras`, and `base_tree` is missing;
- canonical JSON hashing exists (`orchestration/router.py:56-67`) but is forked across 10+ modules with an `ensure_ascii` divergence.

ULT-01a closes exactly that seam. It does **not** make proof *observed*; it
makes proof typed, hashed, and object-bound, with a clean place for the
observation adapter to plug in (ULT-01b).

## §0.5 package header

```text
PACKAGE                     = ULT-01a (canonical id: owner to assign; AT3-05x exhausted, AT3-13x free)
PURPOSE                     = Typed, content-addressed execution identity and evidence attestation;
                              proof v2 that fails closed unless every attestation is bound to the
                              same exact object; v1 dict path preserved
BASE_MAIN                   = 9972d16448a9cefd3444363b59eae2c0dbed7f20   (re-derive at branch time)
BASE_TREE                   = 2067f1253d411e3bef9fd20ad534b29316b6e953
REUSED_COMPONENTS           = atlas3/proof.py PROOF_STAGES + write_json_atomic + Atlas3Error codes;
                              atlas3/iv_bind.py / adv_bind.py fail-closed ladder (unchanged);
                              orchestration/router.canonical_payload_digest (semantics copied, not imported);
                              atlas_contracts.versions ID/HASH patterns; pydantic extra="forbid" convention
                              (orchestration/models.py); EvidenceBundle.payload_sha256 naming;
                              #735 DEP_* dependency-class names (vocabulary only; #735 is unmerged)
NEW_COMPONENTS              = src/atlas_contracts/canonical.py           (one canonical JSON + sha256 helper)
                              src/project_atlas/atlas3/execution_identity.py
                              src/project_atlas/atlas3/attestation.py
                              proof v2 path inside atlas3/proof.py (additive branch)
                              schemas: atlas.execution-identity.v1, atlas.evidence-attestation.v1
                              CLI: `atlas proof --attestations <file> --identity <file>` (existing verb, new flags)
MIGRATION_REQUIRED          = NO (proof v1 reports remain readable; v2 adds proof_version: 2)
COMPATIBILITY_RISK          = LOW — isolated atlas3/ + atlas_contracts/ additions; foundation.py and
                              capabilities.py:81-89 assertions on proof shape kept satisfied
CERTIFIED_SURFACES_TOUCHED  = NONE (no DENY-list path; root cli.py untouched; atlas3/cli.py additive)
AUTHORITY_REQUIRED          = none for code; owner: package id + acceptance of ULT-00
VERIFICATION_LEVEL          = V9 (independent verifier) + exact-head CI (package minimum for ULT-01)
ROLLBACK                    = delete the three new modules and the v2 branch; v1 behaviour byte-identical
```

## Contracts (v1)

### `atlas.execution-identity.v1`

Pydantic, `extra="forbid"`, frozen; canonical serialization → `identity_digest`
(sha256 hex) which is also the `run_id` seed. No wall-clock field.

```yaml
schema: atlas.execution-identity.v1
schema_version: 1
repository: "github.com/b0lk13/project-atlas"      # required, non-empty; reuse CANONICAL_REPOSITORY_IDENTITY as default
project_id: "<safe_project_id>"                    # atlas_contracts safe_relative_component
git:
  base_main: <40hex>                               # required
  base_tree: <40hex>                               # required (new relative to the dispatcher pins)
  candidate_head: <40hex|null>                     # null allowed only while stage == TASK
  candidate_tree: <40hex|null>
environment:                                       # observable-only; anything else is UNKNOWN
  os: "linux" | "windows" | "darwin" | "UNKNOWN"
  arch: "x86_64" | ... | "UNKNOWN"
  python: "3.12.x" | "UNKNOWN"
  environment_digest: <64hex>                      # sha256 over the canonical environment block
tools:
  declared: [{name: "pytest", version: "8.3.2"}, ...]   # declared, sorted by name; may be empty
  tools_digest: <64hex>
agent:
  agent_id: "<ID_PATTERN>" | "UNKNOWN"
  skill_sha256: <64hex|null>                       # reuse SkillBinding.sha256 when present
model:
  provider: "<str>" | "UNKNOWN"
  model: "<str>" | "UNKNOWN"
bindings:                                          # which dimensions are actually observed
  environment_bound: bool                          # false if any environment field is UNKNOWN
  tools_bound: bool                                # false if tools.declared is empty
identity_digest: <64hex>                           # computed, must equal recomputation (fail closed)
```

Rules: any key matching the secret denylist (`token`, `secret`, `password`,
`api_key`, `authorization`, `credential`, …) anywhere in the payload →
`IDENTITY_SECRET_FORBIDDEN`; `merge_authorization`, `execution_authorized`,
`owner_authority`, `trust_score`, `graph_winner` → the same refusal codes
`autonomy_gate._walk_reject` already uses.

### `atlas.evidence-attestation.v1`

```yaml
schema: atlas.evidence-attestation.v1
stage: TESTS                                       # one of PROOF_STAGES (unchanged tuple)
claim: "targeted_regression_passed"                # free identifier, ID_PATTERN
identity_digest: <64hex>                           # must equal the identity the proof is evaluated against
object: {head: <40hex>, tree: <40hex>}             # must equal identity.git.candidate_*
producer: {type: "pytest"|"ruff"|"mypy"|"ci"|"human"|"agent"|..., version: "<str>"|"UNKNOWN"}
command_ref: "<str>"                               # opaque reference, never executed
result: {exit_code: int, artifact_digest: <64hex>, summary: {passed: int, failed: int, ...}}
dependencies: [DEP_HEAD, DEP_TREE, DEP_TOOLCHAIN, ...]   # #735 vocabulary; declares what invalidates this
content_hash: <64hex>                              # canonical hash of everything above; recomputed on read
```

`producer.type == "agent"` or `"model"` can never satisfy `INDEPENDENT_VERIFICATION`
or `ADV` (reuse the AT3-051/052 denylist), and a `producer.type == "model"`
attestation can never satisfy any stage other than `IMPLEMENTATION`
(model claim != proof, made structural instead of a flag).

### proof v2

`evaluate_proof(..., attestations=[...], identity=ExecutionIdentity)`:

1. Recompute `identity_digest`; mismatch → `IDENTITY_DIGEST_MISMATCH`.
2. For each attestation: recompute `content_hash` (`ATTESTATION_HASH_MISMATCH`), require `identity_digest` equality (`ATTESTATION_IDENTITY_MISMATCH`), require `object == identity.git.candidate_*` (`PROOF_OBJECT_MISMATCH`), require `stage ∈ PROOF_STAGES`.
3. Stage status becomes `PRESENT` only from a valid attestation; `chain_status` computed as today; `UNPROVEN_MODEL_CLAIM` semantics preserved.
4. Report gains `proof_version: 2`, `identity_digest`, `object`, `attestations: [{stage, claim, content_hash, dependencies}]`, `environment_bound`, `merge_authorization: "NOT_GRANTED"`, `model_claim_is_proof: False`.
5. Persistence: `generated/ops/atlas3/proof/<task>.json` as today, **plus** `generated/ops/atlas3/proof/<task>/<identity_digest[:16]>.json` so a new object never overwrites the previous object's proof (history without a second ledger).

The v1 call signature (`evidence: dict`) is untouched and yields
`proof_version: 1` byte-identical to today.

## Acceptance (from the package's ULT-01 list, made testable)

| Criterion | Test |
|---|---|
| canonical serialization deterministic | same identity → same digest across 2 processes and on Windows CI; key order and `ensure_ascii=True` pinned; non-ASCII payload digest asserted |
| changed tree invalidates object-bound proof | attestation for tree `A`, identity with tree `B` → `PROOF_OBJECT_MISMATCH`; report not written |
| changed environment invalidates environment-bound proof | identity with `environment_bound: true` and different `environment_digest` → stage marked `INVALIDATED_ENV`, chain not `PROVEN` |
| malformed / missing attestation fails closed | missing `content_hash`, wrong hash, unknown stage, extra key → `Atlas3Error`; nothing persisted |
| model completion claim cannot satisfy proof | `producer.type: model` on `TESTS`/`CI`/`INDEPENDENT_VERIFICATION`/`ADV` → refused; `model_claims_complete=True` still yields `UNPROVEN_MODEL_CLAIM` |
| exact current IV/ADV semantics intact | `test_atlas3_iv_bind_051.py` and `test_atlas3_adv_bind_052.py` unchanged and green; IV/ADV modules byte-identical |
| v1 compatibility explicitly tested | golden v1 report byte-identical before/after; `foundation.py` and `capabilities.py` assertions green |
| no secrets in artifacts | denylisted key → refusal; persisted JSON scanned with `secrets.scan_text` in test |
| no merge authority | every output carries `merge_authorization: "NOT_GRANTED"`; a supplied `merge_authorization: "GRANTED"` is refused |
| no certified surface touched | `test_atlas3_demo_isolation_001.py` green; root `cli.py` not in diff |

## Negative controls (each must fail a distinct, named test set)

```text
NC-A  remove the object == candidate_* check          → object-mismatch tests fail
NC-B  skip content_hash recomputation                 → tampered-attestation tests fail
NC-C  drop the producer.type model/agent stage denylist → model-claim tests fail
NC-D  set ensure_ascii=False in the canonical helper   → non-ASCII digest pin fails
NC-E  write the report before validation              → "nothing persisted on refusal" tests fail
```

Controls are applied under a sha256 assertion that the mutation changed the
file and the source is restored byte-identical, following the F5–F7 seal
practice.

## What this package does NOT claim

- It does not observe git, environment, or tools; values are declared and
  hashed. Observation wiring (reusing `observe_binding_pins`) is ULT-01b.
- It does not make IV/ADV consume the identity (ULT-01b).
- It does not build a proof DAG (ULT-01b); the `dependencies` list is the hook.
- It does not add signing, SLSA, or any compliance claim.
- It does not touch `orchestration/`, `atlas3/iv_bind.py`, `atlas3/adv_bind.py`,
  or any DENY-listed file.
- It does not change `PROOF_STAGES`.

## §0.6 return-packet skeleton (to be filled from the pushed exact object)

```text
BASE_MAIN          =
BASE_TREE          =
CANDIDATE_HEAD     =
CANDIDATE_TREE     =
CHANGED_PATHS      = src/atlas_contracts/canonical.py
                     src/project_atlas/atlas3/execution_identity.py
                     src/project_atlas/atlas3/attestation.py
                     src/project_atlas/atlas3/proof.py
                     src/project_atlas/atlas3/cli.py
                     src/project_atlas/schemas/execution-identity.v1.schema.json
                     src/project_atlas/schemas/evidence-attestation.v1.schema.json
                     tests/unit/test_atlas3_execution_identity_*.py
                     tests/unit/test_atlas3_attestation_*.py
                     tests/unit/test_atlas3_proof_v2_*.py
                     docs/atlas-3/EPICS.md, PACKAGE-MATURITY.json, docs/backlog.md, WORKLOG.md (additive)
TESTS_TARGETED     =
TESTS_AFFECTED     = tests/unit/test_atlas3_proof_001.py, test_atlas3_foundation_001.py,
                     test_atlas3_capabilities_004.py, test_atlas3_cli_001.py,
                     test_atlas3_demo_isolation_001.py, test_atlas3_iv_bind_051.py, test_atlas3_adv_bind_052.py
FULL_SUITE_RESULT  =
RUFF_RESULT        =
MYPY_RESULT        =
CI_EXACT_HEAD      =
IV_EXACT_OBJECT    =
ADV_EXACT_OBJECT   =
NEGATIVE_CONTROLS  = NC-A … NC-E with failing-test-name sets
RESIDUALS          =
CLAIMS_PROVEN      =
CLAIMS_NOT_MADE    = observation, DAG, IV/ADV consumption, signing, SLSA, environment enforcement
MERGE_AUTHORIZATION = NOT_GRANTED
```
