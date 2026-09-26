"""Deterministic transition classifier for AS-ORCH-001A.

Classifies what may happen next. Does not execute the transition, dispatch
another agent, merge code, grant authority, or mutate production state.

Precedence (safety overrides positive workflow status):
  1. malformed / schema-invalid (handled by the validator before this module)
  2. unauthorized mutation
  3. invalid or missing required receipt
  4. explicit blocker / failure
  5. target movement / stale baseline
  6. owner-gated state
  7. normal successful transition
  8. unknown routing-critical state

Classification is pure, deterministic, and replay-safe. It does not read the
clock, network, GitHub, or filesystem. requested_transition is advisory only.
"""

from __future__ import annotations

from project_atlas.orchestration.models import (
    OWNER_GATED_STATES,
    AgentResultEnvelope,
    NextTransition,
    OrchestrationDecision,
    ProducerRole,
    ResultOutcome,
    WorkflowState,
)

_RECOVERABLE_PRESTART_BLOCKER = "LOCAL_EXECUTOR_SETUP_REFRESH_FAILED"


def classify_envelope(envelope: AgentResultEnvelope) -> OrchestrationDecision:
    """Classify a schema-valid envelope. Equivalent inputs yield equivalent decisions."""
    reasons: list[str] = []
    if envelope.requested_transition is not None:
        reasons.append("requested_transition_advisory_only")

    # 2. Unauthorized mutation overrides every positive status.
    if envelope.observations.unauthorized_mutations > 0:
        reasons.append("unauthorized_mutations")
        return _decision(
            envelope,
            next_transition=NextTransition.REJECTED,
            workflow_state=WorkflowState.REJECTED,
            owner_required=False,
            reasons=reasons,
        )

    # 3. Success (and any trusted routing) requires a valid governed receipt.
    if envelope.receipt is None:
        reasons.append("missing_required_receipt")
        return _decision(
            envelope,
            next_transition=NextTransition.REJECTED,
            workflow_state=WorkflowState.REJECTED,
            owner_required=False,
            reasons=reasons,
        )
    if not envelope.receipt.is_valid_evidence():
        reasons.append("invalid_or_rejected_receipt")
        return _decision(
            envelope,
            next_transition=NextTransition.REJECTED,
            workflow_state=WorkflowState.REJECTED,
            owner_required=False,
            reasons=reasons,
        )

    # A narrowly identified launcher failure happened before a worker process
    # started. Route it to the existing reconciliation policy, but only when
    # the structured result carries all fail-closed facts. This remains a
    # classification: 001B emits a non-authoritative directive and the
    # governor/lease/001E path still decides whether any work can run.
    if (
        envelope.outcome == ResultOutcome.BLOCKED
        and envelope.state == "BLOCKED"
        and len(envelope.blockers) == 1
        and envelope.blockers[0].code == _RECOVERABLE_PRESTART_BLOCKER
        and envelope.observations.target_moved is False
        and envelope.observations.unauthorized_mutations == 0
        and envelope.observations.extras.get("retryable") is True
        and envelope.observations.extras.get("process_started") is False
    ):
        reasons.append("known_prestart_infrastructure_failure")
        return _decision(
            envelope,
            next_transition=NextTransition.AUTONOMOUS_RECONCILE,
            workflow_state=WorkflowState.BLOCKED,
            owner_required=False,
            reasons=reasons,
        )

    # 4. Explicit blocker / failure.
    if envelope.blockers:
        reasons.append("explicit_blockers")
        return _decision(
            envelope,
            next_transition=NextTransition.BLOCKED,
            workflow_state=WorkflowState.BLOCKED,
            owner_required=False,
            reasons=reasons,
        )
    if envelope.outcome == ResultOutcome.FAIL:
        if envelope.producer.role == ProducerRole.LOCAL:
            reasons.append("local_fail")
            return _decision(
                envelope,
                next_transition=NextTransition.BLOCKED,
                workflow_state=WorkflowState.BLOCKED,
                owner_required=False,
                reasons=reasons,
            )
        reasons.append("non_local_fail")
        return _decision(
            envelope,
            next_transition=NextTransition.REMEDIATION_REQUIRED,
            workflow_state=WorkflowState.REMEDIATION_REQUIRED,
            owner_required=False,
            reasons=reasons,
        )
    if envelope.outcome == ResultOutcome.BLOCKED:
        reasons.append("outcome_blocked")
        return _decision(
            envelope,
            next_transition=NextTransition.BLOCKED,
            workflow_state=WorkflowState.BLOCKED,
            owner_required=False,
            reasons=reasons,
        )

    # 5. Stale baseline / target movement.
    if envelope.observations.target_moved:
        reasons.append("target_moved")
        return _decision(
            envelope,
            next_transition=NextTransition.RECERTIFY_REQUIRED,
            workflow_state=WorkflowState.RECERTIFY_REQUIRED,
            owner_required=False,
            reasons=reasons,
        )

    # 6. Owner-gated state. MERGE_ELIGIBLE never becomes MERGE.
    if envelope.state in OWNER_GATED_STATES:
        reasons.append("merge_eligible_owner_gate")
        return _decision(
            envelope,
            next_transition=NextTransition.OWNER_REQUIRED,
            workflow_state=WorkflowState.OWNER_REQUIRED,
            owner_required=True,
            reasons=reasons,
        )

    # 7. Normal successful transition (PASS + known certified state).
    if envelope.outcome == ResultOutcome.PASS and envelope.state == "CERTIFIED":
        if envelope.producer.role == ProducerRole.LOCAL:
            reasons.append("local_pass_certified")
            return _decision(
                envelope,
                next_transition=NextTransition.INTEGRATION_VERIFY,
                workflow_state=WorkflowState.LOCAL_ACCEPTED,
                owner_required=False,
                reasons=reasons,
            )
        if envelope.producer.role == ProducerRole.INTEGRATION:
            reasons.append("integration_pass_certified")
            return _decision(
                envelope,
                next_transition=NextTransition.AUTONOMOUS_RECONCILE,
                workflow_state=WorkflowState.INTEGRATION_ACCEPTED,
                owner_required=False,
                reasons=reasons,
            )
        reasons.append("unspecified_autonomous_success_path")
        return _decision(
            envelope,
            next_transition=NextTransition.BLOCKED_UNKNOWN_STATE,
            workflow_state=WorkflowState.BLOCKED_UNKNOWN_STATE,
            owner_required=False,
            reasons=reasons,
        )

    # 8. Unknown routing-critical state or unspecified combination.
    reasons.append("unknown_routing_critical_state")
    return _decision(
        envelope,
        next_transition=NextTransition.BLOCKED_UNKNOWN_STATE,
        workflow_state=WorkflowState.BLOCKED_UNKNOWN_STATE,
        owner_required=False,
        reasons=reasons,
    )


def _decision(
    envelope: AgentResultEnvelope,
    *,
    next_transition: NextTransition,
    workflow_state: WorkflowState,
    owner_required: bool,
    reasons: list[str],
) -> OrchestrationDecision:
    return OrchestrationDecision(
        valid=True,
        producer=envelope.producer.role,
        task=envelope.task.id,
        outcome=envelope.outcome,
        workflow_state=workflow_state,
        next_transition=next_transition,
        execution_authorized=False,
        owner_required=owner_required,
        merge_authorized=False,
        reasons=reasons,
        requested_transition=envelope.requested_transition,
    )
