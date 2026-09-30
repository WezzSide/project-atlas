"""AS-OPT-GATE-001 — fail-closed paths. No partial experiment is promotion eligible."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.opt_gate_helpers import (
    REPO_HEAD,
    REPO_ROOT,
    REPO_TREE,
    baseline_config,
    candidate_config,
    honest_arm,
)

from project_atlas.opt_gate import (
    OptGateError,
    evaluate_hard_gates,
    load_opt_gate_policies,
    run_governed_experiment,
    seal_experiment,
    verify_experiment_receipt,
    verify_sealed_envelope,
)

POLICY_ROOT = REPO_ROOT / "fixtures" / "eval" / "opt-gate"


def _copy_policies(tmp_path: Path) -> Path:
    dest = tmp_path / "opt-gate"
    dest.mkdir()
    for path in sorted(POLICY_ROOT.glob("*.json")):
        (dest / path.name).write_bytes(path.read_bytes())
    return dest


def test_missing_evaluator_digest_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from project_atlas import opt_gate

    monkeypatch.setattr(
        opt_gate,
        "_evaluator_source_paths",
        lambda: (Path("/nonexistent/evaluator.py"),),
    )
    policies = load_opt_gate_policies(POLICY_ROOT)
    with pytest.raises(OptGateError, match="evaluator-digest-missing"):
        seal_experiment(
            repo_root=REPO_ROOT,
            policies=policies,
            baseline_configuration=baseline_config(),
        )


def test_threshold_missing_fail_closed(tmp_path: Path) -> None:
    dest = _copy_policies(tmp_path)
    (dest / "thresholds.json").unlink()
    with pytest.raises(OptGateError, match="threshold-missing"):
        load_opt_gate_policies(dest)


def test_unknown_gate_in_policy_fail_closed(tmp_path: Path) -> None:
    dest = _copy_policies(tmp_path)
    payload = json.loads((dest / "hard-gate-policy.json").read_text(encoding="utf-8"))
    payload["required_gates"] = ["security", "not-a-real-gate"]
    (dest / "hard-gate-policy.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OptGateError, match="gate-unknown"):
        load_opt_gate_policies(dest)


def test_missing_gate_in_policy_fail_closed(tmp_path: Path) -> None:
    dest = _copy_policies(tmp_path)
    payload = json.loads((dest / "hard-gate-policy.json").read_text(encoding="utf-8"))
    payload["required_gates"] = [g for g in payload["required_gates"] if g != "security"]
    (dest / "hard-gate-policy.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OptGateError, match="gate-missing"):
        load_opt_gate_policies(dest)


def test_candidate_config_malformed_fail_closed() -> None:
    with pytest.raises(OptGateError, match="candidate-config-malformed"):
        run_governed_experiment(
            repo_root=REPO_ROOT,
            experiment_id="exp-bad-cfg",
            repo_head=REPO_HEAD,
            repo_tree=REPO_TREE,
            baseline_config=baseline_config(),
            candidate_config={"candidate_id": "candidate-a", "thresholds": {"min": 0}},
            baseline_arm=honest_arm(),
            candidate_arm=honest_arm(),
            broker_session=None,
        )


def test_candidate_cannot_override_scoring_policy() -> None:
    with pytest.raises(OptGateError, match="candidate-config-malformed"):
        run_governed_experiment(
            repo_root=REPO_ROOT,
            experiment_id="exp-bad-policy",
            repo_head=REPO_HEAD,
            repo_tree=REPO_TREE,
            baseline_config=baseline_config(),
            candidate_config={
                "candidate_id": "candidate-a",
                "scoring_policy": {"caller_supplied_scores_accepted": True},
            },
            baseline_arm=honest_arm(),
            candidate_arm=honest_arm(),
            broker_session=None,
        )


def test_holdout_broker_unavailable_not_promote_eligible() -> None:
    receipt = run_governed_experiment(
        repo_root=REPO_ROOT,
        experiment_id="exp-no-broker",
        repo_head=REPO_HEAD,
        repo_tree=REPO_TREE,
        baseline_config=baseline_config(),
        candidate_config=candidate_config(),
        baseline_arm=honest_arm(),
        candidate_arm=honest_arm(),
        broker_session=None,
    )
    assert receipt["promotion_decision"] == "INVALID_EXPERIMENT"
    assert receipt["decision_reason"] == "holdout-broker-unavailable"
    assert receipt["quality_score_considered"] is False


def test_sealed_component_changed_mid_run(tmp_path: Path) -> None:
    from project_atlas.opt_gate import GovernedExperimentSession

    dest = _copy_policies(tmp_path)
    session = GovernedExperimentSession(
        repo_root=REPO_ROOT,
        experiment_id="exp-mutated-seal",
        repo_head=REPO_HEAD,
        repo_tree=REPO_TREE,
        baseline_config=baseline_config(),
        candidate_config=candidate_config(),
        policy_root=dest,
    )
    assert session.envelope is not None
    assert verify_sealed_envelope(session.envelope) is True
    mutated = json.loads((dest / "thresholds.json").read_text(encoding="utf-8"))
    mutated["min_public_matched_delta"] = 99
    (dest / "thresholds.json").write_text(
        json.dumps(mutated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    receipt = session.execute(
        baseline_arm=honest_arm(),
        candidate_arm=honest_arm(),
        broker_session=None,
    )
    assert receipt["promotion_decision"] == "INVALID_EXPERIMENT"
    assert receipt["decision_reason"] == "sealed-component-changed"
    assert receipt["seal_valid"] is False


def test_invalid_receipt_digest_fail_closed() -> None:
    from project_atlas.opt_gate import ScoreCounts, build_experiment_receipt

    policies = load_opt_gate_policies(POLICY_ROOT)
    envelope = seal_experiment(
        repo_root=REPO_ROOT,
        policies=policies,
        baseline_configuration=baseline_config(),
    )
    receipt = build_experiment_receipt(
        experiment_id="exp-bad-digest",
        repo_head=REPO_HEAD,
        repo_tree=REPO_TREE,
        envelope=envelope,
        candidate_configuration=candidate_config(),
        seed=1,
        gate_outcomes={
            name: "PASS"
            for name in (
                "security",
                "provenance_integrity",
                "authority_integrity",
                "unknown_honesty",
                "conflict_honesty",
                "evidence_integrity",
                "determinism",
                "project_isolation",
                "holdout_isolation",
            )
        },
        public_baseline=ScoreCounts(4, 4, 0),
        public_candidate=ScoreCounts(4, 4, 0),
        holdout_baseline=ScoreCounts(2, 2, 0),
        holdout_candidate=ScoreCounts(2, 2, 0),
        holdout_scored=True,
        promotion_decision="PROMOTE_ELIGIBLE",
        decision_reason="all-conditions-met",
        quality_score_considered=True,
        experiment_valid=True,
        seal_valid=True,
    )
    receipt["receipt_digest"] = "0" * 64
    with pytest.raises(OptGateError, match="receipt-invalid"):
        verify_experiment_receipt(receipt)


def test_receipt_schema_mismatch_fail_closed() -> None:
    from project_atlas.opt_gate import ScoreCounts, build_experiment_receipt

    policies = load_opt_gate_policies(POLICY_ROOT)
    envelope = seal_experiment(
        repo_root=REPO_ROOT,
        policies=policies,
        baseline_configuration=baseline_config(),
    )
    receipt = build_experiment_receipt(
        experiment_id="exp-schema-mismatch",
        repo_head=REPO_HEAD,
        repo_tree=REPO_TREE,
        envelope=envelope,
        candidate_configuration=candidate_config(),
        seed=1,
        gate_outcomes={
            name: "PASS"
            for name in (
                "security",
                "provenance_integrity",
                "authority_integrity",
                "unknown_honesty",
                "conflict_honesty",
                "evidence_integrity",
                "determinism",
                "project_isolation",
                "holdout_isolation",
            )
        },
        public_baseline=ScoreCounts(4, 4, 0),
        public_candidate=ScoreCounts(4, 4, 0),
        holdout_baseline=ScoreCounts(2, 2, 0),
        holdout_candidate=ScoreCounts(2, 2, 0),
        holdout_scored=True,
        promotion_decision="PROMOTE_ELIGIBLE",
        decision_reason="all-conditions-met",
        quality_score_considered=True,
        experiment_valid=True,
        seal_valid=True,
    )
    receipt["extra_secret_field"] = "nope"
    with pytest.raises(OptGateError, match="receipt-schema-mismatch"):
        verify_experiment_receipt(receipt)


def test_unsafe_scoring_policy_refused(tmp_path: Path) -> None:
    dest = _copy_policies(tmp_path)
    payload = json.loads((dest / "scoring-policy.json").read_text(encoding="utf-8"))
    payload["caller_supplied_scores_accepted"] = True
    (dest / "scoring-policy.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OptGateError, match="scoring-policy-invalid"):
        load_opt_gate_policies(dest)


def test_score_may_override_gates_refused(tmp_path: Path) -> None:
    dest = _copy_policies(tmp_path)
    payload = json.loads((dest / "hard-gate-policy.json").read_text(encoding="utf-8"))
    payload["score_may_override_gates"] = True
    (dest / "hard-gate-policy.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OptGateError, match="hard-gate-policy-invalid"):
        load_opt_gate_policies(dest)


def test_evaluate_requires_catalog() -> None:
    with pytest.raises(OptGateError, match="honesty-catalog-missing"):
        evaluate_hard_gates(
            arm=honest_arm(),
            candidate_config=candidate_config(),
            catalog={"cases": []},
        )


def test_scoring_broker_partial_failure_not_promote_eligible() -> None:
    from project_atlas.scoring_broker import (
        BrokerCase,
        BrokerHardGates,
        BrokerMetrics,
        BrokerResult,
        ScoringBrokerError,
    )

    class _PartialBroker:
        def __init__(self) -> None:
            self.calls = 0

        def manifest(self) -> list[BrokerCase]:
            return [BrokerCase("a" * 32, "exact", "q")]

        def submit(self, predictions: dict[str, str], candidate: object = None) -> BrokerResult:
            self.calls += 1
            if self.calls >= 2:
                raise ScoringBrokerError("broker-internal-error")
            return BrokerResult(
                metrics=BrokerMetrics(cases_scored=2, cases_matched=2, cases_missed=0),
                hard_gates=BrokerHardGates(
                    all_cases_predicted=True, all_matched=True, budget_ok=True
                ),
                opaque_case_ids=("a" * 32,),
                receipt_digest="b" * 64,
                attempts_remaining=6,
            )

    receipt = run_governed_experiment(
        repo_root=REPO_ROOT,
        experiment_id="exp-partial-broker",
        repo_head=REPO_HEAD,
        repo_tree=REPO_TREE,
        baseline_config=baseline_config(),
        candidate_config=candidate_config(),
        baseline_arm=honest_arm(holdout={"a" * 32: "x"}),
        candidate_arm=honest_arm(holdout={"a" * 32: "x"}),
        broker_session=_PartialBroker(),  # type: ignore[arg-type]
    )
    assert receipt["promotion_decision"] == "INVALID_EXPERIMENT"
    assert receipt["decision_reason"] == "scoring-broker-partial-failure"
    assert receipt["quality_score_considered"] is False
