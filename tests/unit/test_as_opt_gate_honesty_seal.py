"""AS-OPT-GATE-001 IV remediation — honesty catalog object seal + receipt thresholds.

Closes OPT-GATE-SEAL-HOLDOUT-CATALOG-OBJECT-DIGEST-MISSING and the
receipt-threshold hardcoded-zero bypass (D-PROJECT-ATLAS-OPT-GATE-REMEDIATE-030).
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from tests.unit.opt_gate_helpers import (
    REPO_HEAD,
    REPO_ROOT,
    REPO_TREE,
    baseline_config,
    candidate_config,
    honest_answers,
    honest_arm,
    replace_answer,
)

from project_atlas.opt_gate import (
    REQUIRED_HARD_GATES,
    GovernedExperimentSession,
    OptGateError,
    ScoreCounts,
    SealedEnvelope,
    _receipt_digest_for,
    build_experiment_receipt,
    decide_promotion,
    evaluate_hard_gates,
    honesty_catalog_object_digest,
    load_opt_gate_policies,
    seal_experiment,
    verify_experiment_receipt,
    verify_sealed_envelope,
)

POLICY_ROOT = REPO_ROOT / "fixtures" / "eval" / "opt-gate"
_CONFIG = {"candidate_id": "candidate-a", "seed": 1, "parameters": {}}
_PASSING_QUALITY = {
    "public_baseline": ScoreCounts(4, 3, 1),
    "public_candidate": ScoreCounts(4, 4, 0),
    "holdout_baseline": ScoreCounts(2, 2, 0),
    "holdout_candidate": ScoreCounts(2, 2, 0),
    "thresholds": {
        "min_public_matched_delta": 0,
        "min_public_rate_improvement_millis": 0,
    },
}


def _seal() -> SealedEnvelope:
    policies = load_opt_gate_policies(POLICY_ROOT)
    return seal_experiment(
        repo_root=REPO_ROOT,
        policies=policies,
        baseline_configuration=baseline_config(),
    )


def _all_pass(outcomes: dict[str, str]) -> bool:
    return set(outcomes) == set(REQUIRED_HARD_GATES) and all(
        outcomes[name] == "PASS" for name in REQUIRED_HARD_GATES
    )


def _decide(*, seal_valid: bool, outcomes: dict[str, str]) -> str:
    decision, _reason, _considered = decide_promotion(
        experiment_valid=seal_valid,
        seal_valid=seal_valid,
        receipt_schema_valid=True,
        gate_outcomes=outcomes,
        **_PASSING_QUALITY,
    )
    return decision


def _suppress_unknown_arm():
    answers = tuple(a for a in honest_answers() if a.case_id != "EV-GATE-UNK-001")
    return honest_arm(answers=answers)


def _suppress_conflict_arm():
    answers = tuple(a for a in honest_answers() if a.case_id != "EV-GATE-CONFLICT-001")
    return honest_arm(answers=answers)


def _expanded_evidence_arm():
    answers = replace_answer(
        honest_answers(),
        "EV-GATE-KNOWN-001",
        citations=("ev-alpha-status", "ev-fabricated"),
        evidence_ids=("ev-alpha-status", "ev-fabricated"),
    )
    return honest_arm(answers=answers)


def _vacate_status(catalog: dict, status: str) -> None:
    catalog["cases"][:] = [
        case for case in catalog["cases"] if str(case.get("expected_status", "")) != status
    ]


def _expand_known_evidence(catalog: dict, extra: str) -> None:
    for case in catalog["cases"]:
        if case.get("case_id") == "EV-GATE-KNOWN-001":
            ids = list(case.get("canonical_evidence_ids", []))
            ids.append(extra)
            case["canonical_evidence_ids"] = ids
            return
    raise AssertionError("known case missing")


# --- HONESTY_CATALOG_OBJECT_SEAL -------------------------------------------------


def test_seal_binds_honesty_catalog_file_and_object_digests() -> None:
    envelope = _seal()
    digests = envelope.component_digests
    assert "honesty_catalog_file" in digests
    assert "honesty_catalog_object" in digests
    file_digest = digests["honesty_catalog_file"]
    object_digest = digests["honesty_catalog_object"]
    assert len(file_digest) == 64
    assert len(object_digest) == 64
    assert file_digest != object_digest
    assert object_digest == honesty_catalog_object_digest(envelope.honesty_catalog)
    assert verify_sealed_envelope(envelope) is True


def test_canonical_object_digest_ignores_non_semantic_metadata() -> None:
    envelope = _seal()
    baseline = honesty_catalog_object_digest(envelope.honesty_catalog)
    envelope.honesty_catalog["schema_version"] = 99
    envelope.honesty_catalog["version"] = "mutated-runtime"
    envelope.honesty_catalog["package_id"] = "not-a-semantic-field"
    assert honesty_catalog_object_digest(envelope.honesty_catalog) == baseline
    assert verify_sealed_envelope(envelope) is True


def test_semantically_equivalent_reorder_retains_object_digest() -> None:
    envelope = _seal()
    baseline = honesty_catalog_object_digest(envelope.honesty_catalog)
    envelope.honesty_catalog["cases"].reverse()
    envelope.honesty_catalog["foreign_evidence_ids"].reverse()
    for case in envelope.honesty_catalog["cases"]:
        case["canonical_evidence_ids"].reverse()
        case["allowed_project_ids"].reverse()
    assert honesty_catalog_object_digest(envelope.honesty_catalog) == baseline
    assert verify_sealed_envelope(envelope) is True


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c: _vacate_status(c, "unknown"), id="remove-unknown"),
        pytest.param(lambda c: _vacate_status(c, "conflict"), id="remove-conflict"),
        pytest.param(
            lambda c: _expand_known_evidence(c, "ev-fabricated"),
            id="expand-canonical-evidence",
        ),
        pytest.param(
            lambda c: c["cases"][0].__setitem__("expected_status", "known"),
            id="change-expected-status",
        ),
        pytest.param(
            lambda c: c["cases"][1]["canonical_evidence_ids"].pop(),
            id="shrink-required-evidence",
        ),
        pytest.param(
            lambda c: c["cases"][0]["allowed_project_ids"].append("proj-beta"),
            id="nested-allowlist-mutate",
        ),
    ],
)
def test_semantic_catalog_mutation_invalidates_seal(mutate) -> None:
    envelope = _seal()
    assert verify_sealed_envelope(envelope) is True
    mutate(envelope.honesty_catalog)
    assert verify_sealed_envelope(envelope) is False
    assert (
        honesty_catalog_object_digest(envelope.honesty_catalog)
        != envelope.component_digests["honesty_catalog_object"]
    )


def test_replacing_catalog_object_invalidates_seal() -> None:
    envelope = _seal()
    mutated = copy.deepcopy(envelope.honesty_catalog)
    _vacate_status(mutated, "unknown")
    object.__setattr__(envelope, "honesty_catalog", mutated)
    assert verify_sealed_envelope(envelope) is False


# --- HONESTY_MUTATION_BYPASS -----------------------------------------------------


def test_iv_exploit_unknown_vacating_cannot_promote() -> None:
    """seal → REJECT → mutate catalog in memory → seal_valid False → not PROMOTE."""
    envelope = _seal()
    arm = _suppress_unknown_arm()
    baseline_outcomes = evaluate_hard_gates(
        arm=arm, candidate_config=_CONFIG, catalog=envelope.honesty_catalog
    )
    assert baseline_outcomes["unknown_honesty"] == "FAIL"
    assert _decide(seal_valid=True, outcomes=baseline_outcomes) == "REJECT"

    _vacate_status(envelope.honesty_catalog, "unknown")
    mutated_outcomes = evaluate_hard_gates(
        arm=arm, candidate_config=_CONFIG, catalog=envelope.honesty_catalog
    )
    assert _all_pass(mutated_outcomes)
    assert _decide(seal_valid=True, outcomes=mutated_outcomes) == "PROMOTE_ELIGIBLE"

    seal_valid = verify_sealed_envelope(envelope)
    assert seal_valid is False
    assert _decide(seal_valid=False, outcomes=mutated_outcomes) == "INVALID_EXPERIMENT"
    assert _decide(seal_valid=False, outcomes=mutated_outcomes) != "PROMOTE_ELIGIBLE"


def test_iv_exploit_conflict_vacating_cannot_promote() -> None:
    envelope = _seal()
    arm = _suppress_conflict_arm()
    baseline_outcomes = evaluate_hard_gates(
        arm=arm, candidate_config=_CONFIG, catalog=envelope.honesty_catalog
    )
    assert baseline_outcomes["conflict_honesty"] == "FAIL"
    assert _decide(seal_valid=True, outcomes=baseline_outcomes) == "REJECT"

    _vacate_status(envelope.honesty_catalog, "conflict")
    mutated_outcomes = evaluate_hard_gates(
        arm=arm, candidate_config=_CONFIG, catalog=envelope.honesty_catalog
    )
    assert _all_pass(mutated_outcomes)
    assert _decide(seal_valid=True, outcomes=mutated_outcomes) == "PROMOTE_ELIGIBLE"

    assert verify_sealed_envelope(envelope) is False
    assert _decide(seal_valid=False, outcomes=mutated_outcomes) != "PROMOTE_ELIGIBLE"


def test_iv_exploit_canonical_evidence_expansion_cannot_promote() -> None:
    envelope = _seal()
    arm = _expanded_evidence_arm()
    baseline_outcomes = evaluate_hard_gates(
        arm=arm, candidate_config=_CONFIG, catalog=envelope.honesty_catalog
    )
    assert baseline_outcomes["evidence_integrity"] == "FAIL"
    assert _decide(seal_valid=True, outcomes=baseline_outcomes) == "REJECT"

    _expand_known_evidence(envelope.honesty_catalog, "ev-fabricated")
    mutated_outcomes = evaluate_hard_gates(
        arm=arm, candidate_config=_CONFIG, catalog=envelope.honesty_catalog
    )
    assert _all_pass(mutated_outcomes)
    assert _decide(seal_valid=True, outcomes=mutated_outcomes) == "PROMOTE_ELIGIBLE"

    assert verify_sealed_envelope(envelope) is False
    assert _decide(seal_valid=False, outcomes=mutated_outcomes) != "PROMOTE_ELIGIBLE"


def test_session_execute_after_catalog_mutation_is_invalid_experiment() -> None:
    session = GovernedExperimentSession(
        repo_root=REPO_ROOT,
        experiment_id="exp-honesty-mutate",
        repo_head=REPO_HEAD,
        repo_tree=REPO_TREE,
        baseline_config=baseline_config(),
        candidate_config=candidate_config(),
        policy_root=POLICY_ROOT,
    )
    assert session.envelope is not None
    _vacate_status(session.envelope.honesty_catalog, "unknown")
    receipt = session.execute(
        baseline_arm=honest_arm(),
        candidate_arm=_suppress_unknown_arm(),
        broker_session=None,
    )
    assert receipt["seal_valid"] is False
    assert receipt["promotion_decision"] == "INVALID_EXPERIMENT"
    assert receipt["decision_reason"] == "sealed-component-changed"
    assert receipt["promotion_decision"] != "PROMOTE_ELIGIBLE"


# --- RECEIPT THRESHOLD BINDING ---------------------------------------------------


def _receipt_with_delta(
    *, min_delta: int, public_candidate_matched: int
) -> tuple[dict, SealedEnvelope]:
    policies = load_opt_gate_policies(POLICY_ROOT)
    thresholds = copy.deepcopy(policies.thresholds)
    thresholds["min_public_matched_delta"] = min_delta
    policies = replace(policies, thresholds=thresholds)
    envelope = seal_experiment(
        repo_root=REPO_ROOT,
        policies=policies,
        baseline_configuration=baseline_config(),
    )
    outcomes = {name: "PASS" for name in REQUIRED_HARD_GATES}
    decision = "PROMOTE_ELIGIBLE" if public_candidate_matched - 4 >= min_delta else "REJECT"
    reason = "all-conditions-met" if decision == "PROMOTE_ELIGIBLE" else "quality-threshold-not-met"
    receipt = build_experiment_receipt(
        experiment_id="exp-threshold-bind",
        repo_head=REPO_HEAD,
        repo_tree=REPO_TREE,
        envelope=envelope,
        candidate_configuration=candidate_config(),
        seed=1,
        gate_outcomes=outcomes,
        public_baseline=ScoreCounts(4, 4, 0),
        public_candidate=ScoreCounts(4, public_candidate_matched, 4 - public_candidate_matched),
        holdout_baseline=ScoreCounts(2, 2, 0),
        holdout_candidate=ScoreCounts(2, 2, 0),
        holdout_scored=True,
        promotion_decision=decision,
        decision_reason=reason,
        quality_score_considered=True,
        experiment_valid=True,
        seal_valid=True,
    )
    return receipt, envelope


def test_receipt_persists_sealed_thresholds_and_object_digest() -> None:
    receipt, envelope = _receipt_with_delta(min_delta=1, public_candidate_matched=4)
    assert receipt["thresholds"]["min_public_matched_delta"] == 1
    assert receipt["thresholds"]["holdout_non_regression"] is True
    assert receipt["thresholds"]["require_holdout_scored"] is True
    assert len(receipt["threshold_object_digest"]) == 64
    assert len(receipt["honesty_catalog_file_digest"]) == 64
    assert len(receipt["honesty_catalog_object_digest"]) == 64
    assert len(receipt["envelope_digest"]) == 64
    assert receipt["envelope_digest"] == envelope.envelope_digest
    verify_experiment_receipt(receipt, sealed_envelope=envelope)


def test_forged_promote_with_quality_threshold_reject_fails_verify() -> None:
    """IV: REJECT for min_delta=1 must not verify as PROMOTE after digest rewrite.

    Previously verify_experiment_receipt recomputed with hardcoded min_delta=0,
    so forging promotion_decision + receipt_digest succeeded.
    """
    receipt, envelope = _receipt_with_delta(min_delta=1, public_candidate_matched=4)
    assert receipt["promotion_decision"] == "REJECT"
    assert receipt["thresholds"]["min_public_matched_delta"] == 1
    verify_experiment_receipt(receipt, sealed_envelope=envelope)

    receipt["promotion_decision"] = "PROMOTE_ELIGIBLE"
    receipt["decision_reason"] = "all-conditions-met"
    receipt["receipt_digest"] = _receipt_digest_for(receipt)
    with pytest.raises(OptGateError, match="receipt-invalid"):
        verify_experiment_receipt(receipt, sealed_envelope=envelope)


def test_forged_threshold_object_digest_fails_verify() -> None:
    receipt, envelope = _receipt_with_delta(min_delta=1, public_candidate_matched=4)
    receipt["threshold_object_digest"] = "0" * 64
    receipt["receipt_digest"] = _receipt_digest_for(receipt)
    with pytest.raises(OptGateError, match="receipt-invalid"):
        verify_experiment_receipt(receipt, sealed_envelope=envelope)


def test_happy_path_receipt_with_matching_thresholds_still_verifies() -> None:
    receipt, envelope = _receipt_with_delta(min_delta=0, public_candidate_matched=4)
    assert receipt["promotion_decision"] == "PROMOTE_ELIGIBLE"
    verify_experiment_receipt(receipt, sealed_envelope=envelope)


def test_promote_without_sealed_anchors_fails_closed() -> None:
    """PROMOTE_ELIGIBLE cannot be certified from a self-contained forged receipt."""
    receipt, _envelope = _receipt_with_delta(min_delta=0, public_candidate_matched=4)
    assert receipt["promotion_decision"] == "PROMOTE_ELIGIBLE"
    with pytest.raises(OptGateError, match="receipt-invalid"):
        verify_experiment_receipt(receipt)


def test_zero_threshold_downgrade_redigest_fails_with_sealed_anchors() -> None:
    """D-031: rewriting thresholds to zero + redigest must not verify as PROMOTE."""
    from project_atlas.opt_gate import _sealed_thresholds, _sha256_payload

    receipt, envelope = _receipt_with_delta(min_delta=5, public_candidate_matched=4)
    assert receipt["promotion_decision"] == "REJECT"
    assert receipt["thresholds"]["min_public_matched_delta"] == 5

    receipt["thresholds"] = {
        "holdout_non_regression": True,
        "min_public_matched_delta": 0,
        "min_public_rate_improvement_millis": 0,
        "require_holdout_scored": True,
    }
    receipt["threshold_object_digest"] = _sha256_payload(_sealed_thresholds(receipt["thresholds"]))
    receipt["threshold_digest"] = "c" * 64
    receipt["promotion_decision"] = "PROMOTE_ELIGIBLE"
    receipt["decision_reason"] = "all-conditions-met"
    receipt["quality_score_considered"] = True
    receipt["receipt_digest"] = _receipt_digest_for(receipt)

    with pytest.raises(OptGateError, match="receipt-invalid"):
        verify_experiment_receipt(receipt, sealed_envelope=envelope)
    with pytest.raises(OptGateError, match="receipt-invalid"):
        verify_experiment_receipt(receipt)
