"""AS-ORCH-MAILBOX-001 durable ingress and 001A/001B composition."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.governor import AutonomousGovernor
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    AgentCapability,
    AgentRecord,
    TrustedAnchorRecord,
)
from project_atlas.orchestration.autonomy.trust import seal_anchor
from project_atlas.orchestration.mailbox import (
    AgentInboxMessage,
    AgentMailbox,
    InboxRouter,
    MailboxError,
    MailboxGovernorBridge,
    MailboxStatus,
    SuccessorAdmissionError,
    payload_sha256,
)
from project_atlas.orchestration.mailbox.models import MailboxSuccessorRecord
from project_atlas.schema import available_schemas, validate_record

HEAD = "f6b2495a03196901a5a72c2cf3451d4504b54d5f"
TREE = "9c670d710ec63d36fea70c6a181c088b79294336"
OTHER_HEAD = "1" * 40
OTHER_TREE = "2" * 40
RAW_SETUP_FAILURE = (
    "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"
)


def _result_envelope(
    *,
    agent_id: str = "builder-a",
    outcome: str = "BLOCKED",
    state: str = "BLOCKED",
    blocker: str = "LOCAL_EXECUTOR_SETUP_REFRESH_FAILED",
    extras: dict[str, bool | int | str] | None = None,
    task_id: str = "SOURCE-DELEGATION-001",
    attempt: int = 1,
    receipt_status: str = "valid",
    authority: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "producer": {"role": "local", "agent_id": agent_id},
        "task": {"id": task_id, "attempt": attempt},
        "outcome": outcome,
        "state": state,
        "observations": {
            "target_moved": False,
            "unauthorized_mutations": 0,
            "extras": ({"retryable": True, "process_started": False} if extras is None else extras),
        },
        "receipt": {
            "receipt_id": "ASR-mailbox-fixture",
            "status": receipt_status,
            "event_id": "AE-mailbox-fixture",
        },
        "blockers": ([{"code": blocker, "detail": RAW_SETUP_FAILURE}] if blocker else []),
        "requested_transition": "OWNER_REQUIRED" if authority else None,
    }
    if authority:
        payload["authority_grant"] = True
    return payload


def _message(
    *,
    message_id: str = "msg-source-1",
    agent_id: str = "builder-a",
    task_id: str = "SOURCE-DELEGATION-001",
    head: str = HEAD,
    tree: str = TREE,
    kind: str = "BLOCKED",
    result: dict[str, Any] | None = None,
    owner_required: bool = False,
) -> dict[str, Any]:
    result_payload = result or _result_envelope(agent_id=agent_id, task_id=task_id)
    payload = {"result_envelope": result_payload}
    return {
        "schema_version": 1,
        "message_id": message_id,
        "idempotency_key": f"idem-{message_id}",
        "project_id": "project-atlas",
        "task_id": task_id,
        "run_id": "run-source-1",
        "dispatch_id": "dispatch-source-1",
        "correlation_id": "corr-source-1",
        "causation_id": None,
        "requester_id": "requester-original",
        "authority_reference": "grant-source-fixture",
        "attempt_id": "attempt-1",
        "producer": {"role": "local", "agent_id": agent_id},
        "message_kind": kind,
        "task_class": "SOURCE_DELEGATION_VERIFICATION",
        "reason_code": "LOCAL_EXECUTOR_SETUP_REFRESH_FAILED",
        "trusted_head": head,
        "trusted_tree": tree,
        "payload": payload,
        "payload_digest": payload_sha256(payload),
        "created_at": "2026-09-26T12:00:00Z",
        "retryable": True,
        "owner_required": owner_required,
        "authority_claimed": False,
        "execution_authorized": False,
        "merge_authorized": False,
    }


def _router(head: str = HEAD, tree: str = TREE) -> InboxRouter:
    return InboxRouter(
        trusted_head=head,
        trusted_tree=tree,
        identity_verifier=lambda _message: True,
        binding_verifier=lambda _message: True,
    )


def _governor(*agents: AgentRecord) -> AutonomousGovernor:
    predecessor = "1" * 40
    trusted = seal_anchor(
        TrustedAnchorRecord(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=HEAD,
            trusted_tree=TREE,
            predecessor_main=predecessor,
            predecessor_tree="2" * 40,
            advancement_reason="VERIFIED_OWNER_AUTHORIZED_MERGE",
            source_package="AS-ORCH-001D",
            source_directive="D-AS-ORCH-001D-OWNER-MERGE-010",
            source_pr=400,
            merge_commit=HEAD,
            merge_parent_1=predecessor,
            merge_parent_2="3" * 40,
            merge_tree=TREE,
            certified_head="3" * 40,
            certified_tree=TREE,
            certification_status="CERTIFIED",
            independent_verification_status="PASS",
            post_merge_seal="PASS",
            post_merge_ci="PASS",
            evidence_reference="tests/unit/mailbox-bridge-anchor.json",
            evidence_digest="aa" * 32,
            sequence=3,
            record_digest="00" * 32,
        )
    )
    return AutonomousGovernor(
        current_main=HEAD,
        current_tree=TREE,
        trusted_anchor=trusted,
        agents=agents
        or (
            AgentRecord(
                agent_id="discover-worker",
                capabilities=(AgentCapability.DISCOVER,),
            ),
        ),
    )


def test_valid_ingest_and_exact_replay_are_idempotent(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = mailbox.enqueue(_message())
    replay = mailbox.enqueue(_message())

    assert first.status == MailboxStatus.PENDING
    assert first.duplicate is False
    assert replay.duplicate is True
    assert replay.message_id == first.message_id
    assert len(mailbox.records()) == 1


def test_different_agents_correlate_to_one_logical_incident(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    one = mailbox.enqueue(_message(message_id="msg-agent-one", agent_id="builder-a"))
    two = mailbox.enqueue(_message(message_id="msg-agent-two", agent_id="builder-b"))

    assert one.incident_id == two.incident_id
    assert len(mailbox.records()) == 2  # producer observations are preserved
    assert mailbox.incident_message_ids(one.incident_id) == (
        "msg-agent-one",
        "msg-agent-two",
    )


@pytest.mark.parametrize(
    ("task_id", "head", "tree"),
    [("OTHER-TASK", HEAD, TREE), ("SOURCE-DELEGATION-001", OTHER_HEAD, OTHER_TREE)],
)
def test_different_task_or_head_is_a_distinct_incident(
    tmp_path: Path, task_id: str, head: str, tree: str
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    one = mailbox.enqueue(_message(message_id="msg-origin"))
    other = mailbox.enqueue(
        _message(message_id="msg-distinct", task_id=task_id, head=head, tree=tree)
    )
    assert one.incident_id != other.incident_id


def test_same_message_id_with_changed_content_is_quarantined(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message())
    conflict = _message(owner_required=True)
    receipt = mailbox.enqueue(conflict)

    assert receipt.status == MailboxStatus.QUARANTINED
    assert receipt.quarantine_code == "MESSAGE_ID_COLLISION"
    assert len(mailbox.records()) == 1


def test_idempotency_key_replay_suppresses_new_message_id(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(message_id="original"))
    replay = _message(message_id="replay")
    replay["idempotency_key"] = "idem-original"
    receipt = mailbox.enqueue(replay)

    assert receipt.duplicate is True
    assert receipt.duplicate_of == "original"
    assert receipt.status == MailboxStatus.PROCESSED


def test_idempotency_key_reuse_with_changed_payload_is_quarantined(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(message_id="original"))
    changed = _message(message_id="changed", agent_id="builder-b")
    changed["idempotency_key"] = "idem-original"
    receipt = mailbox.enqueue(changed)

    assert receipt.status == MailboxStatus.QUARANTINED
    assert receipt.quarantine_code == "IDEMPOTENCY_KEY_COLLISION"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schema_version=99),
        lambda value: value.update(authority_claimed=True),
        lambda value: value.update(execution_authorized=True),
        lambda value: value.update(merge_authorized=True),
        lambda value: value.update(payload_digest="0" * 64),
    ],
)
def test_malformed_or_authority_claiming_message_is_quarantined(
    tmp_path: Path, mutate: Any
) -> None:
    raw = _message()
    mutate(raw)
    receipt = AgentMailbox(tmp_path, project_id="project-atlas").enqueue(raw)
    assert receipt.status == MailboxStatus.QUARANTINED
    assert receipt.quarantine_code == "MESSAGE_INVALID"


def test_cross_project_message_is_quarantined_and_not_delivered(tmp_path: Path) -> None:
    raw = _message()
    raw["project_id"] = "other-project"
    receipt = AgentMailbox(tmp_path, project_id="project-atlas").enqueue(raw)
    assert receipt.status == MailboxStatus.QUARANTINED
    assert receipt.quarantine_code == "PROJECT_MISMATCH"


def test_secret_shaped_payload_is_quarantined_without_retaining_raw_value(
    tmp_path: Path,
) -> None:
    raw = _message()
    raw["payload"]["secret_note"] = "api_key=abcdefghijklmnopqrstuvwxyz012345"
    raw["payload_digest"] = payload_sha256(raw["payload"])
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    receipt = mailbox.enqueue(raw)

    assert receipt.status == MailboxStatus.QUARANTINED
    assert receipt.quarantine_code == "MESSAGE_INVALID"
    assert "abcdefghijklmnopqrstuvwxyz012345" not in mailbox.state_path.read_text(encoding="utf-8")


def test_persistence_rehydrates_after_store_recreation(tmp_path: Path) -> None:
    first = AgentMailbox(tmp_path, project_id="project-atlas")
    first.enqueue(_message())
    reopened = AgentMailbox(tmp_path, project_id="project-atlas")

    assert reopened.records()[0].status == MailboxStatus.PENDING
    processed = reopened.process_next(_router())
    assert processed is not None
    assert processed.status == MailboxStatus.PROCESSED
    assert (
        AgentMailbox(tmp_path, project_id="project-atlas").records()[0].status
        == MailboxStatus.PROCESSED
    )


def test_known_prestart_launcher_failure_uses_001a_and_001b_without_authority(
    tmp_path: Path,
) -> None:
    raw = _message(
        result=_result_envelope(
            extras={"retryable": True, "process_started": False},
        )
    )
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(raw)
    record = mailbox.process_next(_router())

    assert record is not None
    assert record.status == MailboxStatus.PROCESSED
    assert record.routing is not None
    assert record.routing["decision"]["next_transition"] == "AUTONOMOUS_RECONCILE"
    assert record.routing["route"]["task_type"] == "program_reconciliation"
    assert record.routing["route"]["dispatchable"] is True
    assert record.routing["route"]["execution_authorized"] is False
    assert record.routing["classification"] == "CONTINUATION_ELIGIBLE"


def test_nonretryable_message_does_not_select_recovery_directive(tmp_path: Path) -> None:
    raw = _message(result=_result_envelope(extras={"retryable": True, "process_started": False}))
    raw["retryable"] = False
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(raw)
    record = mailbox.process_next(_router())

    assert record is not None and record.routing is not None
    assert record.routing["classification"] == "WAIT_EXTERNAL"
    assert record.routing["reason_code"] == "PRESTART_RECOVERY_MESSAGE_FACTS_INCOMPLETE"


def test_claimed_owner_required_does_not_override_deterministic_classification(
    tmp_path: Path,
) -> None:
    raw = _message(owner_required=True)
    raw["payload"]["result_envelope"] = _result_envelope(
        extras={"retryable": True, "process_started": False},
        authority=False,
    )
    raw["payload_digest"] = payload_sha256(raw["payload"])
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(raw)
    record = mailbox.process_next(_router())

    assert record is not None and record.routing is not None
    assert record.routing["classification"] == "CONTINUATION_ELIGIBLE"
    assert record.routing["decision"]["owner_required"] is False


def test_real_001a_owner_gate_is_preserved(tmp_path: Path) -> None:
    envelope = _result_envelope(
        outcome="PASS",
        state="MERGE_ELIGIBLE",
        blocker="",
    )
    raw = _message(result=envelope, owner_required=False)
    raw["message_kind"] = "AGENT_RESULT"
    raw["reason_code"] = None
    raw["payload_digest"] = payload_sha256(raw["payload"])
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(raw)
    record = mailbox.process_next(_router())

    assert record is not None and record.routing is not None
    assert record.routing["classification"] == "OWNER_REQUIRED"
    assert record.routing["route"]["owner_gate"] is True
    assert record.routing["route"]["execution_authorized"] is False


def test_stale_source_pin_is_quarantined_before_routing(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(head=OTHER_HEAD, tree=OTHER_TREE))
    record = mailbox.process_next(_router())

    assert record is not None
    assert record.status == MailboxStatus.QUARANTINED
    assert record.quarantine_code == "STALE_SOURCE_PIN"
    assert record.routing is None


def test_unverified_producer_identity_is_quarantined(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message())
    router = InboxRouter(
        trusted_head=HEAD,
        trusted_tree=TREE,
        identity_verifier=lambda _message: False,
        binding_verifier=lambda _message: True,
    )
    record = mailbox.process_next(router)

    assert record is not None
    assert record.status == MailboxStatus.QUARANTINED
    assert record.quarantine_code == "PRODUCER_IDENTITY_UNVERIFIED"


def test_unverified_task_attempt_binding_is_quarantined(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message())
    router = InboxRouter(
        trusted_head=HEAD,
        trusted_tree=TREE,
        identity_verifier=lambda _message: True,
        binding_verifier=lambda _message: False,
    )
    record = mailbox.process_next(router)

    assert record is not None
    assert record.status == MailboxStatus.QUARANTINED
    assert record.quarantine_code == "TASK_ATTEMPT_BINDING_UNVERIFIED"


def test_verifier_result_waits_for_candidate_binding_contract(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    raw = _message(kind="VERIFICATION_RESULT")
    mailbox.enqueue(raw)
    record = mailbox.process_next(_router())

    assert record is not None
    assert record.status == MailboxStatus.PROCESSED
    assert record.routing["classification"] == "WAIT_EXTERNAL"
    assert record.routing["reason_code"] == "VERIFICATION_CANDIDATE_BINDING_NOT_IN_001A"


def test_agent_message_schema_is_registered_and_accepts_model() -> None:
    assert "agent-inbox-message" in available_schemas()
    message = AgentInboxMessage.model_validate(_message())
    validate_record(message, "agent-inbox-message")


def test_malformed_embedded_result_is_quarantined_on_processing(tmp_path: Path) -> None:
    bad_result = _result_envelope(extras={"retryable": True, "process_started": False})
    bad_result["merge_authorized"] = True
    raw = _message(result=bad_result)
    raw["payload_digest"] = payload_sha256(raw["payload"])
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(raw)
    record = mailbox.process_next(_router())

    assert record is not None
    assert record.status == MailboxStatus.QUARANTINED
    assert record.quarantine_code == "RESULT_INVALID"
    assert record.routing is None


def test_duplicate_result_digest_does_not_get_processed_twice(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(message_id="result-one"))
    duplicate = mailbox.enqueue(_message(message_id="result-two"))

    assert duplicate.status == MailboxStatus.PROCESSED
    assert duplicate.duplicate is True
    assert duplicate.duplicate_of == "result-one"
    assert mailbox.process_next(_router()).message.message_id == "result-one"
    assert mailbox.process_next(_router()) is None


def test_processed_message_is_idempotent_and_has_no_dispatch_side_effect(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message())
    first = mailbox.process_next(_router())
    second = mailbox.process_next(_router())

    assert first is not None
    assert first.status == MailboxStatus.PROCESSED
    assert second is None
    assert first.routing["route"]["execution_authorized"] is False


def test_launcher_failure_materializes_read_only_governor_worknode(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    raw = _message()
    mailbox.enqueue(raw)
    mailbox.process_next(_router())
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=governor,
        authority_verifier=lambda item: item.authority_reference == "grant-source-fixture",
    )

    node = bridge.admit("msg-source-1")

    assert node.state.value == "READY"
    assert node.execution_host_class.value == "EXTERNAL_AGENT"
    assert node.agent_capabilities_required == (AgentCapability.DISCOVER,)
    assert node.mutation_surface.paths == ()
    assert node.execution_authorized is False
    assert node.merge_authorized is False
    assert governor.snapshot().leases == ()
    binding = mailbox.successor_records()[0].binding
    validate_record(binding, "mailbox-successor-binding-v1")
    assert binding.source_task_id == raw["task_id"]
    assert binding.requester_id == "requester-original"
    assert binding.authority_reference == "grant-source-fixture"


def test_cross_agent_successor_dedupes_and_rehydrates_after_restart(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="source-agent-a", agent_id="builder-a")
    second = _message(message_id="source-agent-b", agent_id="builder-b")
    second["payload"]["result_envelope"]["blockers"][0]["detail"] = (
        "same launcher incident, different non-authoritative prose"
    )
    second["payload_digest"] = payload_sha256(second["payload"])
    for message in (first, second):
        mailbox.enqueue(message)
        mailbox.process_next(_router())
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=governor,
        authority_verifier=lambda _item: True,
    )

    one = bridge.admit("source-agent-a")
    two = bridge.admit("source-agent-b")

    assert one.package_id == two.package_id
    assert len(mailbox.successor_records()) == 1
    assert len(governor.snapshot().nodes) == 1
    recovered = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _item: True,
    ).reconcile()
    assert len(recovered) == 1
    assert recovered[0].package_id == one.package_id
    assert recovered[0].state.value == "READY"


def test_reconcile_rejects_successor_binding_not_matching_persisted_route(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message())
    mailbox.process_next(_router())
    bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _item: True,
    )
    bridge.admit("msg-source-1")

    state = mailbox._load_state()
    package_id, existing = next(iter(state.successors.items()))
    altered_binding = existing.binding.model_copy(
        update={"route_digest": "a" * 64, "binding_digest": "0" * 64}
    ).seal()
    altered_record = MailboxSuccessorRecord(
        binding=altered_binding,
        work_node=existing.work_node,
        work_node_digest=existing.work_node_digest,
    )
    mailbox._save_state(state.model_copy(update={"successors": {package_id: altered_record}}))

    with pytest.raises(SuccessorAdmissionError) as exc:
        MailboxGovernorBridge(
            mailbox=mailbox,
            governor=_governor(),
            authority_verifier=lambda _item: True,
        ).reconcile()

    assert exc.value.code == "SUCCESSOR_ROUTE_DIGEST_MISMATCH"


def test_candidate_verification_requires_exact_independently_checked_candidate(
    tmp_path: Path,
) -> None:
    candidate_head, candidate_tree = "4" * 40, "5" * 40
    result = _result_envelope(
        outcome="PASS",
        state="CERTIFIED",
        blocker="",
        extras={},
    )
    raw = _message(message_id="candidate-source", kind="AGENT_RESULT", result=result)
    raw["reason_code"] = None
    raw["retryable"] = False
    raw["payload"]["candidate_binding"] = {
        "candidate_head": candidate_head,
        "candidate_tree": candidate_tree,
    }
    raw["payload_digest"] = payload_sha256(raw["payload"])
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(raw)
    mailbox.process_next(_router())
    rejected = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _item: True,
        candidate_identity_verifier=lambda _item, _head, _tree: False,
    )
    with pytest.raises(SuccessorAdmissionError) as unverified:
        rejected.admit("candidate-source")
    assert unverified.value.code == "CANDIDATE_IDENTITY_UNVERIFIED"

    accepted = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _item: True,
        candidate_identity_verifier=lambda _item, head, tree: (
            head == candidate_head and tree == candidate_tree
        ),
    )
    node = accepted.admit("candidate-source")
    assert node.base_pin == candidate_head
    assert node.agent_capabilities_required == (AgentCapability.VERIFY,)
    assert mailbox.successor_records()[0].binding.candidate_tree == candidate_tree


def test_successor_admission_requires_authority_and_current_source_pin(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message())
    mailbox.process_next(_router())
    bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _item: False,
    )
    with pytest.raises(SuccessorAdmissionError) as denied:
        bridge.admit("msg-source-1")
    assert denied.value.code == "SUCCESSOR_AUTHORITY_NOT_VERIFIED"

    moved = _governor()
    moved._current_main = OTHER_HEAD
    stale_bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=moved,
        authority_verifier=lambda _item: True,
    )
    with pytest.raises(SuccessorAdmissionError) as stale:
        stale_bridge.admit("msg-source-1")
    assert stale.value.code == "SUCCESSOR_SOURCE_PIN_STALE"


def test_mutating_remediation_route_is_not_admitted(tmp_path: Path) -> None:
    result = _result_envelope(
        outcome="FAIL",
        state="CERTIFIED",
        blocker="",
        extras={},
    )
    result["producer"] = {"role": "integration", "agent_id": "reviewer-a"}
    raw = _message(
        message_id="remediation-source",
        agent_id="reviewer-a",
        kind="AGENT_RESULT",
        result=result,
    )
    raw["producer"] = {"role": "integration", "agent_id": "reviewer-a"}
    raw["reason_code"] = None
    raw["retryable"] = False
    raw["payload_digest"] = payload_sha256(raw["payload"])
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(raw)
    mailbox.process_next(_router())
    bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _item: True,
    )

    with pytest.raises(SuccessorAdmissionError) as denied:
        bridge.admit("remediation-source")
    assert denied.value.code == "SUCCESSOR_DIRECTIVE_NOT_ADMISSIBLE"


def test_atomic_write_failure_preserves_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(message_id="before-failure"))

    def fail_before_replace(_path: Path, _data: bytes) -> None:
        raise OSError("fixture interrupted before replace")

    monkeypatch.setattr(
        "project_atlas.orchestration.mailbox.store._atomic_replace", fail_before_replace
    )
    with pytest.raises(MailboxError, match="persist"):
        mailbox.enqueue(_message(message_id="interrupted"))

    monkeypatch.undo()
    assert [record.message.message_id for record in mailbox.records()] == ["before-failure"]


def test_corrupt_store_fails_closed(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message())
    mailbox.state_path.write_text('{"schema_version":1}\n', encoding="utf-8")

    with pytest.raises(MailboxError, match="digest mismatch"):
        mailbox.records()


def test_incident_id_is_deterministic_and_uses_task_head_and_reason() -> None:
    one = AgentInboxMessage.model_validate(_message())
    two = AgentInboxMessage.model_validate(_message(message_id="other", agent_id="builder-b"))
    assert one.incident_id() == two.incident_id()
    assert (
        one.incident_id()
        != AgentInboxMessage.model_validate(
            _message(message_id="other-task", task_id="OTHER-TASK")
        ).incident_id()
    )


def test_payload_sha256_is_canonical() -> None:
    assert payload_sha256({"b": 2, "a": 1}) == payload_sha256({"a": 1, "b": 2})
    assert len(payload_sha256({})) == hashlib.sha256(b"{}").digest_size * 2
