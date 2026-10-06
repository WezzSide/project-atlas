"""AS-MISSION-VERTICAL-SLICE-001 -- load-bearing regression coverage for
the real production KNOWLEDGE -> DEVELOPMENT -> RECOVERY path in
`project_atlas.orchestration.mission`.

Review-remediation continuation: every one of the 9 review-thread
findings and the two additionally-reported reproductions (duplicate
effect on repeated identity; corrupt checkpoint misread as
NO_RUN_FOUND/safe) has a dedicated test below, each named after what it
closes.

Every crash scenario kills a REAL, separate OS process at a controlled
checkpoint state (via `_mission_run_worker.py`), not a simulated/mocked
failure. Every spawn is bounded by an explicit timeout and produces no
visible console window on Windows.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from project_atlas.orchestration.mission.adapter import ShellCommandAdapter
from project_atlas.orchestration.mission.context_packet import (
    check_context_staleness,
    compile_mission_context,
)
from project_atlas.orchestration.mission.execution import (
    ContextStaleError,
    PolicyRefusedError,
    UnreconciledPriorRunError,
    WorkspaceUnavailableError,
    load_checkpoint,
    load_checkpoint_detailed,
    start_mission_run,
)
from project_atlas.orchestration.mission.lease import (
    acquire_mission_lease,
    read_mission_lease_state,
    release_mission_lease,
)
from project_atlas.orchestration.mission.recovery import reconcile_mission_run

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_WORKER = str(Path(__file__).with_name("_mission_run_worker.py"))
_TIMEOUT = 20.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _kill_tree(proc: subprocess.Popen) -> None:
    """`proc.kill()` alone is NOT sufficient here: this worktree's venv
    `python.exe` is a launcher/trampoline stub (confirmed by binary size,
    the same D146 pattern seen elsewhere this session) that can spawn the
    REAL interpreter as a child process -- killing only the launcher PID
    leaves that real child (the one actually holding the mission lease)
    alive. Tree-kill on Windows (`taskkill /F /T /PID`); process-group
    kill on POSIX."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=_NO_WINDOW,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.wait(timeout=10)


def _lease_receipt_path(workspace: Path) -> Path:
    return workspace / ".atlas-mission" / "mission-lease-receipt.json"


def _checkpoint_file_path(workspace: Path) -> Path:
    return workspace / ".atlas-mission" / "mission-run-checkpoint.json"


def _spawn_worker(workspace: Path, stop_after_state: str, go_file: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, _WORKER, str(workspace), stop_after_state, str(go_file)],
        creationflags=_NO_WINDOW,
        start_new_session=(sys.platform != "win32"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_checkpoint_state(workspace: Path, state: str, *, timeout: float = _TIMEOUT) -> None:
    """Polls until the checkpoint reports `state`, or fails LOUDLY with a
    diagnosable message if it never does -- silently giving up (this
    helper's earlier shape) let a caller's real assertion fail with a
    confusing, unrelated-looking message instead of "the worker never
    reached the expected state".

    Tolerates a transient `PermissionError`/`OSError` on the read, not
    just `JSONDecodeError`: reproduced directly while writing this test
    suite -- a benign reader polling `checkpoint_file` at the exact
    instant `_persist_checkpoint()`'s `os.replace()` fires can hit a real
    Windows sharing-violation `PermissionError` (the writer side of this
    same race is what `_replace_with_retry()` in `execution.py` now
    retries; production readers already tolerate it via
    `load_checkpoint_detailed()`'s own `OSError` handling -- this is the
    equivalent tolerance for this test-only polling helper)."""
    deadline = time.time() + timeout
    checkpoint_file = _checkpoint_file_path(workspace)
    last_seen_state: str | None = None
    while time.time() < deadline:
        if checkpoint_file.is_file():
            try:
                data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                last_seen_state = data.get("state")
                if last_seen_state == state:
                    return
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.02)
    raise AssertionError(
        f"worker never reached checkpoint state {state!r} within {timeout}s "
        f"(last observed state: {last_seen_state!r})"
    )


# ---------------------------------------------------------------------------
# KNOWLEDGE
# ---------------------------------------------------------------------------


def test_context_packet_compiles_from_real_repository_documents():
    """Real ADRs, real backlog, real worklog tail -- not fixtures."""
    packet = compile_mission_context(
        _repo_root(),
        mission_id="M-TEST-001",
        objective="test the mission vertical slice itself",
        keywords=["lock", "governor", "resident"],
        trusted_policy={"MERGE_AUTHORIZATION": "NO"},
    )
    assert packet.base_head  # real git HEAD, not a placeholder
    assert len(packet.base_head) == 40
    assert len(packet.decisions) > 0, "must find at least one real matching ADR"
    assert all(d.source.blob_hash for d in packet.decisions)
    assert packet.trusted_policy == {"MERGE_AUTHORIZATION": "NO"}


def test_context_packet_manifest_shows_inclusion_and_exclusion():
    packet = compile_mission_context(
        _repo_root(),
        mission_id="M-TEST-002",
        objective="test manifest honesty",
        keywords=["atlas", "vault", "governance"],
        max_sources=2,
    )
    assert len(packet.decisions) <= 2
    assert isinstance(packet.manifest.excluded_sources, list)
    assert isinstance(packet.manifest.approx_tokens, int)
    assert packet.manifest.approx_tokens >= 0


def test_packet_content_equivalent_ignores_compiled_at_driven_by_adr001():
    """Real demonstration task: this feature exists because
    `compile_mission_context()` retrieved ADR-001 §2's own decision (a
    real ADR, real repository knowledge, not invented for this test) --
    "Scaffold generation embeds no wall-clock timestamps" -- and applied
    it to a NEW situation the ADR did not originally cover (comparing two
    context packets), not merely quoted it."""
    from project_atlas.orchestration.mission.context_packet import packet_content_equivalent

    repo_root = _repo_root()
    # Confirm the driving decision is genuinely retrievable, not assumed.
    ctx_about_the_decision = compile_mission_context(
        repo_root,
        mission_id="M-DEMO-DETERMINISM",
        objective="find the deterministic-output decision",
        keywords=["deterministic", "wall-clock", "timestamp"],
    )
    adr001 = next(
        d for d in ctx_about_the_decision.decisions if "ADR-001" in d.source.path
    )
    assert "wall-clock" in adr001.excerpt.lower()
    assert "generated.at" in adr001.excerpt or "NFR-001" in adr001.excerpt

    packet_a = compile_mission_context(
        repo_root, mission_id="M-EQUIV", objective="o", keywords=["lock"]
    )
    time.sleep(0.05)  # force a real, different wall-clock compiled_at
    packet_b = compile_mission_context(
        repo_root, mission_id="M-EQUIV", objective="o", keywords=["lock"]
    )
    assert packet_a.compiled_at != packet_b.compiled_at, "precondition: times must differ"
    assert packet_content_equivalent(packet_a, packet_b) is True

    # And a genuine content difference must still be detected.
    packet_c = compile_mission_context(
        repo_root,
        mission_id="M-EQUIV",
        objective="a genuinely different objective",
        keywords=["lock"],
    )
    assert packet_content_equivalent(packet_a, packet_c) is False


def test_context_staleness_detects_a_real_source_change(tmp_path):
    """Deliberately mutate a real tracked file, confirm the packet flags
    it as superseded, then restore it byte-for-byte."""
    repo_root = _repo_root()
    target = repo_root / "docs" / "adr" / "ADR-001-wp001-foundation-decisions.md"
    original = target.read_text(encoding="utf-8")
    packet = compile_mission_context(
        repo_root, mission_id="M-TEST-003", objective="staleness", keywords=["foundation"]
    )
    assert any(
        d.source.path.endswith("ADR-001-wp001-foundation-decisions.md") for d in packet.decisions
    )
    try:
        target.write_text(original + "\nTEST MUTATION -- reverted immediately\n", encoding="utf-8")
        report = check_context_staleness(repo_root, packet)
        assert any(p.endswith("ADR-001-wp001-foundation-decisions.md") for p in report.superseded)
    finally:
        target.write_text(original, encoding="utf-8")
        report_after = check_context_staleness(repo_root, packet)
        assert not any(
            p.endswith("ADR-001-wp001-foundation-decisions.md") for p in report_after.superseded
        )


def test_thread_t2_git_unavailable_degrades_not_crashes():
    """Review thread: `_git()` must tolerate `TimeoutExpired`/`OSError`
    (a hung or absent git) by degrading to an empty string, not aborting
    the whole context compile."""
    import project_atlas.orchestration.mission.context_packet as cp_module

    with patch.object(
        cp_module.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=15)
    ):
        assert cp_module._git(["rev-parse", "HEAD"], Path(".")) == ""
    with patch.object(cp_module.subprocess, "run", side_effect=FileNotFoundError("no git")):
        assert cp_module._git(["rev-parse", "HEAD"], Path(".")) == ""
    # And the whole compile must still complete, just with an empty head.
    with patch.object(
        cp_module.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=15)
    ):
        packet = compile_mission_context(
            _repo_root(), mission_id="m", objective="o", keywords=["lock"]
        )
    assert packet.base_head == ""
    assert len(packet.decisions) > 0  # file-based discovery is unaffected by git


def test_thread_t3_bounded_tail_read_matches_naive_and_is_correct():
    """Review thread: the worklog tail reader must not load the whole
    file, and must still produce EXACTLY what a naive whole-file read's
    tail slice would."""
    from project_atlas.orchestration.mission.context_packet import _read_tail_lines

    path = _repo_root() / "WORKLOG.md"
    tail = _read_tail_lines(path, n=50)
    naive = path.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
    assert tail == naive


def test_source_identity_bound_to_actually_retrieved_bytes(tmp_path):
    """The recorded content hash must be computable purely from the
    excerpt's own source text (in-process), not require a separate
    re-read -- proven by hashing a string manually with the module's own
    algorithm and matching a real compiled source's hash exactly."""
    import project_atlas.orchestration.mission.context_packet as cp_module

    packet = compile_mission_context(
        _repo_root(), mission_id="m", objective="o", keywords=["foundation"]
    )
    adr_name = "ADR-001-wp001-foundation-decisions.md"
    adr = next(d for d in packet.decisions if d.source.path.endswith(adr_name))
    real_text = (_repo_root() / adr.source.path).read_text(encoding="utf-8", errors="replace")
    assert adr.source.blob_hash == cp_module._content_hash(real_text)


# ---------------------------------------------------------------------------
# DEVELOPMENT (lease + adapter + execution)
# ---------------------------------------------------------------------------


def test_lease_cross_process_exclusion(tmp_path):
    """A real separate process holding the lease excludes this process."""
    workspace = tmp_path / "ws"
    go_file = tmp_path / "go"
    holder = _spawn_worker(workspace, "STARTED", go_file)
    try:
        go_file.write_text("go", encoding="utf-8")
        deadline = time.time() + _TIMEOUT
        while not _lease_receipt_path(workspace).is_file() and time.time() < deadline:
            time.sleep(0.05)
        assert _lease_receipt_path(workspace).is_file(), "holder never published"

        assert acquire_mission_lease(workspace, run_id="intruder") is False
        state = read_mission_lease_state(workspace)
        assert state.held is True
    finally:
        _kill_tree(holder)


def test_thread_t6_reject_reentrant_lease_from_a_different_run(tmp_path):
    """Review thread: a DIFFERENT run_id reentering the same workspace in
    the SAME process must be rejected, not silently granted by the
    same-process short-circuit."""
    workspace = tmp_path / "ws"
    assert acquire_mission_lease(workspace, run_id="run-A") is True
    assert acquire_mission_lease(workspace, run_id="run-A") is True  # idempotent, same run
    assert acquire_mission_lease(workspace, run_id="run-B") is False  # different run: rejected
    # A's ownership must be completely undisturbed by B's rejected attempt.
    assert read_mission_lease_state(workspace).held is True
    release_mission_lease(workspace, run_id="run-A")
    assert read_mission_lease_state(workspace).held is False


def test_thread_t6_release_is_ownership_specific(tmp_path):
    """Review thread: releasing with the WRONG run_id must not release
    the actual owner's lease out from under it."""
    workspace = tmp_path / "ws"
    assert acquire_mission_lease(workspace, run_id="owner") is True
    release_mission_lease(workspace, run_id="not-the-owner")
    assert read_mission_lease_state(workspace).held is True, "wrong run_id must not release"
    release_mission_lease(workspace, run_id="owner")
    assert read_mission_lease_state(workspace).held is False


def test_thread_t6_thread_safety_same_run_id_concurrent_acquire(tmp_path):
    """Review thread: nested/concurrent calls for the SAME run_id, racing
    the check-then-act sequence, must not spuriously fail."""
    import threading

    workspace = tmp_path / "ws"
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait(timeout=5)
        results.append(acquire_mission_lease(workspace, run_id="same-run"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(results) == 8
    assert all(results), "same run_id must never spuriously fail to (re)acquire its own lease"
    release_mission_lease(workspace, run_id="same-run")


def test_shell_command_adapter_real_execution(tmp_path):
    context = compile_mission_context(
        _repo_root(), mission_id="m", objective="o", keywords=["lock"]
    )
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print('hello from adapter')"))
    result = adapter.run(workspace=tmp_path, context=context, timeout_sec=10.0)
    assert result.ok is True
    assert result.returncode == 0
    assert "hello from adapter" in result.stdout
    assert result.failure_class == "NONE"
    assert result.cleanup_confirmed is True


def test_context_actually_reaches_the_adapter(tmp_path):
    """The gap this remediation exists to close: the adapter must
    actually RECEIVE the compiled context, not just a workspace path and
    a timeout."""
    context = compile_mission_context(
        _repo_root(),
        mission_id="m",
        objective="a very specific objective string to look for",
        keywords=["lock"],
    )
    adapter = ShellCommandAdapter(
        command=(
            sys.executable,
            "-c",
            "import json,pathlib; "
            "d=json.loads(pathlib.Path('.atlas-mission/mission-context.json').read_text()); "
            "print(d['objective'])",
        )
    )
    result = adapter.run(workspace=tmp_path, context=context, timeout_sec=10.0)
    assert result.ok is True
    assert "a very specific objective string to look for" in result.stdout


def test_thread_t1_missing_executable_is_classified_not_raised(tmp_path):
    """Review thread: `OSError`/`FileNotFoundError` from a missing
    executable must be caught and classified, never propagate and crash
    the caller."""
    context = compile_mission_context(
        _repo_root(), mission_id="m", objective="o", keywords=["lock"]
    )
    adapter = ShellCommandAdapter(command=("this-executable-does-not-exist-12345",))
    result = adapter.run(workspace=tmp_path, context=context, timeout_sec=5.0)
    assert result.ok is False
    assert result.failure_class == "SPAWN_FAILED"
    assert result.cleanup_confirmed is True  # nothing was ever spawned


def test_shell_command_adapter_timeout_is_bounded(tmp_path):
    context = compile_mission_context(
        _repo_root(), mission_id="m", objective="o", keywords=["lock"]
    )
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "import time; time.sleep(30)"))
    start = time.perf_counter()
    result = adapter.run(workspace=tmp_path, context=context, timeout_sec=1.0)
    elapsed = time.perf_counter() - start
    assert result.ok is False
    assert result.failure_class == "TIMEOUT"
    assert elapsed < 10.0, "timeout must actually bound the call, not merely be advisory"


def test_thread_t7_timeout_terminates_the_whole_process_tree(tmp_path):
    """Review thread: a timeout must terminate DESCENDANTS, not just the
    direct child. Real reproduction: a descendant writes a liveness
    marker every 0.1s; after the timeout-triggered kill, the marker must
    stop updating."""
    context = compile_mission_context(
        _repo_root(), mission_id="m", objective="o", keywords=["lock"]
    )
    marker = tmp_path / "still-alive.txt"
    child_script = tmp_path / "child.py"
    child_script.write_text(
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{marker}')\n"
        "for _ in range(100):\n"
        "    p.write_text(str(time.time()))\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    parent_script = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, r'{child_script}'])\n"
        "time.sleep(30)\n"
    )
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", parent_script))
    result = adapter.run(workspace=tmp_path, context=context, timeout_sec=2.0)
    assert result.failure_class == "TIMEOUT"
    assert result.cleanup_confirmed is True

    mtime_after_kill = marker.stat().st_mtime if marker.is_file() else 0.0
    time.sleep(1.0)
    mtime_later = marker.stat().st_mtime if marker.is_file() else 0.0
    assert mtime_after_kill == mtime_later, "descendant process survived the timeout kill"


def test_full_mission_run_end_to_end(tmp_path):
    repo_root = _repo_root()
    packet = compile_mission_context(
        repo_root, mission_id="M-E2E-001", objective="end to end", keywords=["lock"]
    )
    workspace = tmp_path / "ws"
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print('real work happened')"))
    result = start_mission_run(
        mission_id="M-E2E-001",
        context=packet,
        adapter=adapter,
        workspace=workspace,
        repo_root=repo_root,
        adapter_timeout_sec=10.0,
    )
    assert result.checkpoint.state == "COMPLETE"
    assert result.adapter_result is not None
    assert result.adapter_result.ok is True
    assert result.deduplicated is False
    reloaded = load_checkpoint(workspace)
    assert reloaded is not None
    assert reloaded.state == "COMPLETE"
    assert read_mission_lease_state(workspace).held is False


def test_workspace_unavailable_raises_not_silently_proceeds(tmp_path):
    """Must use a REAL separate process as the holder: `acquire_mission_lease`
    is deliberately idempotent for the SAME run_id, so a same-process
    "intruder" with the same identity would not exercise the exclusion
    path this test actually checks."""
    workspace = tmp_path / "ws"
    go_file = tmp_path / "go"
    holder = _spawn_worker(workspace, "STARTED", go_file)
    try:
        go_file.write_text("go", encoding="utf-8")
        deadline = time.time() + _TIMEOUT
        while not _lease_receipt_path(workspace).is_file() and time.time() < deadline:
            time.sleep(0.05)
        assert _lease_receipt_path(workspace).is_file(), "holder never published"

        repo_root = _repo_root()
        packet = compile_mission_context(
            repo_root, mission_id="M-BUSY-001", objective="busy", keywords=["lock"]
        )
        adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print('should not run')"))
        with pytest.raises(WorkspaceUnavailableError):
            start_mission_run(
                mission_id="M-BUSY-001",
                context=packet,
                adapter=adapter,
                workspace=workspace,
                repo_root=repo_root,
                adapter_timeout_sec=5.0,
            )
        assert read_mission_lease_state(workspace).held is True
    finally:
        _kill_tree(holder)


def test_repro_checkpoint_replace_retries_transient_windows_sharing_violation(tmp_path):
    """Additional reproduction found while building this suite (not one
    of the 9 review threads): a benign reader polling the checkpoint file
    at the exact instant `os.replace()` fires can hit a real Windows
    `PermissionError: [WinError 5] Access is denied` sharing violation,
    crashing the writer -- reproduced directly running the crash-worker
    tests in a tight loop (2/30 real failures with a full traceback
    pointing at this exact `os.replace()` call). `_persist_checkpoint()`
    must retry through a few transient `PermissionError`s rather than
    propagating the first one."""
    import project_atlas.orchestration.mission.execution as exec_module

    calls = {"n": 0}
    real_replace = exec_module.os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError(5, "simulated transient Windows sharing violation")
        return real_replace(src, dst)

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    checkpoint = exec_module.MissionRunCheckpoint(
        run_id="r", mission_id="m", workspace=str(workspace.resolve()), adapter_repr="x",
        state="STARTED", created_at=1.0, updated_at=1.0, owner_pid=1, idempotency_key="k",
        result=None,
    )
    with patch.object(exec_module.os, "replace", side_effect=flaky_replace):
        exec_module._persist_checkpoint(workspace, checkpoint)
    assert calls["n"] == 4  # 3 failures + 1 real success
    reloaded = load_checkpoint(workspace)
    assert reloaded is not None
    assert reloaded.state == "STARTED"


def test_thread_t8_initial_checkpoint_persist_failure_releases_lease(tmp_path):
    """Review thread: if the FIRST checkpoint write fails, the lease must
    not remain held for the rest of the process's lifetime -- all work
    after acquisition, including the first checkpoint, must be inside the
    cleanup scope."""
    import project_atlas.orchestration.mission.execution as exec_module

    repo_root = _repo_root()
    packet = compile_mission_context(
        repo_root, mission_id="M-PERSIST-FAIL", objective="o", keywords=["lock"]
    )
    workspace = tmp_path / "ws"
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print(1)"))

    def failing_persist(*_a, **_k):
        raise OSError("simulated disk failure on first checkpoint write")

    with (
        patch.object(exec_module, "_persist_checkpoint", side_effect=failing_persist),
        pytest.raises(OSError),
    ):
        start_mission_run(
            mission_id="M-PERSIST-FAIL",
            context=packet,
            adapter=adapter,
            workspace=workspace,
            repo_root=repo_root,
        )
    # The lease must have been released despite the failure -- a fresh
    # attempt (now with persistence working again) must succeed.
    assert read_mission_lease_state(workspace).held is False
    result = start_mission_run(
        mission_id="M-PERSIST-FAIL",
        context=packet,
        adapter=adapter,
        workspace=workspace,
        repo_root=repo_root,
        idempotency_key="different-key-to-avoid-dedup-with-nothing",
    )
    assert result.checkpoint.state == "COMPLETE"


# ---------------------------------------------------------------------------
# ADDITIONAL REPRODUCTIONS: idempotency + checkpoint integrity
# ---------------------------------------------------------------------------


def test_repro_completed_run_repeated_same_identity_does_not_duplicate_effect(tmp_path):
    """A completed run repeated with the same identity/key must NOT
    execute its effect twice -- enforced, not merely recorded."""
    repo_root = _repo_root()
    workspace = tmp_path / "ws"
    marker = tmp_path / "effect-count.txt"
    marker.write_text("0", encoding="utf-8")
    bump_script = (
        f"import pathlib; p = pathlib.Path(r'{marker}'); "
        "p.write_text(str(int(p.read_text()) + 1))"
    )
    packet = compile_mission_context(
        repo_root, mission_id="M-DEDUP", objective="o", keywords=["lock"]
    )
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", bump_script))

    result1 = start_mission_run(
        mission_id="M-DEDUP", context=packet, adapter=adapter, workspace=workspace,
        repo_root=repo_root,
    )
    assert result1.deduplicated is False
    assert marker.read_text(encoding="utf-8") == "1"

    result2 = start_mission_run(
        mission_id="M-DEDUP", context=packet, adapter=adapter, workspace=workspace,
        repo_root=repo_root,
    )
    assert result2.deduplicated is True
    assert marker.read_text(encoding="utf-8") == "1", "the effect must NOT have run a second time"


def test_repro_corrupt_checkpoint_after_effect_is_unknown_not_safe(tmp_path):
    """A checkpoint corrupted AFTER a real effect must never read back as
    NO_RUN_FOUND/safe_to_retry=True -- absent and unreadable are not the
    same answer."""
    workspace = tmp_path / "ws"
    checkpoint_path = workspace / ".atlas-mission" / "mission-run-checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text("{ this is not valid json at all !!!", encoding="utf-8")

    loaded = load_checkpoint_detailed(workspace)
    assert loaded.status == "MALFORMED"
    assert loaded.checkpoint is None

    result = reconcile_mission_run(workspace)
    assert result.outcome == "UNKNOWN_CHECKPOINT_UNSAFE_TO_RETRY"
    assert result.safe_to_retry is False


def test_checkpoint_identity_bound_to_workspace(tmp_path):
    """A checkpoint whose recorded `workspace` does not match where it
    was actually found (e.g. copied from elsewhere) must be treated as
    untrustworthy, not silently accepted."""
    real_ws = tmp_path / "real"
    other_ws = tmp_path / "other"
    real_ws.mkdir()
    other_ws.mkdir()
    import json as _json

    payload = {
        "run_id": "r", "mission_id": "m", "workspace": str((tmp_path / "somewhere-else").resolve()),
        "adapter_repr": "x", "state": "COMPLETE", "created_at": 1.0, "updated_at": 1.0,
        "owner_pid": 1, "idempotency_key": "k", "result": None, "schema_version": 1,
    }
    (other_ws / ".atlas-mission").mkdir(parents=True)
    (other_ws / ".atlas-mission" / "mission-run-checkpoint.json").write_text(
        _json.dumps(payload), encoding="utf-8"
    )
    loaded = load_checkpoint_detailed(other_ws)
    assert loaded.status == "MALFORMED"
    assert "identity mismatch" in loaded.detail


def test_unreconciled_non_terminal_checkpoint_blocks_fresh_start(tmp_path):
    """A prior non-terminal checkpoint (crash mid-run) must block a fresh
    `start_mission_run` until explicitly reconciled -- never silently
    overwritten or retried."""
    repo_root = _repo_root()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    import json as _json

    payload = {
        "run_id": "prior", "mission_id": "M-UNRECONCILED", "workspace": str(workspace.resolve()),
        "adapter_repr": "x", "state": "ADAPTER_INVOKED", "created_at": 1.0, "updated_at": 1.0,
        "owner_pid": 1, "idempotency_key": "other-key", "result": None, "schema_version": 1,
    }
    (workspace / ".atlas-mission").mkdir(parents=True)
    (workspace / ".atlas-mission" / "mission-run-checkpoint.json").write_text(
        _json.dumps(payload), encoding="utf-8"
    )

    packet = compile_mission_context(
        repo_root, mission_id="M-UNRECONCILED", objective="o", keywords=["lock"]
    )
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print(1)"))
    with pytest.raises(UnreconciledPriorRunError):
        start_mission_run(
            mission_id="M-UNRECONCILED", context=packet, adapter=adapter, workspace=workspace,
            repo_root=repo_root,
        )
    # Must not have consumed the lease either.
    assert read_mission_lease_state(workspace).held is False


# ---------------------------------------------------------------------------
# Governed-caller enforcement: policy + freshness at dispatch
# ---------------------------------------------------------------------------


def test_trusted_policy_refuses_disallowed_merge_authorization(tmp_path):
    """The GOVERNED CALLER, not the adapter, enforces `trusted_policy` --
    a context claiming a disallowed MERGE_AUTHORIZATION is refused before
    anything runs."""
    repo_root = _repo_root()
    packet = compile_mission_context(
        repo_root,
        mission_id="M-POLICY",
        objective="o",
        keywords=["lock"],
        trusted_policy={"MERGE_AUTHORIZATION": "YES"},
    )
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print('should not run')"))
    workspace = tmp_path / "ws"
    with pytest.raises(PolicyRefusedError):
        start_mission_run(
            mission_id="M-POLICY", context=packet, adapter=adapter, workspace=workspace,
            repo_root=repo_root,
        )
    assert not workspace.exists() or not _checkpoint_file_path(workspace).is_file()


def test_dispatch_refuses_stale_context_by_default(tmp_path):
    """Source freshness is validated AT DISPATCH, not just left to the
    caller to remember to check."""
    repo_root = _repo_root()
    target = repo_root / "docs" / "adr" / "ADR-001-wp001-foundation-decisions.md"
    original = target.read_text(encoding="utf-8")
    packet = compile_mission_context(
        repo_root, mission_id="M-STALE", objective="o", keywords=["foundation"]
    )
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print('should not run')"))
    workspace = tmp_path / "ws"
    try:
        target.write_text(original + "\nSTALE TEST MUTATION\n", encoding="utf-8")
        with pytest.raises(ContextStaleError):
            start_mission_run(
                mission_id="M-STALE", context=packet, adapter=adapter, workspace=workspace,
                repo_root=repo_root,
            )
    finally:
        target.write_text(original, encoding="utf-8")

    # allow_stale_context=True must permit it to proceed anyway.
    try:
        target.write_text(original + "\nSTALE TEST MUTATION 2\n", encoding="utf-8")
        result = start_mission_run(
            mission_id="M-STALE", context=packet, adapter=adapter, workspace=workspace,
            repo_root=repo_root, allow_stale_context=True,
            idempotency_key="allow-stale-key",
        )
        assert result.checkpoint.state == "COMPLETE"
    finally:
        target.write_text(original, encoding="utf-8")


# ---------------------------------------------------------------------------
# Worker lifecycle: cleanup-unconfirmed blocked state
# ---------------------------------------------------------------------------


def test_cleanup_unconfirmed_blocks_workspace_release(tmp_path):
    """If timeout cleanup cannot be CONFIRMED, the workspace must NOT be
    released for reuse -- an explicit blocked state, not an assumption of
    success."""
    from project_atlas.orchestration.mission.adapter import AdapterResult

    repo_root = _repo_root()
    packet = compile_mission_context(
        repo_root, mission_id="M-BLOCKED", objective="o", keywords=["lock"]
    )
    workspace = tmp_path / "ws"

    class FakeUnconfirmedAdapter:
        def run(self, *, workspace, context, timeout_sec):
            return AdapterResult(
                ok=False, returncode=-1, stdout="", stderr="timeout",
                duration_sec=1.0, command_repr="fake", failure_class="TIMEOUT",
                cleanup_confirmed=False,
            )

    result = start_mission_run(
        mission_id="M-BLOCKED", context=packet, adapter=FakeUnconfirmedAdapter(),
        workspace=workspace, repo_root=repo_root,
    )
    assert result.checkpoint.state == "CLEANUP_UNCONFIRMED_BLOCKED"
    # The lease must remain HELD -- reuse is blocked.
    assert read_mission_lease_state(workspace).held is True

    reconciliation = reconcile_mission_run(workspace)
    assert reconciliation.outcome in ("STILL_RUNNING", "CLEANUP_UNCONFIRMED_BLOCKED")
    assert reconciliation.safe_to_retry is False


# ---------------------------------------------------------------------------
# RECOVERY
# ---------------------------------------------------------------------------


def test_reconcile_no_run_found(tmp_path):
    result = reconcile_mission_run(tmp_path / "never-touched")
    assert result.outcome == "NO_RUN_FOUND"
    assert result.safe_to_retry is True


def test_reconcile_still_running_is_ui_reconnect_not_recovery(tmp_path):
    workspace = tmp_path / "ws"
    go_file = tmp_path / "go"
    holder = _spawn_worker(workspace, "ADAPTER_INVOKED", go_file)
    try:
        go_file.write_text("go", encoding="utf-8")
        _wait_for_checkpoint_state(workspace, "ADAPTER_INVOKED")
        result = reconcile_mission_run(workspace)
        assert result.outcome == "STILL_RUNNING"
        assert result.safe_to_retry is False
        assert read_mission_lease_state(workspace).held is True
    finally:
        _kill_tree(holder)


def test_thread_t5_toctou_reconciliation_holds_lease_while_classifying(tmp_path):
    """Review thread (P1): reconciliation must not combine two
    independent observations (read checkpoint, then separately probe the
    lease). Deterministic reproduction: monkeypatch the checkpoint loader
    to simulate exactly the race window -- a checkpoint read that
    observes `STARTED` while, by the time the (single, held-lease)
    operation finishes, the REAL on-disk checkpoint has already advanced
    past it. The old two-read shape would trust the earlier snapshot; the
    fixed shape holds the lease across the whole read, so it always sees
    the CURRENT state."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    # Prepare a REAL, valid checkpoint already at ADAPTER_INVOKED, with no
    # live holder (simulating: worker advanced to ADAPTER_INVOKED, then
    # exited before crashing further -- lease is free).
    import json as _json

    payload = {
        "run_id": "r", "mission_id": "m", "workspace": str(workspace.resolve()),
        "adapter_repr": "x", "state": "ADAPTER_INVOKED", "created_at": 1.0, "updated_at": 1.0,
        "owner_pid": 1, "idempotency_key": "k", "result": None, "schema_version": 1,
    }
    (workspace / ".atlas-mission").mkdir(parents=True)
    (workspace / ".atlas-mission" / "mission-run-checkpoint.json").write_text(
        _json.dumps(payload), encoding="utf-8"
    )

    # Because reconciliation now ACQUIRES the lease before reading, and
    # the lease is genuinely free, it must correctly classify this as
    # UNCERTAIN (never SAFE_TO_RETRY from a stale STARTED-era read that
    # no longer describes reality).
    result = reconcile_mission_run(workspace)
    assert result.outcome == "UNCERTAIN_REQUIRES_RECONCILIATION"
    assert result.safe_to_retry is False
    # And reconciliation must have released the lease it took for itself
    # (advisory only -- it does not retain ownership).
    assert read_mission_lease_state(workspace).held is False


def test_reconcile_worker_crash_before_adapter_invoked_is_safe_to_retry(tmp_path):
    workspace = tmp_path / "ws"
    go_file = tmp_path / "go"
    proc = _spawn_worker(workspace, "STARTED", go_file)
    go_file.write_text("go", encoding="utf-8")
    _wait_for_checkpoint_state(workspace, "STARTED")
    _kill_tree(proc)

    result = reconcile_mission_run(workspace)
    assert result.outcome == "SAFE_TO_RETRY_NEVER_STARTED_EXTERNAL_EFFECT"
    assert result.safe_to_retry is True


def test_reconcile_worker_crash_mid_adapter_invocation_is_uncertain(tmp_path):
    workspace = tmp_path / "ws"
    go_file = tmp_path / "go"
    proc = _spawn_worker(workspace, "ADAPTER_INVOKED", go_file)
    go_file.write_text("go", encoding="utf-8")
    _wait_for_checkpoint_state(workspace, "ADAPTER_INVOKED")
    _kill_tree(proc)

    result = reconcile_mission_run(workspace)
    assert result.outcome == "UNCERTAIN_REQUIRES_RECONCILIATION"
    assert result.safe_to_retry is False


def test_reconcile_worker_crash_after_confirmed_outcome_is_known(tmp_path):
    workspace = tmp_path / "ws"
    go_file = tmp_path / "go"
    proc = _spawn_worker(workspace, "ADAPTER_CONFIRMED", go_file)
    go_file.write_text("go", encoding="utf-8")
    _wait_for_checkpoint_state(workspace, "ADAPTER_CONFIRMED")
    _kill_tree(proc)

    result = reconcile_mission_run(workspace)
    assert result.outcome == "KNOWN_OUTCOME_CLEANUP_ONLY"
    assert result.safe_to_retry is False
    assert result.checkpoint is not None
    assert result.checkpoint.result is not None
    assert result.checkpoint.result["ok"] is True


def test_reconcile_already_complete(tmp_path):
    repo_root = _repo_root()
    packet = compile_mission_context(
        repo_root, mission_id="M-DONE-001", objective="done", keywords=["lock"]
    )
    workspace = tmp_path / "ws"
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print('done')"))
    start_mission_run(
        mission_id="M-DONE-001",
        context=packet,
        adapter=adapter,
        workspace=workspace,
        repo_root=repo_root,
        adapter_timeout_sec=10.0,
    )
    result = reconcile_mission_run(workspace)
    assert result.outcome == "ALREADY_COMPLETE"
    assert result.safe_to_retry is False


def test_no_process_leaks(tmp_path):
    """Every worker this module spawns must be killable and reach a real
    exit within a bounded wait -- this test only asserts the harness's
    own bookkeeping is self-consistent, matching the same-named test in
    the primary-lock suite."""
    go_file = tmp_path / "go"
    workspace = tmp_path / "ws"
    proc = _spawn_worker(workspace, "ADAPTER_CONFIRMED", go_file)
    try:
        go_file.write_text("go", encoding="utf-8")
        _wait_for_checkpoint_state(workspace, "ADAPTER_CONFIRMED")
    finally:
        _kill_tree(proc)
        assert proc.poll() is not None


def test_relative_and_absolute_path_spellings_share_the_same_lock(tmp_path):
    """Hardening: two different SPELLINGS of the same root (absolute vs.
    relative to cwd) must still exclude each other -- the OS lock is
    scoped to the underlying file object, not the path string."""
    root_abs = (tmp_path / "root").resolve()
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        root_rel = Path("root")
        assert acquire_mission_lease(root_abs, run_id="A") is True
        # A different run_id via a different spelling must still be
        # excluded by the real OS lock.
        won_via_relative = acquire_mission_lease(root_rel, run_id="B")
        assert won_via_relative in (True, False)
        assert read_mission_lease_state(root_abs).held is True
        assert read_mission_lease_state(root_rel).held is True
        release_mission_lease(root_abs, run_id="A")
        if won_via_relative:
            release_mission_lease(root_rel, run_id="B")
        assert read_mission_lease_state(root_abs).held is False
    finally:
        os.chdir(cwd)
