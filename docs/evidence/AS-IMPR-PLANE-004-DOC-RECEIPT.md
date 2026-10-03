ATLAS-DOC-RECEIPT

```yaml
event_id: AE-AS-IMPR-PLANE-PROGRESS-SENSITIVITY-004-20260910
goal_id: ATLAS-IMPROVEMENT-PLANE-PROGRESS-SENSITIVITY-004
directive: ATLAS-IMPROVEMENT-PLANE-PROGRESS-SENSITIVITY-004
raw_event: docs/evidence/as-impr-plane-progress-sensitivity-004/e09-e10-primary-evidence.json
normalized_event: pending
atlas_updates:
  - src/project_atlas/improvement_plane/analyze.py
  - src/project_atlas/improvement_plane/report.py
  - src/project_atlas/improvement_plane/compare.py
  - tests/unit/test_as_impr_plane_002_cycle.py
  - scripts/improvement_plane_historical_evaluation.py
  - docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-historical-eval-001/AS-IMPR-PLANE-001-HISTORICAL-EVALUATION-001-CORPUS.json
  - docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-historical-eval-001/historical-evaluation-results.json
  - docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-historical-eval-001/historical-evaluation-results.md
  - docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-historical-eval-001/historical-evaluation-discrepancies.md
  - docs/evidence/AS-IMPR-PLANE-004-ADDITIONAL-CORPUS.json
  - docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-additional-corpus/historical-evaluation-results.json
  - docs/evidence/as-impr-plane-progress-sensitivity-004/candidate-original-corpus/historical-evaluation-results.json
  - docs/evidence/as-impr-plane-progress-sensitivity-004/candidate-additional-corpus/historical-evaluation-results.json
  - docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-vs-candidate-comparison.json
  - docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-vs-candidate-comparison.md
  - docs/AS-IMPR-PLANE-004-PROGRESS-SENSITIVITY.md
  - docs/evidence/AS-IMPR-PLANE-004-DOC-RECEIPT.md
canonical_vault_updates: []
validation: focused_unit_suites_pass_and_corpus_replay_compared
sync_state: pending
blockers:
  - CANONICAL_VAULT_SYNC_NOT_EXECUTED_IN_THIS_LANE
identity:
  worktree: /home/gebruiker/Projects/project-atlas-worktrees/improvement-plane-progress-sensitivity-004
  branch: feat/as-impr-plane-progress-sensitivity-004
  base_frozen_head: d4e084c769bfbddd4b6a390e133bcd39011d2b3b
  base_frozen_tree: 087dc06e5678ca935e308543ab9eb640acff2a17
  relation_to_pr_792: successor_repair_lane_only_pr792_unchanged
discrepancies:
  E09:
    source: docs/evidence/AS-IMPR-PLANE-001-REVIEW-CANDIDATE-PIN.json
    classification: implementation_contract_limitation
    resolution: progress_signals_candidate_supersession_added
  E10:
    source: docs/evidence/atlas-core-ingestion-traversal.json
    classification: implementation_contract_limitation
    resolution: progress_signals_status_progression_added
benchmark:
  baseline_original:
    matches: 10
    mismatches: 2
    mismatch_ids: [E09, E10]
  candidate_original:
    matches: 12
    mismatches: 0
  baseline_additional:
    matches: 4
    mismatches: 2
    mismatch_ids: [P01, P02]
  candidate_additional:
    matches: 6
    mismatches: 0
  false_resolution_baseline: 0
  false_resolution_candidate: 0
  false_signal_baseline: 0
  false_signal_candidate: 0
artifact_checksums:
  e09_e10_primary_evidence_json: 9866ca4a7a40d1daedbd11a7470ed28992164e910e417668f162499f47144e27
  additional_corpus_json: 737cf385b588f806b27880a82f9c7eea5a1314404c3013b333b28699ef193460
  baseline_vs_candidate_comparison_json: 52f5c15da00897f10676fecbafde0011ff0d59cd95c94c55ca26955ac920be14
  baseline_vs_candidate_comparison_md: 03bd6ceca0aaf2edc79f756a6ada4fb8066c146f5a185e276c0feae3a34d5de4
  baseline_additional_results_json: acb21105d605942d52a682913ce91bd69de0ef8979cf20635ee036356bd17137
  candidate_original_results_json: dd0bd8b39ca5742042b75cdadde0f79ce115cf2723c8c39e15bb8e722d0eeabc
  candidate_additional_results_json: d942de6b93686bfd4562c4e64132d8c7ebe15a07599702476934e4c4a81954a8
handoff_patch:
  path: /home/gebruiker/.copilot/session-state/8f23cb61-4fba-4d25-a221-6364e79f6ead/files/AS-IMPR-PLANE-004-successor-repair.patch
  sha256: 42125834e4952854a3a4ba93f69de317d77445132a8c01985dfa57d660fad677
recommendation: adopt_repair
review_status: DEVELOPER_REPAIR_EVALUATION_COMPLETE_NOT_INDEPENDENT_VERIFICATION
summary: >
  This successor lane adds minimal progress-signal extraction for candidate
  supersession and remediation-status progression. It fixes E09/E10-family
  missed-progress outcomes while preserving strict non-resolution boundaries.
  Frozen PR #792 remains unchanged.
```
