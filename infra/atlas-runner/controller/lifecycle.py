"""Worker lifecycle state machine (AS-RUNNER-001).

Contract: every worker execution moves through this fixed state graph;
terminal states are absorbing. EXECUTOR_SUCCESS != VERIFIED: a COMPLETE
worker only means the job ran and evidence was collected, never that the
result is authoritative.
"""

from __future__ import annotations

REQUESTED = "REQUESTED"
ADMITTED = "ADMITTED"
PROVISIONING = "PROVISIONING"
REGISTERING = "REGISTERING"
READY = "READY"
ASSIGNED = "ASSIGNED"
RUNNING = "RUNNING"
COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
DEREGISTERING = "DEREGISTERING"
DESTROYING = "DESTROYING"
COMPLETE = "COMPLETE"
FAILED = "FAILED"
TIMED_OUT = "TIMED_OUT"
CLEANUP_REQUIRED = "CLEANUP_REQUIRED"
BLOCKED = "BLOCKED"

ALL_STATES = frozenset(
    {
        REQUESTED,
        ADMITTED,
        PROVISIONING,
        REGISTERING,
        READY,
        ASSIGNED,
        RUNNING,
        COLLECTING_EVIDENCE,
        DEREGISTERING,
        DESTROYING,
        COMPLETE,
        FAILED,
        TIMED_OUT,
        CLEANUP_REQUIRED,
        BLOCKED,
    }
)

TERMINAL_STATES = frozenset({COMPLETE, FAILED, TIMED_OUT, CLEANUP_REQUIRED, BLOCKED})

# Allowed transitions. Anything not listed here is invalid and fails closed.
TRANSITIONS: dict[str, frozenset[str]] = {
    REQUESTED: frozenset({ADMITTED, BLOCKED}),
    ADMITTED: frozenset({PROVISIONING, BLOCKED}),
    PROVISIONING: frozenset({REGISTERING, DESTROYING, FAILED}),
    REGISTERING: frozenset({READY, DESTROYING, FAILED}),
    READY: frozenset({ASSIGNED, DEREGISTERING, FAILED}),
    ASSIGNED: frozenset({RUNNING, DEREGISTERING, FAILED}),
    RUNNING: frozenset(
        {COLLECTING_EVIDENCE, DEREGISTERING, DESTROYING, TIMED_OUT, FAILED}
    ),
    COLLECTING_EVIDENCE: frozenset({DEREGISTERING, DESTROYING, FAILED}),
    DEREGISTERING: frozenset({DESTROYING, FAILED, CLEANUP_REQUIRED}),
    DESTROYING: frozenset({COMPLETE, CLEANUP_REQUIRED, FAILED, TIMED_OUT}),
}


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(from_state: str, to_state: str) -> bool:
    """True iff the transition is allowed by the fixed state graph."""
    if from_state in TERMINAL_STATES:
        return False
    return to_state in TRANSITIONS.get(from_state, frozenset())
