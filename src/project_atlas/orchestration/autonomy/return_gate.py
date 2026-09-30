"""D-146 — fail-closed outer-session return gate.

Shell/CI/certification completion must not imply owner return.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PACKAGE_ID: Final[str] = "AS-D146-AUTONOMY-RETURN-GATE-001"
D147_CHECKPOINT_REL: Final[Path] = (
    Path(".atlas") / "orchestration" / "sdk-runtime" / "d147-checkpoint.json"
)
STOP_HOOK_RETURN_GATE_MARKER: Final[str] = "[ATLAS_STOP_HOOK_RETURN_GATE]"


class AutonomyReturnState(BaseModel):
    """Counts and frontier flags for outer-session return decisions."""

    model_config = ConfigDict(extra="forbid")

    package_id: str = PACKAGE_ID
    ready_nodes: int = Field(default=0, ge=0)
    running_nodes: int = Field(default=0, ge=0)
    recoverable_failed_nodes: int = Field(default=0, ge=0)
    uncertified_changes: int = Field(default=0, ge=0)
    derivable_successors: int = Field(default=0, ge=0)
    preparable_blocked_work: int = Field(default=0, ge=0)
    integration_ready: int = Field(default=0, ge=0)
    post_merge_seal_pending: int = Field(default=0, ge=0)
    dispatched_nodes: int = Field(default=0, ge=0)
    review_required_nodes: int = Field(default=0, ge=0)
    active_workers: int = Field(default=0, ge=0)
    valid_p0_p1: int = Field(default=0, ge=0)
    closure_integrity_pass: bool = False
    genuine_owner_frontier: bool = False
    external_hard_blocker: bool = False
    project_terminal: bool = False


def autonomous_work_exists(state: AutonomyReturnState) -> bool:
    return any(
        [
            state.ready_nodes > 0,
            state.running_nodes > 0,
            state.recoverable_failed_nodes > 0,
            state.uncertified_changes > 0,
            state.derivable_successors > 0,
            state.preparable_blocked_work > 0,
            state.integration_ready > 0,
            state.post_merge_seal_pending > 0,
            state.dispatched_nodes > 0,
            state.review_required_nodes > 0,
            state.active_workers > 0,
            state.valid_p0_p1 > 0,
        ]
    )


def may_emit_final_return(state: AutonomyReturnState) -> bool:
    """Return True only when no autonomous work remains and a frontier exists."""
    if autonomous_work_exists(state):
        return False
    if state.external_hard_blocker or state.project_terminal:
        return True
    return state.genuine_owner_frontier and state.closure_integrity_pass


def final_response_precheck(state: AutonomyReturnState) -> dict[str, object]:
    """Machine-enforced precheck before outer agent final response."""
    work = autonomous_work_exists(state)
    allowed = may_emit_final_return(state)
    frontier = state.genuine_owner_frontier or state.external_hard_blocker or state.project_terminal
    return {
        "package_id": PACKAGE_ID,
        "autonomous_work_exists": work,
        "may_emit_final_return": allowed,
        "suppress_final_response": not allowed and (work or frontier),
        "continue_dag": work,
        "merge_authorized": False,
    }


def _overlay_checkpoint_counts(
    state: AutonomyReturnState, payload: dict[str, object]
) -> AutonomyReturnState:
    """Merge top-level D-147 mission counts into the return gate snapshot."""
    count_fields = {
        "ready": "ready_nodes",
        "running": "running_nodes",
        "failed_recoverable": "recoverable_failed_nodes",
        "uncertified": "uncertified_changes",
        "derivable": "derivable_successors",
        "blocked_dependency_self_remediable": "preparable_blocked_work",
        "dispatched": "dispatched_nodes",
        "review_required": "review_required_nodes",
        "active_workers": "active_workers",
    }
    updates: dict[str, int | bool] = {}
    for source, target in count_fields.items():
        raw = payload.get(source)
        if isinstance(raw, int) and raw >= 0:
            updates[target] = max(getattr(state, target), raw)
    p0 = payload.get("valid_p0")
    p1 = payload.get("valid_p1")
    p0_i = int(p0) if isinstance(p0, int) and p0 >= 0 else 0
    p1_i = int(p1) if isinstance(p1, int) and p1 >= 0 else 0
    updates["valid_p0_p1"] = max(state.valid_p0_p1, p0_i + p1_i)
    if updates:
        return state.model_copy(update=updates)
    return state


def load_authoritative_return_state(root: Path) -> AutonomyReturnState | None:
    """Load persisted D-147 return gate state. None when absent or invalid."""
    checkpoint_path = root / D147_CHECKPOINT_REL
    if not checkpoint_path.is_file():
        return None
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("return_state")
    if not isinstance(raw, dict):
        return None
    try:
        state = AutonomyReturnState.model_validate(raw)
    except ValidationError:
        return None
    return _overlay_checkpoint_counts(state, payload)


def stop_hook_terminal_return_allowed(root: Path) -> bool:
    """Fail closed when authoritative return state is unavailable or work remains."""
    state = load_authoritative_return_state(root)
    if state is None:
        return False
    return may_emit_final_return(state)


def render_stop_hook_dag_continuation() -> str:
    """Trusted fixed template blocking premature Cursor final return."""
    return (
        f"{STOP_HOOK_RETURN_GATE_MARKER}\n"
        "ACTION: CONTINUE_AUTONOMOUS_DAG\n"
        "FINAL_RETURN: BLOCKED\n"
        "REASON: autonomous_work_remains\n"
    )
