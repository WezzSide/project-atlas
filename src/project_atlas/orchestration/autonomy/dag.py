"""Explicit Work DAG transitions for AS-ORCH-AUTONOMY-001."""

from __future__ import annotations

from project_atlas.orchestration.autonomy.models import NodeState, TransitionRecord, WorkNode

ALLOWED_TRANSITIONS: dict[NodeState, frozenset[NodeState]] = {
    NodeState.DISCOVERED: frozenset(
        {NodeState.READY, NodeState.BLOCKED, NodeState.SUPERSEDED, NodeState.OWNER_HELD}
    ),
    NodeState.READY: frozenset(
        {
            NodeState.LEASED,
            NodeState.BLOCKED,
            NodeState.SUPERSEDED,
            NodeState.OWNER_HELD,
            NodeState.CLOSED,
        }
    ),
    NodeState.LEASED: frozenset({NodeState.ACTIVE, NodeState.READY, NodeState.BLOCKED}),
    NodeState.ACTIVE: frozenset(
        {NodeState.VERIFYING, NodeState.REMEDIATING, NodeState.BLOCKED, NodeState.OWNER_HELD}
    ),
    NodeState.VERIFYING: frozenset(
        {NodeState.CERTIFIED, NodeState.REMEDIATING, NodeState.BLOCKED, NodeState.OWNER_HELD}
    ),
    NodeState.REMEDIATING: frozenset({NodeState.ACTIVE, NodeState.BLOCKED, NodeState.OWNER_HELD}),
    NodeState.CERTIFIED: frozenset(
        {NodeState.OWNER_HELD, NodeState.MERGE_ELIGIBLE, NodeState.CLOSED}
    ),
    NodeState.OWNER_HELD: frozenset(
        {NodeState.MERGE_ELIGIBLE, NodeState.BLOCKED, NodeState.CLOSED, NodeState.SUPERSEDED}
    ),
    NodeState.MERGE_ELIGIBLE: frozenset(
        {NodeState.MERGED, NodeState.OWNER_HELD, NodeState.BLOCKED}
    ),
    NodeState.MERGED: frozenset({NodeState.CLOSED}),
    NodeState.BLOCKED: frozenset(
        {
            NodeState.REMEDIATING,
            NodeState.SUPERSEDED,
            NodeState.CLOSED,
            NodeState.OWNER_HELD,
        }
    ),
    NodeState.SUPERSEDED: frozenset({NodeState.CLOSED}),
    NodeState.CLOSED: frozenset(),
}

TERMINAL_STATES: frozenset[NodeState] = frozenset({NodeState.CLOSED})


class IllegalTransitionError(ValueError):
    """Unknown or illegal DAG transition. Fail closed."""

    code = "ILLEGAL_DAG_TRANSITION"


def assert_transition(
    from_state: NodeState,
    to_state: NodeState,
    *,
    allow_materialization_recovery: bool = False,
) -> None:
    allowed = ALLOWED_TRANSITIONS.get(from_state)
    recovery_ready = (
        allow_materialization_recovery
        and from_state == NodeState.BLOCKED
        and to_state == NodeState.READY
    )
    if allowed is None or (to_state not in allowed and not recovery_ready):
        raise IllegalTransitionError(f"illegal transition {from_state.value} -> {to_state.value}")


def apply_transition(
    node: WorkNode,
    to_state: NodeState,
    *,
    reason: str,
    sequence: int,
    allow_materialization_recovery: bool = False,
) -> tuple[WorkNode, TransitionRecord]:
    """Return a replaced node and an auditable record. Does not grant authority."""
    assert_transition(
        node.state,
        to_state,
        allow_materialization_recovery=allow_materialization_recovery,
    )
    if to_state == NodeState.MERGED:
        raise IllegalTransitionError("governor cannot autonomously transition to MERGED")
    updated = node.model_copy(update={"state": to_state})
    record = TransitionRecord(
        sequence=sequence,
        package_id=node.package_id,
        from_state=node.state,
        to_state=to_state,
        reason=reason[:256],
    )
    return updated, record
