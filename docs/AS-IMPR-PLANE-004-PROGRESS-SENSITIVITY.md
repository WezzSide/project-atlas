# AS-IMPR-PLANE-004 — Progress Sensitivity Repair

Directive: `ATLAS-IMPROVEMENT-PLANE-PROGRESS-SENSITIVITY-004`

Scope relation:

- Frozen baseline implementation remains `#792` at `d4e084c7` / tree `087dc06e`.
- This lane is a successor repair proposal only (`feat/as-impr-plane-progress-sensitivity-004`).
- Historical corpus and baseline results from evaluation-001 are preserved under:
  `docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-historical-eval-001/`.

## 1) E09/E10 discrepancy verification

Primary evidence snapshot:

- `docs/evidence/as-impr-plane-progress-sensitivity-004/e09-e10-primary-evidence.json`

Findings:

1. **E09 (candidate supersession)**  
   Source packet changed `review_candidate_head/tree` and freeze CI context across pinned commits (`e57c0cfb -> d4e084c7`).  
   Baseline compare output stayed `no_material_delta`.
2. **E10 (status progression)**  
   Source packet changed from `remediated-pending-independent-replay` to
   `remediated-and-independently-verified` (`d5232206 -> b25cdb12`).  
   Baseline compare output stayed `no_material_delta`.

Root-cause classification:

- **Implementation/contract limitation** (not adapter corruption): baseline
  observation extraction ignored candidate/status progression fields entirely.
- **Expectation retained as correct**: both episodes are legitimate evidence
  movement and should classify as progress/change, not resolution.

## 2) Progress contract

Supported meaning in this lane:

- **Changed evidence / progress**: observation identity remains and summary state
  changes (`counts.changed`).
- **Confirmed resolution**: unchanged rule — only path-continuous CLOSED evidence
  over an open finding can produce `resolved`.
- **Insufficient information**: disappearance without continuity remains
  `unobservable`; contradictory or mixed windows remain `uncertain`.

No new compare status was introduced. Compatibility implications:

- Report schema stays `atlas.improvement-plane.report.v1`.
- New panel is additive: `panels.progress_signals`.
- Existing consumers that ignore unknown panel keys remain compatible.

## 3) Smallest sound repair

Code changes:

- `src/project_atlas/improvement_plane/analyze.py`
  - Added `analyze_progress_signals(records)` for two packet patterns:
    1. candidate supersession (`review_candidate_head/tree`, `ci_status_at_freeze`)
    2. top-level status progression with `finding_id` + `status`
- `src/project_atlas/improvement_plane/report.py`
  - Added `panels.progress_signals` (additive panel only).
- `src/project_atlas/improvement_plane/compare.py`
  - Included `progress_signals.items` in observation map so state changes
    register as `changed`.
- `tests/unit/test_as_impr_plane_002_cycle.py`
  - Added regression tests for E09/E10 semantics and nearby counterexamples.

No episode IDs, repo IDs, PR numbers, or hardcoded expected verdicts are used in
the production logic.

## 4) False-improvement protections rechecked

Focused protections are preserved and covered:

- superseded-candidate movement is `changed`, not `resolved`
- progress-marker disappearance is `unobservable`, not resolved
- partial progress can coexist with persistent open blockers
- planted/new-path CLOSED remains `claimed_closure` (existing tests)
- coverage reduction remains `uncertain` and non-resolving (existing tests)
- incompatible/incomplete inputs still fail closed (existing tests)

## 5) Baseline vs candidate evaluation

Repro command (candidate lane):

```bash
.venv/bin/python scripts/improvement_plane_historical_evaluation.py \
  --repo /home/gebruiker/Projects/project-atlas-worktrees/improvement-plane-progress-sensitivity-004 \
  --corpus /home/gebruiker/Projects/project-atlas-worktrees/improvement-plane-progress-sensitivity-004/docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-historical-eval-001/AS-IMPR-PLANE-001-HISTORICAL-EVALUATION-001-CORPUS.json \
  --output-dir /home/gebruiker/Projects/project-atlas-worktrees/improvement-plane-progress-sensitivity-004/docs/evidence/as-impr-plane-progress-sensitivity-004/candidate-original-corpus
```

Additional predeclared corpus (selected before candidate output review):

- `docs/evidence/AS-IMPR-PLANE-004-ADDITIONAL-CORPUS.json`
- held-out split: `P05`, `P06`

Set-level comparison:

- `docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-vs-candidate-comparison.json`
- `docs/evidence/as-impr-plane-progress-sensitivity-004/baseline-vs-candidate-comparison.md`

Observed results:

1. Original corpus: baseline `10/12` match -> candidate `12/12` match.
2. Additional corpus: baseline `4/6` match -> candidate `6/6` match.
3. `false_resolution`: baseline `0`, candidate `0`.
4. `unexpected_or_false_signal`: baseline `0`, candidate `0`.
5. Changed classifications are exactly E09/E10 equivalents (`E09`,`E10`,`P01`,`P02`);
   no new false-positive resolutions.

Consumer compatibility check:

- `python docs/examples/as_impr_plane_001_consume_report.py <candidate report>`
  succeeds unchanged because the repair is additive (`panels.progress_signals`)
  and preserves schema `atlas.improvement-plane.report.v1`.

## 6) Recommendation

**Adopt repair** with existing honesty boundaries intact.

Reason:

- resolves demonstrated missed-progress cases (E09/E10 family)
- does not weaken resolution criteria
- preserves conservative behavior for closure lookalikes and unsupported packet
  shapes
- maintains consumer compatibility via additive panel only
