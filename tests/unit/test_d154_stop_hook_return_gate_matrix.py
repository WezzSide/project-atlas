"""D-154 — stop-hook fail-closed return gate adversarial matrix.

Tests the production entry path ``handle_stop_event`` / ``emit_stop_followup``,
not helper functions in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.continuation_broker import emit_stop_followup
from project_atlas.orchestration.autonomy.return_gate import (
    STOP_HOOK_RETURN_GATE_MARKER,
    AutonomyReturnState,
)
from project_atlas.orchestration.cursor_bridge import handle_stop_event

_PIN = "7e797468a2eca37c959920912b1fa264df4be638"
_TREE = "03c3bf404d7916a971c4d40f1378b6ff0c08fe6a"


def _stop(**kwargs: object) -> dict[str, object]:
    payload: dict[str, object] = {"status": "completed", "loop_count": 1}
    payload.update(kwargs)
    return payload


def _runtime(root: Path) -> Path:
    path = root / ".atlas" / "orchestration" / "sdk-runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_d147_checkpoint(
    root: Path,
    *,
    return_state: dict[str, Any] | None = None,
    counts: dict[str, int] | None = None,
    raw_return_state: object = None,
) -> None:
    runtime = _runtime(root)
    state = AutonomyReturnState.model_validate(
        return_state or {"genuine_owner_frontier": True, "closure_integrity_pass": True}
    )
    payload: dict[str, Any] = {
        "directive": "D-147R",
        "return_state": (raw_return_state if raw_return_state is not None else state.model_dump()),
    }
    if counts:
        payload.update(counts)
    (runtime / "d147-checkpoint.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _assert_terminal_allowed(response: dict[str, str]) -> None:
    assert response == {}


def _assert_terminal_blocked(response: dict[str, str]) -> None:
    assert "followup_message" in response
    assert STOP_HOOK_RETURN_GATE_MARKER in response["followup_message"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ready_nodes", 1),
        ("derivable_successors", 1),
        ("uncertified_changes", 1),
        ("preparable_blocked_work", 1),
        ("running_nodes", 1),
        ("dispatched_nodes", 1),
        ("recoverable_failed_nodes", 1),
        ("review_required_nodes", 1),
        ("active_workers", 1),
        ("valid_p0_p1", 1),
    ],
    ids=[
        "ready",
        "derivable",
        "uncertified",
        "self_remediable",
        "running",
        "dispatched",
        "recoverable_failed",
        "review_required",
        "active_workers",
        "valid_p0_p1",
    ],
)
def test_d154_stop_hook_blocks_when_counter_nonzero(tmp_path: Path, field: str, value: int) -> None:
    """Cases 2-11: nonzero executable counters forbid terminal/no-followup."""
    _write_d147_checkpoint(
        tmp_path,
        return_state={
            "genuine_owner_frontier": True,
            "closure_integrity_pass": True,
            field: value,
        },
    )
    _assert_terminal_blocked(handle_stop_event(_stop(), root=tmp_path))
    _assert_terminal_blocked(emit_stop_followup(tmp_path, loop_count=1))


def test_d154_case1_all_clear_allows_terminal(tmp_path: Path) -> None:
    """Broker idle, counters zero, owner frontier satisfied => terminal allowed."""
    _write_d147_checkpoint(
        tmp_path,
        return_state={
            "genuine_owner_frontier": True,
            "closure_integrity_pass": True,
        },
    )
    _assert_terminal_allowed(handle_stop_event(_stop(), root=tmp_path))
    _assert_terminal_allowed(emit_stop_followup(tmp_path))


def test_d154_case12_owner_only_frontier_allows_terminal(tmp_path: Path) -> None:
    _write_d147_checkpoint(
        tmp_path,
        return_state={
            "genuine_owner_frontier": True,
            "closure_integrity_pass": True,
        },
        counts={"blocked_owner": 2},
    )
    _assert_terminal_allowed(handle_stop_event(_stop(), root=tmp_path))


def test_d154_case13_external_ci_blocked_but_ready_work_forbids_return(
    tmp_path: Path,
) -> None:
    _write_d147_checkpoint(
        tmp_path,
        return_state={
            "external_hard_blocker": True,
            "ready_nodes": 1,
            "closure_integrity_pass": True,
        },
        counts={"ready": 1},
    )
    _assert_terminal_blocked(handle_stop_event(_stop(), root=tmp_path))


def test_d154_case14_missing_checkpoint_fails_closed(tmp_path: Path) -> None:
    _assert_terminal_blocked(handle_stop_event(_stop(), root=tmp_path))
    _assert_terminal_blocked(emit_stop_followup(tmp_path, loop_count=1))


def test_d154_case15_corrupt_checkpoint_fails_closed(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    (runtime / "d147-checkpoint.json").write_text("{not-json", encoding="utf-8")
    _assert_terminal_blocked(handle_stop_event(_stop(), root=tmp_path))


def test_d154_case15_stale_return_state_rejected(tmp_path: Path) -> None:
    _write_d147_checkpoint(tmp_path, raw_return_state={"ready_nodes": "many"})
    _assert_terminal_blocked(handle_stop_event(_stop(), root=tmp_path))


def test_d154_project_terminal_liveness(tmp_path: Path) -> None:
    """True terminal state must terminate cleanly without forced followup loops."""
    _write_d147_checkpoint(
        tmp_path,
        return_state={"project_terminal": True},
    )
    _assert_terminal_allowed(handle_stop_event(_stop(), root=tmp_path))
    _assert_terminal_allowed(handle_stop_event(_stop(loop_count=2), root=tmp_path))


def test_d154_broker_queued_followup_not_replaced_by_return_gate(tmp_path: Path) -> None:
    """Safety without false continuation: queued broker successor keeps priority."""
    from project_atlas.orchestration.autonomy.continuation_broker import (
        BROKER_MARKER,
        SuccessorKind,
        enqueue_successor,
    )

    _write_d147_checkpoint(tmp_path, return_state={"ready_nodes": 5})
    enqueue_successor(
        tmp_path,
        cycle_id="CYCLE-D154",
        kind=SuccessorKind.CHECKPOINT_CONTINUE,
        trusted_main=_PIN,
        trusted_tree=_TREE,
        next_action_class="MONITOR_EXACT_HEAD_CI",
    )
    response = handle_stop_event(_stop(loop_count=0), root=tmp_path)
    assert "followup_message" in response
    assert BROKER_MARKER in response["followup_message"]
    assert STOP_HOOK_RETURN_GATE_MARKER not in response["followup_message"]
