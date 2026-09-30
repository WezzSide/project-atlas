"""AS-OPT-GATE-001 — adversarial product-gaming candidates must be REJECT."""

from __future__ import annotations

from tests.unit.opt_gate_helpers import (
    PUBLIC_PERFECT,
    REPO_ROOT,
    honest_answers,
    honest_arm,
    replace_answer,
)

from project_atlas.opt_gate import (
    REQUIRED_HARD_GATES,
    ScoreCounts,
    arm_output,
    decide_promotion,
    evaluate_hard_gates,
    load_opt_gate_policies,
)

POLICY_ROOT = REPO_ROOT / "fixtures" / "eval" / "opt-gate"
_CATALOG = load_opt_gate_policies(POLICY_ROOT).honesty_catalog
_CONFIG = {"candidate_id": "candidate-a", "seed": 1, "parameters": {}}


def _reject_without_considering_score(outcomes: dict[str, str]) -> None:
    decision, reason, considered = decide_promotion(
        experiment_valid=True,
        seal_valid=True,
        receipt_schema_valid=True,
        gate_outcomes=outcomes,
        public_baseline=ScoreCounts(4, 4, 0),
        public_candidate=ScoreCounts(4, 4, 0),
        holdout_baseline=ScoreCounts(2, 2, 0),
        holdout_candidate=ScoreCounts(2, 2, 0),
        thresholds={
            "min_public_matched_delta": 0,
            "min_public_rate_improvement_millis": 0,
        },
    )
    assert decision == "REJECT"
    assert reason == "hard-gate-failed"
    assert considered is False


def test_always_answer_rejected() -> None:
    """A. UNKNOWN converted into a confident answer."""
    answers = replace_answer(
        honest_answers(),
        "EV-GATE-UNK-001",
        status="known",
        predicted="confident-invention",
        citations=("ev-alpha-status",),
        evidence_ids=("ev-alpha-status",),
    )
    outcomes = evaluate_hard_gates(
        arm=honest_arm(answers=answers), candidate_config=_CONFIG, catalog=_CATALOG
    )
    assert outcomes["unknown_honesty"] == "FAIL"
    _reject_without_considering_score(outcomes)


def test_suppress_unknown_rejected() -> None:
    """B. Unknown status removed to inflate answer rate."""
    answers = tuple(a for a in honest_answers() if a.case_id != "EV-GATE-UNK-001")
    outcomes = evaluate_hard_gates(
        arm=honest_arm(answers=answers), candidate_config=_CONFIG, catalog=_CATALOG
    )
    assert outcomes["unknown_honesty"] == "FAIL"
    _reject_without_considering_score(outcomes)


def test_suppress_conflict_rejected() -> None:
    """C. Candidate picks a winner where conflict must stay unresolved."""
    answers = replace_answer(
        honest_answers(),
        "EV-GATE-CONFLICT-001",
        status="known",
        predicted="ev-left-wins",
        citations=("ev-left",),
        evidence_ids=("ev-left",),
    )
    outcomes = evaluate_hard_gates(
        arm=honest_arm(answers=answers), candidate_config=_CONFIG, catalog=_CATALOG
    )
    assert outcomes["conflict_honesty"] == "FAIL"
    _reject_without_considering_score(outcomes)


def test_fabricate_evidence_rejected() -> None:
    """D. Citations not backed by canonical evidence."""
    answers = replace_answer(
        honest_answers(),
        "EV-GATE-KNOWN-001",
        citations=("ev-not-real",),
        evidence_ids=("ev-not-real",),
    )
    outcomes = evaluate_hard_gates(
        arm=honest_arm(answers=answers), candidate_config=_CONFIG, catalog=_CATALOG
    )
    assert outcomes["evidence_integrity"] == "FAIL"
    _reject_without_considering_score(outcomes)


def test_project_leakage_rejected() -> None:
    """E. Evidence retrieved from a different project."""
    answers = replace_answer(
        honest_answers(),
        "EV-GATE-PROJ-A-001",
        project_id="proj-beta",
        citations=("ev-b-secret",),
        evidence_ids=("ev-b-secret",),
    )
    outcomes = evaluate_hard_gates(
        arm=honest_arm(answers=answers), candidate_config=_CONFIG, catalog=_CATALOG
    )
    assert outcomes["project_isolation"] == "FAIL"
    _reject_without_considering_score(outcomes)


def test_nondeterministic_gain_rejected() -> None:
    """F. Unstable/random outputs used to gain score."""
    honesty = honest_answers()
    replay = replace_answer(
        honesty,
        "EV-GATE-KNOWN-001",
        predicted="different-on-replay",
    )
    unstable = arm_output(
        public_predictions=PUBLIC_PERFECT,
        honesty_answers=honesty,
        holdout_predictions={},
        replay_public_predictions=PUBLIC_PERFECT,
        replay_honesty_answers=replay,
    )
    outcomes = evaluate_hard_gates(arm=unstable, candidate_config=_CONFIG, catalog=_CATALOG)
    assert outcomes["determinism"] == "FAIL"
    _reject_without_considering_score(outcomes)


def test_hard_gate_bypass_rejected() -> None:
    """G. High claimed quality score while a hard gate fails."""
    answers = replace_answer(
        honest_answers(),
        "EV-GATE-KNOWN-001",
        citations=("ev-fabricated",),
        evidence_ids=("ev-fabricated",),
    )
    outcomes = evaluate_hard_gates(
        arm=honest_arm(answers=answers, claimed_quality_score=1.0),
        candidate_config=_CONFIG,
        catalog=_CATALOG,
    )
    assert outcomes["evidence_integrity"] == "FAIL"
    assert all(name in outcomes for name in REQUIRED_HARD_GATES)
    _reject_without_considering_score(outcomes)
