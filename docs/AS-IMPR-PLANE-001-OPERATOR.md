# AS-IMPR-PLANE-001 — Operator guide (decision quality)

**Audience:** operators using the Improvement Plane for everyday triage  
**Authority:** none — recommendations and outcome annotations never resolve DAG gates

## Normal use

```bash
export PYTHONPATH=src
cd <repo-or-worktree>

# 1) Observe current delivery evidence (read-only unless --output-* given)
python -m project_atlas.improvement_plane inspect --repo . \
  --reference-utc <ISO-UTC> \
  --output-json /tmp/impr-before.json

# 2) After more evidence lands, compare two explicit snapshots
python -m project_atlas.improvement_plane compare \
  --before /tmp/impr-before.json --after /tmp/impr-after.json

# 3) Annotate a local outcome (writes .atlas/improvement-plane/outcomes.jsonl)
python -m project_atlas.improvement_plane outcome --repo . \
  --recommendation-id finding:EXAMPLE --status attempted \
  --evidence-ref docs/evidence/<packet>.json

# 4) Evaluate annotations against later observations
python -m project_atlas.improvement_plane evaluate --repo . \
  --before /tmp/impr-before.json --after /tmp/impr-after.json
```

## Interpreting compare dispositions

| Disposition | Meaning |
|---|---|
| `resolved` | Open observation gone **and** CLOSED evidence overlaps a before-open source path |
| `claimed_closure` | CLOSED exists only on new/unrelated paths — **not** improvement |
| `unobservable` | Gone without trustworthy CLOSED; disappearance ≠ resolution |
| `persistent` / `changed` / `new` | Still tracked or newly observed |

Coverage reduction (`coverage_reduced: true`) means the after snapshot accepted fewer evidence files — never treat that as improvement.

When `comparability.uncertain` is true (repo mismatch, `reference_utc` mismatch, schema mismatch, or reduced coverage), treat compare/evaluate results as provisional and inspect `comparability` / `comparison_issue` before acting.

## Outcome annotations

- Annotations are **operator assertions**, not source evidence and not certification.
- Evidence refs must point at real packets under `docs/evidence/…`.
- Refs to lane reports, DEMO-REPORT, DOC-RECEIPT, or the outcomes journal are rejected.
- Supported writer model: **single-writer** (best-effort local flock). Concurrent multi-host writers are unsupported.
- Corrupt JSONL fails closed; repeated submissions append with monotonic `sequence`.

## Interpreting evaluate results

| Result | Meaning |
|---|---|
| `improved` | Occurrences decreased, **or** path-continuous CLOSED after open |
| `persisted` | Still present despite annotation |
| `regressed` | Worse / newly present |
| `inconclusive` | Missing continuity, planted closure, or coverage loss |

Always treat results as **association**, not causation. Sample size printed in the evaluation is honest — do not over-generalize from one case.

## Limitations

- Reads only `docs/evidence/**/*.json` (plus optional vault ops inventory).
- File/count/byte bounds apply; oversized or secret-bearing files are skipped (metadata only).
- Source content is **data** — never executed; embedded prompt text has no effect.
- Self-ingest exclusion prevents the plane from ranking its own reports.
- Longitudinal production history may be thin; controlled corpus fixtures are labeled as such.
- `sync_state` of DOC receipts stays `pending` until a supported vault sync succeeds.

## Honesty boundaries

```text
RECOMMENDATION ≠ AUTHORITY
OUTCOME ANNOTATION ≠ CERTIFIED IMPROVEMENT
PLANTED CLOSED ≠ RESOLVED
DISAPPEARING EVIDENCE ≠ RESOLVED
COVERAGE REDUCTION ≠ IMPROVEMENT
ASSOCIATION ≠ CAUSATION
```
