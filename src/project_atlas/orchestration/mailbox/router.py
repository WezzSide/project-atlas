"""Pure, deterministic bridge from inbox messages to AS-ORCH-001A/001B."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError

from project_atlas.orchestration.mailbox.models import (
    AgentInboxMessage,
    InboxMessageKind,
    InboxRoutingResult,
)
from project_atlas.orchestration.models import NextTransition, RouteKind, TaskType
from project_atlas.orchestration.router import route
from project_atlas.orchestration.transitions import classify_envelope
from project_atlas.orchestration.validator import ResultValidationError, parse_envelope


class InboxRouter:
    """Revalidate result evidence and return only a typed non-authoritative route."""

    def __init__(
        self,
        *,
        trusted_head: str,
        trusted_tree: str,
        identity_verifier: Callable[[AgentInboxMessage], bool],
        binding_verifier: Callable[[AgentInboxMessage], bool],
    ) -> None:
        self.trusted_head = trusted_head
        self.trusted_tree = trusted_tree
        self.identity_verifier = identity_verifier
        self.binding_verifier = binding_verifier

    def classify(self, message: AgentInboxMessage) -> InboxRoutingResult:
        if (message.trusted_head, message.trusted_tree) != (
            self.trusted_head,
            self.trusted_tree,
        ):
            return InboxRoutingResult(
                classification="QUARANTINE",
                reason_code="STALE_SOURCE_PIN",
            )
        try:
            identity_verified = self.identity_verifier(message)
        except Exception:
            identity_verified = False
        if not identity_verified:
            return InboxRoutingResult(
                classification="QUARANTINE",
                reason_code="PRODUCER_IDENTITY_UNVERIFIED",
            )
        try:
            binding_verified = self.binding_verifier(message)
        except Exception:
            binding_verified = False
        if not binding_verified:
            return InboxRoutingResult(
                classification="QUARANTINE",
                reason_code="TASK_ATTEMPT_BINDING_UNVERIFIED",
            )
        if message.message_kind not in {
            InboxMessageKind.AGENT_RESULT,
            InboxMessageKind.BLOCKED,
        }:
            if message.message_kind == InboxMessageKind.VERIFICATION_RESULT:
                return InboxRoutingResult(
                    classification="WAIT_EXTERNAL",
                    reason_code="VERIFICATION_CANDIDATE_BINDING_NOT_IN_001A",
                )
            return InboxRoutingResult(
                classification="WAIT_EXTERNAL",
                reason_code="MESSAGE_KIND_REQUIRES_SEPARATE_HANDLER",
            )
        if message.expires_at is not None and message.expires_at <= datetime.now(UTC):
            return InboxRoutingResult(classification="QUARANTINE", reason_code="MESSAGE_EXPIRED")
        if message.run_id is None or message.dispatch_id is None or message.attempt_id is None:
            return InboxRoutingResult(
                classification="QUARANTINE",
                reason_code="INCOMPLETE_ATTEMPT_BINDING",
            )
        raw_envelope = message.payload.get("result_envelope")
        if raw_envelope is None:
            raw_envelope = message.payload
        try:
            envelope = parse_envelope(raw_envelope)
        except (ResultValidationError, ValidationError, TypeError, ValueError):
            return InboxRoutingResult(classification="QUARANTINE", reason_code="RESULT_INVALID")
        if envelope.task.id != message.task_id:
            return InboxRoutingResult(classification="QUARANTINE", reason_code="TASK_MISMATCH")
        if envelope.producer != message.producer:
            return InboxRoutingResult(classification="QUARANTINE", reason_code="PRODUCER_MISMATCH")
        if message.message_kind == InboxMessageKind.BLOCKED and envelope.outcome.value != "BLOCKED":
            return InboxRoutingResult(classification="QUARANTINE", reason_code="OUTCOME_MISMATCH")
        if message.reason_code is not None and message.reason_code not in {
            item.code for item in envelope.blockers
        }:
            return InboxRoutingResult(classification="QUARANTINE", reason_code="REASON_MISMATCH")
        known_recovery_code = "LOCAL_EXECUTOR_SETUP_REFRESH_FAILED"
        envelope_blocker_codes = {item.code for item in envelope.blockers}
        if known_recovery_code in envelope_blocker_codes and (
            message.reason_code != known_recovery_code or not message.retryable
        ):
            return InboxRoutingResult(
                classification="WAIT_EXTERNAL",
                reason_code="PRESTART_RECOVERY_MESSAGE_FACTS_INCOMPLETE",
            )
        if message.reason_code == known_recovery_code:
            recovery_facts = envelope.observations.extras
            if (
                not message.retryable
                or recovery_facts.get("retryable") is not True
                or recovery_facts.get("process_started") is not False
            ):
                return InboxRoutingResult(
                    classification="WAIT_EXTERNAL",
                    reason_code="PRESTART_RECOVERY_FACTS_INCOMPLETE",
                )
        decision = classify_envelope(envelope)
        route_out = route(decision, envelope)
        classification: Literal[
            "CONTINUATION_ELIGIBLE",
            "TASK_DIRECTIVE_READY",
            "OWNER_REQUIRED",
            "WAIT_EXTERNAL",
            "FAIL_TERMINAL",
            "QUARANTINE",
        ]
        if decision.next_transition == NextTransition.OWNER_REQUIRED:
            classification = "OWNER_REQUIRED"
        elif (
            decision.next_transition == NextTransition.AUTONOMOUS_RECONCILE
            and route_out.route_kind == RouteKind.TASK
            and route_out.task_type == TaskType.PROGRAM_RECONCILIATION
            and route_out.dispatchable
        ):
            classification = "CONTINUATION_ELIGIBLE"
        elif route_out.route_kind == RouteKind.TASK and route_out.dispatchable:
            classification = "TASK_DIRECTIVE_READY"
        elif decision.next_transition == NextTransition.BLOCKED:
            classification = "WAIT_EXTERNAL" if message.retryable else "FAIL_TERMINAL"
        elif decision.next_transition == NextTransition.REJECTED:
            classification = "QUARANTINE"
        else:
            classification = "FAIL_TERMINAL"
        return InboxRoutingResult(
            classification=classification,
            decision=decision.model_dump(mode="json"),
            route=route_out.model_dump(mode="json"),
            reason_code=(decision.reasons[-1] if decision.reasons else None),
        )
