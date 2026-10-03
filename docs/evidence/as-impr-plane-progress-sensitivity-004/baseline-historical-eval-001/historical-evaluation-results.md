# AS-IMPR-PLANE-001 Historical Evaluation 001

- Frozen implementation subject: `d4e084c769bfbddd4b6a390e133bcd39011d2b3b` / `087dc06e5678ca935e308543ab9eb640acff2a17`
- Corpus episodes: **12** (development 9, held-out 3)
- Match / mismatch: **10 / 2**

## Results table

| Episode | Split | Category | Expected | Actual | Verdict | Discrepancy |
|---|---|---|---|---|---|---|
| E01 | development | new_observable_failure_signal | delta_present | delta_present | match | - |
| E02 | development | owner_gate_frontier_signal | delta_present | delta_present | match | - |
| E03 | development | queued_and_external_dependency_signal | delta_present | delta_present | match | - |
| E04 | development | ci_gate_findings_signal | no_material_delta | no_material_delta | match | - |
| E05 | development | ci_runtime_wiring_signal | no_material_delta | no_material_delta | match | - |
| E06 | development | self_ingest_exclusion | no_material_delta | no_material_delta | match | - |
| E07 | development | self_ingest_exclusion | no_material_delta | no_material_delta | match | - |
| E08 | development | self_ingest_exclusion | no_material_delta | no_material_delta | match | - |
| E09 | development | candidate_identity_supersession | delta_present | no_material_delta | mismatch | missed_progress_or_insensitive |
| E10 | held_out | status_progress_outside_modeled_shape | delta_present | no_material_delta | mismatch | missed_progress_or_insensitive |
| E11 | held_out | measurement_schema_refinement | no_material_delta | no_material_delta | match | - |
| E12 | held_out | generic_receipt_identity_churn | no_material_delta | no_material_delta | match | - |

## Counts

- False resolution: 0
- Missed progress/insensitive: 2
- Unexpected/false signal: 0
- Compare non-ok: 0
- Episodes with duplicate recommendations: 0
- Episodes flagged uncertain by comparability checks: 12

## Operator assessment

The frozen implementation avoided false resolution but missed progress in multiple authentic transitions. Keep it for narrowly supported packet shapes only and prioritize parser/contract expansion.

## Known capability limits observed

- Self-ingest exclusions prevent scoring lane-generated AS-IMPR packets.
- Generic receipts without findings/owner_gated_nodes/successor_dag produce no observation deltas.
- Status/prose updates in unsupported packet shapes can represent real progress but remain unclassified.
- Coverage/incompatibility checks are conservative and avoided false resolved classifications in this corpus.
