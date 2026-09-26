"""Fail-closed materialization of a validated mailbox directive into 001E WorkNode."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
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
    ) -> None:
        self.mailbox = mailbox
        self.governor = governor
        self.authority_verifier = authority_verifier
        self.candidate_identity_verifier = candidate_identity_verifier

    def admit(self, message_id: str) -> WorkNode:
        record = self.mailbox.get_record(message_id)
        if record is None or record.status.value != "PROCESSED":
            raise SuccessorAdmissionError("SUCCESSOR_SOURCE_NOT_PROCESSED")
        message = record.message
        if (record.routing or {}).get("classification") == "DUPLICATE_MESSAGE":
            duplicate_matches = [
                item
                for item in self.mailbox.successor_records()
                if item.binding.incident_id == record.incident_id
                and item.lifecycle in ACTIVE_SUCCESSOR_LIFECYCLES
            ]
            if len(duplicate_matches) == 1:
                return self._materialize(duplicate_matches[0])
            raise SuccessorAdmissionError("DUPLICATE_RESULT_HAS_NO_ACTIVE_SUCCESSOR")
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

        routing = record.routing or {}
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
        generation = 1
        supersedes_package_id = None
        if active:
            generation = active[0].generation
        elif incident_records:
            previous = max(incident_records, key=lambda item: item.generation)
            if directive.transition.value not in {
                "AUTONOMOUS_RECONCILE",
                "RECERTIFY_REQUIRED",
                "REMEDIATION_REQUIRED",
            }:
                raise SuccessorAdmissionError("SUCCESSOR_SUPERSESSION_REQUIRED")
            generation = previous.generation + 1
            supersedes_package_id = previous.binding.package_id
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
            return self._materialize(active_successor)
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
        )
        return self._materialize(persisted)

    def reconcile(self) -> tuple[WorkNode, ...]:
        """Rebuild missing governor nodes from the mailbox admission journal."""
        nodes: list[WorkNode] = []
        for item in self.mailbox.successor_records():
            binding = item.binding.verify()
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
            if not isinstance(route_value, dict):
                raise SuccessorAdmissionError("SUCCESSOR_ROUTE_MISSING")
            try:
                route = OrchestrationRoute.model_validate(route_value)
            except (ValidationError, TypeError, ValueError):
                raise SuccessorAdmissionError("SUCCESSOR_ROUTE_INVALID") from None
            if self._route_digest(route, binding.candidate_head, binding.candidate_tree) != (
                binding.route_digest
            ):
                raise SuccessorAdmissionError("SUCCESSOR_ROUTE_DIGEST_MISMATCH")
            candidate_bound = (
                binding.candidate_head is not None or binding.candidate_tree is not None
            )
            if candidate_bound and (
                binding.candidate_head is None
                or binding.candidate_tree is None
                or not self._verify_candidate(
                    message, binding.candidate_head, binding.candidate_tree
                )
            ):
                raise SuccessorAdmissionError("CANDIDATE_IDENTITY_UNVERIFIED")
            expected_node = self._node_for_binding(binding)
            try:
                persisted_node = WorkNode.model_validate(item.work_node)
            except (ValidationError, TypeError, ValueError):
                raise SuccessorAdmissionError("PERSISTED_WORKNODE_INVALID") from None
            if persisted_node != expected_node:
                raise SuccessorAdmissionError("PERSISTED_WORKNODE_BINDING_MISMATCH")
            existing = next(
                (
                    node
                    for node in self.governor.snapshot().nodes
                    if node.package_id == item.binding.package_id
                ),
                None,
            )
            if existing is None:
                if item.lifecycle == SuccessorLifecycle.PREPARED:
                    nodes.append(self._materialize(item))
                elif item.lifecycle != SuccessorLifecycle.TERMINAL:
                    self.mailbox.set_successor_lifecycle(
                        item.binding.package_id, SuccessorLifecycle.WAIT_RECONCILIATION
                    )
                continue
            nodes.append(self._materialize(item))
        return tuple(nodes)

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

    def _materialize(self, successor: MailboxSuccessorRecord) -> WorkNode:
        raw_node = successor.work_node
        try:
            node = WorkNode.model_validate(raw_node)
        except (ValidationError, TypeError, ValueError):
            raise SuccessorAdmissionError("PERSISTED_WORKNODE_INVALID") from None
        existing = next(
            (item for item in self.governor.snapshot().nodes if item.package_id == node.package_id),
            None,
        )
        if existing is not None:
            if existing.model_copy(update={"state": NodeState.DISCOVERED}) != node:
                raise SuccessorAdmissionError("WORKNODE_IDENTITY_COLLISION")
            if existing.state == NodeState.DISCOVERED:
                self.governor.mark_ready(node.package_id)
            observed = next(
                item
                for item in self.governor.snapshot().nodes
                if item.package_id == node.package_id
            )
            observed_lifecycle = self._lifecycle_for_state(observed.state)
            if (
                successor.lifecycle == SuccessorLifecycle.TERMINAL
                and observed_lifecycle != SuccessorLifecycle.TERMINAL
            ):
                raise SuccessorAdmissionError("SUCCESSOR_LIFECYCLE_REGRESSION")
            self.mailbox.set_successor_lifecycle(node.package_id, observed_lifecycle)
            return observed
        if successor.lifecycle != SuccessorLifecycle.PREPARED:
            self.mailbox.set_successor_lifecycle(
                node.package_id, SuccessorLifecycle.WAIT_RECONCILIATION
            )
            raise SuccessorAdmissionError("SUCCESSOR_REQUIRES_RECONCILIATION")
        self.mailbox.set_successor_lifecycle(node.package_id, SuccessorLifecycle.MATERIALIZING)
        try:
            self.governor.add_node(node)
            self.governor.mark_ready(node.package_id)
        except GovernorError as exc:
            raise SuccessorAdmissionError(
                getattr(exc, "code", "GOVERNOR_ADMISSION_FAILED")
            ) from None
        observed = next(
            item for item in self.governor.snapshot().nodes if item.package_id == node.package_id
        )
        self.mailbox.set_successor_lifecycle(
            node.package_id, self._lifecycle_for_state(observed.state)
        )
        return observed

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
