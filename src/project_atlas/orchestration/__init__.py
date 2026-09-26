"""AS-ORCH-001A/001B/001C + AUTONOMY-001 — result, routing, bridge, governor.

STRUCTURED_RESULT_CONTRACT = IMPLEMENTED
DETERMINISTIC_CLASSIFICATION = IMPLEMENTED
DETERMINISTIC_POLICY_ROUTING = IMPLEMENTED
TYPED_TASK_DIRECTIVE = IMPLEMENTED
CURSOR_BRIDGE_CORE = IMPLEMENTED
CURSOR_STOP_HOOK_ADAPTER = IMPLEMENTED
EXPLICIT_COMPLETION_TRANSPORT = IMPLEMENTED
AUTONOMOUS_GOVERNOR = IMPLEMENTED
WORK_DAG = IMPLEMENTED
AGENT_LEASE_MODEL = IMPLEMENTED
SURFACE_OVERLAP_GATE = IMPLEMENTED
AUTONOMOUS_CONTINUATION_POLICY = IMPLEMENTED
AUTOMATIC_REMEDIATION = IMPLEMENTED
IV_ROUTING = IMPLEMENTED
ADVERSARIAL_REVIEW_TRIGGER = IMPLEMENTED
EVIDENCE_CONTRACT = IMPLEMENTED
OWNER_GATES_A_F = IMPLEMENTED
AUTHENTIC_CURSOR_STOP_EVENT_DELIVERY = NOT_RELIABLE_IN_CURRENT_WINDOWS_CLI_RUNTIME
AUTHENTIC_CURSOR_STOP_EVENT_DELIVERY = ENVIRONMENT_DEPENDENT
HOOK_RUNTIME_REQUIRED_FOR_CORE_FLOW = NO
PRIMARY_CONTINUATION_BACKEND = CURSOR_SDK_DURABLE_AGENT_RUNTIME
STOP_HOOK_BACKEND = FALLBACK
CURSOR_SDK_DURABLE_AGENT_RUNTIME = IMPLEMENTED
CROSS_AGENT_DISPATCH = IMPLEMENTED
AGENT_DISPATCH = IMPLEMENTED
SINGLE_HOP_AGENT_DISPATCHER = IMPLEMENTED
MULTI_HOP_AUTODISPATCH = NOT_IMPLEMENTED
AUTONOMOUS_LOOP = IMPLEMENTED
AUTONOMOUS_LOOP_001E = IMPLEMENTED
SUCCESSOR_EXECUTION_UNDER_NEW_MODEL = ACTIVE
AUTOMATIC_MERGE = NOT_IMPLEMENTED
OWNER AUTHORITY = STILL REQUIRED

The Cursor SDK durable agent runtime is the primary continuation backend.
The Cursor stop hook is fallback / safety / legacy UI compatibility only.
Atlas remains the source of workflow truth. RUN_TERMINAL != DAG_END.
``execution_authorized`` is always false.
"""

from project_atlas.orchestration.cursor_bridge import (
    CompletionTransport,
    CursorBridgeResponse,
    CursorBridgeState,
    CursorStopEvent,
    HandoffPacket,
    complete_staged_handoff,
    handle_stop_event,
    stage_result,
    surface_pending_handoff,
)
from project_atlas.orchestration.dispatcher import (
    DispatchReceipt,
    DispatchRecord,
    run_dispatch_once,
)
from project_atlas.orchestration.mailbox import (
    AgentInboxMessage,
    AgentMailbox,
    InboxMessageKind,
    InboxRouter,
    MailboxStatus,
)
from project_atlas.orchestration.models import (
    PACKAGE_ID,
    SCHEMA_KIND,
    TRUTH_BOUNDARY,
    AgentResultEnvelope,
    NextTransition,
    OrchestrationDecision,
    OrchestrationRoute,
    ProducerRole,
    RequestedTransition,
    ResultOutcome,
    RouteKind,
    TaskDirective,
    TaskType,
    WorkflowState,
)
from project_atlas.orchestration.policy import (
    POLICY_ID,
    POLICY_VERSION,
    ROUTING_PACKAGE_ID,
    resolve_policy,
)
from project_atlas.orchestration.router import (
    RouteConsistencyError,
    route,
    route_payload,
    run_route_result,
    source_result_digest,
)
from project_atlas.orchestration.transitions import classify_envelope
from project_atlas.orchestration.validator import (
    ResultValidationError,
    parse_envelope,
    run_validate_result,
    validate_and_classify,
)

__all__ = [
    "PACKAGE_ID",
    "POLICY_ID",
    "POLICY_VERSION",
    "ROUTING_PACKAGE_ID",
    "SCHEMA_KIND",
    "TRUTH_BOUNDARY",
    "AgentInboxMessage",
    "AgentMailbox",
    "AgentResultEnvelope",
    "CompletionTransport",
    "CursorBridgeResponse",
    "CursorBridgeState",
    "CursorStopEvent",
    "DispatchReceipt",
    "DispatchRecord",
    "HandoffPacket",
    "InboxMessageKind",
    "InboxRouter",
    "MailboxStatus",
    "NextTransition",
    "OrchestrationDecision",
    "OrchestrationRoute",
    "ProducerRole",
    "RequestedTransition",
    "ResultOutcome",
    "ResultValidationError",
    "RouteConsistencyError",
    "RouteKind",
    "TaskDirective",
    "TaskType",
    "WorkflowState",
    "classify_envelope",
    "complete_staged_handoff",
    "handle_stop_event",
    "parse_envelope",
    "resolve_policy",
    "route",
    "route_payload",
    "run_dispatch_once",
    "run_route_result",
    "run_validate_result",
    "source_result_digest",
    "stage_result",
    "surface_pending_handoff",
    "validate_and_classify",
]
