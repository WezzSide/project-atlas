import pytest

from experiments.agents_sdk.lab import (
    DecisionInput,
    Governor,
    GuardrailViolation,
    LaneState,
    evaluate_gate,
)


def test_demo_flow_enforces_verifier_identity() -> None:
    governor = Governor()
    decision = governor.run(owner_request="Normalize path guards in ingest.")
    assert decision.verifier_report.producer_role == "verifier"
    assert decision.implementer_output.producer_role == "implementer"
    assert decision.verdict in {"BLOCK", "APPROVE"}


def test_implementer_output_cannot_masquerade_as_verifier() -> None:
    governor = Governor()
    with_violating_patch = {
        "producer_role": "implementer",
        "kind": "verifier_verdict",
        "content": {"verdict": "APPROVE"},
    }
    with pytest.raises(GuardrailViolation):
        governor.validate_implementer_output(with_violating_patch)


def test_eval_implementer_self_certification_is_blocked() -> None:
    result = evaluate_gate(
        DecisionInput(
            remote_head_match=True,
            exact_head_ci=True,
            exact_head_iv=True,
            claim_integrity=True,
            p0_count=0,
            p1_count=0,
            current_main_compatibility=True,
            mergeable=True,
            owner_gate_resolved=True,
            stale_head=False,
            implementer_self_certification_attempt=True,
            verifier_repo_write_attempt=False,
            lane_states=[LaneState("lane-a", "RUNNABLE"), LaneState("lane-b", "WAITING_CI")],
            head_moved_after_decision=False,
        )
    )
    assert result.verdict == "BLOCK"
    assert "implementer_self_certification" in result.reasons


def test_eval_verifier_write_attempt_is_blocked() -> None:
    result = evaluate_gate(
        DecisionInput(
            remote_head_match=True,
            exact_head_ci=True,
            exact_head_iv=True,
            claim_integrity=True,
            p0_count=0,
            p1_count=0,
            current_main_compatibility=True,
            mergeable=True,
            owner_gate_resolved=True,
            stale_head=False,
            implementer_self_certification_attempt=False,
            verifier_repo_write_attempt=True,
            lane_states=[LaneState("lane-a", "RUNNABLE")],
            head_moved_after_decision=False,
        )
    )
    assert result.verdict == "BLOCK"
    assert "verifier_write_attempt" in result.reasons


def test_eval_stale_head_is_rejected() -> None:
    result = evaluate_gate(
        DecisionInput(
            remote_head_match=True,
            exact_head_ci=True,
            exact_head_iv=True,
            claim_integrity=True,
            p0_count=0,
            p1_count=0,
            current_main_compatibility=True,
            mergeable=True,
            owner_gate_resolved=True,
            stale_head=True,
            implementer_self_certification_attempt=False,
            verifier_repo_write_attempt=False,
            lane_states=[LaneState("lane-a", "RUNNABLE")],
            head_moved_after_decision=False,
        )
    )
    assert result.verdict == "BLOCK"
    assert "stale_head" in result.reasons


def test_eval_ci_pass_iv_missing_is_blocked() -> None:
    result = evaluate_gate(
        DecisionInput(
            remote_head_match=True,
            exact_head_ci=True,
            exact_head_iv=False,
            claim_integrity=True,
            p0_count=0,
            p1_count=0,
            current_main_compatibility=True,
            mergeable=True,
            owner_gate_resolved=True,
            stale_head=False,
            implementer_self_certification_attempt=False,
            verifier_repo_write_attempt=False,
            lane_states=[LaneState("lane-a", "RUNNABLE")],
            head_moved_after_decision=False,
        )
    )
    assert result.verdict == "BLOCK"
    assert "exact_head_iv_missing" in result.reasons


def test_eval_iv_fail_ci_pass_is_blocked() -> None:
    result = evaluate_gate(
        DecisionInput(
            remote_head_match=True,
            exact_head_ci=True,
            exact_head_iv=False,
            claim_integrity=True,
            p0_count=0,
            p1_count=1,
            current_main_compatibility=True,
            mergeable=True,
            owner_gate_resolved=True,
            stale_head=False,
            implementer_self_certification_attempt=False,
            verifier_repo_write_attempt=False,
            lane_states=[LaneState("lane-a", "RUNNABLE")],
            head_moved_after_decision=False,
        )
    )
    assert result.verdict == "BLOCK"
    assert "p1_findings" in result.reasons


def test_eval_owner_gate_unresolved_is_blocked() -> None:
    result = evaluate_gate(
        DecisionInput(
            remote_head_match=True,
            exact_head_ci=True,
            exact_head_iv=True,
            claim_integrity=True,
            p0_count=0,
            p1_count=0,
            current_main_compatibility=True,
            mergeable=True,
            owner_gate_resolved=False,
            stale_head=False,
            implementer_self_certification_attempt=False,
            verifier_repo_write_attempt=False,
            lane_states=[LaneState("lane-a", "RUNNABLE")],
            head_moved_after_decision=False,
        )
    )
    assert result.verdict == "BLOCK"
    assert "owner_gate_unresolved" in result.reasons


def test_lane_waiting_does_not_block_runnable_lane() -> None:
    result = evaluate_gate(
        DecisionInput(
            remote_head_match=True,
            exact_head_ci=True,
            exact_head_iv=True,
            claim_integrity=True,
            p0_count=0,
            p1_count=0,
            current_main_compatibility=True,
            mergeable=True,
            owner_gate_resolved=True,
            stale_head=False,
            implementer_self_certification_attempt=False,
            verifier_repo_write_attempt=False,
            lane_states=[LaneState("lane-a", "WAITING_CI"), LaneState("lane-b", "RUNNABLE")],
            head_moved_after_decision=False,
        )
    )
    assert result.runnable_lanes == ["lane-b"]
    assert "runnable_lanes_nonempty" in result.reasons


def test_head_moves_after_decision_invalidates_authorization() -> None:
    result = evaluate_gate(
        DecisionInput(
            remote_head_match=True,
            exact_head_ci=True,
            exact_head_iv=True,
            claim_integrity=True,
            p0_count=0,
            p1_count=0,
            current_main_compatibility=True,
            mergeable=True,
            owner_gate_resolved=True,
            stale_head=False,
            implementer_self_certification_attempt=False,
            verifier_repo_write_attempt=False,
            lane_states=[LaneState("lane-a", "RUNNABLE")],
            head_moved_after_decision=True,
        )
    )
    assert result.verdict == "BLOCK"
    assert "head_moved_after_decision" in result.reasons
