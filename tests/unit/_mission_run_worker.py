"""Standalone process spawned by
`test_mission_vertical_slice.py` to genuinely crash a mission run at a
controlled checkpoint state, for real (SIGKILL/TerminateProcess from the
test), not simulated.

Usage: _mission_run_worker.py <workspace> <stop_after_state> <go_file>
  stop_after_state: STARTED | ADAPTER_INVOKED | ADAPTER_CONFIRMED
    The process blocks (sleeps) right after reaching that checkpoint
    state, so the test can kill it there deterministically.
"""

from __future__ import annotations

import sys
import time
from dataclasses import asdict
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1])
    stop_after_state = sys.argv[2]
    go_file = Path(sys.argv[3])

    import os
    import time as _time
    import uuid

    from project_atlas.orchestration.mission.adapter import ShellCommandAdapter
    from project_atlas.orchestration.mission.context_packet import compile_mission_context
    from project_atlas.orchestration.mission.execution import (
        MissionRunCheckpoint,
        _persist_checkpoint,
    )
    from project_atlas.orchestration.mission.lease import acquire_mission_lease

    deadline = time.time() + 30.0
    while not go_file.is_file():
        if time.time() > deadline:
            return 1
        time.sleep(0.001)

    rid = uuid.uuid4().hex
    if not acquire_mission_lease(workspace, run_id=rid):
        return 1

    # Workspace now exists (acquire_mission_lease mkdir'd it) -- record
    # the SAME resolved identity `load_checkpoint_detailed` will expect.
    workspace_identity = str(workspace.resolve())

    now = _time.time()
    checkpoint = MissionRunCheckpoint(
        run_id=rid,
        mission_id="M-CRASH-TEST",
        workspace=workspace_identity,
        adapter_repr="ShellCommandAdapter",
        state="STARTED",
        created_at=now,
        updated_at=now,
        owner_pid=os.getpid(),
        idempotency_key=f"crash-test:{rid}",
        result=None,
    )
    _persist_checkpoint(workspace, checkpoint)
    if stop_after_state == "STARTED":
        time.sleep(30.0)
        return 0

    checkpoint = MissionRunCheckpoint(**{**asdict(checkpoint), "state": "ADAPTER_INVOKED"})
    _persist_checkpoint(workspace, checkpoint)
    if stop_after_state == "ADAPTER_INVOKED":
        time.sleep(30.0)
        return 0

    repo_root = Path(__file__).resolve().parents[2]
    context = compile_mission_context(
        repo_root, mission_id="M-CRASH-TEST", objective="crash test", keywords=["lock"]
    )
    adapter = ShellCommandAdapter(command=(sys.executable, "-c", "print('ok')"))
    result = adapter.run(workspace=workspace, context=context, timeout_sec=10.0)
    checkpoint = MissionRunCheckpoint(
        **{**asdict(checkpoint), "state": "ADAPTER_CONFIRMED", "result": asdict(result)}
    )
    _persist_checkpoint(workspace, checkpoint)
    if stop_after_state == "ADAPTER_CONFIRMED":
        time.sleep(30.0)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
