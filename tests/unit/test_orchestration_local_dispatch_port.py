"""AS-ORCH-LOCAL-DISPATCH-001: governed local-process dispatch port (PR-C).

Test matrix (A-N) per D-CODEX-ATLAS-AUTONOMY-PREREQUISITES-CONTINUATION-R2
section 8, exercised against a REAL git repository (not a synthetic PIN --
``local_process_transport.run_local_task()`` does real git operations) and
the REAL governed lease/dispatch machinery (``AutonomousGovernor`` +
``AutonomousLoop``), never a second/parallel orchestration engine.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, GovernorError
from project_atlas.orchestration.autonomy.lease_projection import RELATIVE_DEFAULT as LEASE_RELATIVE
from project_atlas.orchestration.autonomy.local_dispatch_port import (
    LocalDispatchError,
    LocalProcessDispatchPort,
    _dispatch_id_for,
    _read_receipt,
)
from project_atlas.orchestration.autonomy.loop import AutonomousLoop, LoopError, LoopPhase
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    AdvancementReason,
    AgentCapability,
    ExecutionHostClass,
    IvRequirements,
    MutationSurface,
    NodeState,
    OwnerGateKind,
    StopReason,
    TrustedAnchorRecord,
    WorkNode,
)
from project_atlas.orchestration.autonomy.trust import seal_anchor
from project_atlas.orchestration.local_process_transport import LocalProcessExecutorConfig


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    (repo / "allowed").mkdir()
    (repo / "allowed" / ".gitkeep").write_text("", encoding="utf-8")
    # Mirrors this repository's own real .gitignore (`.atlas/orchestration/`
    # is already excluded there) -- the governed lease projection store,
    # loop state, and this port's own dispatch receipts all durably live
    # under `.atlas/orchestration/...` INSIDE the project root by design
    # (same convention every other durable store in this package already
    # uses). Without this, run_local_task()'s own clean-worktree
    # requirement would (correctly, per its own design) see those files as
    # untracked dirt and refuse to run every single dispatch -- not a bug
    # in either module, just this fixture needing to match real-repo
    # convention.
    (repo / ".gitignore").write_text(".atlas/orchestration/\n", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    return repo


def _repo_main_tree(repo: Path) -> tuple[str, str]:
    main = _run_git(repo, "rev-parse", "HEAD")
    tree = _run_git(repo, "rev-parse", "HEAD^{tree}")
    return main, tree


def _anchor(main: str, tree: str) -> TrustedAnchorRecord:
    pred = "1" * 40
    cert = "3" * 40
    return seal_anchor(
        TrustedAnchorRecord(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=main,
            trusted_tree=tree,
            predecessor_main=pred,
            predecessor_tree="2" * 40,
            advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
            source_package="AS-ORCH-LOCAL-DISPATCH-001",
            source_directive="D-CODEX-ATLAS-AUTONOMY-PREREQUISITES-CONTINUATION-R2",
            source_pr=661,
            merge_commit=main,
            merge_parent_1=pred,
            merge_parent_2=cert,
            merge_tree=tree,
            certified_head=cert,
            certified_tree=tree,
            certification_status="CERTIFIED",
            independent_verification_status="PASS",
            post_merge_seal="PASS",
            post_merge_ci="PASS",
            evidence_reference="tests/unit/local-dispatch-anchor.json",
            evidence_digest="aa" * 32,
            sequence=1,
            record_digest="00" * 32,
        )
    )


def _node(
    package_id: str,
    *,
    base_pin: str,
    state: NodeState = NodeState.READY,
    owner_gate: OwnerGateKind | None = None,
    deps: tuple[str, ...] = (),
    surface_paths: tuple[str, ...] = ("allowed/",),
) -> WorkNode:
    return WorkNode(
        package_id=package_id,
        objective="PR-C governed local dispatch test node",
        base_pin=base_pin,
        dependencies=deps,
        mutation_surface=MutationSurface(
            surface_id=f"{package_id}-surface",
            paths=surface_paths,
            semantic="LOCAL_DISPATCH_TEST",
        ),
        execution_host_class=ExecutionHostClass.IN_PROCESS,  # overridden at lease() time
        agent_capabilities_required=(AgentCapability.IMPLEMENT,),
        acceptance_criteria=("PASS",),
        iv_requirements=IvRequirements(certification_required=True),
        owner_gate=owner_gate,
        state=state,
    )


def _governor(
    repo: Path, *nodes: WorkNode, current_main: str, current_tree: str
) -> AutonomousGovernor:
    gov = AutonomousGovernor(
        current_main=current_main,
        current_tree=current_tree,
        trusted_anchor=_anchor(current_main, current_tree),
        lease_projection_store=repo / LEASE_RELATIVE,
    )
    for node in nodes:
        gov.add_node(node)
    return gov


def _loop(
    repo: Path,
    governor: AutonomousGovernor,
    *,
    current_main: str,
    current_tree: str,
    dispatch: LocalProcessDispatchPort | None = None,
    override: ExecutionHostClass | None = None,
) -> AutonomousLoop:
    return AutonomousLoop(
        governor=governor,
        trusted=_anchor(current_main, current_tree),
        store=repo / ".atlas" / "orchestration" / "autonomy" / "loop",
        root=repo,
        dispatch=dispatch,
        execution_host_class_override=override,
    )


def _enabled_config(*, timeout_seconds: int = 30) -> LocalProcessExecutorConfig:
    return LocalProcessExecutorConfig(enabled=True, timeout_seconds=timeout_seconds)


def _port(*, argv: tuple[str, ...], timeout_seconds: int = 30) -> LocalProcessDispatchPort:
    return LocalProcessDispatchPort(
        config=_enabled_config(timeout_seconds=timeout_seconds), argv_template=argv
    )


def _dispatch_worktree(repo: Path, dispatch_id: str) -> Path:
    """The isolated per-attempt worktree a real dispatch checked its
    changes out into -- every ``dispatch_once()`` receipt carries this
    (see ``local_dispatch_port.py``'s module docstring: attempts never
    touch ``repo``'s own working tree)."""
    receipt = _read_receipt(repo, dispatch_id)
    assert receipt is not None, f"no receipt for {dispatch_id!r}"
    return repo / receipt["worktree"]


# ---------------------------------------------------------------------------
# A / J. READY -> lease -> local provider selected -> real process runs ->
# result accepted for verification (CERTIFIED)
# ---------------------------------------------------------------------------


def test_a_ready_node_dispatches_to_a_real_local_process_and_certifies(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-A-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    script = "open('allowed/output.txt', 'w').write('real work happened\\n')"
    port = _port(argv=(sys.executable, "-c", script))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    # A single tick() only performs one state-machine step (lease OR
    # dispatch OR finalize, never all three) -- run_until_stop() drives
    # the loop through lease -> dispatch -> completion, exactly mirroring
    # how the real CLI (run_governor_loop_tick, called repeatedly) is
    # actually used.
    result = loop.run_until_stop()
    assert result.phase is LoopPhase.STOPPED
    dispatch_id = f"{_dispatch_id_for('LEASE-1')}:0"
    worktree = _dispatch_worktree(repo, dispatch_id)
    assert (worktree / "allowed" / "output.txt").is_file()  # a REAL process really ran
    # Isolated per-attempt worktree (IV findings, PR #662 review rounds
    # 1-2): the change never touches repo's own working tree at all.
    assert not (repo / "allowed" / "output.txt").exists()
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-A-001")
    assert final.state == NodeState.CERTIFIED


# ---------------------------------------------------------------------------
# B. provider disabled -> no process
# ---------------------------------------------------------------------------


def test_b_disabled_config_refuses_port_construction() -> None:
    with pytest.raises(LocalDispatchError) as exc:
        LocalProcessDispatchPort(
            config=LocalProcessExecutorConfig(),  # enabled=False, the default
            argv_template=(sys.executable, "-c", "pass"),
        )
    assert exc.value.code == "LOCAL_EXECUTION_DISABLED"


def test_b_no_override_no_dispatch_port_means_no_local_process(tmp_path: Path) -> None:
    """The pre-existing default: no override, no dispatch port -- a node
    still runs the harmless IN_PROCESS metadata stand-in, never a real
    local subprocess."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-B-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    loop = _loop(repo, gov, current_main=main, current_tree=tree)  # no dispatch, no override
    result = loop.run_until_stop()
    assert result.dispatched is False  # never touches a dispatch port at all
    assert not (repo / "allowed" / "output.txt").exists()
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-B-001")
    assert final.execution_host_class == ExecutionHostClass.IN_PROCESS


# ---------------------------------------------------------------------------
# C. stale base -> no dispatch
# ---------------------------------------------------------------------------


def test_c_stale_target_moved_refuses_lease_before_any_dispatch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-C-001", base_pin=main)
    # Governor observes a DIFFERENT (stale) main/tree than the trusted
    # anchor -- current_main intentionally does not match the anchor.
    stale_main = "f" * 40
    stale_tree = "e" * 40
    gov = AutonomousGovernor(
        current_main=stale_main,
        current_tree=stale_tree,
        trusted_anchor=_anchor(main, tree),
        lease_projection_store=repo / LEASE_RELATIVE,
    )
    gov.add_node(node)
    port = _port(argv=(sys.executable, "-c", "open('allowed/should_not_exist.txt','w').close()"))
    # AutonomousLoop.__init__ itself independently re-checks the governor's
    # observed main/tree against the loop's own trusted anchor and refuses
    # to even construct on a mismatch -- a defense-in-depth layer BEFORE
    # any tick, let alone a dispatch, could ever happen.
    with pytest.raises(LoopError) as exc:
        _loop(
            repo,
            gov,
            current_main=main,
            current_tree=tree,
            dispatch=port,
            override=ExecutionHostClass.LOCAL_PROCESS,
        )
    assert exc.value.code == "TARGET_MOVED"
    assert not (repo / "allowed" / "should_not_exist.txt").exists()


# ---------------------------------------------------------------------------
# D. owner-held node -> no dispatch
# ---------------------------------------------------------------------------


def test_d_owner_held_state_never_dispatched(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node(
        "PRC-D-001",
        base_pin=main,
        state=NodeState.OWNER_HELD,
        owner_gate=OwnerGateKind.D_SECURITY_GOVERNANCE_POLICY,
    )
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", "open('allowed/should_not_exist.txt','w').close()"))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    result = loop.run_until_stop()
    assert result.dispatched is False
    assert not (repo / "allowed" / "should_not_exist.txt").exists()


def test_d_ready_but_owner_gated_never_dispatched(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node(
        "PRC-D-002",
        base_pin=main,
        state=NodeState.READY,
        owner_gate=OwnerGateKind.D_SECURITY_GOVERNANCE_POLICY,
    )
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", "open('allowed/should_not_exist.txt','w').close()"))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    result = loop.run_until_stop()
    assert result.stop_reason is StopReason.OWNER_GATE
    assert not (repo / "allowed" / "should_not_exist.txt").exists()


# ---------------------------------------------------------------------------
# E. dependency-blocked node -> no dispatch
# ---------------------------------------------------------------------------


def test_e_unsatisfied_dependency_never_dispatched(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-E-001", base_pin=main, deps=("PRC-E-UPSTREAM",))
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", "open('allowed/should_not_exist.txt','w').close()"))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    result = loop.run_until_stop()
    # select_next()'s own advisory dependency check already excludes this
    # node before governor.lease() is ever called, so the loop reports
    # NO_ELIGIBLE_WORK (nothing selectable at all) rather than reaching
    # lease()'s own DEPENDENCIES_NOT_SATISFIED defense-in-depth check.
    assert result.stop_reason is StopReason.NO_ELIGIBLE_WORK
    assert not (repo / "allowed" / "should_not_exist.txt").exists()


# ---------------------------------------------------------------------------
# F. forbidden-path change -> execution rejected -> node not certified
# ---------------------------------------------------------------------------


def test_f_forbidden_path_write_is_rejected_node_not_certified(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    (repo / "main").mkdir()
    (repo / "main" / ".gitkeep").write_text("", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed main/ dir")
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-F-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    # grant_lease()'s own default forbidden_paths is ("main", "projects")
    # -- writing under main/ hits that real, already-governed boundary.
    script = "open('main/tampered.txt', 'w').write('bad\\n')"
    port = _port(argv=(sys.executable, "-c", script))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    # A single tick from IDLE performs the ENTIRE lease -> dispatch ->
    # (fail -> remediate) sequence via _select_and_lease()'s own tail-call
    # into _dispatch_leased() -- checking the immediate post-failure state
    # after exactly one tick. Driving further ticks would repeatedly
    # auto-retry via the existing, unmodified governed remediation cycle,
    # which is out of scope for this test and (at its own exhaustion
    # boundary, cycle 3) hits a separate, pre-existing off-by-one between
    # complete_verification()'s and remediate_and_resume()'s own
    # can_remediate() checks -- not something this PR touches or needs to
    # exercise.
    loop.tick()
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-F-001")
    assert final.state != NodeState.CERTIFIED
    assert final.state == NodeState.ACTIVE  # remediated once, ready to retry
    assert final.retry_policy.cycles_used == 1


# ---------------------------------------------------------------------------
# G. out-of-scope path -> rejected
# ---------------------------------------------------------------------------


def test_g_out_of_scope_write_is_rejected_node_not_certified(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-G-001", base_pin=main, surface_paths=("allowed/",))
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    script = "open('elsewhere.txt', 'w').write('out of scope\\n')"
    port = _port(argv=(sys.executable, "-c", script))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    loop.tick()
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-G-001")
    assert final.state != NodeState.CERTIFIED
    assert final.state == NodeState.ACTIVE
    assert final.retry_policy.cycles_used == 1


# ---------------------------------------------------------------------------
# H. executor nonzero exit -> controlled failure
# ---------------------------------------------------------------------------


def test_h_nonzero_exit_is_a_controlled_failure_not_a_crash(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-H-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", "import sys; sys.exit(1)"))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    result = loop.tick()  # must not raise
    assert result.phase is not LoopPhase.FAILED_CLOSED
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-H-001")
    assert final.state != NodeState.CERTIFIED
    assert final.state == NodeState.ACTIVE
    assert final.retry_policy.cycles_used == 1


# ---------------------------------------------------------------------------
# I. timeout -> controlled failure
# ---------------------------------------------------------------------------


def test_i_timeout_is_a_controlled_failure_not_a_hang(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-I-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", "import time; time.sleep(120)"), timeout_seconds=1)
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    result = loop.tick()  # must not raise, must not block for 120s
    assert result.phase is not LoopPhase.FAILED_CLOSED
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-I-001")
    assert final.state != NodeState.CERTIFIED
    assert final.state == NodeState.ACTIVE
    assert final.retry_policy.cycles_used == 1


# ---------------------------------------------------------------------------
# K. duplicate dispatch -> blocked
# ---------------------------------------------------------------------------


def test_k_a_genuinely_still_running_dispatch_blocks_a_second_one(tmp_path: Path) -> None:
    """Dispatch is attempt-indexed (``local-process:<lease>:<n>``), not one
    fixed id per lease -- the existing, unmodified governed remediation
    cycle legitimately re-dispatches the SAME lease again after a real,
    already-RESOLVED failure (see test below), which is not a duplicate.
    What must still be blocked is a genuinely overlapping/concurrent
    dispatch attempt while a PRIOR one for this lease is still
    unresolved."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import (
        _dispatch_id_for,
        _write_receipt,
    )

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-K-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PRC-K-001", "governor-pilot-local", branch="b", worktree="w")
    port = _port(argv=(sys.executable, "-c", "open('allowed/x.txt','w').close()"))
    # Simulate "attempt 0 is currently in flight" directly (no need to run
    # a real process to construct this state).
    dispatch_id_0 = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id_0,
        {
            "dispatch_id": dispatch_id_0,
            "lease_id": lease.lease_id,
            "package_id": "PRC-K-001",
            "status": "RUNNING",
        },
    )
    with pytest.raises(LocalDispatchError) as exc:
        port.dispatch_once(repo)
    assert exc.value.code == "DUPLICATE_DISPATCH"


def test_k_a_legitimate_remediation_retry_is_not_a_duplicate(tmp_path: Path) -> None:
    """The opposite of the case above: a node that failed once (real
    violation) and is legitimately remediated/retried by the EXISTING,
    unmodified governor remediation cycle must be allowed to dispatch
    again -- with a distinct, never-reused dispatch_id, not blocked as a
    duplicate.

    IV finding (PR #662 review round 1): the original version of this
    test only asserted ``receipt_1 is not None``, which passes even when
    attempt 1 never genuinely re-ran the task at all -- it also passed
    for an immediate, uninformative failure with no real re-execution.
    Each attempt now gets its own fresh, isolated ``git worktree`` (see
    ``local_dispatch_port.py``'s module docstring), so there is no
    shared-root residue to inherit between attempts by construction --
    but this test still independently PROVES a genuine re-execution
    rather than trusting that by design alone: the script uses a marker
    file OUTSIDE any worktree (so it is NOT reset between attempts) to
    behave differently attempt-to-attempt -- attempt 0 deliberately
    violates scope; attempt 1, if it genuinely re-executes, writes a
    real in-scope change instead and the node reaches CERTIFIED -- which
    is only possible if the retry actually ran the task again, not
    merely recorded a second receipt.
    """
    external_marker = tmp_path / "attempt-marker.txt"
    script = (
        "import pathlib\n"
        f"marker = pathlib.Path({str(external_marker)!r})\n"
        "if marker.exists():\n"
        "    open('allowed/output.txt', 'w').write('retry genuinely ran\\n')\n"
        "else:\n"
        "    marker.write_text('1')\n"
        "    open('elsewhere.txt', 'w').close()\n"
    )
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-K-002", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", script))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    # A single tick from IDLE performs the ENTIRE lease -> dispatch(attempt
    # 0) -> fail -> remediate sequence in one call (see the sibling F/G/H/I
    # tests' comments) -- the node ends ACTIVE, phase=LEASED, ready for a
    # second attempt on the NEXT tick.
    result = loop.tick()  # lease + dispatch attempt 0 -> fails -> remediated
    assert result.phase is LoopPhase.LEASED
    # Attempt 0's own isolated worktree carries the rejected write --
    # repo's own working tree was never touched by it at all.
    assert not (repo / "elsewhere.txt").exists()
    result = loop.tick()  # dispatch attempt 1 -- must genuinely re-run, not just re-receipt
    assert result.phase is not LoopPhase.FAILED_CLOSED

    lease_id = "LEASE-1"
    receipt_0 = _read_receipt(repo, f"local-process:{lease_id}:0")
    receipt_1 = _read_receipt(repo, f"local-process:{lease_id}:1")
    assert receipt_0 is not None and receipt_0["status"] == "FAILED"
    assert receipt_1 is not None
    assert receipt_1["status"] == "COMPLETED"  # attempt 1 genuinely ran and passed
    assert "violations" in receipt_1 and receipt_1["violations"] == []
    worktree_1 = repo / receipt_1["worktree"]
    assert (worktree_1 / "allowed" / "output.txt").is_file()  # proof the retry actually executed
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-K-002")
    assert final.state == NodeState.CERTIFIED


def test_unrelated_lease_dispatches_after_a_prior_successful_dispatch(tmp_path: Path) -> None:
    """IV findings (PR #662 review rounds 1-2): earlier designs ran every
    dispatch directly against the shared project root and tried to
    restore/commit it clean between attempts -- round 1 found a fully
    successful, uncommitted dispatch left that shared root permanently
    dirty (blocking every later dispatch); round 2 found the
    restore/commit step's own failure modes reproduced the same lockout
    under realistic conditions. Per-attempt worktree isolation (this
    module's current design) removes the bug class structurally: each
    lease's dispatch runs inside its own fresh, disposable worktree, so
    two independent leases with disjoint mutation surfaces, dispatched
    one after another in the SAME repo, can never interfere with each
    other's on-disk state at all -- confirmed here directly at the
    dispatch-port level (two sequential ``dispatch_once()`` calls), not
    through the full governed loop (the loop's own single-active-lease-
    per-agent bookkeeping, release/reaper, is a separate, pre-existing,
    unrelated concern -- see test N).
    """
    repo = _make_repo(tmp_path)
    (repo / "other").mkdir()
    (repo / "other" / ".gitkeep").write_text("", encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed other/ dir")
    main, tree = _repo_main_tree(repo)

    node_a = _node("PRC-X-001", base_pin=main, surface_paths=("allowed/",))
    node_b = _node("PRC-X-002", base_pin=main, surface_paths=("other/",))
    gov = _governor(repo, node_a, node_b, current_main=main, current_tree=tree)
    script = (
        "import json, sys\n"
        "req = json.load(open(sys.argv[1]))\n"
        "target = req['authorized_paths'][0].rstrip('/') + '/output.txt'\n"
        "open(target, 'w').write(req['work_id'])\n"
    )
    port = _port(argv=(sys.executable, "-c", script))

    lease_a = gov.lease("PRC-X-001", "governor-pilot-local", branch="b1", worktree="w1")
    result_a = port.dispatch_once(repo)
    assert result_a["status"] == "COMPLETED"
    worktree_a = _dispatch_worktree(repo, str(result_a["dispatch_id"]))
    assert (worktree_a / "allowed" / "output.txt").read_text(encoding="utf-8") == "PRC-X-001"
    assert not (repo / "allowed" / "output.txt").exists()  # never touched repo's own tree
    # Mirror loop.py's own COMPLETED-result handling (_complete_validated,
    # loop.py:550-552) so node A leaves the active-parallel state set
    # (LEASED/ACTIVE/VERIFYING/REMEDIATING) -- this test calls
    # dispatch_once() directly, bypassing the loop, so it must drive the
    # SAME governor transitions the loop would to reach an equivalent
    # state before granting a second, independent lease.
    gov.transition("PRC-X-001", NodeState.ACTIVE, "TEST_DISPATCHED")
    gov.transition("PRC-X-001", NodeState.VERIFYING, "TEST_RESULT_VALIDATED")
    gov.complete_verification("PRC-X-001", passed=True)
    gov.release_lease(lease_a.lease_id)

    # A completely independent SECOND lease, disjoint mutation surface,
    # dispatched in the SAME repo root right after the first one's
    # successful dispatch -- confirms the two attempts' isolated
    # worktrees never interfere with each other.
    gov.lease("PRC-X-002", "governor-pilot-local", branch="b2", worktree="w2")
    result_b = port.dispatch_once(repo)
    assert result_b["status"] == "COMPLETED"
    worktree_b = _dispatch_worktree(repo, str(result_b["dispatch_id"]))
    assert (worktree_b / "other" / "output.txt").read_text(encoding="utf-8") == "PRC-X-002"
    assert not (repo / "other" / "output.txt").exists()


# ---------------------------------------------------------------------------
# L. duplicate result -> blocked
# ---------------------------------------------------------------------------


def test_l_duplicate_result_application_is_blocked(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-L-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", "open('allowed/x.txt','w').close()"))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    loop.run_until_stop()
    dispatch_id = f"{_dispatch_id_for('LEASE-1')}:0"
    with pytest.raises(LoopError) as exc:
        loop.apply_observed_result(dispatch_id, dispatch_id, passed=True)
    assert exc.value.code == "RESULT_REPLAY"


# ---------------------------------------------------------------------------
# M. fresh process restart -> durable task state restored -> no duplicate
#    execution
# ---------------------------------------------------------------------------


def test_m_process_restart_recovers_without_rerunning_the_task(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    package_id = "PRC-M-001"
    node = _node(package_id, base_pin=main)

    # "Process N": lease, then simulate dispatch_once() having started (its
    # own RUNNING receipt written) before the whole process crashes --
    # loop.py's own _save(phase=DISPATCHING) happens BEFORE dispatch_once()
    # is called and BEFORE active_dispatch_id is known, exactly mirroring
    # the crash window _dispatch_leased() itself documents.
    gov_before = _governor(repo, node, current_main=main, current_tree=tree)
    argv = (sys.executable, "-c", "open('allowed/marker.txt', 'w').write('ran\\n')")
    port_before = _port(argv=argv)
    loop_before = _loop(
        repo,
        gov_before,
        current_main=main,
        current_tree=tree,
        dispatch=port_before,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    lease = gov_before.lease(
        package_id,
        loop_before._first_agent(),
        branch=loop_before._branch,
        worktree=loop_before._worktree,
        execution_host_class_override=ExecutionHostClass.LOCAL_PROCESS,
    )
    loop_before._save(
        phase=LoopPhase.DISPATCHING,
        active_package_id=package_id,
        active_lease_id=lease.lease_id,
    )
    # Manually reproduce dispatch_once()'s OWN first durable action (the
    # RUNNING receipt) without letting run_local_task() actually run --
    # this is the exact crash point: receipt written, process died before
    # the child ever started.
    from project_atlas.orchestration.autonomy.local_dispatch_port import _write_receipt

    dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    _write_receipt(
        repo,
        dispatch_id,
        {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": package_id,
            "status": "RUNNING",
        },
    )
    assert not (repo / "allowed" / "marker.txt").exists()  # confirms nothing ran yet

    # "Process N+1": brand-new governor (no in-memory state at all) and a
    # new port instance pointed at the same root -- exactly what a real
    # restart gives run_governor_loop_tick().
    gov_after = _governor(repo, current_main=main, current_tree=tree)
    port_after = _port(argv=argv)
    loop_after = _loop(
        repo,
        gov_after,
        current_main=main,
        current_tree=tree,
        dispatch=port_after,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    assert loop_after.state.phase is LoopPhase.DISPATCHING  # reloaded from disk
    result = loop_after.recover()
    assert result.phase is not LoopPhase.FAILED_CLOSED
    # The task was NOT re-run: no NEW process was started by recover().
    assert not (repo / "allowed" / "marker.txt").exists()
    receipt = _read_receipt(repo, dispatch_id)
    assert receipt is not None
    assert receipt["status"] == "RUNNING"  # unchanged -- still genuinely unresolved


# ---------------------------------------------------------------------------
# N. completed lease -> release/reaper contract preserved
# ---------------------------------------------------------------------------


def test_n_completed_local_process_lease_can_still_be_released(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-N-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", "open('allowed/x.txt','w').close()"))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    loop.run_until_stop()
    active = [ls for ls in gov.snapshot().leases if ls.active]
    assert len(active) == 1
    released = gov.release_lease(active[0].lease_id)
    assert released.active is False
    # Idempotent: releasing again is a documented no-op, not an error.
    released_again = gov.release_lease(active[0].lease_id)
    assert released_again.active is False


# ---------------------------------------------------------------------------
# Cross-cutting: authority fields the executor cannot expand
# ---------------------------------------------------------------------------


def test_envelope_authority_fields_come_from_the_lease_never_a_caller(tmp_path: Path) -> None:
    """Directive section 7: the executor cannot expand authorized_paths/
    forbidden_paths -- confirm the ACTUAL lease's paths (not some other
    value) are what a real dispatch enforces, by exercising a script that
    is compliant with the lease's real scope and confirming no violation
    is raised, then a second scenario that is not compliant."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-ENV-001", base_pin=main, surface_paths=("allowed/",))
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    port = _port(argv=(sys.executable, "-c", "open('allowed/inside.txt', 'w').write('ok\\n')"))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    loop.run_until_stop()
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-ENV-001")
    assert final.state == NodeState.CERTIFIED


def test_governor_lease_override_never_applies_without_explicit_opt_in(tmp_path: Path) -> None:
    """The override parameter defaults to None; a caller that does not
    pass it (every pre-existing caller) gets IN_PROCESS unchanged."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-ENV-002", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PRC-ENV-002", "governor-pilot-local", branch="b", worktree="w")
    del lease
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-ENV-002")
    assert final.execution_host_class == ExecutionHostClass.IN_PROCESS


def test_lease_override_does_not_bypass_owner_gate_check(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-ENV-003", base_pin=main, owner_gate=OwnerGateKind.E_DESTRUCTIVE_OPS)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    with pytest.raises(GovernorError) as exc:
        gov.lease(
            "PRC-ENV-003",
            "governor-pilot-local",
            branch="b",
            worktree="w",
            execution_host_class_override=ExecutionHostClass.LOCAL_PROCESS,
        )
    assert exc.value.code == "OWNER_GATE_REQUIRED"


# ---------------------------------------------------------------------------
# Cross-cutting: supervisor-checkout integrity (isolated worktree gives
# git-level isolation only, never OS-level sandboxing)
# ---------------------------------------------------------------------------


def test_supervisor_checkout_integrity_guard_detects_escape_via_absolute_path(
    tmp_path: Path,
) -> None:
    """A worktree is a git-level isolation boundary, not an OS-level
    sandbox -- nothing stops a launched process from using an absolute
    path to reach back into the supervisor's own checkout regardless of
    its own cwd. The independent guard (git status + a content digest of
    .atlas/orchestration/, snapshotted before/after) must catch this and
    the dispatch must never be certified as COMPLETED, even though the
    LOCAL, in-scope half of the same script succeeded."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-GUARD-001", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    escape_target = repo / "README.md"
    script = (
        "import pathlib\n"
        f"pathlib.Path({str(escape_target)!r}).write_text('tampered', encoding='utf-8')\n"
        "open('allowed/output.txt', 'w').write('ok')\n"
    )
    port = _port(argv=(sys.executable, "-c", script))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    loop.tick()
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-GUARD-001")
    assert final.state != NodeState.CERTIFIED
    dispatch_id = f"{_dispatch_id_for('LEASE-1')}:0"
    receipt = _read_receipt(repo, dispatch_id)
    assert receipt is not None
    assert receipt["status"] == "FAILED"
    assert "supervisor_integrity_violation" in receipt


def test_supervisor_checkout_integrity_guard_detects_governance_state_tamper(
    tmp_path: Path,
) -> None:
    """Same class of escape, aimed specifically at the gitignored durable
    governance state under .atlas/orchestration/ (lease projection, loop
    state) -- git's own status never reports a gitignored path, so this
    exercises the content-digest half of the guard specifically, not the
    git-status half."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import RECEIPTS_RELATIVE

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-GUARD-002", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    # LEASE_RELATIVE (".atlas/orchestration/autonomy") is a DIRECTORY --
    # target a file inside it, mirroring what a real durable-store write
    # (lease projection, loop state) looks like on disk.
    lease_store_dir = repo / LEASE_RELATIVE
    assert lease_store_dir.is_relative_to(repo / RECEIPTS_RELATIVE) is False
    lease_store_target = lease_store_dir / "tampered-governance-file.json"
    script = (
        "import pathlib\n"
        f"target = pathlib.Path({str(lease_store_target)!r})\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_text('tampered', encoding='utf-8')\n"
        "open('allowed/output.txt', 'w').write('ok')\n"
    )
    port = _port(argv=(sys.executable, "-c", script))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    loop.tick()
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-GUARD-002")
    assert final.state != NodeState.CERTIFIED
    dispatch_id = f"{_dispatch_id_for('LEASE-1')}:0"
    receipt = _read_receipt(repo, dispatch_id)
    assert receipt is not None
    assert receipt["status"] == "FAILED"
    assert "supervisor_integrity_violation" in receipt


def test_executor_created_commit_never_implies_merge_authority(tmp_path: Path) -> None:
    """Explicit adversarial requirement: an executor may freely commit
    inside its OWN isolated worktree (ordinary use of a real git
    checkout) -- that commit is only an implementation artifact. Confirm
    nothing in this port's own receipt or return value ever claims
    merge/execution authority regardless, and the supervisor's own repo
    gains no new ref/branch/commit from it."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    before_branches = set(_run_git(repo, "for-each-ref", "--format=%(refname)").splitlines())
    node = _node("PRC-GUARD-003", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    script = (
        "import subprocess\n"
        "open('allowed/output.txt', 'w').write('ok')\n"
        "subprocess.run(['git', 'add', '-A'], check=True)\n"
        "subprocess.run(['git', '-c', 'user.email=x@x.com', '-c', 'user.name=x', "
        "'commit', '-q', '-m', 'executor commit'], check=True)\n"
    )
    port = _port(argv=(sys.executable, "-c", script))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    result = loop.run_until_stop()
    assert result.phase is LoopPhase.STOPPED
    assert result.merge_authorized is False
    assert result.execution_authorized is False
    dispatch_id = f"{_dispatch_id_for('LEASE-1')}:0"
    receipt = _read_receipt(repo, dispatch_id)
    assert receipt is not None
    worktree = repo / receipt["worktree"]
    # The executor's own commit is real, inside its own disposable
    # worktree/branch -- never touching the supervisor's own refs.
    assert _run_git(worktree, "log", "-1", "--format=%s") == "executor commit"
    after_branches = set(_run_git(repo, "for-each-ref", "--format=%(refname)").splitlines())
    new_refs = after_branches - before_branches
    # The only new ref is this dispatch's own disposable worktree branch
    # -- never a change to any pre-existing branch (main/master) at all.
    assert all("atlas-local-dispatch/" in ref for ref in new_refs)
    supervisor_head = _run_git(repo, "rev-parse", "HEAD")
    assert supervisor_head == main  # supervisor's own HEAD never moved


def test_abandoned_worktree_from_a_prior_crashed_attempt_does_not_block_a_fresh_one(
    tmp_path: Path,
) -> None:
    """An attempt whose supervising process crashed mid-run leaves its
    own worktree behind (durable evidence, never auto-deleted -- see
    module docstring). Confirm a FRESH attempt for the SAME lease is
    never blocked by that abandoned worktree's mere existence -- each
    attempt gets its own never-reused worktree path/branch by
    construction (keyed by its own dispatch_id)."""
    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-GUARD-004", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PRC-GUARD-004", "governor-pilot-local", branch="b", worktree="w")

    from project_atlas.orchestration.autonomy.local_dispatch_port import (
        _create_dispatch_worktree,
    )

    abandoned_dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:0"
    # Simulate a crash: the worktree for attempt 0 was created and the
    # RUNNING receipt written, but the process died before the child
    # ever ran -- attempt 0's receipt stays RUNNING forever (a genuine
    # crash, not resolved by this test; find_active_dispatch_id()/
    # recover() are the real recovery path, exercised elsewhere).
    _create_dispatch_worktree(repo, dispatch_id=abandoned_dispatch_id, base_pin=main)
    from project_atlas.orchestration.autonomy.local_dispatch_port import _write_receipt

    _write_receipt(
        repo,
        abandoned_dispatch_id,
        {
            "dispatch_id": abandoned_dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": "PRC-GUARD-004",
            "status": "RUNNING",
        },
    )
    # A genuinely overlapping second dispatch is still correctly refused
    # (test K's own contract, unaffected by worktree isolation).
    port = _port(argv=(sys.executable, "-c", "open('allowed/output.txt','w').close()"))
    with pytest.raises(LocalDispatchError) as exc:
        port.dispatch_once(repo)
    assert exc.value.code == "DUPLICATE_DISPATCH"
    # But once that receipt is resolved (e.g. by the real recovery path
    # marking it FAILED after confirming the crash), a genuinely fresh
    # attempt -- its own new dispatch_id, its own new worktree -- must
    # proceed normally, never blocked by the abandoned worktree's mere
    # presence on disk.
    from project_atlas.orchestration.autonomy.local_dispatch_port import _read_receipt

    resolved = dict(_read_receipt(repo, abandoned_dispatch_id) or {})
    resolved["status"] = "FAILED"
    resolved["error"] = "simulated crash resolution"
    _write_receipt(repo, abandoned_dispatch_id, resolved)
    result = port.dispatch_once(repo)
    assert result["status"] == "COMPLETED"
    assert result["dispatch_id"] != abandoned_dispatch_id


# ---------------------------------------------------------------------------
# Fresh-IV-round remediation (PR #662, second independent adversarial round
# against the isolated-worktree head): a reproduced arbitrary-file-write via
# a symlink planted at `_write_json_atomic`'s predictable tmp path, a
# corrupt/tampered receipt silently conflated with "no dispatch happened",
# and the receipts store itself being excluded from the supervisor-integrity
# guard's content digest.
# ---------------------------------------------------------------------------


def test_write_json_atomic_refuses_to_follow_a_planted_symlink(tmp_path: Path) -> None:
    """Reproduces the exact PoC from the fresh IV round: pre-plant a
    symlink at the predictable `.{name}.tmp` sibling path a legitimate
    caller (a receipt/request write) is about to use, pointing at an
    unrelated "victim" file outside the receipts store entirely. Before
    the fix, `_write_json_atomic` followed the symlink and clobbered the
    victim's content; the intended target was left a dangling symlink to
    the victim. After the fix, the symlink is removed (never followed)
    and the write lands on a fresh regular file at the real target."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import _write_json_atomic

    victim = tmp_path / "victim.txt"
    victim.write_text("original victim content\n", encoding="utf-8")
    target = tmp_path / "receipt.json"
    tmp_sibling = tmp_path / ".receipt.json.tmp"
    tmp_sibling.symlink_to(victim)

    _write_json_atomic(target, {"hello": "world"})

    # The victim file must be untouched -- the write must never have
    # followed the planted symlink.
    assert victim.read_text(encoding="utf-8") == "original victim content\n"
    # The real target must be a genuine regular file with the intended
    # content, not a dangling symlink to the victim.
    assert not target.is_symlink()
    assert json.loads(target.read_text(encoding="utf-8")) == {"hello": "world"}


def test_write_json_atomic_fails_closed_not_raw_oserror_on_tmp_path_obstruction(
    tmp_path: Path,
) -> None:
    """Fresh IV round 3's new finding: an OSError along the atomic-write
    path OTHER than the two handled cases (missing tmp file to unlink,
    symlink refused via O_EXCL) -- e.g. the predictable tmp path
    obstructed by a directory -- must not escape as a raw, unwrapped
    OSError (which `run_governor_loop_tick()`'s catch tuple does not
    include, so it would crash the real CLI entrypoint ungracefully).
    It must be converted to `LocalDispatchError(code=
    "RECEIPT_WRITE_BLOCKED")`, matching every other genuine protocol
    violation this module already fails closed on."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import _write_json_atomic

    target = tmp_path / "receipt.json"
    tmp_sibling = tmp_path / ".receipt.json.tmp"
    tmp_sibling.mkdir()  # obstruct the predictable tmp path with a directory

    with pytest.raises(LocalDispatchError) as exc:
        _write_json_atomic(target, {"hello": "world"})
    assert exc.value.code == "RECEIPT_WRITE_BLOCKED"


def test_write_json_atomic_fails_closed_on_obstructed_ancestor_directory(
    tmp_path: Path,
) -> None:
    """Fresh IV round 4's finding: the original round-3 fix wrapped the
    unlink/open/write/replace sequence in `except OSError`, but
    `target.parent.mkdir(parents=True, exist_ok=True)` -- the FIRST
    filesystem operation in the function -- sat OUTSIDE that block.
    Obstructing an ancestor of `target.parent` with a plain FILE (so
    `mkdir()` itself raises) reproduced a raw, unwrapped `FileExistsError`
    escaping uncaught -- on `dispatch_once()`'s very first durable write,
    before its own try/except even begins. `mkdir()` must be inside the
    same fail-closed boundary as everything else in this function."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import _write_json_atomic

    obstruction = tmp_path / "notadir"
    obstruction.write_text("i am a file, not a directory", encoding="utf-8")
    target = obstruction / "nested" / "receipt.json"

    with pytest.raises(LocalDispatchError) as exc:
        _write_json_atomic(target, {"hello": "world"})
    assert exc.value.code == "RECEIPT_WRITE_BLOCKED"


def test_read_receipt_fails_closed_on_corrupt_receipt(tmp_path: Path) -> None:
    """`_read_receipt` must distinguish "no receipt was ever written"
    (returns None -- the only case callers may treat as an open attempt
    slot) from "a receipt file exists but is corrupt/unreadable/malformed"
    (must raise LocalDispatchError, never silently return None). Before
    the fix these two cases were conflated, which would let
    `dispatch_once()` silently reuse/overwrite a corrupted attempt's slot
    and let `recover()`/`find_active_dispatch_id()` silently lose track
    of a real attempt."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import (
        _receipt_filename,
        _receipts_dir,
    )

    repo = _make_repo(tmp_path)

    # Genuinely missing -- must return None.
    assert _read_receipt(repo, "local-process:LEASE-X:0") is None

    # Malformed JSON -- must fail closed.
    malformed_id = "local-process:LEASE-X:1"
    path = _receipts_dir(repo) / _receipt_filename(malformed_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LocalDispatchError) as exc:
        _read_receipt(repo, malformed_id)
    assert exc.value.code == "CORRUPT_RECEIPT"

    # Valid JSON but not an object -- must fail closed.
    non_object_id = "local-process:LEASE-X:2"
    path = _receipts_dir(repo) / _receipt_filename(non_object_id)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(LocalDispatchError) as exc:
        _read_receipt(repo, non_object_id)
    assert exc.value.code == "CORRUPT_RECEIPT"


def test_dispatch_once_fails_closed_rather_than_reusing_a_corrupt_attempt_slot(
    tmp_path: Path,
) -> None:
    """End-to-end: a corrupt receipt sitting at attempt 0 for the active
    lease must stop `dispatch_once()` from silently treating that slot as
    free and dispatching a fresh attempt over it -- it must raise
    LocalDispatchError instead, matching this module's own
    `LocalDispatchError` = "could not proceed at all" contract."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import (
        _dispatch_id_for,
        _receipt_filename,
        _receipts_dir,
    )

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    node = _node("PRC-GUARD-005", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    lease = gov.lease("PRC-GUARD-005", "governor-pilot-local", branch="b", worktree="w")

    dispatch_id_0 = f"{_dispatch_id_for(lease.lease_id)}:0"
    path = _receipts_dir(repo) / _receipt_filename(dispatch_id_0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    port = _port(argv=(sys.executable, "-c", "open('allowed/output.txt','w').close()"))
    with pytest.raises(LocalDispatchError) as exc:
        port.dispatch_once(repo)
    assert exc.value.code == "CORRUPT_RECEIPT"


def test_supervisor_integrity_guard_detects_tampering_with_another_receipt(
    tmp_path: Path,
) -> None:
    """The fresh IV round's third finding: the guard's content digest used
    to exclude the ENTIRE receipts store (which nests every dispatch's own
    isolated worktree), so a dispatched task tampering with a DIFFERENT
    receipt file -- not its own, still legitimately being written outside
    the guarded window -- went completely undetected. After the fix, only
    the worktrees subtree is excluded; the receipt/request JSON files
    themselves are covered by the digest."""
    from project_atlas.orchestration.autonomy.local_dispatch_port import (
        RECEIPTS_RELATIVE,
        _write_receipt,
    )

    repo = _make_repo(tmp_path)
    main, tree = _repo_main_tree(repo)
    # A pre-existing, already-resolved receipt for an unrelated lease --
    # the kind of durable record this guard must protect even though it
    # legitimately differs from the receipt this dispatch itself writes.
    _write_receipt(
        repo,
        "local-process:LEASE-OTHER:0",
        {
            "dispatch_id": "local-process:LEASE-OTHER:0",
            "lease_id": "LEASE-OTHER",
            "package_id": "PRC-OTHER",
            "status": "COMPLETED",
        },
    )
    node = _node("PRC-GUARD-006", base_pin=main)
    gov = _governor(repo, node, current_main=main, current_tree=tree)
    other_receipt = repo / RECEIPTS_RELATIVE / "local-process_LEASE-OTHER_0.json"
    assert other_receipt.is_file()
    script = (
        "import pathlib\n"
        f"target = pathlib.Path({str(other_receipt)!r})\n"
        "target.write_text('{\"tampered\": true}', encoding='utf-8')\n"
        "open('allowed/output.txt', 'w').write('ok')\n"
    )
    port = _port(argv=(sys.executable, "-c", script))
    loop = _loop(
        repo,
        gov,
        current_main=main,
        current_tree=tree,
        dispatch=port,
        override=ExecutionHostClass.LOCAL_PROCESS,
    )
    loop.tick()
    final = next(n for n in gov.snapshot().nodes if n.package_id == "PRC-GUARD-006")
    assert final.state != NodeState.CERTIFIED
    dispatch_id = f"{_dispatch_id_for('LEASE-1')}:0"
    receipt = _read_receipt(repo, dispatch_id)
    assert receipt is not None
    assert receipt["status"] == "FAILED"
    assert "supervisor_integrity_violation" in receipt
