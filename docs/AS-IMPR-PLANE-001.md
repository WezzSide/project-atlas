# AS-IMPR-PLANE-001 — Delivery-evidence improvement plane

**Status:** Decision-quality-003 hardening (lane-local; not merged; no authority)  
**Directives:** `ATLAS-PARALLEL-IMPROVEMENT-PLANE-20260910` → `CONTINUATION-002` → `DECISION-QUALITY-003`  
**Package:** `AS-IMPR-PLANE-001`  
**Entry point:** `python -m project_atlas.improvement_plane`  
**Operator guide:** `docs/AS-IMPR-PLANE-001-OPERATOR.md`  
**Consumer contract:** `docs/AS-IMPR-PLANE-001-CONSUMER.md`  
**Review packet:** `docs/AS-IMPR-PLANE-001-REVIEW-PACKET.md`

## Cycle

```text
observe → compare → explain → recommend → record outcome → evaluate
```

| Command | Mode | Purpose |
|---|---|---|
| `report` / `inspect` | read-only (+ optional output files) | Coverage, panels, kind-split recommendations |
| `compare --before --after` | read-only (+ optional outputs) | new/resolved/claimed_closure/persistent/changed/unobservable |
| `outcome ...` | **writes** `.atlas/improvement-plane/outcomes.jsonl` | Local annotation only (cannot certify) |
| `evaluate --before --after` | read-only (+ optional outputs) | improved/persisted/regressed/inconclusive |

**Resolved / improved** require path-continuous CLOSED evidence (overlap with before-open sources).  
Planted CLOSED on a new path is `claimed_closure` / evaluate `inconclusive`.  
Disappearance alone is `unobservable` / `inconclusive`.  
Outcome annotations never upgrade those classifications.

## Identities (do not conflate)

| Identity | Meaning |
|---|---|
| Source pin | Analysis baseline (`docs/evidence/AS-IMPR-PLANE-001-SOURCE-PIN.json`) |
| DQ baseline pin | Decision-quality freeze (`docs/evidence/AS-IMPR-PLANE-001-DQ-BASELINE-PIN.json`) |
| Implementation commit | Lane code checkpoint(s) on this branch |
| Generated reports / audits | Regenerable; excluded via self-ingest guard when named `*-REPORT` |
| Outcome annotations | Local non-authoritative store; never DAG authority |

## Honesty

```text
RECOMMENDATION ≠ AUTHORITY
OUTCOME ANNOTATION ≠ GATE RESOLUTION / CERTIFICATION
PLANTED CLOSED ≠ RESOLVED
DISAPPEARING EVIDENCE ≠ RESOLVED
COVERAGE REDUCTION ≠ IMPROVEMENT
ASSOCIATION ≠ CAUSATION
FILE COUNT ≠ EVIDENCE QUALITY
```

## Decision-quality corpus

Controlled fixtures (labeled synthetic):  
`tests/fixtures/improvement_plane/dq_corpus/` with `EXPECTED.json`.

## Reproduce

```bash
cd /home/gebruiker/Projects/project-atlas-worktrees/improvement-plane
export PYTHONPATH=src

python -m pytest \
  tests/unit/test_as_impr_plane_001.py \
  tests/unit/test_as_impr_plane_002_cycle.py \
  tests/unit/test_as_impr_plane_003_decision_quality.py \
  tests/unit/test_as_impr_plane_003_dq_corpus.py \
  --no-cov

python -m project_atlas.improvement_plane report --repo . \
  --reference-utc 2026-09-10T06:00:00Z \
  --output-json /tmp/impr-before.json
```

## Optional adoption

Thin `atlas impr …` wrappers may later call these APIs. Not required for this lane;
shared CLI registration remains untouched.
