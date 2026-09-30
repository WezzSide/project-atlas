"""AS-ORCH-LEASE-RECOVERY-001 adversarial test matrix.

Real repo, real governor lease, real dispatch receipts (via the same
private helpers `test_orchestration_local_dispatch_port.py` already uses),
real `LoopState` persistence -- no mocking of the evidence gate itself.
Every refusal below was confirmed, during authorship, to genuinely fire
against the vulnerable precondition (the single condition each test
removes), matching this codebase's own IV discipline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.test_orchestration_local_dispatch_port import (
    _anchor,
    _governor,
    _make_repo,
    _node,
    _repo_main_tree,
)

from project_atlas.orchestration.autonomy.governor import AutonomousGovernor
from project_atlas.orchestration.autonomy.lease_projection import (
    RELATIVE_DEFAULT as LEASE_RELATIVE,
)
from project_atlas.orchestration.autonomy.lease_projection import load_projection
from project_atlas.orchestration.autonomy.lease_recovery import (
    LeaseRecoveryError,
    release_stalled_lease_after_exhausted_dispatch,
)
from project_atlas.orchestration.autonomy.local_dispatch_port import (
    LocalDispatchError,
    _dispatch_id_for,
    _write_receipt,
)
from project_atlas.orchestration.autonomy.loop import (
    STATE_DIR_RELATIVE,
    LoopPhase,
    LoopState,
    initial_loop_state,
    persist_loop_state,
    seal_loop_state,
)
from project_atlas.orchestration.autonomy.models import StopReason


def _loop_store(repo: Path) -> Path:
    return repo / STATE_DIR_RELATIVE


def _stopped_state(
    repo: Path,
    *,
    main: str,
    tree: str,
    active_lease_id: str | None,
    active_package_id: str | None,
    stop_reason: StopReason,
    phase: LoopPhase = LoopPhase.STOPPED,
) -> LoopState:
    base = initial_loop_state(_anchor(main, tree))
    updated = base.model_copy(
        update={
            "phase": phase,
            "sequence": 8,
            "ticks_in_invocation": 8,
            "active_lease_id": active_lease_id,
            "active_package_id": active_package_id,
            "stop_reason": stop_reason,
        }
    )
    sealed = seal_loop_state(updated)
    persist_loop_state(_loop_store(repo), sealed)
    return sealed


def _lease_and_receipts(
    repo: Path,
    main: str,
    tree: str,
    *,
    package_id: str = "PKG-RECOVERY-1",
    receipt_statuses: tuple[tuple[str, bool | None], ...] = (("FAILED", False),),
    receipt_lease_id_override: str | None = None,
) -> tuple[AutonomousGovernor, object]:
    node = _node(package_id, base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease(package_id, "governor-pilot-local", branch="b", worktree="w")
    for attempt, (status, authority_clean) in enumerate(receipt_statuses):
        dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:{attempt}"
        _write_receipt(
            repo,
            dispatch_id,
            {
                "dispatch_id": dispatch_id,
                "lease_id": receipt_lease_id_override or lease.lease_id,
                "package_id": package_id,
                "status": status,
                "authority_clean": authority_clean,
                "exit_code": 0,
                "timed_out": False,
            },
        )
    return gov, lease


# ---------------------------------------------------------------------------
# Positive: genuine RESOURCE_BOUNDARY stall, every receipt a real failure.
# ---------------------------------------------------------------------------


def test_releases_when_every_receipt_genuinely_failed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(
        repo,
        main,
        tree,
        receipt_statuses=(("FAILED", False), ("FAILED", False)),
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    result = release_stalled_lease_after_exhausted_dispatch(
        repo,
        lease_id=lease.lease_id,
        loop_store=_loop_store(repo),
        lease_projection_store=repo / LEASE_RELATIVE,
    )

    assert result["abandoned"] is True
    assert result["evidence_receipt_count"] == 2
    projection = load_projection(repo / LEASE_RELATIVE)
    row = next(r for r in projection.leases if r.lease_id == lease.lease_id)
    # ABANDONED, never RELEASED -- see module docstring: RELEASED is
    # reserved for a real success, and _originate()'s CERTIFIED-witness
    # set only ever looks at RELEASED rows.
    assert row.status == "ABANDONED"


# ---------------------------------------------------------------------------
# Adversarial: each refusal, one precondition removed at a time.
# ---------------------------------------------------------------------------


def test_refuses_when_loop_is_not_stopped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(repo, main, tree)
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
        phase=LoopPhase.LEASED,  # still live -- not this mechanism's business
    )

    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "LOOP_NOT_STOPPED"


@pytest.mark.parametrize(
    "stop_reason",
    [StopReason.OWNER_GATE, StopReason.HARD_BLOCKER, StopReason.SAFETY_BOUNDARY],
)
def test_refuses_for_any_stop_reason_other_than_resource_boundary(
    tmp_path: Path, stop_reason: StopReason
) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(repo, main, tree)
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=stop_reason,
    )

    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "NOT_RESOURCE_BOUNDARY_STOP"


def test_refuses_when_requested_lease_is_not_the_loops_own_stalled_lease(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(repo, main, tree)
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id="LEASE-SOME-OTHER-ONE",
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "LEASE_ID_MISMATCH"


def test_refuses_unknown_lease_id(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id="LEASE-DOES-NOT-EXIST",
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id="LEASE-DOES-NOT-EXIST",
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "LEASE_UNKNOWN"


def test_refuses_when_lease_already_released(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    gov, lease = _lease_and_receipts(repo, main, tree)
    gov.release_lease(lease.lease_id)  # the real, legitimate release path
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "LEASE_NOT_ACTIVE"


def test_refuses_with_zero_dispatch_evidence(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    # No receipt ever written for this lease.
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "NO_DISPATCH_EVIDENCE"


def test_refuses_a_receipt_that_does_not_self_consistently_name_this_lease(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(
        repo,
        main,
        tree,
        receipt_statuses=(("FAILED", False),),
        receipt_lease_id_override="LEASE-FORGED",
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    with pytest.raises(LocalDispatchError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "RECEIPT_LEASE_MISMATCH"


def test_refuses_when_any_receipt_is_a_genuine_authority_clean_completion(
    tmp_path: Path,
) -> None:
    """The core anti-fabrication property: a real success anywhere in the
    attempt history must never be discarded by this recovery path."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(
        repo,
        main,
        tree,
        receipt_statuses=(
            ("FAILED", False),
            ("COMPLETED", True),  # a genuine success buried among failures
            ("FAILED", False),
        ),
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "NOT_POSITIVELY_PROVEN_FAILED"

    # And the ledger genuinely was not touched.
    projection = load_projection(repo / LEASE_RELATIVE)
    row = next(r for r in projection.leases if r.lease_id == lease.lease_id)
    assert row.status == "ACTIVE"


def test_completed_without_authority_clean_true_is_not_hidden_success_but_still_denied(
    tmp_path: Path,
) -> None:
    """Correction of a prior misreading of the real incident shape: the
    actual INT-013 incident receipts were `status="FAILED"` (production's
    own `passed = exit_code == 0 and authority_clean` means a non-clean
    authority result is written as FAILED, never as COMPLETED -- see
    LocalDispatchReceiptStatus's own docstring). A receipt claiming
    `status="COMPLETED"` with `authority_clean=False` is therefore a
    shape real production never produces -- not a disguised failure, an
    anomalous/malformed one -- and owner-review-round-2's positive
    `status == FAILED` check correctly denies it regardless of
    authority_clean's value."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(
        repo,
        main,
        tree,
        receipt_statuses=(("FAILED", False),),
    )
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:1"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "COMPLETED",
            "authority_clean": False,
            "exit_code": 0,
            "timed_out": False,
        },
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "NOT_POSITIVELY_PROVEN_FAILED"


# ---------------------------------------------------------------------------
# The deep regression: an evidence-gated abandonment must not be
# misread by rehydration._originate()'s CERTIFIED-witness inference as
# proof of a real success. Real origination pipeline, real lease, real
# (planted) failed receipt, real fresh-governor rehydration.
# ---------------------------------------------------------------------------


def test_abandoned_lease_does_not_fabricate_a_certified_witness(tmp_path: Path) -> None:
    from tests.unit.test_orchestration_origination_rehydration import (
        _lease_origination_node,
        _make_trust_store,
        _run_git,
        _write_specified_project,
    )
    from tests.unit.test_orchestration_origination_rehydration import (
        _make_repo as _make_origin_repo,
    )

    from project_atlas.orchestration.autonomy.models import NodeState
    from project_atlas.orchestration.autonomy.rehydration import rehydrate_governor

    repo = _make_origin_repo(tmp_path)
    main = _run_git(repo, "rev-parse", "origin/main")
    tree = _run_git(repo, "rev-parse", "origin/main^{tree}")
    trust_store = _make_trust_store(tmp_path, main, tree)
    lease_store = tmp_path / "leases"
    loop_store = tmp_path / "loop"
    origination_store = tmp_path / "origination"
    project_root = tmp_path / "project"
    _write_specified_project(project_root)

    trusted, inventory, lease, package_id = _lease_origination_node(
        repo, trust_store, lease_store, origination_store, project_root
    )

    # Real receipt proving genuine, exhausted failure for this lease --
    # the exact shape of the real INT-013 incident (exit_code 0, but not
    # authority_clean).
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": package_id,
            "status": "FAILED",
            "authority_clean": False,
            "exit_code": 0,
            "timed_out": False,
        },
    )

    stopped = initial_loop_state(trusted).model_copy(
        update={
            "phase": LoopPhase.STOPPED,
            "active_package_id": package_id,
            "active_lease_id": lease.lease_id,
            "sequence": 8,
            "ticks_in_invocation": 8,
            "stop_reason": StopReason.RESOURCE_BOUNDARY,
        }
    )
    persist_loop_state(loop_store, seal_loop_state(stopped))

    result = release_stalled_lease_after_exhausted_dispatch(
        repo,
        lease_id=lease.lease_id,
        loop_store=loop_store,
        lease_projection_store=lease_store,
    )
    assert result["abandoned"] is True

    # Process C: a brand-new governor + a brand-new (never-before-seen)
    # loop session -- simulates exactly what the real M3 resume driver
    # does after an abandonment: start a fresh session and let discovery
    # run again.
    fresh = AutonomousGovernor(
        current_main=inventory.current_main,
        current_tree=inventory.current_tree,
        trusted_anchor=trusted,
        lease_projection_store=lease_store,
    )
    fresh_loop_store = tmp_path / "loop-session-2"
    rehydrate_governor(
        fresh,
        inventory=inventory,
        trusted=trusted,
        loop_store=fresh_loop_store,
        lease_projection_store=lease_store,
        origination_projection_store=origination_store,
    )

    node = next(item for item in fresh.snapshot().nodes if item.package_id == package_id)
    # The core assertion: NOT fabricated as CERTIFIED ...
    assert node.state != NodeState.CERTIFIED
    # ... but genuinely re-offered for a real retry.
    assert node.state == NodeState.READY
    assert fresh.snapshot().leases == ()


# ---------------------------------------------------------------------------
# Real call site: run_release_stalled_lease() (Codex's PR #672 finding --
# "the new precondition is never invoked by production code" -- applies
# equally here; this is the fix for that class of gap on this mechanism).
# ---------------------------------------------------------------------------


def test_cli_entrypoint_succeeds_with_real_evidence(tmp_path: Path) -> None:
    from project_atlas.orchestration.autonomy.cli import (
        EXIT_ERROR,
        EXIT_OK,
        run_release_stalled_lease,
    )

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(repo, main, tree)
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    report, exit_code = run_release_stalled_lease(root=repo, lease_id=lease.lease_id)
    assert exit_code == EXIT_OK
    assert report["abandoned"] is True
    assert report["merge_authorized"] is False
    assert report["execution_authorized"] is False

    # Same evidence gate, real CLI path: a second call against the now-
    # ABANDONED lease refuses rather than silently no-op'ing.
    report2, exit_code2 = run_release_stalled_lease(root=repo, lease_id=lease.lease_id)
    assert exit_code2 == EXIT_ERROR
    assert report2["blocker"] == "LEASE_NOT_ACTIVE"


def test_cli_entrypoint_refuses_without_real_evidence(tmp_path: Path) -> None:
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_release_stalled_lease

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    report, exit_code = run_release_stalled_lease(root=repo, lease_id=lease.lease_id)
    assert exit_code == EXIT_ERROR
    assert report["blocker"] == "NO_DISPATCH_EVIDENCE"
    assert report["abandoned"] is False


# ---------------------------------------------------------------------------
# IV round 2 (independent verification, fresh subagent) regression tests --
# 3 confirmed findings, each proven against the vulnerable code before the
# fix (see the reverts below), fixed, and locked in here.
# ---------------------------------------------------------------------------


def test_reap_orphaned_lease_releases_does_not_flip_abandoned_to_released(
    tmp_path: Path,
) -> None:
    """IV finding 1 (MEDIUM): reap_orphaned_lease_releases()'s skip guard
    used to check only `!= "RELEASED"`, so an ABANDONED row whose
    lease_id happened to appear in completed_lease_ids would be silently
    reconstructed and passed to project_release() -- flipping it
    ABANDONED -> RELEASED, exactly the CERTIFIED-witness fabrication this
    whole mechanism exists to prevent. `!= "ACTIVE"` closes it."""
    from project_atlas.orchestration.autonomy.lease_projection import (
        reap_orphaned_lease_releases,
    )

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(repo, main, tree)
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    result = release_stalled_lease_after_exhausted_dispatch(
        repo,
        lease_id=lease.lease_id,
        loop_store=_loop_store(repo),
        lease_projection_store=repo / LEASE_RELATIVE,
    )
    assert result["abandoned"] is True

    # A row genuinely ABANDONED must never be touched by the reaper, no
    # matter what completed_lease_ids claims.
    reaped = reap_orphaned_lease_releases(repo / LEASE_RELATIVE, (lease.lease_id,))
    assert reaped == ()
    projection = load_projection(repo / LEASE_RELATIVE)
    row = next(r for r in projection.leases if r.lease_id == lease.lease_id)
    assert row.status == "ABANDONED"


def test_list_dispatch_receipts_does_not_hide_a_later_receipt_past_a_gap(
    tmp_path: Path,
) -> None:
    """IV finding 2 (MEDIUM): list_dispatch_receipts() used to scan
    attempts 0,1,2,... and stop at the first missing index -- an
    intermediate receipt lost/deleted would silently hide every later
    receipt, including a genuine success, from the evidence gate."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import (
        list_dispatch_receipts,
    )

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")

    # Attempt 0: FAILED. Attempt 1: deliberately never written (the gap).
    # Attempt 2: a genuine, authority-clean success.
    dispatch_id_0 = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id_0,
        {
            "dispatch_id": dispatch_id_0,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "FAILED",
            "authority_clean": False,
        },
    )
    dispatch_id_2 = f"{_dispatch_id_for(lease.lease_id)}:2"
    _write_receipt(
        repo,
        dispatch_id_2,
        {
            "dispatch_id": dispatch_id_2,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "COMPLETED",
            "authority_clean": True,
        },
    )

    receipts = list_dispatch_receipts(repo, lease_id=lease.lease_id)
    dispatch_ids = {r.dispatch_id for r in receipts}
    assert dispatch_ids == {dispatch_id_0, dispatch_id_2}

    # And the evidence gate genuinely refuses, because it now sees the
    # real success past the gap.
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    with pytest.raises(LeaseRecoveryError) as exc:
        release_stalled_lease_after_exhausted_dispatch(
            repo,
            lease_id=lease.lease_id,
            loop_store=_loop_store(repo),
            lease_projection_store=repo / LEASE_RELATIVE,
        )
    assert exc.value.code == "NOT_POSITIVELY_PROVEN_FAILED"


def test_cli_entrypoint_fails_closed_on_a_corrupt_receipt(tmp_path: Path) -> None:
    """IV finding 3 (MEDIUM): run_release_stalled_lease()'s except tuple
    omitted LocalDispatchError, so a corrupt/tampered receipt (which
    list_dispatch_receipts() correctly fails closed on, via
    _read_receipt()) crashed the CLI entrypoint uncaught instead of
    returning the designed (report, EXIT_ERROR)."""
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_release_stalled_lease
    from project_atlas.orchestration.autonomy.local_dispatch_port import (
        RECEIPTS_RELATIVE,
        _receipt_filename,
    )

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    receipt_path = repo / RECEIPTS_RELATIVE / _receipt_filename(dispatch_id)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("{not valid json", encoding="utf-8")

    report, exit_code = run_release_stalled_lease(root=repo, lease_id=lease.lease_id)
    assert exit_code == EXIT_ERROR
    assert report["blocker"] == "CORRUPT_RECEIPT"
    assert report["abandoned"] is False


# ---------------------------------------------------------------------------
# Owner review round 2 (2026-09-02) -- D-ATLAS-M3-OWNER-REVIEW-LEASE-
# RECOVERY-HARDENING-AND-INTEGRATION, receipt-semantics matrix A-H.
#
# The prior evidence gate asked only "does no receipt positively look
# like a hidden success?" -- a missing/RUNNING/unrecognized status, or a
# tampered non-bool authority_clean, all silently passed that check as
# "not a hidden success", which is not the same claim as "positive proof
# of a real, terminal failure". Every case below is a real, constructed
# receipt reaching the CLI entrypoint end to end; each must produce
# ABANDONED = FALSE and a stable, fail-closed error -- never a crash,
# never a silent accept.
# ---------------------------------------------------------------------------


def _assert_cli_denies(repo: Path, lease: object, *, expected_blocker: str) -> None:
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_release_stalled_lease

    report, exit_code = run_release_stalled_lease(root=repo, lease_id=lease.lease_id)  # type: ignore[attr-defined]
    assert exit_code == EXIT_ERROR
    assert report["blocker"] == expected_blocker
    assert report["abandoned"] is False
    # And the ledger genuinely was not touched -- every denial in this
    # matrix must be a true no-op on durable state.
    projection = load_projection(repo / LEASE_RELATIVE)
    row = next(r for r in projection.leases if r.lease_id == lease.lease_id)  # type: ignore[attr-defined]
    assert row.status == "ACTIVE"


def test_matrix_a_missing_status_field_is_denied(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            # "status" deliberately omitted.
            "authority_clean": False,
        },
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="MALFORMED_RECEIPT")


def test_matrix_b_running_status_is_denied(tmp_path: Path) -> None:
    """Real, well-formed production shape (dispatch_once()'s own
    pre-flight receipt) -- genuinely unresolved, never failure evidence."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(repo, main, tree, receipt_statuses=(("RUNNING", None),))
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="NOT_POSITIVELY_PROVEN_FAILED")


def test_matrix_c_unknown_status_string_is_denied(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "ABORTED",  # not one of RUNNING/COMPLETED/FAILED
            "authority_clean": False,
        },
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="MALFORMED_RECEIPT")


def test_matrix_d1_failed_status_with_missing_authority_clean_is_accepted(
    tmp_path: Path,
) -> None:
    """The REAL exception-path production shape (dispatch_once()'s own
    `except Exception` branch, before any LocalExecutionResult exists):
    `{dispatch_id, lease_id, package_id, status="FAILED", error=...}`,
    with no `authority_clean` field at all. This must NOT be denied --
    requiring a field production itself never writes for this real
    failure mode would make that entire failure class permanently
    unrecoverable through this mechanism."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "FAILED",
            "error": "worktree creation failed: <real exception text>",
            # authority_clean deliberately absent -- matches production.
        },
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )

    result = release_stalled_lease_after_exhausted_dispatch(
        repo,
        lease_id=lease.lease_id,
        loop_store=_loop_store(repo),
        lease_projection_store=repo / LEASE_RELATIVE,
    )
    assert result["abandoned"] is True


def test_matrix_d2_completed_status_with_missing_authority_clean_is_denied(
    tmp_path: Path,
) -> None:
    """Not a real production shape (COMPLETED always carries a real
    authority_clean -- see LocalDispatchReceiptStatus's docstring), and
    denied anyway since status != FAILED."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "COMPLETED",
            # authority_clean deliberately absent.
        },
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="NOT_POSITIVELY_PROVEN_FAILED")


@pytest.mark.parametrize("bad_authority_clean", ["false", "true", 0, 1, "FAILED", []])
def test_matrix_e_non_bool_authority_clean_is_denied(
    tmp_path: Path, bad_authority_clean: object
) -> None:
    """A tampered/corrupt authority_clean (string, int, list -- anything
    that is not a real bool) must fail closed even when status is
    otherwise the accepted FAILED value -- pydantic's default coercion
    (e.g. "true"/1 -> True) is exactly what StrictBool exists to refuse."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "FAILED",
            "authority_clean": bad_authority_clean,
        },
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="MALFORMED_RECEIPT")


def test_matrix_f_receipt_dispatch_id_disagrees_with_its_own_durable_slot(
    tmp_path: Path,
) -> None:
    """The receipt at attempt-0's file slot internally claims to be
    attempt 5 -- a receipt cannot be trusted about which attempt it
    actually is when its own identity disagrees with where it was
    found."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    real_slot_dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    forged_dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:5"
    _write_receipt(
        repo,
        real_slot_dispatch_id,  # written to slot 0's real filename
        {
            "dispatch_id": forged_dispatch_id,  # but claims to be slot 5
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "FAILED",
            "authority_clean": False,
        },
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="RECEIPT_SLOT_IDENTITY_MISMATCH")


def test_matrix_g_receipt_package_id_disagrees_with_the_projected_lease(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PKG-SOME-OTHER-PACKAGE",  # disagrees with the lease row
            "status": "FAILED",
            "authority_clean": False,
        },
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="RECEIPT_PACKAGE_MISMATCH")


def test_matrix_h_receipt_lease_id_disagrees_with_the_requested_lease(
    tmp_path: Path,
) -> None:
    """H, retained from IV round 2 -- now enforced centrally in
    list_dispatch_receipts() rather than lease_recovery.py's own loop."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    _gov, lease = _lease_and_receipts(
        repo,
        main,
        tree,
        receipt_statuses=(("FAILED", False),),
        receipt_lease_id_override="LEASE-FORGED",
    )
    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="RECEIPT_LEASE_MISMATCH")


# ---------------------------------------------------------------------------
# GitHub Copilot review finding, PR #673 (against pre-round-2 head
# b75f8234): "receipt enumeration can silently drop evidence on
# duplicate/non-canonical attempt filenames." The directory-listing fix
# that closed the earlier "stops at first gap" bug (IV round 2) used a
# plain dict keyed by `int(attempt_number)` -- two different real files
# (a duplicate, or a zero-padded name) that parse to the same integer
# would silently collapse, with `iterdir()`'s non-guaranteed order
# deciding (non-deterministically) which one's evidence survived.
# ---------------------------------------------------------------------------


def _write_raw_receipt_file(repo: Path, filename: str, payload: str) -> None:
    from project_atlas.orchestration.autonomy.local_dispatch_port import RECEIPTS_RELATIVE

    receipts_dir = repo / RECEIPTS_RELATIVE
    receipts_dir.mkdir(parents=True, exist_ok=True)
    (receipts_dir / filename).write_text(payload, encoding="utf-8")


def test_noncanonical_attempt_filename_colliding_with_a_real_one_is_denied(
    tmp_path: Path,
) -> None:
    """A zero-padded sibling naming the SAME attempt as a real, canonical
    receipt -- if this silently "won", its content would be irrelevant;
    the point is the collision itself must be refused, not resolved by
    picking one arbitrarily."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import _safe_token

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")

    # The real, canonical attempt-0 receipt: a genuine FAILED outcome.
    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "FAILED",
            "authority_clean": False,
        },
    )
    # A non-canonical, zero-padded sibling naming the SAME attempt (0),
    # planted directly on disk (never something the real writer
    # produces).
    zero_padded_name = _safe_token(f"{_dispatch_id_for(lease.lease_id)}:00") + ".json"
    _write_raw_receipt_file(
        repo,
        zero_padded_name,
        '{"dispatch_id": "irrelevant", "lease_id": "irrelevant", '
        '"package_id": "irrelevant", "status": "COMPLETED", "authority_clean": true}',
    )

    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    # Caught as non-canonical before the (now-unreachable-in-practice,
    # kept as a backstop) pure-duplicate check ever gets a chance to fire.
    _assert_cli_denies(repo, lease, expected_blocker="NONCANONICAL_ATTEMPT_FILENAME")


def test_lone_noncanonical_attempt_filename_is_denied_not_silently_dropped(
    tmp_path: Path,
) -> None:
    """delta IV round 2 (MEDIUM): a non-canonical file naming an attempt
    number no OTHER file claims used to be silently excluded (empty
    result, no error) rather than raising -- a real liveness gap even
    though it happened to still fail closed one layer up
    (NO_DISPATCH_EVIDENCE). Now denied explicitly, with a clear error
    naming the exact malformed file."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import _safe_token

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")

    # ONLY a non-canonical attempt-0 file exists -- no canonical sibling,
    # no collision, nothing else on disk for this lease at all.
    zero_padded_name = _safe_token(f"{_dispatch_id_for(lease.lease_id)}:00") + ".json"
    _write_raw_receipt_file(
        repo,
        zero_padded_name,
        '{"dispatch_id": "irrelevant", "lease_id": "irrelevant", '
        '"package_id": "irrelevant", "status": "FAILED", "authority_clean": false}',
    )

    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="NONCANONICAL_ATTEMPT_FILENAME")


def test_hidden_success_under_a_noncanonical_unclaimed_attempt_is_denied(
    tmp_path: Path,
) -> None:
    """delta IV round 2 (HIGH), the critical end-to-end scenario: a real
    canonical FAILED receipt at attempt 0, plus a genuine, authority-
    clean COMPLETED receipt stored ONLY under a non-canonical filename
    naming a DIFFERENT, otherwise-unclaimed attempt (1). No collision
    ever fires (different integers) -- the first fix's collision-only
    check let this through, and the real CLI genuinely abandoned the
    lease (exit_code=0, abandoned=True) despite the hidden real success.
    This is exactly the fabrication AS-ORCH-LEASE-RECOVERY-001 exists to
    prevent, reachable through a non-canonical filename alone."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import _safe_token

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PKG-RECOVERY-1", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PKG-RECOVERY-1", "governor-pilot-local", branch="b", worktree="w")

    dispatch_id_0 = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id_0,
        {
            "dispatch_id": dispatch_id_0,
            "lease_id": lease.lease_id,
            "package_id": "PKG-RECOVERY-1",
            "status": "FAILED",
            "authority_clean": False,
        },
    )
    # A genuine success, at attempt 1 -- but stored ONLY under a
    # non-canonical, zero-padded filename. No file named the canonical
    # "..._1.json" exists at all.
    noncanonical_attempt_1 = _safe_token(f"{_dispatch_id_for(lease.lease_id)}:01") + ".json"
    _write_raw_receipt_file(
        repo,
        noncanonical_attempt_1,
        f'{{"dispatch_id": "{_dispatch_id_for(lease.lease_id)}:1", '
        f'"lease_id": "{lease.lease_id}", "package_id": "PKG-RECOVERY-1", '
        '"status": "COMPLETED", "authority_clean": true}',
    )

    _stopped_state(
        repo,
        main=main,
        tree=tree,
        active_lease_id=lease.lease_id,
        active_package_id="PKG-RECOVERY-1",
        stop_reason=StopReason.RESOURCE_BOUNDARY,
    )
    _assert_cli_denies(repo, lease, expected_blocker="NONCANONICAL_ATTEMPT_FILENAME")
