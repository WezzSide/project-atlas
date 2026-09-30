"""AS-ORCH-001E persistent loop matrices and adversarial cases."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from project_atlas.orchestration.autonomy.continuation_broker import (
    SuccessorKind,
    recover_broker,
)
from project_atlas.orchestration.autonomy.evidence import hash_payload
from project_atlas.orchestration.autonomy.governor import AutonomousGovernor
from project_atlas.orchestration.autonomy.loop import (
    LOOP_PACKAGE_ID,
    MAX_TICKS_PER_INVOCATION,
    AutonomousLoop,
    CallableDispatchPort,
    LoopError,
    LoopPhase,
    LoopState,
    initial_loop_state,
    load_loop_state,
    persist_loop_state,
    seal_loop_state,
    verify_loop_state,
)
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    AdvancementReason,
    AgentCapability,
    ExecutionHostClass,
    IvRequirements,
    MutationSurface,
    NodeState,
    OwnerGateKind,
    RetryPolicy,
    RiskTag,
    StopReason,
    TrustedAnchorRecord,
    WorkNode,
)
from project_atlas.orchestration.autonomy.owner_gates import OwnerGateError
from project_atlas.orchestration.autonomy.trust import seal_anchor
from project_atlas.schema import available_schemas, validate_record

PIN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TREE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _anchor() -> TrustedAnchorRecord:
    pred = "1111111111111111111111111111111111111111"
    cert = "3333333333333333333333333333333333333333"
    return seal_anchor(
        TrustedAnchorRecord(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=PIN,
            trusted_tree=TREE,
            predecessor_main=pred,
            predecessor_tree="2222222222222222222222222222222222222222",
            advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
            source_package="AS-ORCH-001D",
            source_directive="D-AS-ORCH-001D-OWNER-MERGE-010",
            source_pr=400,
            merge_commit=PIN,
            merge_parent_1=pred,
            merge_parent_2=cert,
            merge_tree=TREE,
            certified_head=cert,
            certified_tree=TREE,
            certification_status="CERTIFIED",
            independent_verification_status="PASS",
            post_merge_seal="PASS",
            post_merge_ci="PASS",
            evidence_reference="tests/unit/loop-anchor.json",
            evidence_digest="aa" * 32,
            sequence=3,
            record_digest="00" * 32,
        )
    )


def _node(
    package_id: str,
    *,
    state: NodeState = NodeState.READY,
    host: ExecutionHostClass = ExecutionHostClass.IN_PROCESS,
    owner_gate: OwnerGateKind | None = None,
    deps: tuple[str, ...] = (),
    surface: str = "loop-surface",
) -> WorkNode:
    return WorkNode(
        package_id=package_id,
        objective="001e test node",
        base_pin=PIN,
        dependencies=deps,
        mutation_surface=MutationSurface(
            surface_id=surface,
            paths=("src/project_atlas/orchestration/autonomy",),
            semantic="ORCHESTRATION_AUTONOMY_LOOP",
        ),
        execution_host_class=host,
        agent_capabilities_required=(AgentCapability.IMPLEMENT,),
        acceptance_criteria=("PASS",),
        iv_requirements=IvRequirements(certification_required=True, adversarial_required=True),
        owner_gate=owner_gate,
        state=state,
        risk_tags=(RiskTag.CONTROL_PLANE, RiskTag.HIGH_BLAST_RADIUS),
    )


def _governor(*nodes: WorkNode) -> AutonomousGovernor:
    gov = AutonomousGovernor(current_main=PIN, current_tree=TREE, trusted_anchor=_anchor())
    for node in nodes:
        gov.add_node(node)
    return gov


def _loop(
    tmp_path: Path,
    governor: AutonomousGovernor,
    dispatch: CallableDispatchPort | None = None,
) -> AutonomousLoop:
    return AutonomousLoop(
        governor=governor,
        trusted=_anchor(),
        store=tmp_path / "loop-store",
        root=tmp_path,
        dispatch=dispatch,
    )


def test_schema_registered() -> None:
    assert "autonomy-loop-state" in available_schemas()
    validate_record(initial_loop_state(_anchor()).model_dump(mode="json"), "autonomy-loop-state")


def test_in_process_ready_completes_and_stops_without_owner(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-NEXT-001"))
    loop = _loop(tmp_path, gov)
    result = loop.run_until_stop()
    assert result.phase is LoopPhase.STOPPED
    assert result.stop_reason is StopReason.NO_ELIGIBLE_WORK
    assert result.merge_authorized is False
    assert result.authority_granted is False
    node = next(item for item in gov.snapshot().nodes if item.package_id == "AS-ORCH-NEXT-001")
    assert node.state in {NodeState.CERTIFIED, NodeState.OWNER_HELD, NodeState.CLOSED}


def test_in_process_recovery_within_same_process_after_partial_execution(
    tmp_path: Path,
) -> None:
    """Pre-existing recovery defect (found during PR #635 independent IV,
    confirmed pre-existing via a control test, not introduced by that PR):
    a crash between `governor.execute_leased()` succeeding (node -> ACTIVE)
    and `apply_observed_result()` running (which is what would move the
    loop's own phase away from LEASED) leaves the loop's persisted state at
    `phase=LEASED` with a node that is no longer `LEASED`. On restart,
    `recover()`'s LEASED branch used to call `execute_leased()` again
    unconditionally, attempting an illegal `ACTIVE -> ACTIVE` transition
    that raised `IllegalTransitionError` uncaught -- escaping `cli.py`'s
    handler (which only catches `TrustError`/`DiscoveryError`/`LoopError`)
    and permanently re-crashing on every subsequent tick.

    Scope note (PR #637 review thread PRRT_kwDOTtguR86dRAuB): this test
    reuses the *same* in-memory `AutonomousGovernor` object for both the
    "before crash" and "recovery" calls, so it documents and exercises the
    in-process/same-Python-object recovery contract (e.g. a caller that
    catches an exception mid-tick and retries `recover()` without the OS
    process actually restarting) -- not a real cross-process restart. A
    real restart is covered separately by
    `test_in_process_recovery_after_real_process_restart_fails_closed`
    below, which constructs a second, fully independent governor instance
    with empty node state, exactly like `run_governor_loop_tick()` (cli.py)
    does on every invocation.
    """
    gov = _governor(_node("AS-ORCH-CRASH-001"))
    loop = _loop(tmp_path, gov)
    # Lease directly through the governor (the same call _select_and_lease()
    # makes) and persist the matching loop state by hand, mirroring exactly
    # what _select_and_lease() itself saves just before calling
    # _dispatch_leased() -- this does not go through the loop's own
    # _select_and_lease()/_dispatch_leased() machinery at all.
    lease = gov.lease(
        "AS-ORCH-CRASH-001", loop._first_agent(), branch=loop._branch, worktree=loop._worktree
    )
    loop._save(
        phase=LoopPhase.LEASED,
        active_package_id="AS-ORCH-CRASH-001",
        active_lease_id=lease.lease_id,
    )
    # Simulate "the crash happened right after execute_leased() succeeded,
    # before apply_observed_result() ran" -- call it directly, exactly what
    # _dispatch_leased()'s IN_PROCESS branch does as its first step, without
    # the loop's own state file ever finding out.
    gov.execute_leased(lease.lease_id)
    node_state = next(
        item for item in gov.snapshot().nodes if item.package_id == "AS-ORCH-CRASH-001"
    ).state
    assert node_state is NodeState.ACTIVE  # confirms the precise crash window
    assert loop.state.phase is LoopPhase.LEASED  # loop is unaware

    # A fresh process would call exactly this on restart.
    result = loop.recover()  # must not raise IllegalTransitionError
    assert result.phase is not LoopPhase.FAILED_CLOSED
    node = next(item for item in gov.snapshot().nodes if item.package_id == "AS-ORCH-CRASH-001")
    assert node.state in {NodeState.CERTIFIED, NodeState.OWNER_HELD, NodeState.CLOSED}


def test_in_process_recovery_fails_closed_on_unexpected_node_state(tmp_path: Path) -> None:
    """Independent-IV finding: `node.state != LEASED` alone does not mean
    "safe to finalize" -- `apply_observed_result()` itself unconditionally
    attempts `transition(..., VERIFYING, ...)`, which is only legal from
    `ACTIVE`. A node in some other unexpected state (e.g. `BLOCKED`) would
    reintroduce an uncaught `IllegalTransitionError` one level down --
    exactly the crash loop this whole fix exists to close. Must fail
    closed with a clear diagnostic instead.
    """
    gov = _governor(_node("AS-ORCH-CRASH-002"))
    loop = _loop(tmp_path, gov)
    lease = gov.lease(
        "AS-ORCH-CRASH-002", loop._first_agent(), branch=loop._branch, worktree=loop._worktree
    )
    loop._save(
        phase=LoopPhase.LEASED,
        active_package_id="AS-ORCH-CRASH-002",
        active_lease_id=lease.lease_id,
    )
    # Force the node into some other, unexpected state (LEASED -> BLOCKED
    # is itself a legal transition, e.g. a concurrent hard-blocker) while
    # the loop's own state still says LEASED.
    gov.transition("AS-ORCH-CRASH-002", NodeState.BLOCKED, "test-unexpected-state")

    with pytest.raises(LoopError) as exc:
        loop.recover()
    assert exc.value.code == "EXECUTION_STATE_CONFLICT"
    assert loop.state.phase is LoopPhase.FAILED_CLOSED
    node = next(item for item in gov.snapshot().nodes if item.package_id == "AS-ORCH-CRASH-002")
    assert node.state is NodeState.BLOCKED  # untouched, not corrupted by a partial transition


def test_in_process_recovery_after_real_process_restart_fails_closed(tmp_path: Path) -> None:
    """D-203 round 3 (PR #637 review thread PRRT_kwDOTtguR86dRAuB,
    chatgpt-codex-connector): the two tests above reuse the *same*
    in-memory `AutonomousGovernor` Python object across the "before crash"
    and "recovery" calls, so neither actually exercises what a real
    process restart does. The real CLI entry point
    (`run_governor_loop_tick()` in `orchestration/autonomy/cli.py`)
    constructs a brand-new `AutonomousGovernor` from live inventory alone
    on *every* invocation -- its node/lease list starts empty -- and only
    the loop's own `LoopState` (phase, active_package_id, active_lease_id)
    survives on disk between processes.

    This test simulates that honestly: two fully independent
    `AutonomousGovernor` instances (no shared Python object at all), with
    the second `AutonomousLoop` built only from what the first one
    persisted to `store` on disk -- exactly the "process N" / "process
    N+1" boundary `run_governor_loop_tick()` crosses on a real restart.

    Before the D-203-round-3 fix, `_dispatch_leased()`'s node lookup was a
    bare ``next(item for item in governor.snapshot().nodes if ...)`` with
    no default, which raised an uncaught `StopIteration` against process
    N+1's empty node list -- not a `LoopError`, so `cli.py`'s
    ``except (TrustError, DiscoveryError, LoopError)`` never catches it and
    it escapes as an unstructured crash on every subsequent tick. Full
    governor node/lease rehydration across a real process restart is
    intentionally out of scope for this PR (it is ORCH001E-011's own,
    separately tracked fix); until that lands, the loop must fail closed
    with a structured, CLI-catchable error instead of crashing uncaught.
    """
    package_id = "AS-ORCH-RESTART-001"

    # "Process N": lease the node, then simulate a crash in the exact
    # window D-203 documents -- execute_leased() succeeded (node -> ACTIVE)
    # but apply_observed_result() never ran, so the on-disk loop state is
    # still LEASED. Process N never calls recover() itself: a real crash
    # means it never gets the chance to.
    gov_before_restart = _governor(_node(package_id))
    loop_before_restart = _loop(tmp_path, gov_before_restart)
    lease = gov_before_restart.lease(
        package_id,
        loop_before_restart._first_agent(),
        branch=loop_before_restart._branch,
        worktree=loop_before_restart._worktree,
    )
    loop_before_restart._save(
        phase=LoopPhase.LEASED,
        active_package_id=package_id,
        active_lease_id=lease.lease_id,
    )
    gov_before_restart.execute_leased(lease.lease_id)
    assert loop_before_restart.state.phase is LoopPhase.LEASED  # loop file unaware of the crash

    # "Process N+1": a brand-new AutonomousGovernor with no nodes at all
    # (exactly what run_governor_loop_tick() constructs), and a new
    # AutonomousLoop pointed at the *same on-disk store path* -- it only
    # ever reads the persisted LoopState, never the governor object above.
    gov_after_restart = _governor()  # no add_node() calls: an empty, fresh governor
    assert gov_after_restart.snapshot().nodes == ()  # confirms this really is a fresh governor
    loop_after_restart = _loop(tmp_path, gov_after_restart)
    assert loop_after_restart.state.phase is LoopPhase.LEASED  # reloaded from disk
    assert loop_after_restart.state.active_package_id == package_id

    with pytest.raises(LoopError) as exc:
        loop_after_restart.recover()
    assert exc.value.code == "GOVERNOR_STATE_NOT_REHYDRATED"
    assert loop_after_restart.state.phase is LoopPhase.FAILED_CLOSED


def test_owner_gate_stop_no_dispatch(tmp_path: Path) -> None:
    gov = _governor(
        _node(
            "AS-ORCH-OWN-001",
            state=NodeState.OWNER_HELD,
            owner_gate=OwnerGateKind.A_PROTECTED_MAIN_MERGE,
        )
    )
    calls: list[str] = []
    port = CallableDispatchPort(
        lambda _root: calls.append("dispatch") or {"dispatch_id": "x", "status": "COMPLETED"}
    )
    loop = _loop(tmp_path, gov, port)
    result = loop.run_until_stop()
    assert result.stop_reason is StopReason.OWNER_GATE
    assert calls == []
    assert result.dispatched is False


def test_merge_eligible_never_dispatched(tmp_path: Path) -> None:
    gov = _governor(
        _node(
            "AS-ORCH-MER-001",
            state=NodeState.MERGE_ELIGIBLE,
            owner_gate=OwnerGateKind.A_PROTECTED_MAIN_MERGE,
        )
    )
    loop = _loop(tmp_path, gov)
    result = loop.run_until_stop()
    assert result.stop_reason is StopReason.OWNER_GATE
    with pytest.raises(OwnerGateError):
        loop.refuse_owner_actions()


def test_refuse_owner_actions_checks_every_gate_not_just_a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ORCHAUT-010 (2026-08-28): ``refuse_owner_actions`` previously called
    ``require_owner`` for gate A only, while its own docstring and the
    module's ``LOOP_CAN_BYPASS_OWNER_GATE = NO`` contract claimed the
    general property for all six gates. Because the real (unpatched)
    ``require_owner`` always raises with no grant, a plain
    ``pytest.raises(OwnerGateError)`` on the whole call can't distinguish
    "checked gate A only, then stopped" from "checked all six" -- both
    look identical from outside. Patches ``require_owner`` in the loop
    module's own namespace with a non-raising recorder to observe every
    gate the method actually attempts, proving it doesn't short-circuit
    after A."""
    seen: list[OwnerGateKind] = []

    def _record(gate: OwnerGateKind, *, owner_grant: bool = False) -> None:
        assert owner_grant is False
        seen.append(gate)

    import project_atlas.orchestration.autonomy.loop as loop_module

    monkeypatch.setattr(loop_module, "require_owner", _record)

    gov = _governor(_node("AS-ORCH-REFUSE-001"))
    loop = _loop(tmp_path, gov)
    loop.refuse_owner_actions()

    assert seen == list(OwnerGateKind)


@pytest.mark.parametrize(
    "gate",
    [
        OwnerGateKind.C_CERTIFIED_OBJECT_MUTATION,
        OwnerGateKind.D_SECURITY_GOVERNANCE_POLICY,
        OwnerGateKind.E_DESTRUCTIVE_OPS,
        OwnerGateKind.F_MATERIAL_EXTERNAL_SPEND,
    ],
)
def test_ready_owner_gated_node_never_leased_or_dispatched(
    tmp_path: Path, gate: OwnerGateKind
) -> None:
    """ORCHAUT-010 regression: gates C-F must fail closed at the real
    select/lease boundary, not just via the standalone, never-called
    `request_*` primitives. A node reaching READY while tagged with an
    owner_gate must stop the loop before any lease or dispatch, exactly
    like OWNER_HELD/MERGE_ELIGIBLE already does for gate A.
    """
    gov = _governor(_node("AS-ORCH-GATE-001", state=NodeState.READY, owner_gate=gate))
    calls: list[str] = []
    port = CallableDispatchPort(
        lambda _root: calls.append("dispatch") or {"dispatch_id": "x", "status": "COMPLETED"}
    )
    loop = _loop(tmp_path, gov, port)
    result = loop.run_until_stop()
    assert result.stop_reason is StopReason.OWNER_GATE
    assert calls == []
    assert result.dispatched is False
    node = next(item for item in gov.snapshot().nodes if item.package_id == "AS-ORCH-GATE-001")
    assert node.state is NodeState.READY


def test_hard_blocker_stop(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-BLK-001", state=NodeState.BLOCKED))
    result = _loop(tmp_path, gov).run_until_stop()
    assert result.stop_reason is StopReason.HARD_BLOCKER


def test_no_eligible_work_resume_does_not_ignore_hard_blockers(tmp_path: Path) -> None:
    """Cursor Bugbot finding on PR #654 (Medium): ``_may_resume_from_no_
    eligible_work()`` used to call ``select_next()`` without
    ``hard_blockers=``, unlike ``_select_and_lease()``. Once ANY durable
    hard blocker is recorded on the governor (here: a separate node's
    remediation-exhausted verification failure -- ``select_next()``
    treats ``hard_blockers`` as a global kill switch, not per-package), a
    second, genuinely READY node looked "resumable" to the stale check,
    flipping STOPPED -> IDLE, and the very same tick's
    ``_select_and_lease()`` would immediately re-stop at HARD_BLOCKER -- a
    stop this method never resumes from, permanently wedging the loop
    even after the blocker is later addressed. The fix reuses the exact
    same ``hard_blockers``-aware eligibility check as the real lease path,
    so the loop correctly stays at the recoverable NO_ELIGIBLE_WORK stop
    instead of self-inflicting a permanent one.
    """
    blocked_id = "AS-ORCH-RESUME-BLOCKED"
    resumable_id = "AS-ORCH-RESUME-READY"
    blocked_node = _node(blocked_id).model_copy(update={"retry_policy": RetryPolicy(cycles_used=3)})
    gov = _governor(blocked_node, _node(resumable_id, surface="loop-surface-2"))
    loop = _loop(tmp_path, gov)
    lease = gov.lease(blocked_id, loop._first_agent(), branch=loop._branch, worktree=loop._worktree)
    gov.execute_leased(lease.lease_id)
    gov.transition(blocked_id, NodeState.VERIFYING, "test-exhaust-remediation")
    gov.complete_verification(blocked_id, passed=False)
    assert gov.snapshot().hard_blockers  # sanity: a durable hard blocker now exists
    resumable_node = next(n for n in gov.snapshot().nodes if n.package_id == resumable_id)
    assert resumable_node.state is NodeState.READY  # sanity: genuinely still selectable

    loop._save(phase=LoopPhase.STOPPED, stop_reason=StopReason.NO_ELIGIBLE_WORK)
    result = loop.tick()

    # Must NOT have resumed into an immediate re-block: still the original,
    # recoverable stop -- not a fresh, permanent HARD_BLOCKER.
    assert result.phase is LoopPhase.STOPPED
    assert result.stop_reason is StopReason.NO_ELIGIBLE_WORK


def test_lease_projection_lock_contention_is_resumable_not_permanent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cursor Bugbot finding on PR #654 (Medium): every ``ProjectionError``
    raised by ``governor.lease()`` -- including transient
    ``CONCURRENT_PROJECTION`` lock-wait timeouts -- was mapped to the same
    permanent ``HARD_BLOCKER`` as real corruption (``LEASE_REPLAY`` /
    ``STALE_LEASE`` / ``STATE_CORRUPT``). A brief lock held by a sibling
    process should not wedge the loop forever. Fail closed the first tick,
    then auto-resume and succeed once the lock clears.
    """
    from project_atlas.orchestration.autonomy.lease_projection import ProjectionError

    gov = _governor(_node("AS-ORCH-CONTEND-001"))
    loop = _loop(tmp_path, gov)
    real_lease = AutonomousGovernor.lease
    calls = {"n": 0}

    def _flaky_lease(self: AutonomousGovernor, *args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProjectionError("projection lock is held", code="CONCURRENT_PROJECTION")
        return real_lease(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(AutonomousGovernor, "lease", _flaky_lease)

    first = loop.tick()
    assert first.phase is LoopPhase.STOPPED
    assert first.stop_reason is StopReason.PROJECTION_CONTENTION

    # Auto-resumes on the very next tick (no new work needed -- the same
    # node is still there) and makes real forward progress: with only one
    # in-process node and no dependents, reaching NO_ELIGIBLE_WORK proves
    # the retried lease actually succeeded and the node was dispatched.
    final = loop.run_until_stop()
    assert final.phase is LoopPhase.STOPPED
    assert final.stop_reason is StopReason.NO_ELIGIBLE_WORK
    assert calls["n"] >= 2


def test_loop_fails_closed_on_governor_dependency_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-PHASE2A-1a defense-in-depth (independent-IV finding): select_next
    ()'s own dependency check (continuation.py's `deps_ok`) should always
    already exclude anything governor.lease() would reject for
    DEPENDENCIES_NOT_SATISFIED -- self-dependency, the one concrete way
    these two independent checks were found to disagree, is now rejected
    at the WorkNode model boundary instead (see
    test_work_node_rejects_self_dependency in test_orchestration_autonomy
    .py), so neither layer can observe it anymore. This test proves the
    belt-and-suspenders catch in loop.py itself, independent of that fix:
    if select_next() and governor.lease() were ever to disagree again for
    some other reason, the loop must fail closed with a StopReason, not
    crash with an uncaught GovernorError. Forces the disagreement directly
    via monkeypatch (select_next always says "ready") rather than via a
    real WorkNode shape, since after the model-layer fix no such shape is
    known to exist anymore.
    """
    prereq = _node("AS-ORCH-DEP-PREREQ", state=NodeState.DISCOVERED, surface="dep-prereq-surface")
    dependent = _node("AS-ORCH-DEP-DEPENDENT", deps=("AS-ORCH-DEP-PREREQ",))
    gov = _governor(prereq, dependent)

    from project_atlas.orchestration.autonomy.continuation import ContinuationDecision

    monkeypatch.setattr(
        "project_atlas.orchestration.autonomy.loop.select_next",
        lambda *args, **kwargs: ContinuationDecision("AS-ORCH-DEP-DEPENDENT", None),
    )
    result = _loop(tmp_path, gov).tick()
    assert result.stop_reason is StopReason.HARD_BLOCKER
    # never actually leased: the node's own state is untouched
    dependent_after = next(
        n for n in gov.snapshot().nodes if n.package_id == "AS-ORCH-DEP-DEPENDENT"
    )
    assert dependent_after.state is NodeState.READY


def test_external_dispatch_once_then_await(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-EXT-001", host=ExecutionHostClass.EXTERNAL_AGENT))
    port = CallableDispatchPort(lambda _root: {"dispatch_id": "disp-1", "status": "RUNNING"})
    loop = _loop(tmp_path, gov, port)
    result = loop.tick()
    assert result.dispatched is True
    assert result.dispatch_id == "disp-1"
    assert result.phase is LoopPhase.AWAITING_RESULT
    again = loop.tick()
    assert again.recovered is True
    assert again.dispatched is False
    assert again.phase is LoopPhase.AWAITING_RESULT


def test_orphaned_dispatch_recovery_reconciles_completed(tmp_path: Path) -> None:
    """ORCH001E-008 P3 remediation: a crash between dispatch_once()
    persisting its own 001D-side record and the loop persisting
    active_dispatch_id must not be silently treated as "nothing was
    dispatched". If the dispatch port can independently find what it
    actually started, the loop must reconcile from that, not stay stuck.
    """
    gov = _governor(_node("AS-ORCH-ORPHAN-001", host=ExecutionHostClass.EXTERNAL_AGENT))
    calls: list[str] = []
    port = CallableDispatchPort(
        lambda _root: calls.append("dispatch") or {"dispatch_id": "orphan-1", "status": "RUNNING"},
        recover=lambda _root, _id: {"status": "COMPLETED", "digest": "aa" * 32},
        find_active_dispatch_id=lambda _root, _lease_id: "orphan-1",
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()  # LEASED -> DISPATCHING -> AWAITING_RESULT (normal path)
    # Simulate the exact crash window: DISPATCHING persisted, but
    # active_dispatch_id was never recorded (as if the process had just
    # crashed between dispatch_once() returning and the loop's own save).
    loop._save(phase=LoopPhase.DISPATCHING, active_dispatch_id=None)
    result = loop.recover()
    assert calls == ["dispatch"]  # never re-dispatched a duplicate process
    assert result.stop_reason is None
    node = next(item for item in gov.snapshot().nodes if item.package_id == "AS-ORCH-ORPHAN-001")
    assert node.state in {NodeState.CERTIFIED, NodeState.OWNER_HELD, NodeState.CLOSED}


def test_orphaned_dispatch_recovery_reconciles_still_running(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-ORPHAN-002", host=ExecutionHostClass.EXTERNAL_AGENT))
    port = CallableDispatchPort(
        lambda _root: {"dispatch_id": "orphan-2", "status": "RUNNING"},
        recover=lambda _root, _id: {"status": "RUNNING"},
        find_active_dispatch_id=lambda _root, _lease_id: "orphan-2",
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()
    loop._save(phase=LoopPhase.DISPATCHING, active_dispatch_id=None)
    result = loop.recover()
    assert result.phase is LoopPhase.AWAITING_RESULT
    assert loop.state.active_dispatch_id == "orphan-2"  # identity recovered, not lost


def test_orphaned_dispatch_recovery_finds_nothing_retries_cleanly(tmp_path: Path) -> None:
    """When the port genuinely has no record either (crash happened before
    even the 001D-side persist), retry from LEASED is safe -- no process is
    known to exist, so re-dispatching cannot duplicate one.
    """
    gov = _governor(_node("AS-ORCH-ORPHAN-003", host=ExecutionHostClass.EXTERNAL_AGENT))
    calls: list[str] = []
    port = CallableDispatchPort(
        lambda _root: calls.append("dispatch") or {"dispatch_id": "orphan-3", "status": "RUNNING"},
        find_active_dispatch_id=lambda _root, _lease_id: None,
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()
    assert calls == ["dispatch"]  # the original, pre-crash dispatch
    loop._save(phase=LoopPhase.DISPATCHING, active_dispatch_id=None)
    result = loop.recover()
    assert calls == ["dispatch", "dispatch"]  # one clean retry, not a hang
    assert result.dispatch_id == "orphan-3"
    assert result.phase is LoopPhase.AWAITING_RESULT


def test_orphaned_dispatch_recovery_treats_empty_string_as_not_found(tmp_path: Path) -> None:
    """Independent-IV finding: `found is None` alone let a legal-but-empty
    `""` return slip through as if it were a real identity, silently
    persisting active_dispatch_id="" and then permanently re-stalling
    (structurally the same bug this whole fix exists to close). An empty
    string must never be treated as a found identity.
    """
    gov = _governor(_node("AS-ORCH-ORPHAN-004", host=ExecutionHostClass.EXTERNAL_AGENT))
    calls: list[str] = []
    port = CallableDispatchPort(
        lambda _root: calls.append("dispatch") or {"dispatch_id": "orphan-4", "status": "RUNNING"},
        find_active_dispatch_id=lambda _root, _lease_id: "",
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()
    loop._save(phase=LoopPhase.DISPATCHING, active_dispatch_id=None)
    result = loop.recover()
    assert loop.state.active_dispatch_id != ""
    assert calls == ["dispatch", "dispatch"]  # treated as not-found -> clean retry
    assert result.phase is LoopPhase.AWAITING_RESULT


def test_orphaned_dispatch_recovery_fails_closed_on_ambiguous_discovery(tmp_path: Path) -> None:
    """Independent-IV finding: the port's own docstring said `None` means
    "no record exists OR the port cannot determine one" -- collapsing
    those into one auto-retry response risks duplicating a real in-flight
    process on a merely transient discovery failure. A port that cannot
    positively confirm either way must raise, and the loop must fail
    closed (observable), not silently retry.
    """
    gov = _governor(_node("AS-ORCH-ORPHAN-005", host=ExecutionHostClass.EXTERNAL_AGENT))
    calls: list[str] = []

    def _find_raises(_root: Path, _lease_id: str) -> str | None:
        raise RuntimeError("001D-side active-dispatch record is unreadable")

    port = CallableDispatchPort(
        lambda _root: calls.append("dispatch") or {"dispatch_id": "orphan-5", "status": "RUNNING"},
        find_active_dispatch_id=_find_raises,
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()
    assert calls == ["dispatch"]
    loop._save(phase=LoopPhase.DISPATCHING, active_dispatch_id=None)
    with pytest.raises(LoopError) as exc:
        loop.recover()
    assert exc.value.code == "DISPATCH_RECOVERY_AMBIGUOUS"
    assert calls == ["dispatch"]  # never auto-retried on ambiguity
    assert loop.state.phase is LoopPhase.FAILED_CLOSED


def test_orphaned_dispatch_recovery_passes_the_correct_lease_id(tmp_path: Path) -> None:
    """The abstract port cannot scope a match to the right lease on its
    own -- it needs the loop to tell it which lease is active. Prove the
    loop actually passes it, so a real adapter's per-lease scoping
    (independent-IV finding: the underlying 001D slot is global, not
    per-lease) has something correct to filter on.
    """
    gov = _governor(_node("AS-ORCH-ORPHAN-006", host=ExecutionHostClass.EXTERNAL_AGENT))
    seen_lease_ids: list[str] = []

    def _find(_root: Path, lease_id: str) -> str | None:
        seen_lease_ids.append(lease_id)
        return None

    port = CallableDispatchPort(
        lambda _root: {"dispatch_id": "orphan-6", "status": "RUNNING"},
        find_active_dispatch_id=_find,
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()
    expected_lease_id = loop.state.active_lease_id
    assert expected_lease_id
    loop._save(phase=LoopPhase.DISPATCHING, active_dispatch_id=None)
    loop.recover()
    assert seen_lease_ids == [expected_lease_id]


def test_duplicate_result_replay_rejected(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-DUP-001", host=ExecutionHostClass.EXTERNAL_AGENT))
    port = CallableDispatchPort(
        lambda _root: {"dispatch_id": "disp-dup", "status": "COMPLETED", "digest": "ff" * 32}
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()
    with pytest.raises(LoopError, match="duplicate"):
        loop.apply_observed_result("disp-dup", "ff" * 32, passed=True)


def test_lease_replay_rejected(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-LSR-001"))
    loop = _loop(tmp_path, gov)
    loop.tick()
    loop._save(phase=LoopPhase.IDLE, active_package_id=None, active_lease_id=None)
    assert loop.state.completed_lease_ids


def _dangling_validating(
    loop: AutonomousLoop,
    *,
    package_id: str,
    lease_id: str,
    dispatch_id: str,
) -> None:
    """Reproduce the exact ORCH001E-012 crash window: `apply_observed_result()`'s
    own first `_save(phase=VALIDATING)` persisted, but whatever
    interrupted the call happened before its final `_save(...)` ever
    cleared `active_dispatch_id`. Bypasses `apply_observed_result()` itself
    -- exactly like the existing DISPATCHING-crash-window tests above
    bypass _dispatch_leased() by calling `loop._save(...)` directly.
    """
    loop._save(
        phase=LoopPhase.VALIDATING,
        active_package_id=package_id,
        active_lease_id=lease_id,
        active_dispatch_id=dispatch_id,
    )


def test_validating_dangling_in_process_redrives_and_completes(tmp_path: Path) -> None:
    """ORCH001E-012 core reproduction: before this fix, `_complete_validated()`
    saw `active_dispatch_id is not None` and returned
    `self._result()` verbatim -- no `_save`, no error, no progress. Phase
    stayed `VALIDATING` forever; a caller retrying `tick()` (e.g.
    `run_until_stop()`) would land right back here every time. Here the
    interruption happened before any governor mutation ran (node still
    `ACTIVE`) -- must be safe to redrive apply_observed_result() from
    scratch and actually reach a terminal state.
    """
    package_id = "AS-ORCH-VALSTUCK-001"
    gov = _governor(_node(package_id))
    loop = _loop(tmp_path, gov)
    lease = gov.lease(package_id, loop._first_agent(), branch=loop._branch, worktree=loop._worktree)
    gov.execute_leased(lease.lease_id)  # node -> ACTIVE, mirrors _dispatch_leased()'s first step
    dispatch_id = f"in-process:{lease.lease_id}"
    _dangling_validating(
        loop, package_id=package_id, lease_id=lease.lease_id, dispatch_id=dispatch_id
    )
    assert loop.state.phase is LoopPhase.VALIDATING
    assert loop.state.active_dispatch_id == dispatch_id

    result = loop.tick()  # must not silently no-op

    assert result.phase is LoopPhase.IDLE
    assert loop.state.active_dispatch_id is None
    assert dispatch_id in loop.state.completed_dispatch_ids
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state in {NodeState.CERTIFIED, NodeState.OWNER_HELD}
    # A repeated tick() on the now-IDLE loop must not re-stall either.
    again = loop.tick()
    assert again.phase is LoopPhase.STOPPED
    assert again.stop_reason is StopReason.NO_ELIGIBLE_WORK


def test_validating_dangling_after_certified_finishes_without_illegal_transition(
    tmp_path: Path,
) -> None:
    """The harder half of the crash window: the interrupted
    apply_observed_result() call got far enough to fully drive the
    governor to a terminal verified state (CERTIFIED) before whatever
    interrupted it -- only its own final bookkeeping `_save()` never ran.
    Re-driving apply_observed_result() blindly here would attempt an
    illegal `CERTIFIED -> VERIFYING` transition -- structurally the exact
    "already-transitioned node" hazard PR #637 already closed for
    LEASED/ACTIVE via the same check-node-state-before-redo idiom this
    mirrors. Must finish the LoopState bookkeeping instead of repeating
    governor mutations that already succeeded.
    """
    package_id = "AS-ORCH-VALSTUCK-002"
    gov = _governor(_node(package_id))
    loop = _loop(tmp_path, gov)
    lease = gov.lease(package_id, loop._first_agent(), branch=loop._branch, worktree=loop._worktree)
    gov.execute_leased(lease.lease_id)
    gov.transition(package_id, NodeState.VERIFYING, "test-pre-interruption-verify")
    gov.complete_verification(package_id, passed=True)
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state is NodeState.CERTIFIED  # confirms the crash window this test targets

    dispatch_id = f"in-process:{lease.lease_id}"
    _dangling_validating(
        loop, package_id=package_id, lease_id=lease.lease_id, dispatch_id=dispatch_id
    )

    result = loop.tick()  # must not raise IllegalTransitionError

    assert result.phase is LoopPhase.IDLE
    assert dispatch_id in loop.state.completed_dispatch_ids
    assert lease.lease_id in loop.state.completed_lease_ids
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state is NodeState.CERTIFIED  # untouched a second time, not corrupted


def test_validating_dangling_external_dispatch_reobserves_before_redriving(tmp_path: Path) -> None:
    """External dispatch never mutates the node itself (only
    apply_observed_result() does) -- node stays LEASED all the way through
    the crash window. Recovery must re-derive `passed` from the dispatch
    port's own durable record (the same move `recover()` already makes
    for DISPATCHING/AWAITING_RESULT), not trust a stale, unpersisted
    in-memory value -- proven here by actually observing the port get
    re-queried.
    """
    package_id = "AS-ORCH-VALSTUCK-003"
    gov = _governor(_node(package_id, host=ExecutionHostClass.EXTERNAL_AGENT))
    recover_calls: list[str] = []
    port = CallableDispatchPort(
        lambda _root: {"dispatch_id": "val-3", "status": "RUNNING"},
        recover=lambda _root, dispatch_id: (
            recover_calls.append(dispatch_id) or {"status": "COMPLETED", "digest": "cc" * 32}
        ),
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()  # LEASED -> DISPATCHING -> AWAITING_RESULT
    assert loop.state.phase is LoopPhase.AWAITING_RESULT
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state is NodeState.LEASED  # external dispatch never touches node state itself

    # Simulate apply_observed_result() being invoked (as recover() would)
    # and its own first _save(phase=VALIDATING) running, then interrupted
    # before any governor mutation.
    loop._save(phase=LoopPhase.VALIDATING)
    assert loop.state.active_dispatch_id == "val-3"  # unchanged, still dangling

    result = loop.tick()

    assert recover_calls == ["val-3"]  # re-observed the durable record
    assert result.phase is LoopPhase.IDLE
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state in {NodeState.CERTIFIED, NodeState.OWNER_HELD}
    assert "cc" * 32 in loop.state.completed_result_digests


def test_validating_dangling_remediating_node_resumes_to_leased(tmp_path: Path) -> None:
    """The `passed=False` half of the crash window, ordinary remediation
    branch: complete_verification(passed=False) already moved the node to
    REMEDIATING before the interruption. Must resume it exactly as
    apply_observed_result()'s own REMEDIATING branch would.
    """
    package_id = "AS-ORCH-VALSTUCK-004"
    gov = _governor(_node(package_id))
    loop = _loop(tmp_path, gov)
    lease = gov.lease(package_id, loop._first_agent(), branch=loop._branch, worktree=loop._worktree)
    gov.execute_leased(lease.lease_id)
    gov.transition(package_id, NodeState.VERIFYING, "test-pre-interruption-verify")
    gov.complete_verification(package_id, passed=False)
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state is NodeState.REMEDIATING

    dispatch_id = f"in-process:{lease.lease_id}"
    _dangling_validating(
        loop, package_id=package_id, lease_id=lease.lease_id, dispatch_id=dispatch_id
    )

    result = loop.tick()

    assert result.phase is LoopPhase.LEASED
    assert loop.state.active_dispatch_id is None
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state is NodeState.ACTIVE  # remediate_and_resume() moved it back to ACTIVE


def test_validating_dangling_blocked_node_stops_hard_blocker(tmp_path: Path) -> None:
    """The `passed=False` half of the crash window, remediation-exhausted
    branch: complete_verification(passed=False) already moved the node to
    BLOCKED before the interruption. Must stop with HARD_BLOCKER exactly
    as apply_observed_result()'s own BLOCKED branch would, not silently
    no-op.
    """
    package_id = "AS-ORCH-VALSTUCK-005"
    exhausted = _node(package_id).model_copy(update={"retry_policy": RetryPolicy(cycles_used=3)})
    gov = _governor(exhausted)
    loop = _loop(tmp_path, gov)
    lease = gov.lease(package_id, loop._first_agent(), branch=loop._branch, worktree=loop._worktree)
    gov.execute_leased(lease.lease_id)
    gov.transition(package_id, NodeState.VERIFYING, "test-pre-interruption-verify")
    gov.complete_verification(package_id, passed=False)
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state is NodeState.BLOCKED  # remediation exhausted, confirms the crash window

    dispatch_id = f"in-process:{lease.lease_id}"
    _dangling_validating(
        loop, package_id=package_id, lease_id=lease.lease_id, dispatch_id=dispatch_id
    )

    result = loop.tick()

    assert result.phase is LoopPhase.STOPPED
    assert result.stop_reason is StopReason.HARD_BLOCKER


def test_validating_dangling_ambiguous_node_state_fails_closed(tmp_path: Path) -> None:
    """No normal, synchronous code path leaves a node at VERIFYING when
    `_complete_validated()` re-enters (complete_verification() always
    transitions a node OUT of VERIFYING before returning, or never gets
    called at all) -- an adversarial/pathological case this fix has no
    established, safe mapping for. Must fail closed rather than guess
    whether the original observation was `passed=True` or `passed=False`.
    """
    package_id = "AS-ORCH-VALSTUCK-006"
    gov = _governor(_node(package_id))
    loop = _loop(tmp_path, gov)
    lease = gov.lease(package_id, loop._first_agent(), branch=loop._branch, worktree=loop._worktree)
    gov.execute_leased(lease.lease_id)
    gov.transition(package_id, NodeState.VERIFYING, "test-interrupted-mid-verify")
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state is NodeState.VERIFYING

    dispatch_id = f"in-process:{lease.lease_id}"
    _dangling_validating(
        loop, package_id=package_id, lease_id=lease.lease_id, dispatch_id=dispatch_id
    )

    with pytest.raises(LoopError) as exc:
        loop.tick()
    assert exc.value.code == "VALIDATION_STATE_AMBIGUOUS"
    assert loop.state.phase is LoopPhase.FAILED_CLOSED


def test_validating_dangling_certified_node_but_dispatch_now_reports_failed_fails_closed(
    tmp_path: Path,
) -> None:
    """Adversarial consistency check: the governor node reached a
    passed-verification terminal state (CERTIFIED), but the dispatch
    port's own durable record now disagrees (FAILED) -- a genuinely
    irreconcilable mismatch between the two sources of truth this fix
    reads from. Must fail closed rather than silently trust either one.
    """
    package_id = "AS-ORCH-VALSTUCK-007"
    gov = _governor(_node(package_id, host=ExecutionHostClass.EXTERNAL_AGENT))
    port = CallableDispatchPort(
        lambda _root: {"dispatch_id": "val-7", "status": "RUNNING"},
        recover=lambda _root, _id: {"status": "FAILED"},
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()
    assert loop.state.active_dispatch_id == "val-7"
    # Drive the governor all the way to CERTIFIED directly, as if an
    # earlier apply_observed_result() call had observed passed=True and
    # fully completed its governor mutations before being interrupted.
    gov.transition(package_id, NodeState.ACTIVE, "test-pre-interruption")
    gov.transition(package_id, NodeState.VERIFYING, "test-pre-interruption")
    gov.complete_verification(package_id, passed=True)
    node = next(item for item in gov.snapshot().nodes if item.package_id == package_id)
    assert node.state is NodeState.CERTIFIED

    loop._save(phase=LoopPhase.VALIDATING)  # active_dispatch_id == "val-7" already

    with pytest.raises(LoopError) as exc:
        loop.tick()
    assert exc.value.code == "VALIDATION_STATE_AMBIGUOUS"
    assert loop.state.phase is LoopPhase.FAILED_CLOSED


def test_corrupt_state_fails_closed(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-COR-001"))
    loop = _loop(tmp_path, gov)
    raw = (tmp_path / "loop-store" / "current.json").read_text(encoding="utf-8")
    (tmp_path / "loop-store" / "current.json").write_text(
        raw.replace(loop.state.record_digest, "ab" * 32),
        encoding="utf-8",
    )
    with pytest.raises(LoopError) as exc:
        AutonomousLoop(
            governor=gov,
            trusted=_anchor(),
            store=tmp_path / "loop-store",
            root=tmp_path,
        )
    assert exc.value.code == "STATE_CORRUPT"


def test_cross_project_rejected(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-XPR-001"))
    with pytest.raises(LoopError) as exc:
        AutonomousLoop(
            governor=gov,
            trusted=_anchor(),
            store=tmp_path / "loop-store",
            root=tmp_path,
            expected_repository_identity="github.com/other/repo",
        )
    assert exc.value.code == "CROSS_PROJECT"


def test_target_moved_refuses_loop(tmp_path: Path) -> None:
    other = "cccccccccccccccccccccccccccccccccccccccc"
    gov = AutonomousGovernor(current_main=other, current_tree=TREE, trusted_anchor=_anchor())
    gov.add_node(_node("AS-ORCH-MOV-001"))
    with pytest.raises(LoopError) as exc:
        AutonomousLoop(
            governor=gov,
            trusted=_anchor(),
            store=tmp_path / "loop-store",
            root=tmp_path,
        )
    assert exc.value.code == "TARGET_MOVED"


def test_crash_recover_does_not_respawn(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-CRASH-001", host=ExecutionHostClass.EXTERNAL_AGENT))
    recover_calls: list[str] = []
    port = CallableDispatchPort(
        lambda _root: {"dispatch_id": "disp-crash", "status": "RUNNING"},
        recover=lambda _root, did: (
            recover_calls.append(did) or {"dispatch_id": did, "status": "RUNNING"}
        ),
    )
    loop = _loop(tmp_path, gov, port)
    loop.tick()
    recovered = loop.recover()
    assert recovered.recovered is True
    assert recover_calls == ["disp-crash"]
    assert recovered.dispatched is False


def test_max_ticks_resource_boundary(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-RUN-001", host=ExecutionHostClass.EXTERNAL_AGENT))
    port = CallableDispatchPort(lambda _root: {"dispatch_id": "disp-run", "status": "RUNNING"})
    loop = _loop(tmp_path, gov, port)
    last = loop.run_until_stop()
    assert last.phase in {LoopPhase.AWAITING_RESULT, LoopPhase.STOPPED}
    loop._save(ticks_in_invocation=MAX_TICKS_PER_INVOCATION)
    bounded = loop.tick()
    assert bounded.stop_reason is StopReason.RESOURCE_BOUNDARY


def test_state_cannot_carry_authority() -> None:
    with pytest.raises(ValidationError):
        LoopState(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=PIN,
            trusted_tree=TREE,
            phase=LoopPhase.IDLE,
            sequence=0,
            ticks_in_invocation=0,
            merge_authorized=True,  # type: ignore[arg-type]
            record_digest="00" * 32,
        )


def test_digest_roundtrip(tmp_path: Path) -> None:
    state = initial_loop_state(_anchor())
    persisted = persist_loop_state(tmp_path / "s", state)
    assert verify_loop_state(persisted).record_digest == hash_payload(persisted.unsigned_payload())
    assert (
        persisted.record_digest
        != seal_loop_state(
            persisted.model_copy(update={"sequence": 1, "record_digest": "00" * 32})
        ).record_digest
    )


def test_package_id_constant() -> None:
    assert LOOP_PACKAGE_ID == "AS-ORCH-001E"


def test_resource_boundary_enqueues_yield_not_owner(tmp_path: Path) -> None:
    gov = _governor(_node("AS-ORCH-NEXT-001"))
    loop = _loop(tmp_path, gov)
    persist_loop_state(
        tmp_path / "loop-store",
        seal_loop_state(
            loop.state.model_copy(
                update={
                    "ticks_in_invocation": MAX_TICKS_PER_INVOCATION,
                    "record_digest": "00" * 32,
                }
            )
        ),
    )
    loop._state = load_loop_state(tmp_path / "loop-store")
    result = loop.tick()
    assert result.stop_reason is StopReason.RESOURCE_BOUNDARY
    recovered = recover_broker(tmp_path)
    assert recovered is not None
    assert recovered.kind is SuccessorKind.RESOURCE_YIELD
    assert recovered.execution_authorized is False
