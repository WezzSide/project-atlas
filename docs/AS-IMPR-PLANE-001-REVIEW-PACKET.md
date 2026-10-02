# AS-IMPR-PLANE-001 — Independent review packet

**Assessment type:** implementation review (not independent verification)  
**Directive:** `ATLAS-IMPROVEMENT-PLANE-REVIEW-READINESS-004`  
**PR:** https://github.com/B0LK13/project-atlas/pull/792  
**Merge authorization:** not claimed  
**IV authorization:** not claimed; dispatch only if owner permits

## Frozen review identity

| Field | Value |
|---|---|
| Source pin (analysis baseline) | `b87b4a226f4aa8b2f669edf112aa3476454f754f` / tree `46d1989b026a2f15920ec5e1c78a106799bd1249` |
| Review candidate head | `38cec36dffd03d6cf0dc48180f9e0ffd5ba5dbbe` |
| Review candidate tree | `b1c79af00967b296407bb5d0c31a0f7c3ad42bb9` |
| DQ delivery checkpoint (`tip_head` in DOC receipt) | `7aecdc3fa6312335a0c237cba62f3dfa0365b29a` / tree `b3fc11990973fcbd47108066af383990fcf69087` |
| Branch | `feat/as-impr-plane-001-delivery-evidence-report` |
| Base | `origin/main` @ source pin |
| Diff scope | 31 files / +4965 lines under `src/project_atlas/improvement_plane/`, focused tests, lane docs/evidence only |

CI conclusions for this head are recorded in `docs/evidence/AS-IMPR-PLANE-001-REVIEW-CI-REPORT.json` (refresh when the run completes; freeze pin may move if a lane-attributable repair lands).

## Architecture and entry points

```text
docs/evidence/*.json  →  readers (bounds, secrets, self-ingest) 
                      →  analyze (panels + ranked recommendations)
                      →  report JSON/MD
compare(before, after) → dispositions including claimed_closure
outcome → local .atlas/improvement-plane/outcomes.jsonl (annotation only)
evaluate(before, after, outcomes) → improved|persisted|regressed|inconclusive
```

| Surface | Entry |
|---|---|
| Supported install | `pip install -e ".[dev]"` then `python -m project_atlas.improvement_plane …` |
| Public Python | `compile_improvement_report` from `project_atlas.improvement_plane` |
| Shared `atlas` CLI | **not** registered (intentional) |

## Claims → evidence map

| Claim | Supporting evidence |
|---|---|
| Path-continuous CLOSED required for `resolved`/`improved` | `test_path_continuous_*`, corpus T02; planted path → T01 / `claimed_closure` |
| Annotation cannot certify resolution | `outcomes.certifies_resolution=False`; evaluate refuses planted closure |
| Reduced coverage ≠ improvement | `coverage_reduced` + unobservable note; corpus T03 |
| Uncertain compare windows exposed | `comparability.uncertain` for repo / `reference_utc` / schema mismatch |
| Duplicate hard-counters do not inflate priority | related-id merge + occurrence cap tests |
| Self-ingest excluded | DEMO/REPORT/AUDIT-REPORT excluded in readers + cycle tests |
| Focused unit suite | 24 tests under `tests/unit/test_as_impr_plane_*.py` |
| Live rec trace sample | `AS-IMPR-PLANE-001-DQ-AUDIT-REPORT.json` (n=11; no productivity claim) |

## Known limitations / unverified

- No authentic multi-day longitudinal production history (controlled corpus labeled synthetic).
- Outcome store: single-writer best-effort lock; multi-host concurrency unsupported.
- Packet-backed owner/external recommendations may be stale vs live PR/merge state.
- Path-continuous CLOSED is **observed continuity**, not causal proof of fix quality.
- Full-repo CI / external IV / vault sync / merge: separate gates (see DOC receipt).
- Baseline CI coverage of this lane code is not claimed until CI on this head is green and reviewed.

## Reproduction

```bash
pip install -e ".[dev]"
python -m pytest \
  tests/unit/test_as_impr_plane_001.py \
  tests/unit/test_as_impr_plane_002_cycle.py \
  tests/unit/test_as_impr_plane_003_decision_quality.py \
  tests/unit/test_as_impr_plane_003_dq_corpus.py --no-cov
python -m project_atlas.improvement_plane report --repo . \
  --reference-utc 2026-09-10T06:00:00Z --output-json /tmp/impr-report.json --json
python docs/examples/as_impr_plane_001_consume_report.py /tmp/impr-report.json
```

## Adversarial questions for reviewers

1. Can an operator plant a CLOSED finding on a **new path** and obtain `resolved` / `improved`? (Expect: no → `claimed_closure` / `inconclusive`.)
2. Can an outcome annotation referencing the outcomes journal or a lane `*-REPORT` certify success? (Expect: rejected.)
3. If after-coverage drops, can disappearance of an open finding look like improvement? (Expect: unobservable + `coverage_reduced`.)
4. Do duplicate OPEN rows in one file outrank a finding with more unique sources? (Expect: capped ranking prefers unique sources.)
5. Does `python -m` work after `pip install -e` without `PYTHONPATH=src`? (Expect: yes.)
6. Are generated reports accepted as delivery evidence on the next run? (Expect: self-ingest excluded.)
7. Does path-continuous CLOSED prove the underlying defect is fixed in production? (Expect: no — association / continuity only.)

## Highlighted honesty points

- **Path-continuous CLOSED** establishes overlapping source-path continuity between before-open and after-CLOSED evidence. It does **not** prove root-cause remediation or authorize DAG gates.
- **Reduced coverage** marks comparisons uncertain; missing evidence must not score as improvement.
- **Annotations** are operator assertions (`authority: none`, `certifies_resolution: false`) and remain separate from observed compare/evaluate dispositions.
