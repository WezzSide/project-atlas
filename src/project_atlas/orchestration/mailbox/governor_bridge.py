"""Fail-closed materialization of a validated mailbox directive into 001E WorkNode."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import ValidationError

from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, GovernorError
from project_atlas.orchestration.autonomy.models import (
    AgentCapability,
    ExecutionHostClass,
    IvRequirements,
    MutationSurface,
    NodeState,
    WorkNode,
)
from project_atlas.orchestration.mailbox.models import (
    ACTIVE_SUCCESSOR_LIFECYCLES,
    AgentInboxMessage,
    MailboxSuccessorBindingV1,
    MailboxSuccessorRecord,
    SuccessorLifecycle,
    canonical_json,
)
from project_atlas.orchestration.mailbox.store import AgentMailbox
from project_atlas.orchestration.models import OrchestrationRoute, TaskType

_RECOVERY_REASON = "LOCAL_EXECUTOR_SETUP_REFRESH_FAILED"
_ALLOWED_TASKS = frozenset(
    {
        TaskType.CANDIDATE_VERIFICATION,
        TaskType.RECERTIFICATION,
        TaskType.PROGRAM_RECONCILIATION,
    }
)


class SuccessorAdmissionError(ValueError):
    """A typed, fail-closed mailbox-to-governor admission error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MaterializationRecoveryRequest:
    """Observed durable state a reconciler requests to recover, not authority."""

    package_id: str
    generation: int
    lifecycle_revision: int
    lifecycle: SuccessorLifecycle
    owner_token: str | None

    @classmethod
    def from_record(cls, item: MailboxSuccessorRecord) -> MaterializationRecoveryRequest:
        return cls(
            item.binding.package_id,
            item.generation,
            item.lifecycle_revision,
            item.lifecycle,
            item.materialization_owner_token,
        )


@dataclass(frozen=True)
class MaterializationClaim:
    """Durable generation/revision/token fence required for governor effects."""

    package_id: str
    generation: int
    lifecycle_revision: int
    owner_token: str


class MailboxGovernorBridge:
    """Persists a governed successor binding, then idempotently adds its WorkNode.

    This adapter never leases work or calls DispatchPort. The autonomous loop
    remains the sole owner of selection, lease and dispatch.
    """

    def __init__(
        self,
        *,
        mailbox: AgentMailbox,
        governor: AutonomousGovernor,
        authority_verifier: Callable[[AgentInboxMessage], bool],
        candidate_identity_verifier: Callable[[AgentInboxMessage, str, str], bool] | None = None,
        retry_verifier: Callable[[AgentInboxMessage, str, MailboxSuccessorRecord], bool]
        | None = None,
    ) -> None:
        self.mailbox = mailbox
        self.governor = governor
        self.authority_verifier = authority_verifier
        self.candidate_identity_verifier = candidate_identity_verifier
        self.retry_verifier = retry_verifier
        # A READY record may be restored only when this bridge observed that
        # exact generation/revision as READY at startup. This distinguishes a
        # genuine restarted governor from a second live bridge that was already
        # present before a competing admission completed.
        self._startup_successors = {
            item.binding.package_id: (item.generation, item.lifecycle_revision, item.lifecycle)
            for item in mailbox.successor_records()
        }
        for item in mailbox.successor_records():
            self._register_execution_guard(item.binding.package_id)

    def admit(
        self,
        message_id: str,
        *,
        retry_id: str | None = None,
        prior_successor_id: str | None = None,
        prior_generation: int | None = None,
    ) -> WorkNode:
        record = self.mailbox.get_record(message_id)
        if record is None or record.status.value != "PROCESSED":
            raise SuccessorAdmissionError("SUCCESSOR_SOURCE_NOT_PROCESSED")
        message = record.message
        snapshot = self.governor.snapshot()
        if snapshot.target_moved or (snapshot.current_main, snapshot.current_tree) != (
            message.trusted_head,
            message.trusted_tree,
        ):
            raise SuccessorAdmissionError("SUCCESSOR_SOURCE_PIN_STALE")
        if not message.requester_id or not message.authority_reference:
            raise SuccessorAdmissionError("SUCCESSOR_REQUESTER_OR_AUTHORITY_REFERENCE_MISSING")
        if message.run_id is None or message.dispatch_id is None or message.attempt_id is None:
            raise SuccessorAdmissionError("SUCCESSOR_ATTEMPT_BINDING_MISSING")
        try:
            authorized = self.authority_verifier(message)
        except Exception:
            authorized = False
        if not authorized:
            raise SuccessorAdmissionError("SUCCESSOR_AUTHORITY_NOT_VERIFIED")

        route_source = record
        routing = record.routing or {}
        if routing.get("classification") == "DUPLICATE_MESSAGE":
            if not record.duplicate_of:
                raise SuccessorAdmissionError("DUPLICATE_RESULT_SOURCE_MISSING")
            duplicate_source = self.mailbox.get_record(record.duplicate_of)
            if duplicate_source is None or duplicate_source.status.value != "PROCESSED":
                raise SuccessorAdmissionError("DUPLICATE_RESULT_SOURCE_INVALID")
            route_source = duplicate_source
            routing = route_source.routing or {}
        if routing.get("classification") not in {
            "CONTINUATION_ELIGIBLE",
            "TASK_DIRECTIVE_READY",
        }:
            raise SuccessorAdmissionError("SUCCESSOR_ROUTE_NOT_ELIGIBLE")
        route_value = routing.get("route")
        if not isinstance(route_value, dict):
            raise SuccessorAdmissionError("SUCCESSOR_ROUTE_MISSING")
        try:
            route = OrchestrationRoute.model_validate(route_value)
        except (ValidationError, TypeError, ValueError):
            raise SuccessorAdmissionError("SUCCESSOR_ROUTE_INVALID") from None
        directive = route.task
        if (
            not route.dispatchable
            or route.task_type not in _ALLOWED_TASKS
            or directive is None
            or directive.owner_gate is not False
            or directive.execution_authorized is not False
            or directive.permissions.authority_grant
            or directive.permissions.merge
            or directive.permissions.production_mutation
            or directive.permissions.repository_write
            or directive.permissions.branch_write
            or directive.permissions.pull_request_write
        ):
            raise SuccessorAdmissionError("SUCCESSOR_DIRECTIVE_NOT_ADMISSIBLE")

        candidate_head: str | None = None
        candidate_tree: str | None = None
        if route.task_type in {
            TaskType.CANDIDATE_VERIFICATION,
            TaskType.RECERTIFICATION,
        }:
            binding = message.payload.get("candidate_binding")
            if not isinstance(binding, dict):
                raise SuccessorAdmissionError("CANDIDATE_BINDING_REQUIRED")
            head, tree = binding.get("candidate_head"), binding.get("candidate_tree")
            if not isinstance(head, str) or not isinstance(tree, str):
                raise SuccessorAdmissionError("CANDIDATE_BINDING_INVALID")
            try:
                candidate_ok = bool(
                    self.candidate_identity_verifier
                    and self.candidate_identity_verifier(message, head, tree)
                )
            except Exception:
                candidate_ok = False
            if not candidate_ok:
                raise SuccessorAdmissionError("CANDIDATE_IDENTITY_UNVERIFIED")
            candidate_head, candidate_tree = head, tree

        if route.task_type == TaskType.PROGRAM_RECONCILIATION:
            self._validate_read_only_recovery(message, route)

        task_type = route.task_type
        if task_type is None:
            raise SuccessorAdmissionError("SUCCESSOR_TASK_TYPE_MISSING")
        required = (
            (AgentCapability.DISCOVER,)
            if task_type == TaskType.PROGRAM_RECONCILIATION
            else (AgentCapability.VERIFY,)
        )
        route_digest = self._route_digest(route, candidate_head, candidate_tree)
        incident_records = tuple(
            item
            for item in self.mailbox.successor_records()
            if item.binding.incident_id == record.incident_id
        )
        active = [item for item in incident_records if item.lifecycle.value != "TERMINAL"]
        if len(active) > 1:
            raise SuccessorAdmissionError("SUCCESSOR_REQUIRES_RECONCILIATION")
        if retry_id is not None and (
            prior_successor_id is None or prior_generation is None or self.retry_verifier is None
        ):
            raise SuccessorAdmissionError("SUCCESSOR_RETRY_BINDING_REQUIRED")
        if retry_id is None and (prior_successor_id is not None or prior_generation is not None):
            raise SuccessorAdmissionError("SUCCESSOR_RETRY_ID_REQUIRED")
        message_successor = next(
            (item for item in incident_records if item.binding.message_id == message_id), None
        )
        if message_successor is not None:
            prior_binding = message_successor.binding
            if (
                prior_binding.source_task_id != message.task_id
                or prior_binding.source_attempt_id != message.attempt_id
                or prior_binding.source_result_digest != message.payload_digest
                or prior_binding.requester_id != message.requester_id
                or prior_binding.authority_reference != message.authority_reference
                or prior_binding.trusted_main != message.trusted_head
                or prior_binding.trusted_tree != message.trusted_tree
                or prior_binding.candidate_head != candidate_head
                or prior_binding.candidate_tree != candidate_tree
                or prior_binding.route_digest != route_digest
                or prior_binding.task_type != task_type.value
                or prior_binding.transition != directive.transition.value
            ):
                raise SuccessorAdmissionError("SUCCESSOR_CURRENT_CONTEXT_MISMATCH")
            if retry_id is not None and message_successor.retry_id != retry_id:
                raise SuccessorAdmissionError("SUCCESSOR_RETRY_ID_COLLISION")
            if message_successor.lifecycle == SuccessorLifecycle.TERMINAL:
                raise SuccessorAdmissionError("SUCCESSOR_ALREADY_TERMINAL")
            return self._materialize_for_admission(message_successor)
        if retry_id is not None:
            prior = next((item for item in incident_records if item.retry_id == retry_id), None)
            if prior is not None:
                if prior_generation is None:
                    raise SuccessorAdmissionError("SUCCESSOR_RETRY_BINDING_REQUIRED")
                prior_source = next(
                    (
                        item
                        for item in incident_records
                        if item.binding.package_id == prior_successor_id
                    ),
                    None,
                )
                if (
                    prior.supersedes_package_id != prior_successor_id
                    or prior.generation != prior_generation + 1
                ):
                    raise SuccessorAdmissionError("SUCCESSOR_RETRY_ID_COLLISION")
                if prior_source is None or not self._verify_retry(message, retry_id, prior_source):
                    raise SuccessorAdmissionError("SUCCESSOR_RETRY_NOT_AUTHORIZED")
                return self._materialize_for_admission(prior)
        generation = 1
        supersedes_package_id = None
        if active:
            generation = active[0].generation
        elif incident_records:
            previous = max(incident_records, key=lambda item: item.generation)
            if retry_id is None:
                raise SuccessorAdmissionError("SUCCESSOR_ALREADY_TERMINAL")
            if (
                previous.binding.package_id != prior_successor_id
                or previous.generation != prior_generation
                or previous.lifecycle != SuccessorLifecycle.TERMINAL
                or not self._verify_retry(message, retry_id, previous)
                or directive.transition.value
                not in {
                    "AUTONOMOUS_RECONCILE",
                    "RECERTIFY_REQUIRED",
                    "REMEDIATION_REQUIRED",
                }
            ):
                raise SuccessorAdmissionError("SUCCESSOR_RETRY_NOT_AUTHORIZED")
            generation = previous.generation + 1
            supersedes_package_id = previous.binding.package_id
        elif retry_id is not None:
            raise SuccessorAdmissionError("SUCCESSOR_RETRY_SOURCE_NOT_TERMINAL")
        package_material = {
            "project_id": message.project_id,
            "incident_id": record.incident_id,
            "generation": generation,
        }
        package_id = "MBX-SUCC-" + hashlib.sha256(canonical_json(package_material)).hexdigest()[:24]
        task_type_value = cast(
            Literal["candidate_verification", "recertification", "program_reconciliation"],
            task_type.value,
        )
        capability_values: tuple[Literal["DISCOVER", "VERIFY"], ...] = (
            ("DISCOVER",) if task_type == TaskType.PROGRAM_RECONCILIATION else ("VERIFY",)
        )
        active_records = [
            item
            for item in self.mailbox.successor_records()
            if item.binding.incident_id == record.incident_id
            and item.lifecycle in ACTIVE_SUCCESSOR_LIFECYCLES
        ]
        if active_records:
            if len(active_records) != 1:
                raise SuccessorAdmissionError("SUCCESSOR_REQUIRES_RECONCILIATION")
            active_successor = active_records[0]
            existing_source = self.mailbox.get_record(active_successor.binding.message_id)
            existing_route_value = (
                (existing_source.routing or {}).get("route") if existing_source else None
            )
            try:
                existing_route = OrchestrationRoute.model_validate(existing_route_value)
            except (ValidationError, TypeError, ValueError):
                raise SuccessorAdmissionError("SUCCESSOR_ROUTE_INVALID") from None
            if (
                active_successor.binding.requester_id != message.requester_id
                or active_successor.binding.authority_reference != message.authority_reference
                or active_successor.binding.source_task_id != message.task_id
                or active_successor.binding.trusted_main != message.trusted_head
                or active_successor.binding.trusted_tree != message.trusted_tree
                or active_successor.binding.task_type != task_type.value
                or active_successor.binding.transition != directive.transition.value
                or active_successor.binding.candidate_head != candidate_head
                or active_successor.binding.candidate_tree != candidate_tree
                or self._route_policy_signature(existing_route)
                != self._route_policy_signature(route)
            ):
                raise SuccessorAdmissionError("DUPLICATE_ACTIVE_SUCCESSOR")
            return self._materialize_for_admission(active_successor)
        binding = MailboxSuccessorBindingV1(
            package_id=package_id,
            message_id=message.message_id,
            incident_id=record.incident_id,
            source_task_id=message.task_id,
            source_attempt_id=message.attempt_id,
            source_result_digest=message.payload_digest,
            route_digest=route_digest,
            correlation_id=message.correlation_id,
            causation_id=message.causation_id,
            requester_id=message.requester_id,
            trusted_main=message.trusted_head,
            trusted_tree=message.trusted_tree,
            candidate_head=candidate_head,
            candidate_tree=candidate_tree,
            task_type=task_type_value,
            transition=directive.transition.value,
            required_capabilities=capability_values,
            authority_reference=message.authority_reference,
            binding_digest="0" * 64,
        ).seal()
        work_node = self._work_node(
            package_id=package_id,
            source_task_id=message.task_id,
            task_type=task_type,
            base_pin=candidate_head or message.trusted_head,
            required=required,
        )
        persisted, _duplicate = self.mailbox.persist_successor(
            message_id=message_id,
            binding=binding,
            work_node=work_node.model_dump(mode="json"),
            generation=generation,
            supersedes_package_id=supersedes_package_id,
            retry_id=retry_id,
        )
        return self._materialize_for_admission(persisted)

    def _materialize_for_admission(self, successor: MailboxSuccessorRecord) -> WorkNode:
        materialized = self._materialize(successor)
        if materialized is None:
            raise SuccessorAdmissionError("SUCCESSOR_MATERIALIZATION_REQUIRES_RECONCILIATION")
        return materialized

    def _verify_retry(
        self, message: AgentInboxMessage, retry_id: str, prior: MailboxSuccessorRecord
    ) -> bool:
        try:
            return bool(self.retry_verifier and self.retry_verifier(message, retry_id, prior))
        except Exception:
            return False

    def reconcile(self) -> tuple[WorkNode, ...]:
        """Rebuild/reconcile nodes using a generation-scoped recovery fence."""
        nodes: list[WorkNode] = []
        for item in self.mailbox.successor_records():
            if item.lifecycle == SuccessorLifecycle.TERMINAL:
                continue
            existing = next(
                (
                    node
                    for node in self.governor.snapshot().nodes
                    if node.package_id == item.binding.package_id
                ),
                None,
            )
            recovery_request: MaterializationRecoveryRequest | None = None
            if item.lifecycle in {
                SuccessorLifecycle.MATERIALIZING,
                SuccessorLifecycle.WAIT_RECONCILIATION,
            } or (
                item.lifecycle == SuccessorLifecycle.READY
                and (existing is None or existing.state == NodeState.DISCOVERED)
            ):
                recovery_request = MaterializationRecoveryRequest.from_record(item)
            elif item.lifecycle == SuccessorLifecycle.PREPARED:
                materialized = self._materialize(item)
                if materialized is not None:
                    nodes.append(materialized)
                continue
            if recovery_request is not None:
                materialized = self._materialize(item, recovery_request=recovery_request)
                if materialized is not None:
                    nodes.append(materialized)
                continue
            if existing is None:
                self._validate_current_materialization(item)
                self.mailbox.set_successor_lifecycle(
                    item.binding.package_id, SuccessorLifecycle.WAIT_RECONCILIATION
                )
                continue
            expected_node, _message = self._validate_current_materialization(item)
            if existing.model_copy(update={"state": NodeState.DISCOVERED}) != expected_node:
                raise SuccessorAdmissionError("WORKNODE_IDENTITY_COLLISION")
            observed_lifecycle = self._lifecycle_for_state(existing.state)
            if item.lifecycle != observed_lifecycle:
                self.mailbox.set_successor_lifecycle(item.binding.package_id, observed_lifecycle)
            nodes.append(existing)
        return tuple(nodes)

    def _validate_current_materialization(
        self, successor: MailboxSuccessorRecord
    ) -> tuple[WorkNode, AgentInboxMessage]:
        """Revalidate all executable successor context against current state."""
        binding = successor.binding.verify()
        source = self.mailbox.get_record(binding.message_id)
        if source is None or source.status.value != "PROCESSED":
            raise SuccessorAdmissionError("SUCCESSOR_SOURCE_NOT_PROCESSED")
        message = source.message
        snapshot = self.governor.snapshot()
        if snapshot.target_moved or (snapshot.current_main, snapshot.current_tree) != (
            binding.trusted_main,
            binding.trusted_tree,
        ):
            raise SuccessorAdmissionError("SUCCESSOR_SOURCE_PIN_STALE")
        if (
            message.project_id != self.mailbox.project_id
            or message.requester_id != binding.requester_id
            or message.authority_reference != binding.authority_reference
            or message.attempt_id != binding.source_attempt_id
            or message.payload_digest != binding.source_result_digest
            or message.correlation_id != binding.correlation_id
            or message.causation_id != binding.causation_id
            or not self._verify_authority(message)
        ):
            raise SuccessorAdmissionError("SUCCESSOR_AUTHORITY_NOT_VERIFIED")
        route_value = (source.routing or {}).get("route")
        if (source.routing or {}).get("classification") == "DUPLICATE_MESSAGE":
            duplicate_of = source.duplicate_of
            duplicate_source = self.mailbox.get_record(duplicate_of) if duplicate_of else None
            if duplicate_source is None or duplicate_source.status.value != "PROCESSED":
                raise SuccessorAdmissionError("DUPLICATE_RESULT_SOURCE_INVALID")
            route_value = (duplicate_source.routing or {}).get("route")
        if not isinstance(route_value, dict):
            raise SuccessorAdmissionError("SUCCESSOR_ROUTE_MISSING")
        try:
            route = OrchestrationRoute.model_validate(route_value)
        except (ValidationError, TypeError, ValueError):
            raise SuccessorAdmissionError("SUCCESSOR_ROUTE_INVALID") from None
        directive = route.task
        if (
            not route.dispatchable
            or route.task_type not in _ALLOWED_TASKS
            or directive is None
            or directive.owner_gate is not False
            or directive.execution_authorized is not False
            or directive.permissions.authority_grant
            or directive.permissions.merge
            or directive.permissions.production_mutation
            or directive.permissions.repository_write
            or directive.permissions.branch_write
            or directive.permissions.pull_request_write
        ):
            raise SuccessorAdmissionError("SUCCESSOR_DIRECTIVE_NOT_ADMISSIBLE")
        if self._route_digest(route, binding.candidate_head, binding.candidate_tree) != (
            binding.route_digest
        ):
            raise SuccessorAdmissionError("SUCCESSOR_ROUTE_DIGEST_MISMATCH")
        candidate_bound = binding.candidate_head is not None or binding.candidate_tree is not None
        if candidate_bound and (
            binding.candidate_head is None
            or binding.candidate_tree is None
            or not self._verify_candidate(message, binding.candidate_head, binding.candidate_tree)
        ):
            raise SuccessorAdmissionError("CANDIDATE_IDENTITY_UNVERIFIED")
        if route.task_type == TaskType.PROGRAM_RECONCILIATION:
            self._validate_read_only_recovery(message, route)
        try:
            expected_node = self._node_for_binding(binding)
            persisted_node = WorkNode.model_validate(successor.work_node)
        except (ValidationError, TypeError, ValueError):
            raise SuccessorAdmissionError("PERSISTED_WORKNODE_INVALID") from None
        if persisted_node != expected_node:
            raise SuccessorAdmissionError("PERSISTED_WORKNODE_BINDING_MISMATCH")
        return expected_node, message

    def _verify_authority(self, message: AgentInboxMessage) -> bool:
        try:
            return bool(self.authority_verifier(message))
        except Exception:
            return False

    def _verify_candidate(self, message: AgentInboxMessage, head: str, tree: str) -> bool:
        if self.candidate_identity_verifier is None:
            return False
        try:
            return bool(self.candidate_identity_verifier(message, head, tree))
        except Exception:
            return False

    @staticmethod
    def _route_digest(
        route: OrchestrationRoute,
        candidate_head: str | None,
        candidate_tree: str | None,
    ) -> str:
        directive = route.task
        if directive is None:
            raise SuccessorAdmissionError("SUCCESSOR_ROUTE_MISSING")
        semantic_route = {
            # Provenance is bound separately by the successor binding; the route digest
            # identifies policy/action semantics so cross-producer observations coalesce.
            "transition": directive.transition.value,
            "target": directive.target.model_dump(mode="json"),
            "task_type": directive.task_type.value,
            "permissions": directive.permissions.model_dump(mode="json"),
            "owner_gate": directive.owner_gate,
            "execution_authorized": directive.execution_authorized,
            "policy_id": directive.policy_id,
            "policy_version": directive.policy_version,
            "inputs": directive.inputs.model_dump(mode="json"),
            "candidate_head": candidate_head,
            "candidate_tree": candidate_tree,
        }
        return hashlib.sha256(canonical_json(semantic_route)).hexdigest()

    @staticmethod
    def _route_policy_signature(route: OrchestrationRoute) -> bytes:
        directive = route.task
        if directive is None:
            raise SuccessorAdmissionError("SUCCESSOR_ROUTE_MISSING")
        return canonical_json(
            {
                "task_type": directive.task_type.value,
                "transition": directive.transition.value,
                "target": directive.target.model_dump(mode="json"),
                "permissions": directive.permissions.model_dump(mode="json"),
                "owner_gate": directive.owner_gate,
                "execution_authorized": directive.execution_authorized,
                "policy_id": directive.policy_id,
                "policy_version": directive.policy_version,
                "inputs": directive.inputs.model_dump(mode="json"),
            }
        )

    @staticmethod
    def _node_for_binding(binding: MailboxSuccessorBindingV1) -> WorkNode:
        task_type = TaskType(binding.task_type)
        required = (
            (AgentCapability.DISCOVER,)
            if task_type == TaskType.PROGRAM_RECONCILIATION
            else (AgentCapability.VERIFY,)
        )
        return MailboxGovernorBridge._work_node(
            package_id=binding.package_id,
            source_task_id=binding.source_task_id,
            task_type=task_type,
            base_pin=binding.candidate_head or binding.trusted_main,
            required=required,
        )

    @staticmethod
    def _validate_read_only_recovery(message: AgentInboxMessage, route: OrchestrationRoute) -> None:
        envelope = message.payload.get("result_envelope")
        if not isinstance(envelope, dict):
            raise SuccessorAdmissionError("RECOVERY_RESULT_MISSING")
        observations = envelope.get("observations")
        extras = observations.get("extras") if isinstance(observations, dict) else None
        blockers = envelope.get("blockers")
        target_moved = observations.get("target_moved") if isinstance(observations, dict) else None
        unauthorized_mutations = (
            observations.get("unauthorized_mutations") if isinstance(observations, dict) else None
        )
        if (
            message.reason_code != _RECOVERY_REASON
            or not message.retryable
            or route.task_type != TaskType.PROGRAM_RECONCILIATION
            or not isinstance(extras, dict)
            or extras.get("retryable") is not True
            or extras.get("process_started") is not False
            or target_moved is not False
            or unauthorized_mutations != 0
            or not isinstance(blockers, list)
            or [item.get("code") for item in blockers if isinstance(item, dict)]
            != [_RECOVERY_REASON]
        ):
            raise SuccessorAdmissionError("RECOVERY_FACTS_NOT_ADMISSIBLE")

    @staticmethod
    def _work_node(
        *,
        package_id: str,
        source_task_id: str,
        task_type: TaskType,
        base_pin: str,
        required: tuple[AgentCapability, ...],
    ) -> WorkNode:
        objectives = {
            TaskType.PROGRAM_RECONCILIATION: (
                f"Read-only reconcile the pre-start executor setup failure for {source_task_id}"
            ),
            TaskType.CANDIDATE_VERIFICATION: (
                f"Independently verify the pinned candidate for {source_task_id}"
            ),
            TaskType.RECERTIFICATION: f"Recertify the pinned target for {source_task_id}",
        }
        criteria = {
            TaskType.PROGRAM_RECONCILIATION: ("READ_ONLY_EXECUTOR_RECONCILIATION",),
            TaskType.CANDIDATE_VERIFICATION: ("VERIFY_BOUND_CANDIDATE",),
            TaskType.RECERTIFICATION: ("RECERTIFY_BOUND_TARGET",),
        }
        surface_suffix = hashlib.sha256(package_id.encode("ascii")).hexdigest()[:16]
        return WorkNode(
            package_id=package_id,
            objective=objectives[task_type],
            base_pin=base_pin,
            dependencies=(),
            mutation_surface=MutationSurface(
                surface_id=f"mbx-read-{surface_suffix}",
                paths=(),
                semantic="READ_ONLY_MAILBOX_SUCCESSOR",
            ),
            execution_host_class=ExecutionHostClass.EXTERNAL_AGENT,
            agent_capabilities_required=required,
            acceptance_criteria=criteria[task_type],
            test_requirements=("RESULT_BOUND_TO_SOURCE_INCIDENT",),
            iv_requirements=IvRequirements(certification_required=False),
            owner_gate=None,
            state=NodeState.DISCOVERED,
            risk_tags=(),
            destructive=False,
            merge_authorized=False,
            execution_authorized=False,
        )

    def _materialize(
        self,
        successor: MailboxSuccessorRecord,
        *,
        recovery_request: MaterializationRecoveryRequest | None = None,
    ) -> WorkNode | None:
        """Materialize only under a generation lock and current durable fence."""
        package_id = successor.binding.package_id
        self._register_execution_guard(package_id)
        try:
            with self.mailbox.materialization_guard(package_id, successor.generation) as guard:
                current = self.mailbox.get_successor(package_id)
                if current is None:
                    raise SuccessorAdmissionError("SUCCESSOR_NOT_FOUND")
                if current.generation != successor.generation:
                    raise SuccessorAdmissionError("SUCCESSOR_GENERATION_MISMATCH")
                if (
                    current.binding != successor.binding
                    or current.work_node_digest != successor.work_node_digest
                ):
                    raise SuccessorAdmissionError("SUCCESSOR_CURRENT_CONTEXT_MISMATCH")
                existing = next(
                    (
                        item
                        for item in self.governor.snapshot().nodes
                        if item.package_id == package_id
                    ),
                    None,
                )
                try:
                    node, _message = self._validate_current_materialization(current)
                except SuccessorAdmissionError:
                    if recovery_request is not None:
                        if existing is not None and existing.state == NodeState.READY:
                            self.governor.transition(
                                package_id,
                                NodeState.BLOCKED,
                                "mailbox materialization requires current authority revalidation",
                            )
                        if current.lifecycle == SuccessorLifecycle.MATERIALIZING:
                            if current.materialization_owner_token is None:
                                raise SuccessorAdmissionError(
                                    "MATERIALIZATION_FENCE_INVALID"
                                ) from None
                            self.mailbox.defer_materialization_recovery(
                                package_id,
                                generation=current.generation,
                                expected_revision=current.lifecycle_revision,
                                owner_token=current.materialization_owner_token,
                                guard=guard,
                            )
                        elif current.lifecycle == SuccessorLifecycle.READY:
                            self.mailbox.set_successor_lifecycle(
                                package_id, SuccessorLifecycle.WAIT_RECONCILIATION
                            )
                        return None
                    raise

                if (
                    existing is not None
                    and existing.model_copy(update={"state": NodeState.DISCOVERED}) != node
                ):
                    raise SuccessorAdmissionError("WORKNODE_IDENTITY_COLLISION")
                if current.lifecycle == SuccessorLifecycle.TERMINAL:
                    raise SuccessorAdmissionError("SUCCESSOR_REQUIRES_RECONCILIATION")

                if current.lifecycle == SuccessorLifecycle.READY:
                    if existing is not None and existing.state != NodeState.DISCOVERED:
                        observed_lifecycle = self._lifecycle_for_state(existing.state)
                        if observed_lifecycle != current.lifecycle:
                            self.mailbox.set_successor_lifecycle(package_id, observed_lifecycle)
                        return existing
                    if recovery_request is None:
                        raise SuccessorAdmissionError("SUCCESSOR_REQUIRES_RECONCILIATION")
                    if recovery_request.lifecycle == SuccessorLifecycle.MATERIALIZING:
                        # Another recovery completed while this caller waited.
                        # Do not resurrect READY in this caller's stale governor.
                        return None
                    startup_identity = self._startup_successors.get(package_id)
                    if (
                        recovery_request.lifecycle != SuccessorLifecycle.READY
                        or startup_identity
                        != (
                            recovery_request.generation,
                            recovery_request.lifecycle_revision,
                            SuccessorLifecycle.READY,
                        )
                        or current.lifecycle_revision != recovery_request.lifecycle_revision
                    ):
                        return None
                    expected_lifecycle = SuccessorLifecycle.READY
                    expected_token = None
                elif current.lifecycle == SuccessorLifecycle.MATERIALIZING:
                    if recovery_request is None:
                        raise SuccessorAdmissionError("SUCCESSOR_MATERIALIZATION_CLAIM_REQUIRED")
                    expected_lifecycle = SuccessorLifecycle.MATERIALIZING
                    expected_token = current.materialization_owner_token
                elif current.lifecycle == SuccessorLifecycle.WAIT_RECONCILIATION:
                    if (
                        recovery_request is None
                        or recovery_request.lifecycle != SuccessorLifecycle.WAIT_RECONCILIATION
                        or recovery_request.generation != current.generation
                        or recovery_request.lifecycle_revision != current.lifecycle_revision
                        or recovery_request.owner_token is not None
                    ):
                        return None
                    expected_lifecycle = SuccessorLifecycle.WAIT_RECONCILIATION
                    expected_token = None
                elif current.lifecycle == SuccessorLifecycle.PREPARED:
                    if existing is not None:
                        raise SuccessorAdmissionError("SUCCESSOR_MATERIALIZATION_CLAIM_REQUIRED")
                    owner_token = f"mat-{uuid.uuid4().hex}"
                    claimed, won = self.mailbox.claim_materialization(
                        package_id,
                        generation=current.generation,
                        expected_revision=current.lifecycle_revision,
                        owner_token=owner_token,
                        guard=guard,
                    )
                    if not won:
                        raise SuccessorAdmissionError("SUCCESSOR_MATERIALIZATION_CLAIM_LOST")
                    claim = MaterializationClaim(
                        package_id,
                        claimed.generation,
                        claimed.lifecycle_revision,
                        owner_token,
                    )
                else:
                    if existing is None:
                        raise SuccessorAdmissionError("SUCCESSOR_REQUIRES_RECONCILIATION")
                    observed_lifecycle = self._lifecycle_for_state(existing.state)
                    if observed_lifecycle != current.lifecycle:
                        self.mailbox.set_successor_lifecycle(package_id, observed_lifecycle)
                    return existing

                if current.lifecycle in {
                    SuccessorLifecycle.MATERIALIZING,
                    SuccessorLifecycle.READY,
                    SuccessorLifecycle.WAIT_RECONCILIATION,
                }:
                    if (
                        expected_lifecycle == SuccessorLifecycle.MATERIALIZING
                        and expected_token is None
                    ):
                        raise SuccessorAdmissionError("MATERIALIZATION_FENCE_INVALID")
                    owner_token = f"mat-{uuid.uuid4().hex}"
                    claimed, won = self.mailbox.recover_materialization_claim(
                        package_id,
                        generation=current.generation,
                        expected_revision=current.lifecycle_revision,
                        expected_owner_token=expected_token,
                        expected_lifecycle=expected_lifecycle,
                        new_owner_token=owner_token,
                        guard=guard,
                    )
                    if not won:
                        return None
                    claim = MaterializationClaim(
                        package_id,
                        claimed.generation,
                        claimed.lifecycle_revision,
                        owner_token,
                    )

                self.mailbox.assert_materialization_claim(
                    package_id,
                    generation=claim.generation,
                    expected_revision=claim.lifecycle_revision,
                    owner_token=claim.owner_token,
                    guard=guard,
                )
                if existing is None:
                    try:
                        self.governor.add_node(node)
                    except GovernorError as exc:
                        raise SuccessorAdmissionError(
                            getattr(exc, "code", "GOVERNOR_ADMISSION_FAILED")
                        ) from None
                observed = next(
                    (
                        item
                        for item in self.governor.snapshot().nodes
                        if item.package_id == package_id
                    ),
                    None,
                )
                if observed is None:
                    raise SuccessorAdmissionError("GOVERNOR_NODE_MISSING_AFTER_ADD")
                if observed.model_copy(update={"state": NodeState.DISCOVERED}) != node:
                    raise SuccessorAdmissionError("WORKNODE_IDENTITY_COLLISION")
                self.mailbox.assert_materialization_claim(
                    package_id,
                    generation=claim.generation,
                    expected_revision=claim.lifecycle_revision,
                    owner_token=claim.owner_token,
                    guard=guard,
                )
                if observed.state in {NodeState.DISCOVERED, NodeState.BLOCKED}:
                    try:
                        if observed.state == NodeState.BLOCKED:
                            self.governor.restore_blocked_materialization(
                                package_id,
                                revalidate=lambda: self._current_materialization_is_valid(
                                    package_id
                                ),
                            )
                        else:
                            self.governor.mark_ready(package_id)
                    except GovernorError as exc:
                        raise SuccessorAdmissionError(
                            getattr(exc, "code", "GOVERNOR_ADMISSION_FAILED")
                        ) from None
                observed = next(
                    item for item in self.governor.snapshot().nodes if item.package_id == package_id
                )
                self.mailbox.assert_materialization_claim(
                    package_id,
                    generation=claim.generation,
                    expected_revision=claim.lifecycle_revision,
                    owner_token=claim.owner_token,
                    guard=guard,
                )
                self.mailbox.finalize_materialization(
                    package_id,
                    generation=claim.generation,
                    expected_revision=claim.lifecycle_revision,
                    owner_token=claim.owner_token,
                    lifecycle=self._lifecycle_for_state(observed.state),
                    guard=guard,
                )
                return observed
        except TimeoutError:
            raise SuccessorAdmissionError("MATERIALIZATION_OWNER_ACTIVE") from None

    def _register_execution_guard(self, package_id: str) -> None:
        self.governor.register_execution_guard(
            package_id, lambda: self._execution_authorized(package_id)
        )

    def _current_materialization_is_valid(self, package_id: str) -> bool:
        current = self.mailbox.get_successor(package_id)
        if current is None:
            return False
        try:
            self._validate_current_materialization(current)
            return True
        except SuccessorAdmissionError:
            return False

    def _execution_authorized(self, package_id: str) -> bool:
        current = self.mailbox.get_successor(package_id)
        if current is None or current.lifecycle != SuccessorLifecycle.READY:
            return False
        try:
            expected, _message = self._validate_current_materialization(current)
        except SuccessorAdmissionError:
            return False
        node = next(
            (item for item in self.governor.snapshot().nodes if item.package_id == package_id),
            None,
        )
        return (
            node is not None and node.model_copy(update={"state": NodeState.DISCOVERED}) == expected
        )

    @staticmethod
    def _lifecycle_for_state(state: NodeState) -> SuccessorLifecycle:
        mapping = {
            NodeState.DISCOVERED: SuccessorLifecycle.MATERIALIZING,
            NodeState.READY: SuccessorLifecycle.READY,
            NodeState.LEASED: SuccessorLifecycle.LEASED,
            NodeState.ACTIVE: SuccessorLifecycle.ACTIVE,
            NodeState.VERIFYING: SuccessorLifecycle.VERIFYING,
            NodeState.REMEDIATING: SuccessorLifecycle.REMEDIATING,
            NodeState.BLOCKED: SuccessorLifecycle.WAITING_EXTERNAL,
            NodeState.OWNER_HELD: SuccessorLifecycle.WAITING_EXTERNAL,
            NodeState.MERGE_ELIGIBLE: SuccessorLifecycle.WAITING_EXTERNAL,
            NodeState.CERTIFIED: SuccessorLifecycle.TERMINAL,
            NodeState.MERGED: SuccessorLifecycle.TERMINAL,
            NodeState.SUPERSEDED: SuccessorLifecycle.TERMINAL,
            NodeState.CLOSED: SuccessorLifecycle.TERMINAL,
        }
        try:
            return mapping[state]
        except KeyError:
            return SuccessorLifecycle.WAIT_RECONCILIATION
