"""Unit + adversarial tests for AS-ORCH-SPECULATIVE-CERTIFICATION-001."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from project_atlas.orchestration.sdk.models import SdkRuntimeError
from project_atlas.orchestration.sdk.speculative_certification import (
    PACKAGE_CERT_LANES,
    REQUIRED_LANES,
    CertificationState,
    LaneReceipt,
    LaneResult,
    cancel_for_tip_drift,
    evaluate_barrier,
    load_barrier,
    load_candidate_seal,
    promote_exact_pin_evidence,
    record_lane_result,
    seal_candidate,
    speculative_cert_dir,
)
from project_atlas.orchestration.sdk.speculative_certification_oracle import (
    assert_oracle_parity,
    oracle_evaluate,
)

HEAD = "2c81c6d61c981b346968d022c88d985e5e86673a"
TREE = "43befdb707cf0455572242ca3a1d1b87f71050ce"
MAIN = "7e797468a2eca37c959920912b1fa264df4be638"
OTHER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_TREE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _receipt(
    lane: str,
    *,
    result: LaneResult = LaneResult.PASS,
    generation: int = 1,
    head: str = HEAD,
    tree: str = TREE,
    new_p0: int = 0,
    new_p1: int = 0,
    previous_p1_reopened: int = 0,
) -> LaneReceipt:
    return LaneReceipt(
        lane=lane,
        result=result,
        head=head,
        tree=tree,
        generation=generation,
        new_p0=new_p0,
        new_p1=new_p1,
        previous_p1_reopened=previous_p1_reopened,
    )


def _pass_all(
    root: Path,
    *,
    generation: int = 1,
    lanes: tuple[str, ...] = REQUIRED_LANES,
) -> list[LaneReceipt]:
    out: list[LaneReceipt] = []
    for lane in lanes:
        receipt = _receipt(lane, generation=generation)
        record_lane_result(root, receipt)
        out.append(receipt)
    return out


def test_seal_and_promote_exact_pin(tmp_path: Path) -> None:
    seal = seal_candidate(tmp_path, generation=107, head=HEAD, tree=TREE, base_main=MAIN)
    assert seal.merge_authorization == "NOT_GRANTED"
    receipts = _pass_all(tmp_path, generation=107)
    barrier = evaluate_barrier(tmp_path)
    assert barrier.state == CertificationState.CERTIFIED
    promoted = promote_exact_pin_evidence(tmp_path, live_head=HEAD, live_tree=TREE, live_main=MAIN)
    assert promoted.state == CertificationState.EVIDENCE_PROMOTED
    assert promoted.exact_pin_evidence_promoted is True
    assert promoted.certification_frozen is True
    assert promoted.merge_authorization == "NOT_GRANTED"
    expect = oracle_evaluate(
        generation=107,
        head=HEAD,
        tree=TREE,
        base_main=MAIN,
        required_lanes=REQUIRED_LANES,
        receipts=receipts,
        live_head=HEAD,
        live_tree=TREE,
        live_main=MAIN,
        promote=True,
    )
    assert_oracle_parity(
        barrier_state=promoted.state,
        barrier_p0=promoted.new_p0,
        barrier_p1=promoted.new_p1,
        barrier_prev=promoted.previous_p1_reopened,
        barrier_promoted=promoted.exact_pin_evidence_promoted,
        barrier_frozen=promoted.certification_frozen,
        barrier_merge=promoted.merge_authorization,
        expectation=expect,
    )


def test_lane_pin_mismatch_rejected(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=1, head=HEAD, tree=TREE, base_main=MAIN)
    with pytest.raises(SdkRuntimeError) as exc:
        record_lane_result(tmp_path, _receipt("CI", head=OTHER))
    assert exc.value.code == "SPECULATIVE_CERT_PIN_MISMATCH"


def test_tip_drift_cancels_and_blocks_promotion(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=2, head=HEAD, tree=TREE, base_main=MAIN)
    _pass_all(tmp_path, generation=2)
    evaluate_barrier(tmp_path)
    cancelled = cancel_for_tip_drift(tmp_path, live_head=OTHER, live_tree=TREE)
    assert cancelled.state == CertificationState.CANCELLED_TIP_DRIFT
    drifted = promote_exact_pin_evidence(tmp_path, live_head=OTHER, live_tree=TREE, live_main=MAIN)
    assert drifted.state == CertificationState.CANCELLED_TIP_DRIFT


def test_target_moved_blocks_promotion(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=3, head=HEAD, tree=TREE, base_main=MAIN)
    _pass_all(tmp_path, generation=3)
    evaluate_barrier(tmp_path)
    with pytest.raises(SdkRuntimeError) as exc:
        promote_exact_pin_evidence(tmp_path, live_head=HEAD, live_tree=TREE, live_main=OTHER)
    assert exc.value.code == "SPECULATIVE_CERT_TARGET_MOVED"


def test_new_p0_fails_barrier(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=4, head=HEAD, tree=TREE, base_main=MAIN)
    for lane in REQUIRED_LANES:
        record_lane_result(
            tmp_path,
            _receipt(lane, generation=4, new_p0=1 if lane == "ADV" else 0),
        )
    barrier = evaluate_barrier(tmp_path)
    assert barrier.state == CertificationState.BARRIER_FAILED


def test_cross_generation_rejected(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=5, head=HEAD, tree=TREE, base_main=MAIN)
    with pytest.raises(SdkRuntimeError) as exc:
        record_lane_result(tmp_path, _receipt("CI", generation=4))
    assert exc.value.code == "SPECULATIVE_CERT_CROSS_GENERATION"


def test_pass_then_fail_stale_rejected(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=6, head=HEAD, tree=TREE, base_main=MAIN)
    record_lane_result(tmp_path, _receipt("CI", generation=6))
    with pytest.raises(SdkRuntimeError) as exc:
        record_lane_result(tmp_path, _receipt("CI", generation=6, result=LaneResult.FAIL))
    assert exc.value.code == "SPECULATIVE_CERT_STALE_RECEIPT"


def test_fail_then_pass_rejected(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=7, head=HEAD, tree=TREE, base_main=MAIN)
    record_lane_result(tmp_path, _receipt("CI", generation=7, result=LaneResult.FAIL))
    with pytest.raises(SdkRuntimeError) as exc:
        record_lane_result(tmp_path, _receipt("CI", generation=7))
    assert exc.value.code == "SPECULATIVE_CERT_STALE_RECEIPT"


def test_duplicate_pass_idempotent(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=8, head=HEAD, tree=TREE, base_main=MAIN)
    record_lane_result(tmp_path, _receipt("CI", generation=8))
    again = record_lane_result(tmp_path, _receipt("CI", generation=8))
    assert again.lanes["CI"].result == LaneResult.PASS


def test_unknown_lane_rejected(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=9, head=HEAD, tree=TREE, base_main=MAIN)
    with pytest.raises(SdkRuntimeError) as exc:
        record_lane_result(tmp_path, _receipt("NOT_A_LANE", generation=9))
    assert exc.value.code == "SPECULATIVE_CERT_LANE_UNKNOWN"


def test_short_and_uppercase_sha_rejected(tmp_path: Path) -> None:
    with pytest.raises(SdkRuntimeError):
        seal_candidate(tmp_path, generation=1, head="abc", tree=TREE, base_main=MAIN)
    with pytest.raises(SdkRuntimeError):
        seal_candidate(
            tmp_path,
            generation=1,
            head=HEAD.upper(),
            tree=TREE,
            base_main=MAIN,
        )


def test_zero_generation_rejected(tmp_path: Path) -> None:
    with pytest.raises((SdkRuntimeError, ValueError)):
        seal_candidate(tmp_path, generation=0, head=HEAD, tree=TREE, base_main=MAIN)


def test_merge_grant_injection_stripped_on_load(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=10, head=HEAD, tree=TREE, base_main=MAIN)
    path = speculative_cert_dir(tmp_path) / "certification-barrier.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["merge_authorization"] = "GRANTED"
    path.write_text(json.dumps(data), encoding="utf-8")
    barrier = load_barrier(tmp_path)
    assert barrier.merge_authorization == "NOT_GRANTED"


def test_torn_json_rejected(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=11, head=HEAD, tree=TREE, base_main=MAIN)
    path = speculative_cert_dir(tmp_path) / "certification-barrier.json"
    path.write_text('{"schema_version": 1, "package_id":', encoding="utf-8")
    with pytest.raises(SdkRuntimeError) as exc:
        load_barrier(tmp_path)
    assert exc.value.code == "SPECULATIVE_CERT_TORN_WRITE"


def test_crash_recover_seal_without_barrier(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=12, head=HEAD, tree=TREE, base_main=MAIN)
    barrier_path = speculative_cert_dir(tmp_path) / "certification-barrier.json"
    barrier_path.unlink()
    barrier = load_barrier(tmp_path)
    assert barrier.state == CertificationState.CANDIDATE_SEALED
    assert set(barrier.lanes) == set(REQUIRED_LANES)
    assert all(r.result == LaneResult.PENDING for r in barrier.lanes.values())


def test_concurrent_all_lanes_no_lost_updates(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=13, head=HEAD, tree=TREE, base_main=MAIN)

    def _write(lane: str) -> str:
        record_lane_result(tmp_path, _receipt(lane, generation=13))
        return lane

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_write, lane) for lane in REQUIRED_LANES]
        done = {f.result() for f in as_completed(futures)}
    assert done == set(REQUIRED_LANES)
    barrier = evaluate_barrier(tmp_path)
    assert barrier.state == CertificationState.CERTIFIED
    assert all(r.result == LaneResult.PASS for r in barrier.lanes.values())


def test_concurrent_duplicate_same_lane(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=14, head=HEAD, tree=TREE, base_main=MAIN)
    errors: list[str] = []

    def _dup() -> None:
        try:
            record_lane_result(tmp_path, _receipt("CI", generation=14))
        except SdkRuntimeError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=_dup) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    barrier = load_barrier(tmp_path)
    assert barrier.lanes["CI"].result == LaneResult.PASS
    assert not errors or set(errors) <= {"SPECULATIVE_CERT_STALE_RECEIPT"}


def test_cancelled_cannot_become_certified(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=15, head=HEAD, tree=TREE, base_main=MAIN)
    cancel_for_tip_drift(tmp_path, live_head=OTHER, live_tree=TREE)
    with pytest.raises(SdkRuntimeError):
        record_lane_result(tmp_path, _receipt("CI", generation=15))
    barrier = evaluate_barrier(tmp_path)
    assert barrier.state == CertificationState.CANCELLED_TIP_DRIFT


def test_failed_cannot_become_certified_without_reseal(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=16, head=HEAD, tree=TREE, base_main=MAIN)
    for lane in REQUIRED_LANES:
        result = LaneResult.FAIL if lane == "IV" else LaneResult.PASS
        record_lane_result(tmp_path, _receipt(lane, generation=16, result=result))
    failed = evaluate_barrier(tmp_path)
    assert failed.state == CertificationState.BARRIER_FAILED
    again = evaluate_barrier(tmp_path)
    assert again.state == CertificationState.BARRIER_FAILED


def test_promotion_blocked_when_not_all_pass(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=17, head=HEAD, tree=TREE, base_main=MAIN)
    for lane in REQUIRED_LANES[:-1]:
        record_lane_result(tmp_path, _receipt(lane, generation=17))
    with pytest.raises(SdkRuntimeError) as exc:
        promote_exact_pin_evidence(tmp_path, live_head=HEAD, live_tree=TREE, live_main=MAIN)
    assert exc.value.code == "SPECULATIVE_CERT_NOT_CERTIFIED"


def test_wrong_tree_exact_head_rejected(tmp_path: Path) -> None:
    seal_candidate(tmp_path, generation=18, head=HEAD, tree=TREE, base_main=MAIN)
    with pytest.raises(SdkRuntimeError) as exc:
        record_lane_result(tmp_path, _receipt("CI", generation=18, tree=OTHER_TREE))
    assert exc.value.code == "SPECULATIVE_CERT_PIN_MISMATCH"


def test_package_cert_lanes_dogfood_path(tmp_path: Path) -> None:
    seal_candidate(
        tmp_path,
        generation=1,
        head=HEAD,
        tree=TREE,
        base_main=MAIN,
        required_lanes=PACKAGE_CERT_LANES,
    )
    receipts = _pass_all(tmp_path, generation=1, lanes=PACKAGE_CERT_LANES)
    barrier = evaluate_barrier(tmp_path)
    promoted = promote_exact_pin_evidence(tmp_path, live_head=HEAD, live_tree=TREE, live_main=MAIN)
    expect = oracle_evaluate(
        generation=1,
        head=HEAD,
        tree=TREE,
        base_main=MAIN,
        required_lanes=PACKAGE_CERT_LANES,
        receipts=receipts,
        live_head=HEAD,
        live_tree=TREE,
        live_main=MAIN,
        promote=True,
    )
    assert_oracle_parity(
        barrier_state=promoted.state,
        barrier_p0=promoted.new_p0,
        barrier_p1=promoted.new_p1,
        barrier_prev=promoted.previous_p1_reopened,
        barrier_promoted=promoted.exact_pin_evidence_promoted,
        barrier_frozen=promoted.certification_frozen,
        barrier_merge=promoted.merge_authorization,
        expectation=expect,
    )
    assert load_candidate_seal(tmp_path).required_lanes == PACKAGE_CERT_LANES
    assert barrier.state == CertificationState.CERTIFIED
