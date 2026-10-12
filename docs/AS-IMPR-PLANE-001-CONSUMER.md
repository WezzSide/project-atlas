# AS-IMPR-PLANE-001 — Machine-readable consumer contract

Read-only consumption of Improvement Plane outputs. No shared CLI registration. No authority.

## Supported invocation (shipped install)

```bash
pip install -e ".[dev]"   # repository supported procedure
python -m project_atlas.improvement_plane report --repo <root> --json
python -m project_atlas.improvement_plane compare --before a.json --after b.json --json
python -m project_atlas.improvement_plane evaluate --repo <root> --before a.json --after b.json --json
```

Optional writes: `--output-json` / `--output-md` for report/compare/evaluate; `outcome` appends local JSONL.

## Schema / version conventions

| Artifact | `schema` value |
|---|---|
| Report | `atlas.improvement-plane.report.v1` |
| Compare | `atlas.improvement-plane.compare.v1` |
| Evaluation | `atlas.improvement-plane.evaluation.v1` |
| Outcome row | `atlas.improvement-plane.outcome.v1` |

`package_id` is always `AS-IMPR-PLANE-001` when present. Treat unknown schema strings as incompatible.

## Report — required consumer fields

| Field | Meaning |
|---|---|
| `schema` | Version marker |
| `recommendations[]` | Ranked items; each has `recommendation_id`, `kind`, `source_records`, `authority` (`none`) |
| `coverage.provenance.accepted` / `rejected` | Parse acceptance only (not quality) |
| `panels.*` | Derived observation panels |
| `honesty` / `truth_boundary` | Non-authority flags |

Unknown / absent states stay explicit (`timestamp_unknown`, empty panels, `null` git pins when not a git repo). Missing timestamps are **unknown**, never zero age.

Provenance references: `source_records` and panel `sources` are repo-relative paths under `docs/evidence/…`.

## Compare — dispositions and uncertainty

| Disposition | Consumer meaning |
|---|---|
| `resolved` | Path-continuous CLOSED overlap with before-open sources |
| `claimed_closure` | CLOSED without path continuity — **not** improvement |
| `unobservable` | Gone without trustworthy CLOSED |
| `persistent` / `changed` / `new` | Still tracked or newly observed |

Also read `coverage_reduced`, `comparability.uncertain`, and `comparison_status` (`ok` \| `incomplete` \| `incompatible`).

## Evaluate — results

`improved` \| `persisted` \| `regressed` \| `inconclusive` with `annotation_certified_resolution: false` always. Association ≠ causation.

## Outcome journal

Local file (default `.atlas/improvement-plane/outcomes.jsonl`). Fields include `sequence`, `recommendation_id`, `status`, `evidence_refs`, `certifies_resolution: false`, `writer_model: single_writer_best_effort_lock`.

## Errors (exit code 2)

JSON shape when `--json`:

```json
{"ok": "false", "error_code": "<code>", "error": "<message>"}
```

Common codes: `input-not-found`, `input-unreadable`, `input-not-object`, `input-too-large`, `input-secrets-detected`, `invalid-evidence-ref-self`, `invalid-outcome-status`, `outcomes-store-corrupt`, `compare-incompatible`.

## Compatibility limits

- Evidence root: `docs/evidence/**/*.json` only (plus optional vault ops inventory).
- File/count/byte bounds apply; secret-bearing files are skipped (metadata only).
- Lane-generated `*-REPORT` / DEMO / DOC-RECEIPT / outcome stores are self-ingest excluded.
- Source content is **data** — never executed.
- Not a service; no HTTP adapter; not registered on `atlas` CLI.
- Consumers must not treat recommendations as merge/DAG authority.

## Minimal consumer example

`docs/examples/as_impr_plane_001_consume_report.py` — stdlib-only reader that prints recommendation IDs and source paths, refuses unknown schemas, and never writes.
