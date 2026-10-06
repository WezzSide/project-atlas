# AS-IMPR-PLANE-001 Historical Evaluation 001

- Frozen implementation subject: `d4e084c769bfbddd4b6a390e133bcd39011d2b3b` / `087dc06e5678ca935e308543ab9eb640acff2a17`
- Corpus episodes: **6** (development 4, held-out 2)
- Match / mismatch: **6 / 0**

## Results table

| Episode | Split | Category | Expected | Actual | Verdict | Discrepancy |
|---|---|---|---|---|---|---|
| P01 | development | candidate_identity_supersession_progress | delta_present | delta_present | match | - |
| P02 | development | status_progression_without_resolution_claim | delta_present | delta_present | match | - |
| P03 | development | closure_lookalike_without_open_finding | no_material_delta | no_material_delta | match | - |
| P04 | development | self_ingest_review_ci_packet | no_material_delta | no_material_delta | match | - |
| P05 | held_out | generic_receipt_claim_boundary_change | no_material_delta | no_material_delta | match | - |
| P06 | held_out | self_ingest_dq_audit_packet | no_material_delta | no_material_delta | match | - |

## Counts

- False resolution: 0
- Missed progress/insensitive: 0
- Unexpected/false signal: 0
- Compare non-ok: 0
- Episodes with duplicate recommendations: 0
- Episodes flagged uncertain by comparability checks: 6

## Operator assessment

Corpus results match expected transitions. Keep current capability within the evaluated scope; continue monitoring with new authentic episodes.

## Known capability limits observed

- Self-ingest exclusions prevent scoring lane-generated AS-IMPR packets.
- Generic receipts without findings/owner_gated_nodes/successor_dag produce no observation deltas.
- Status/prose updates in unsupported packet shapes can represent real progress but remain unclassified.
- Coverage/incompatibility checks are conservative and avoided false resolved classifications in this corpus.
