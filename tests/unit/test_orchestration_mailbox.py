"""AS-ORCH-MAILBOX-001 durable ingress and 001A/001B composition."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from project_atlas.orchestration.autonomy.dag import IllegalTransitionError
from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, GovernorError
from project_atlas.orchestration.autonomy.loop import (
    AutonomousLoop,
    CallableDispatchPort,
    LoopError,
    LoopPhase,
)
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    AgentCapability,
    AgentRecord,
    NodeState,
    TrustedAnchorRecord,
    WorkNode,
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
from project_atlas.orchestration.mailbox.models import (
    MailboxSuccessorRecord,
    SuccessorLifecycle,
    record_sha256,
)
from project_atlas.orchestration.mailbox.store import _MailboxFileLock
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


def test_idempotency_key_replay_waits_for_validated_routing(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(message_id="original"))
    mailbox.process_next(_router())
    replay = _message(message_id="replay")
    replay["idempotency_key"] = "idem-original"
    receipt = mailbox.enqueue(replay)

    assert receipt.duplicate is False
    assert receipt.status == MailboxStatus.PENDING
    routed = mailbox.process_next(_router())
    assert routed is not None and routed.duplicate_of == "original"


def test_idempotency_key_reuse_with_changed_payload_is_quarantined(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(message_id="original"))
    mailbox.process_next(_router())
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


def test_stale_result_cannot_suppress_later_valid_context(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    stale = mailbox.enqueue(_message(message_id="stale", head=OTHER_HEAD, tree=OTHER_TREE))
    assert stale.status == MailboxStatus.PENDING
    rejected = mailbox.process_next(_router())
    assert rejected is not None and rejected.status == MailboxStatus.QUARANTINED

    valid = mailbox.enqueue(_message(message_id="valid", head=HEAD, tree=TREE))
    assert valid.status == MailboxStatus.PENDING
    assert not valid.duplicate
    accepted = mailbox.process_next(_router())
    assert accepted is not None and accepted.message.message_id == "valid"
    assert accepted.status == MailboxStatus.PROCESSED


def test_unvalidated_stale_record_cannot_reserve_idempotency_key(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    stale = _message(message_id="stale-reservation", head=OTHER_HEAD, tree=OTHER_TREE)
    mailbox.enqueue(stale)
    valid = _message(message_id="valid-reservation")
    valid["idempotency_key"] = stale["idempotency_key"]
    receipt = mailbox.enqueue(valid)
    assert receipt.status == MailboxStatus.PENDING
    assert not receipt.duplicate


def test_invalid_cross_project_message_cannot_consume_valid_result(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    invalid = _message(message_id="cross-project")
    invalid["project_id"] = "other-project"
    assert mailbox.enqueue(invalid).status == MailboxStatus.QUARANTINED

    valid = mailbox.enqueue(_message(message_id="valid-after-reject"))
    assert valid.status == MailboxStatus.PENDING
    assert mailbox.process_next(_router()).message.message_id == "valid-after-reject"


def test_rejected_identity_record_cannot_consume_valid_transport_retry(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    raw = _message(message_id="rejected-identity")
    mailbox.enqueue(raw)
    rejecting_router = InboxRouter(
        trusted_head=HEAD,
        trusted_tree=TREE,
        identity_verifier=lambda _message: False,
        binding_verifier=lambda _message: True,
    )
    rejected = mailbox.process_next(rejecting_router)
    assert rejected is not None and rejected.status == MailboxStatus.QUARANTINED

    valid = _message(message_id="valid-retry")
    valid["idempotency_key"] = raw["idempotency_key"]
    receipt = mailbox.enqueue(valid)
    assert receipt.status == MailboxStatus.PENDING
    assert not receipt.duplicate
    accepted = mailbox.process_next(_router())
    assert accepted is not None and accepted.message.message_id == "valid-retry"


def test_processed_exact_transport_replay_remains_idempotent(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    raw = _message(message_id="validated-result")
    mailbox.enqueue(raw)
    mailbox.process_next(_router())

    replay = mailbox.enqueue(raw)
    assert replay.duplicate
    assert replay.message_id == "validated-result"
    assert replay.status == MailboxStatus.PROCESSED


def test_validated_equivalent_result_dedupes_after_validation(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(message_id="result-one"))
    assert mailbox.process_next(_router()).status == MailboxStatus.PROCESSED

    duplicate = mailbox.enqueue(_message(message_id="result-two"))
    assert duplicate.status == MailboxStatus.PENDING
    routed = mailbox.process_next(_router())
    assert routed is not None and routed.duplicate_of == "result-one"
    assert routed.duplicate_of == "result-one"


def test_stale_result_dedupe_context_survives_restart_without_consuming_valid_result(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    mailbox.enqueue(_message(message_id="stale-before-restart", head=OTHER_HEAD, tree=OTHER_TREE))
    stale = mailbox.process_next(_router())
    assert stale is not None and stale.status == MailboxStatus.QUARANTINED

    reopened = AgentMailbox(tmp_path, project_id="project-atlas")
    valid = reopened.enqueue(_message(message_id="valid-after-restart"))
    assert valid.status == MailboxStatus.PENDING
    assert not valid.duplicate


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
    assert mailbox.process_next(_router()).message.message_id == "result-one"
    duplicate = mailbox.enqueue(_message(message_id="result-two"))

    assert duplicate.status == MailboxStatus.PENDING
    routed = mailbox.process_next(_router())
    assert routed is not None and routed.duplicate_of == "result-one"
    assert routed.duplicate_of == "result-one"
    assert mailbox.process_next(_router()) is None


def test_mailbox_lock_age_never_revokes_live_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "live.lock"
    entered = threading.Event()
    release = threading.Event()
    outcome: list[str] = []

    def owner() -> None:
        with _MailboxFileLock(lock_path, wait_seconds=0.5):
            entered.set()
            assert release.wait(2)

    def contender() -> None:
        try:
            with _MailboxFileLock(lock_path, wait_seconds=0.1):
                outcome.append("acquired")
        except TimeoutError:
            outcome.append("blocked")

    first = threading.Thread(target=owner)
    first.start()
    assert entered.wait(1)
    old = time.time() - 3600
    os.utime(lock_path, (old, old))
    second = threading.Thread(target=contender)
    second.start()
    second.join(1)
    assert outcome == ["blocked"]
    release.set()
    first.join(1)
    assert not first.is_alive()


def test_released_lock_owner_cannot_unlock_new_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "ownership.lock"
    old_owner = _MailboxFileLock(lock_path, wait_seconds=0.2)
    old_owner.__enter__()
    old_owner.__exit__(None, None, None)

    new_owner = _MailboxFileLock(lock_path, wait_seconds=0.2)
    new_owner.__enter__()
    old_owner.__exit__(None, None, None)
    with pytest.raises(TimeoutError), _MailboxFileLock(lock_path, wait_seconds=0.05):
        pass
    new_owner.__exit__(None, None, None)
    with _MailboxFileLock(lock_path, wait_seconds=0.2):
        pass


def _hold_mailbox_lock(lock_path: str, ready: object) -> None:
    with _MailboxFileLock(Path(lock_path), wait_seconds=1):
        ready.set()  # type: ignore[attr-defined]
        time.sleep(10)


def test_dead_process_releases_kernel_owned_lock(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    lock_path = tmp_path / "process-owned.lock"
    process = context.Process(target=_hold_mailbox_lock, args=(str(lock_path), ready))
    process.start()
    assert ready.wait(5)
    process.terminate()
    process.join(5)
    assert not process.is_alive()
    with _MailboxFileLock(lock_path, wait_seconds=0.5):
        pass


def test_concurrent_mailbox_writes_retain_both_messages(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def write(message_id: str) -> None:
        try:
            barrier.wait()
            mailbox.enqueue(_message(message_id=message_id))
        except BaseException as exc:
            errors.append(exc)

    writers = [
        threading.Thread(target=write, args=(message_id,))
        for message_id in ("parallel-a", "parallel-b")
    ]
    for writer in writers:
        writer.start()
    barrier.wait()
    for writer in writers:
        writer.join(3)
    assert errors == []
    assert {item.message.message_id for item in mailbox.records()} == {
        "parallel-a",
        "parallel-b",
    }


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
    restarted_governor = _governor()
    recovered = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=restarted_governor,
        authority_verifier=lambda _item: True,
    ).reconcile()
    assert len(recovered) == 1
    assert recovered[0].state == NodeState.READY
    assert len(restarted_governor.snapshot().nodes) == 1
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.READY


def _processed_for_admission(mailbox: AgentMailbox, raw: dict[str, Any]) -> None:
    receipt = mailbox.enqueue(raw)
    if receipt.status == MailboxStatus.PROCESSED:
        return
    record = mailbox.process_next(_router())
    assert record is not None and record.status == MailboxStatus.PROCESSED


def test_same_incident_different_producer_roles_admits_one_successor(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="local-observation", agent_id="builder-a")
    second = _message(message_id="autonomous-observation", agent_id="builder-b")
    second["producer"] = {"role": "autonomous", "agent_id": "builder-b"}
    second["payload"]["result_envelope"]["producer"] = {
        "role": "autonomous",
        "agent_id": "builder-b",
    }
    second["payload_digest"] = payload_sha256(second["payload"])
    _processed_for_admission(mailbox, first)
    _processed_for_admission(mailbox, second)
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _message: True
    )

    one = bridge.admit("local-observation")
    two = bridge.admit("autonomous-observation")
    assert one.package_id == two.package_id
    assert (
        len(mailbox.incident_message_ids(AgentInboxMessage.model_validate(first).incident_id()))
        == 2
    )
    assert len(mailbox.successor_records()) == 1
    assert len(governor.snapshot().nodes) == 1


def test_concurrent_same_incident_admission_claims_one_successor(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="concurrent-local", agent_id="builder-a")
    second = _message(message_id="concurrent-autonomous", agent_id="builder-b")
    second["producer"] = {"role": "autonomous", "agent_id": "builder-b"}
    second["payload"]["result_envelope"]["producer"] = {
        "role": "autonomous",
        "agent_id": "builder-b",
    }
    second["payload_digest"] = payload_sha256(second["payload"])
    _processed_for_admission(mailbox, first)
    _processed_for_admission(mailbox, second)
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _message: True
    )
    barrier = threading.Barrier(3)
    results: list[str] = []
    errors: list[BaseException] = []

    def admit(message_id: str) -> None:
        try:
            barrier.wait()
            results.append(bridge.admit(message_id).package_id)
        except BaseException as exc:
            errors.append(exc)

    workers = [
        threading.Thread(target=admit, args=(message_id,))
        for message_id in ("concurrent-local", "concurrent-autonomous")
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(3)
    assert 1 <= len(results) <= 2, errors
    assert len(errors) <= 1
    if errors:
        assert len(results) == 1
        assert isinstance(errors[0], SuccessorAdmissionError)
        assert errors[0].code == "SUCCESSOR_MATERIALIZATION_CLAIM_LOST"
    else:
        assert len(results) == 2 and len(set(results)) == 1
    assert len(mailbox.successor_records()) == 1
    assert len(governor.snapshot().nodes) == 1
    assert bridge.reconcile()[0].package_id == results[0]
    assert len(governor.snapshot().nodes) == 1


def test_same_incident_new_attempt_and_message_preserves_observation_without_second_node(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="attempt-one")
    second = _message(message_id="attempt-two")
    second["attempt_id"] = "attempt-2"
    second["payload"]["result_envelope"]["task"]["attempt"] = 2
    second["payload_digest"] = payload_sha256(second["payload"])
    _processed_for_admission(mailbox, first)
    _processed_for_admission(mailbox, second)
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _message: True
    )

    first_node = bridge.admit("attempt-one")
    second_node = bridge.admit("attempt-two")
    assert first_node.package_id == second_node.package_id
    assert len(mailbox.successor_records()) == 1
    assert len(governor.snapshot().nodes) == 1
    assert len(mailbox.records()) == 2


def test_same_producer_new_message_id_has_one_successor(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    for message_id in ("same-producer-one", "same-producer-two"):
        _processed_for_admission(mailbox, _message(message_id=message_id))
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _message: True
    )
    assert (
        bridge.admit("same-producer-one").package_id == bridge.admit("same-producer-two").package_id
    )
    assert len(mailbox.successor_records()) == 1


def test_distinct_incident_can_admit_distinct_successor(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="incident-one")
    second = _message(message_id="incident-two", task_id="SOURCE-DELEGATION-002")
    _processed_for_admission(mailbox, first)
    _processed_for_admission(mailbox, second)
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _message: True
    )
    one, two = bridge.admit("incident-one"), bridge.admit("incident-two")
    assert one.package_id != two.package_id
    assert len(mailbox.successor_records()) == 2
    assert len(governor.snapshot().nodes) == 2


def test_terminal_successor_requires_authorized_retry_identity_before_new_generation(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="generation-one")
    second = _message(message_id="generation-two")
    second["attempt_id"] = "attempt-2"
    second["payload"]["result_envelope"]["task"]["attempt"] = 2
    second["payload_digest"] = payload_sha256(second["payload"])
    _processed_for_admission(mailbox, first)
    _processed_for_admission(mailbox, second)
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=governor,
        authority_verifier=lambda _message: True,
        retry_verifier=lambda _message, retry_id, prior: (
            retry_id == "retry-generation-two" and prior.generation == 1
        ),
    )
    original = bridge.admit("generation-one")
    mailbox.set_successor_lifecycle(original.package_id, SuccessorLifecycle.TERMINAL)

    retry = bridge.admit(
        "generation-two",
        retry_id="retry-generation-two",
        prior_successor_id=original.package_id,
        prior_generation=1,
    )
    assert retry.package_id != original.package_id
    records = mailbox.successor_records()
    assert len(records) == 2
    new = next(item for item in records if item.binding.package_id == retry.package_id)
    assert new.generation == 2
    assert new.supersedes_package_id == original.package_id
    assert new.retry_id == "retry-generation-two"


def test_completed_successor_is_not_recreated_ready_after_restart(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="completed-node"))
    original_bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _message: True,
    )
    node = original_bridge.admit("completed-node")
    mailbox.set_successor_lifecycle(node.package_id, SuccessorLifecycle.TERMINAL)
    new_governor = _governor()
    assert (
        MailboxGovernorBridge(
            mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
            governor=new_governor,
            authority_verifier=lambda _message: True,
        ).reconcile()
        == ()
    )
    assert new_governor.snapshot().nodes == ()


def test_schema_v2_successor_migrates_to_reconciliation_required(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="legacy-successor"))
    bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _message: True,
    )
    bridge.admit("legacy-successor")
    raw = json.loads(mailbox.state_path.read_text(encoding="utf-8"))
    raw["schema_version"] = 2
    for item in raw["successors"].values():
        item.pop("lifecycle")
        item.pop("generation")
        item.pop("supersedes_package_id")
    unsigned = dict(raw)
    unsigned.pop("state_digest")
    raw["state_digest"] = record_sha256(unsigned)
    mailbox.state_path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = AgentMailbox(tmp_path, project_id="project-atlas").successor_records()[0]
    assert migrated.lifecycle == SuccessorLifecycle.WAIT_RECONCILIATION


def test_schema_v3_materializing_successor_migrates_to_reconciliation_required(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="legacy-v3-materializing"))
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    bridge.admit("legacy-v3-materializing")
    raw = json.loads(mailbox.state_path.read_text(encoding="utf-8"))
    raw["schema_version"] = 3
    for item in raw["successors"].values():
        item["lifecycle"] = SuccessorLifecycle.MATERIALIZING.value
        item.pop("retry_id", None)
        item.pop("lifecycle_revision", None)
        item.pop("materialization_owner_token", None)
    unsigned = dict(raw)
    unsigned.pop("state_digest")
    raw["state_digest"] = record_sha256(unsigned)
    mailbox.state_path.write_text(json.dumps(raw), encoding="utf-8")

    migrated = AgentMailbox(tmp_path, project_id="project-atlas").successor_records()[0]
    assert migrated.lifecycle == SuccessorLifecycle.WAIT_RECONCILIATION
    assert migrated.materialization_owner_token is None


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


def test_n1_retryable_fact_change_is_reclassified_instead_of_deduped(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    incomplete = _message(message_id="n1-incomplete")
    incomplete["retryable"] = False
    assert mailbox.enqueue(incomplete).status == MailboxStatus.PENDING
    first = mailbox.process_next(_router())
    assert first is not None and first.routing["classification"] == "WAIT_EXTERNAL"

    corrected = _message(message_id="n1-corrected")
    receipt = mailbox.enqueue(corrected)
    assert receipt.status == MailboxStatus.PENDING and not receipt.duplicate
    second = mailbox.process_next(_router())
    assert second is not None and second.message.message_id == "n1-corrected"
    assert second.routing["classification"] == "CONTINUATION_ELIGIBLE"


def test_n1_owner_fact_change_reaches_current_routing_validation(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="n1-owner-false")
    assert mailbox.enqueue(first).status == MailboxStatus.PENDING
    mailbox.process_next(_router())
    second = _message(message_id="n1-owner-true", owner_required=True)
    receipt = mailbox.enqueue(second)
    assert receipt.status == MailboxStatus.PENDING and not receipt.duplicate
    assert mailbox.process_next(_router()).message.message_id == "n1-owner-true"


def test_n1_missing_prestart_fact_does_not_consume_corrected_observation(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    missing = _message(
        message_id="n1-no-start-fact",
        result=_result_envelope(extras={"retryable": True}),
    )
    mailbox.enqueue(missing)
    incomplete = mailbox.process_next(_router())
    assert incomplete is not None
    assert incomplete.routing["classification"] == "WAIT_EXTERNAL"

    corrected = _message(
        message_id="n1-with-start-fact",
        result=_result_envelope(extras={"retryable": True, "process_started": False}),
    )
    receipt = mailbox.enqueue(corrected)
    assert receipt.status == MailboxStatus.PENDING and not receipt.duplicate
    routed = mailbox.process_next(_router())
    assert routed is not None and routed.message.message_id == "n1-with-start-fact"
    assert routed.routing["classification"] == "CONTINUATION_ELIGIBLE"


def test_n1_authority_reference_change_is_not_cross_message_duplicate(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n1-authority-a"))
    changed = _message(message_id="n1-authority-b")
    changed["authority_reference"] = "grant-other-fixture"
    receipt = mailbox.enqueue(changed)
    assert receipt.status == MailboxStatus.PENDING and not receipt.duplicate
    routed = mailbox.process_next(_router())
    assert routed is not None and routed.message.message_id == "n1-authority-b"
    assert routed.duplicate_of is None


def test_n1_corrected_observation_survives_restart(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    incomplete = _message(message_id="n1-before-restart")
    incomplete["retryable"] = False
    mailbox.enqueue(incomplete)
    mailbox.process_next(_router())
    reopened = AgentMailbox(tmp_path, project_id="project-atlas")
    corrected = _message(message_id="n1-after-restart")
    assert reopened.enqueue(corrected).status == MailboxStatus.PENDING
    routed = reopened.process_next(_router())
    assert routed is not None and routed.routing["classification"] == "CONTINUATION_ELIGIBLE"


def test_n2_duplicate_admission_rechecks_revoked_authority(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n2-original"))
    _processed_for_admission(mailbox, _message(message_id="n2-replay"))
    governor = _governor()
    first_bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _item: True
    )
    first_bridge._materialize = lambda successor: WorkNode.model_validate(successor.work_node)
    first_bridge.admit("n2-original")
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.PREPARED

    revoked = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _item: False
    )
    with pytest.raises(SuccessorAdmissionError) as denied:
        revoked.admit("n2-replay")
    assert denied.value.code == "SUCCESSOR_AUTHORITY_NOT_VERIFIED"
    assert governor.snapshot().nodes == ()


def test_n2_first_and_duplicate_admission_share_current_authority_guard(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n2-guard-a"))
    _processed_for_admission(mailbox, _message(message_id="n2-guard-b"))
    calls: list[str] = []

    def current_authority(message: AgentInboxMessage) -> bool:
        calls.append(message.message_id)
        return True

    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=current_authority
    )
    bridge.admit("n2-guard-a")
    bridge.admit("n2-guard-b")
    assert set(calls) == {"n2-guard-a", "n2-guard-b"}
    assert len(governor.snapshot().nodes) == 1


def test_n2_valid_duplicate_can_create_only_after_current_guard(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n2-valid-first"))
    _processed_for_admission(mailbox, _message(message_id="n2-valid-duplicate"))
    assert mailbox.get_record("n2-valid-duplicate").duplicate_of == "n2-valid-first"
    governor = _governor()
    calls: list[str] = []
    bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=governor,
        authority_verifier=lambda message: calls.append(message.message_id) or True,
    )
    node = bridge.admit("n2-valid-duplicate")
    assert node.state.value == "READY"
    assert calls
    assert set(calls) == {"n2-valid-duplicate"}
    assert len(mailbox.successor_records()) == 1


def test_n2_duplicate_admission_rechecks_moved_source_pin(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n2-pin-original"))
    _processed_for_admission(mailbox, _message(message_id="n2-pin-replay"))
    governor = _governor()
    first_bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _item: True
    )
    first_bridge._materialize = lambda successor: WorkNode.model_validate(successor.work_node)
    first_bridge.admit("n2-pin-original")
    moved = _governor()
    moved._current_main = OTHER_HEAD
    duplicate_bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=moved, authority_verifier=lambda _item: True
    )
    with pytest.raises(SuccessorAdmissionError) as stale:
        duplicate_bridge.admit("n2-pin-replay")
    assert stale.value.code == "SUCCESSOR_SOURCE_PIN_STALE"
    assert moved.snapshot().nodes == ()


def test_n2_duplicate_admission_rechecks_candidate_identity(tmp_path: Path) -> None:
    candidate_head, candidate_tree = "4" * 40, "5" * 40
    result = _result_envelope(outcome="PASS", state="CERTIFIED", blocker="", extras={})
    original = _message(message_id="n2-candidate-a", kind="AGENT_RESULT", result=result)
    original["reason_code"] = None
    original["retryable"] = False
    original["payload"]["candidate_binding"] = {
        "candidate_head": candidate_head,
        "candidate_tree": candidate_tree,
    }
    original["payload_digest"] = payload_sha256(original["payload"])
    duplicate = dict(original)
    duplicate["message_id"] = "n2-candidate-b"
    duplicate["idempotency_key"] = "idem-n2-candidate-b"
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, original)
    _processed_for_admission(mailbox, duplicate)
    governor = _governor()
    checks: list[tuple[str, str]] = []
    initial = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=governor,
        authority_verifier=lambda _item: True,
        candidate_identity_verifier=lambda _item, head, tree: checks.append((head, tree)) or True,
    )
    initial._materialize = lambda successor: WorkNode.model_validate(successor.work_node)
    initial.admit("n2-candidate-a")
    revoked = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=governor,
        authority_verifier=lambda _item: True,
        candidate_identity_verifier=lambda _item, _head, _tree: False,
    )
    with pytest.raises(SuccessorAdmissionError) as invalid:
        revoked.admit("n2-candidate-b")
    assert invalid.value.code == "CANDIDATE_IDENTITY_UNVERIFIED"
    assert checks == [(candidate_head, candidate_tree)]
    assert governor.snapshot().nodes == ()


def test_n3_materialization_claim_is_compare_and_set(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n3-source"))
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    bridge._materialize = lambda successor: WorkNode.model_validate(successor.work_node)
    bridge.admit("n3-source")
    successor = mailbox.successor_records()[0]
    with mailbox.materialization_guard(successor.binding.package_id, successor.generation) as guard:
        claimed, won = mailbox.claim_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            expected_revision=successor.lifecycle_revision,
            owner_token="owner-a",
            guard=guard,
        )
        assert won and claimed.lifecycle == SuccessorLifecycle.MATERIALIZING
        losing, won_again = mailbox.claim_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            expected_revision=successor.lifecycle_revision,
            owner_token="owner-b",
            guard=guard,
        )
    assert not won_again
    assert losing.lifecycle == SuccessorLifecycle.MATERIALIZING
    assert losing.materialization_owner_token == "owner-a"


def test_n3_two_bridges_with_two_governors_have_one_materializer(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="n3-bridge-a", agent_id="builder-a")
    second = _message(message_id="n3-bridge-b", agent_id="builder-b")
    _processed_for_admission(mailbox, first)
    _processed_for_admission(mailbox, second)
    bridges = [
        MailboxGovernorBridge(
            mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
        )
        for _ in range(2)
    ]
    results: list[WorkNode] = []
    errors: list[BaseException] = []
    start = threading.Barrier(3)

    def admit(bridge: MailboxGovernorBridge, message_id: str) -> None:
        start.wait(timeout=3)
        try:
            results.append(bridge.admit(message_id))
        except BaseException as exc:
            errors.append(exc)

    workers = [
        threading.Thread(target=admit, args=(bridges[0], "n3-bridge-a"), name="bridge-a"),
        threading.Thread(target=admit, args=(bridges[1], "n3-bridge-b"), name="bridge-b"),
    ]
    for worker in workers:
        worker.start()
    start.wait(timeout=3)
    for worker in workers:
        worker.join(5)

    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], SuccessorAdmissionError)
    assert errors[0].code == "SUCCESSOR_REQUIRES_RECONCILIATION"
    assert sum(len(bridge.governor.snapshot().nodes) for bridge in bridges) == 1
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.READY
    reconciled = bridges[1].reconcile()
    if bridges[1].governor.snapshot().nodes:
        assert len(reconciled) == 1
        assert reconciled[0].state == NodeState.READY
    else:
        assert reconciled == ()
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.READY


def test_n3_same_governor_loser_cannot_promote_discovered_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n3-same-governor"))
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _item: True
    )

    node_discovered = threading.Event()
    allow_claim_owner_to_continue = threading.Event()
    ready_callers: list[str] = []
    results: list[WorkNode] = []
    errors: list[BaseException] = []
    original_add_node = governor.add_node
    original_mark_ready = governor.mark_ready
    original_materialize = bridge._materialize

    def pause_after_discovery(node: WorkNode) -> None:
        original_add_node(node)
        if threading.current_thread().name == "claim-owner":
            node_discovered.set()
            assert allow_claim_owner_to_continue.wait(3)

    def track_ready(package_id: str) -> None:
        ready_callers.append(threading.current_thread().name)
        original_mark_ready(package_id)

    def route_loser_after_discovery(successor: MailboxSuccessorRecord) -> WorkNode:
        if threading.current_thread().name == "claim-loser":
            assert node_discovered.wait(3)
        return original_materialize(successor)

    monkeypatch.setattr(governor, "add_node", pause_after_discovery)
    monkeypatch.setattr(governor, "mark_ready", track_ready)
    monkeypatch.setattr(bridge, "_materialize", route_loser_after_discovery)

    def admit() -> None:
        try:
            results.append(bridge.admit("n3-same-governor"))
        except BaseException as exc:
            errors.append(exc)

    owner = threading.Thread(target=admit, name="claim-owner")
    loser = threading.Thread(target=admit, name="claim-loser")
    owner.start()
    assert node_discovered.wait(3)
    loser.start()
    loser.join(3)
    assert not loser.is_alive()

    # The losing caller must not promote the shared DISCOVERED node while the
    # materialization owner is paused between add_node and mark_ready.
    assert ready_callers == []
    discovered = next(node for node in governor.snapshot().nodes)
    assert discovered.state == NodeState.DISCOVERED

    allow_claim_owner_to_continue.set()
    owner.join(3)
    assert not owner.is_alive()
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], SuccessorAdmissionError)
    assert errors[0].code == "MATERIALIZATION_OWNER_ACTIVE"
    assert governor.snapshot().nodes[0].state == NodeState.READY
    assert ready_callers == ["claim-owner"]
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.READY


def _n3_prepared_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message_id: str
) -> tuple[AgentMailbox, AutonomousGovernor, MailboxGovernorBridge, MailboxSuccessorRecord]:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id=message_id))
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _item: True
    )
    original_materialize = bridge._materialize
    captured: list[MailboxSuccessorRecord] = []

    def leave_prepared(successor: MailboxSuccessorRecord) -> WorkNode:
        captured.append(successor)
        return WorkNode.model_validate(successor.work_node)

    monkeypatch.setattr(bridge, "_materialize", leave_prepared)
    node = bridge.admit(message_id)
    monkeypatch.setattr(bridge, "_materialize", original_materialize)
    governor.add_node(node)
    assert captured and mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.PREPARED
    return mailbox, governor, bridge, mailbox.successor_records()[0]


def test_n3_r3_no_durable_materializing_claim_cannot_promote_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mailbox, governor, bridge, successor = _n3_prepared_discovered(
        tmp_path, monkeypatch, "n3-r3-no-claim"
    )

    with pytest.raises(SuccessorAdmissionError) as blocked:
        bridge._materialize(successor)

    assert blocked.value.code == "SUCCESSOR_MATERIALIZATION_CLAIM_REQUIRED"
    assert governor.snapshot().nodes[0].state == NodeState.DISCOVERED


def test_n3_r3_generation_mismatch_cannot_promote_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mailbox, governor, bridge, successor = _n3_prepared_discovered(
        tmp_path, monkeypatch, "n3-r3-generation-mismatch"
    )

    with pytest.raises(SuccessorAdmissionError) as blocked:
        bridge._materialize(successor.model_copy(update={"generation": successor.generation + 1}))

    assert blocked.value.code == "SUCCESSOR_GENERATION_MISMATCH"
    assert governor.snapshot().nodes[0].state == NodeState.DISCOVERED


def test_n3_r3_durable_owner_token_alone_cannot_promote_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox, governor, bridge, successor = _n3_prepared_discovered(
        tmp_path, monkeypatch, "n3-r3-owner-token"
    )
    with mailbox.materialization_guard(successor.binding.package_id, successor.generation) as guard:
        claimed, won = mailbox.claim_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            expected_revision=successor.lifecycle_revision,
            owner_token="materializer-A",
            guard=guard,
        )
    assert won and claimed.materialization_owner_token == "materializer-A"

    # A persisted token is not ambient authority for a replay/competitor; only
    # the stack that won the CAS performs READY promotion.
    with pytest.raises(SuccessorAdmissionError) as blocked:
        bridge._materialize(claimed)

    assert blocked.value.code == "SUCCESSOR_MATERIALIZATION_CLAIM_REQUIRED"
    assert governor.snapshot().nodes[0].state == NodeState.DISCOVERED


def test_n3_r3_reconcile_does_not_promote_discovered_without_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mailbox, governor, bridge, _successor = _n3_prepared_discovered(
        tmp_path, monkeypatch, "n3-r3-reconcile"
    )

    with pytest.raises(SuccessorAdmissionError) as blocked:
        bridge.reconcile()

    assert blocked.value.code == "SUCCESSOR_MATERIALIZATION_CLAIM_REQUIRED"
    assert governor.snapshot().nodes[0].state == NodeState.DISCOVERED


def test_n3_stale_materialization_owner_cannot_finalize(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n3-finalize"))
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    bridge._materialize = lambda successor: WorkNode.model_validate(successor.work_node)
    bridge.admit("n3-finalize")
    successor = mailbox.successor_records()[0]
    with mailbox.materialization_guard(successor.binding.package_id, 1) as guard:
        mailbox.claim_materialization(
            successor.binding.package_id,
            generation=1,
            expected_revision=successor.lifecycle_revision,
            owner_token="owner-current",
            guard=guard,
        )
    with mailbox.materialization_guard(successor.binding.package_id, 1) as guard:
        recovered, won = mailbox.recover_materialization_claim(
            successor.binding.package_id,
            generation=1,
            expected_revision=successor.lifecycle_revision + 1,
            expected_owner_token="owner-current",
            expected_lifecycle=SuccessorLifecycle.MATERIALIZING,
            new_owner_token="owner-recovered",
            guard=guard,
        )
        assert won
        with pytest.raises(MailboxError) as stale:
            mailbox.finalize_materialization(
                successor.binding.package_id,
                generation=1,
                expected_revision=successor.lifecycle_revision + 1,
                owner_token="owner-current",
                lifecycle=SuccessorLifecycle.READY,
                guard=guard,
            )
        assert stale.value.code == "MATERIALIZATION_STALE_OWNER"
        assert recovered.materialization_owner_token == "owner-recovered"


def test_n3_restart_during_materialization_recovers_with_new_fenced_claim(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n3-restart"))
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    bridge._materialize = lambda successor: WorkNode.model_validate(successor.work_node)
    bridge.admit("n3-restart")
    successor = mailbox.successor_records()[0]
    with mailbox.materialization_guard(successor.binding.package_id, 1) as guard:
        mailbox.claim_materialization(
            successor.binding.package_id,
            generation=1,
            expected_revision=successor.lifecycle_revision,
            owner_token="owner-survives-restart",
            guard=guard,
        )

    recovered = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=_governor(),
        authority_verifier=lambda _item: True,
    )
    restored = recovered.reconcile()
    assert len(restored) == 1
    assert restored[0].package_id == successor.binding.package_id
    assert restored[0].state == NodeState.READY
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.READY


def _r4_prepared_successor(
    tmp_path: Path, message_id: str
) -> tuple[AgentMailbox, AutonomousGovernor, MailboxGovernorBridge, MailboxSuccessorRecord]:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id=message_id))
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _item: True
    )
    original_materialize = bridge._materialize
    bridge._materialize = lambda successor: WorkNode.model_validate(successor.work_node)
    bridge.admit(message_id)
    bridge._materialize = original_materialize
    successor = mailbox.successor_records()[0]
    assert successor.lifecycle == SuccessorLifecycle.PREPARED
    return mailbox, governor, bridge, successor


@pytest.mark.parametrize(
    ("crash_point", "expected_governor_state"),
    [
        ("after_claim", ()),
        ("after_add", (NodeState.DISCOVERED,)),
        ("after_ready", (NodeState.READY,)),
    ],
)
def test_r4_reconcile_recovers_each_materialization_crash_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
    expected_governor_state: tuple[NodeState, ...],
) -> None:
    mailbox, governor, bridge, successor = _r4_prepared_successor(tmp_path, f"r4-{crash_point}")
    original_add = governor.add_node
    original_ready = governor.mark_ready
    original_finalize = mailbox.finalize_materialization

    if crash_point == "after_claim":

        def crash_before_add(_node: WorkNode) -> None:
            raise RuntimeError("injected crash after durable claim")

        monkeypatch.setattr(governor, "add_node", crash_before_add)
    elif crash_point == "after_add":
        monkeypatch.setattr(governor, "add_node", original_add)

        def crash_before_ready(_package_id: str) -> None:
            raise RuntimeError("injected crash after governor add")

        monkeypatch.setattr(governor, "mark_ready", crash_before_ready)
    else:
        monkeypatch.setattr(governor, "add_node", original_add)
        monkeypatch.setattr(governor, "mark_ready", original_ready)

        def crash_before_finalize(*args: Any, **kwargs: Any) -> MailboxSuccessorRecord:
            raise RuntimeError("injected crash after governor ready")

        monkeypatch.setattr(mailbox, "finalize_materialization", crash_before_finalize)

    with pytest.raises(RuntimeError, match="injected crash"):
        bridge._materialize(successor)
    assert tuple(node.state for node in governor.snapshot().nodes) == expected_governor_state
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.MATERIALIZING

    monkeypatch.setattr(governor, "add_node", original_add)
    monkeypatch.setattr(governor, "mark_ready", original_ready)
    monkeypatch.setattr(mailbox, "finalize_materialization", original_finalize)
    recovered = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=governor,
        authority_verifier=lambda _item: True,
    )

    restored = recovered.reconcile()

    assert len(restored) == 1
    assert restored[0].package_id == successor.binding.package_id
    assert restored[0].state == NodeState.READY
    assert len(governor.snapshot().nodes) == 1
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.READY


def test_r4_ready_successor_restores_same_node_after_restart(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="r4-ready-restore"))
    original = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    ).admit("r4-ready-restore")
    restarted_governor = _governor()
    recovered = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=restarted_governor,
        authority_verifier=lambda _item: True,
    ).reconcile()

    assert len(recovered) == 1
    assert recovered[0].package_id == original.package_id
    assert recovered[0].state == NodeState.READY
    assert restarted_governor.snapshot().nodes == recovered
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.READY


def test_r4_ready_restore_fails_closed_when_authority_is_revoked(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="r4-authority-revoked"))
    MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    ).admit("r4-authority-revoked")
    restarted_governor = _governor()
    recovered = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=restarted_governor,
        authority_verifier=lambda _item: False,
    )

    assert recovered.reconcile() == ()
    assert restarted_governor.snapshot().nodes == ()
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.WAIT_RECONCILIATION


def test_r4_wait_reconciliation_resumes_same_generation_after_authority_returns(
    tmp_path: Path,
) -> None:
    mailbox, governor, _bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-authority-restored"
    )
    with mailbox.materialization_guard(successor.binding.package_id, successor.generation) as guard:
        claimed, won = mailbox.claim_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            expected_revision=successor.lifecycle_revision,
            owner_token="owner-interrupted",
            guard=guard,
        )
    assert won and claimed.lifecycle == SuccessorLifecycle.MATERIALIZING

    denied = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=governor,
        authority_verifier=lambda _item: False,
    )
    assert denied.reconcile() == ()
    waiting = mailbox.get_successor(successor.binding.package_id)
    assert waiting is not None
    assert waiting.lifecycle == SuccessorLifecycle.WAIT_RECONCILIATION
    assert waiting.generation == successor.generation
    assert waiting.materialization_owner_token is None

    restored = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=governor,
        authority_verifier=lambda _item: True,
    )
    nodes = restored.reconcile()
    assert len(nodes) == 1
    assert nodes[0].package_id == successor.binding.package_id
    assert nodes[0].state == NodeState.READY
    assert mailbox.get_successor(successor.binding.package_id).generation == successor.generation
    assert mailbox.get_successor(successor.binding.package_id).lifecycle == SuccessorLifecycle.READY

    admitted = restored.admit("r4-authority-restored")
    assert admitted == nodes[0]


def test_r4_revoked_authority_blocks_live_ready_node_until_revalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox, governor, bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-live-ready-authority-revoked"
    )
    original_finalize = mailbox.finalize_materialization

    def crash_before_finalize(*args: Any, **kwargs: Any) -> MailboxSuccessorRecord:
        raise RuntimeError("injected crash after governor ready")

    monkeypatch.setattr(mailbox, "finalize_materialization", crash_before_finalize)
    with pytest.raises(RuntimeError, match="injected crash"):
        bridge.admit("r4-live-ready-authority-revoked")
    assert governor.snapshot().nodes[0].state == NodeState.READY
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.MATERIALIZING
    monkeypatch.setattr(mailbox, "finalize_materialization", original_finalize)

    revoked = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=governor,
        authority_verifier=lambda _item: False,
    )
    assert revoked.reconcile() == ()
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.WAIT_RECONCILIATION
    assert governor.snapshot().nodes[0].state == NodeState.BLOCKED
    with pytest.raises(GovernorError):
        governor.lease(
            successor.binding.package_id,
            "discover-worker",
            branch="test",
            worktree=str(tmp_path),
        )

    restored = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=governor,
        authority_verifier=lambda _item: True,
    )
    recovered = restored.reconcile()
    assert len(recovered) == 1
    assert recovered[0].package_id == successor.binding.package_id
    assert mailbox.successor_records()[0].generation == successor.generation
    assert recovered[0].state == NodeState.READY
    assert governor.snapshot().nodes == recovered


def test_r4_existing_lease_cannot_execute_after_mailbox_authority_revocation(
    tmp_path: Path,
) -> None:
    authority = {"valid": True}
    _mailbox, governor, bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-revoked-existing-lease"
    )
    bridge.authority_verifier = lambda _item: authority["valid"]

    ready = bridge.reconcile()
    assert len(ready) == 1
    lease = governor.lease(
        successor.binding.package_id,
        "discover-worker",
        branch="test",
        worktree=str(tmp_path),
    )
    authority["valid"] = False

    with pytest.raises(GovernorError) as exc_info:
        governor.execute_leased(lease.lease_id)
    assert exc_info.value.code == "MAILBOX_AUTHORITY_REVALIDATION_REQUIRED"
    assert governor.snapshot().nodes[0].state == NodeState.LEASED


def test_r4_blocked_to_ready_is_not_a_generic_dag_transition(tmp_path: Path) -> None:
    mailbox, governor, bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-generic-blocked-ready"
    )
    recovered = bridge.reconcile()
    assert len(recovered) == 1
    governor.transition(successor.binding.package_id, NodeState.BLOCKED, "test block")

    with pytest.raises(IllegalTransitionError):
        governor.transition(successor.binding.package_id, NodeState.READY, "generic restore")
    with pytest.raises(GovernorError) as exc_info:
        governor._restore_blocked_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            lifecycle_revision=successor.lifecycle_revision,
            owner_token="untrusted-owner",
            guard=object(),
        )

    assert exc_info.value.code == "MATERIALIZATION_REVALIDATION_REQUIRED"
    assert mailbox.successor_records()[0].lifecycle == SuccessorLifecycle.READY
    assert governor.snapshot().nodes[0].state == NodeState.BLOCKED


def test_r4_external_loop_dispatch_revalidates_mailbox_authority(tmp_path: Path) -> None:
    authority = {"valid": True}
    _mailbox, governor, bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-external-loop-dispatch-guard"
    )
    bridge.authority_verifier = lambda _item: authority["valid"]
    ready = bridge.reconcile()
    assert len(ready) == 1
    assert ready[0].execution_host_class.value == "EXTERNAL_AGENT"
    dispatches: list[dict[str, object]] = []
    loop = AutonomousLoop(
        governor=governor,
        trusted=governor._trusted,
        store=tmp_path / "loop-store",
        root=tmp_path,
        dispatch=CallableDispatchPort(
            lambda _root: (
                dispatches.append({"dispatch_id": "dispatch-r4", "status": "RUNNING"})
                or dispatches[-1]
            )
        ),
    )

    lease = governor.lease(
        successor.binding.package_id,
        "discover-worker",
        branch="test",
        worktree=str(tmp_path),
    )
    loop._save(
        phase=LoopPhase.LEASED,
        active_package_id=successor.binding.package_id,
        active_lease_id=lease.lease_id,
    )
    authority["valid"] = False
    with pytest.raises(LoopError) as exc_info:
        loop.tick()

    assert exc_info.value.code == "MAILBOX_AUTHORITY_REVALIDATION_REQUIRED"
    assert dispatches == []
    assert governor.snapshot().nodes[0].state == NodeState.LEASED


def test_r4_recovery_claim_rotates_owner_and_fences_stale_finalize(tmp_path: Path) -> None:
    mailbox, _governor_state, _bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-stale-owner"
    )
    with mailbox.materialization_guard(successor.binding.package_id, successor.generation) as guard:
        old_claim, won = mailbox.claim_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            expected_revision=successor.lifecycle_revision,
            owner_token="owner-old",
            guard=guard,
        )
    assert won

    with mailbox.materialization_guard(successor.binding.package_id, successor.generation) as guard:
        recovered_claim, won = mailbox.recover_materialization_claim(
            successor.binding.package_id,
            generation=old_claim.generation,
            expected_revision=old_claim.lifecycle_revision,
            expected_owner_token=old_claim.materialization_owner_token,
            expected_lifecycle=SuccessorLifecycle.MATERIALIZING,
            new_owner_token="owner-new",
            guard=guard,
        )
        assert won
        with pytest.raises(MailboxError) as stale:
            mailbox.finalize_materialization(
                successor.binding.package_id,
                generation=old_claim.generation,
                expected_revision=old_claim.lifecycle_revision,
                owner_token="owner-old",
                lifecycle=SuccessorLifecycle.READY,
                guard=guard,
            )
        assert stale.value.code == "MATERIALIZATION_STALE_OWNER"
        mailbox.finalize_materialization(
            successor.binding.package_id,
            generation=recovered_claim.generation,
            expected_revision=recovered_claim.lifecycle_revision,
            owner_token="owner-new",
            lifecycle=SuccessorLifecycle.READY,
            guard=guard,
        )


def test_r4_recovery_claim_rejects_generation_mismatch(tmp_path: Path) -> None:
    mailbox, _governor_state, _bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-generation-mismatch"
    )
    with mailbox.materialization_guard(successor.binding.package_id, successor.generation) as guard:
        claimed, _ = mailbox.claim_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            expected_revision=successor.lifecycle_revision,
            owner_token="owner-current",
            guard=guard,
        )
    with mailbox.materialization_guard(
        successor.binding.package_id, claimed.generation + 1
    ) as guard:
        recovered, won = mailbox.recover_materialization_claim(
            successor.binding.package_id,
            generation=claimed.generation + 1,
            expected_revision=claimed.lifecycle_revision,
            expected_owner_token=claimed.materialization_owner_token,
            expected_lifecycle=SuccessorLifecycle.MATERIALIZING,
            new_owner_token="owner-recovery",
            guard=guard,
        )
    assert not won
    assert recovered.lifecycle == SuccessorLifecycle.MATERIALIZING
    assert recovered.materialization_owner_token == "owner-current"


def test_r4_active_materialization_guard_is_not_stolen(tmp_path: Path) -> None:
    mailbox, _governor_state, _bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-active-owner"
    )
    with (
        mailbox.materialization_guard(successor.binding.package_id, successor.generation),
        pytest.raises(TimeoutError),
        mailbox.materialization_guard(
            successor.binding.package_id, successor.generation, wait_seconds=0.01
        ),
    ):
        pytest.fail("competing recovery stole a live materialization guard")


def test_r4_duck_typed_guard_cannot_rotate_live_materialization_claim(
    tmp_path: Path,
) -> None:
    mailbox, _governor, _bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-spoofed-materialization-guard"
    )
    with mailbox.materialization_guard(successor.binding.package_id, successor.generation) as guard:
        claimed, won = mailbox.claim_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            expected_revision=successor.lifecycle_revision,
            owner_token="owner-live",
            guard=guard,
        )
        assert won
        spoofed_guard = SimpleNamespace(held=True, path=guard.path)

        with pytest.raises(MailboxError) as exc_info:
            mailbox.recover_materialization_claim(
                successor.binding.package_id,
                generation=claimed.generation,
                expected_revision=claimed.lifecycle_revision,
                expected_owner_token=claimed.materialization_owner_token,
                expected_lifecycle=SuccessorLifecycle.MATERIALIZING,
                new_owner_token="owner-spoofed",
                guard=cast(_MailboxFileLock, spoofed_guard),
            )

        assert exc_info.value.code == "MATERIALIZATION_GUARD_REQUIRED"
        current = mailbox.get_successor(successor.binding.package_id)
        assert current is not None
        assert current.lifecycle_revision == claimed.lifecycle_revision
        assert current.materialization_owner_token == "owner-live"


def _r4_recovery_process(
    root: str,
    observed: multiprocessing.Queue[tuple[str, str, int]],
    resume: multiprocessing.Event,
) -> None:
    mailbox = AgentMailbox(Path(root), project_id="project-atlas")
    governor = _governor()
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=governor, authority_verifier=lambda _item: True
    )
    original = bridge._materialize

    def synchronized_recovery(
        successor: MailboxSuccessorRecord,
        *,
        recovery_request: Any = None,
    ) -> WorkNode | None:
        observed.put(("observed", str(os.getpid()), successor.generation))
        if not resume.wait(10):
            raise RuntimeError("recovery test release timed out")
        result = original(successor, recovery_request=recovery_request)
        observed.put(("done", str(os.getpid()), len(governor.snapshot().nodes)))
        return result

    bridge._materialize = synchronized_recovery
    bridge.reconcile()


def test_r4_two_recovery_processes_rotate_one_claim_and_materialize_once(
    tmp_path: Path,
) -> None:
    mailbox, _governor_state, _bridge, successor = _r4_prepared_successor(
        tmp_path, "r4-two-processes"
    )
    with mailbox.materialization_guard(successor.binding.package_id, successor.generation) as guard:
        claimed, won = mailbox.claim_materialization(
            successor.binding.package_id,
            generation=successor.generation,
            expected_revision=successor.lifecycle_revision,
            owner_token="r4-process-owner",
            guard=guard,
        )
    assert won and claimed.lifecycle == SuccessorLifecycle.MATERIALIZING

    context = multiprocessing.get_context("spawn")
    observed: multiprocessing.Queue[tuple[str, str, int]] = context.Queue()
    resume = context.Event()
    workers = [
        context.Process(
            target=_r4_recovery_process,
            args=(str(tmp_path), observed, resume),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    observations = [observed.get(timeout=15) for _ in range(2)]
    assert all(item[0] == "observed" for item in observations)
    resume.set()
    completions = [observed.get(timeout=15) for _ in range(2)]
    for worker in workers:
        worker.join(15)
    assert all(not worker.is_alive() for worker in workers)
    assert all(worker.exitcode == 0 for worker in workers)
    assert sorted(item[2] for item in completions) == [0, 1]
    final_mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    final_successor = final_mailbox.get_successor(successor.binding.package_id)
    assert final_successor is not None
    assert final_successor.lifecycle == SuccessorLifecycle.READY
    assert final_successor.generation == successor.generation


def test_r4_terminal_replay_does_not_recover_materialization(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="r4-terminal-replay"))
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    node = bridge.admit("r4-terminal-replay")
    mailbox.set_successor_lifecycle(node.package_id, SuccessorLifecycle.TERMINAL)
    restarted = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=_governor(),
        authority_verifier=lambda _item: True,
    )
    assert restarted.reconcile() == ()
    with pytest.raises(SuccessorAdmissionError) as terminal:
        restarted.admit("r4-terminal-replay")
    assert terminal.value.code == "SUCCESSOR_ALREADY_TERMINAL"
    assert len(mailbox.successor_records()) == 1


def test_n4_original_admission_replay_cannot_create_generation(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n4-original"))
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    node = bridge.admit("n4-original")
    mailbox.set_successor_lifecycle(node.package_id, SuccessorLifecycle.TERMINAL)
    restarted = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=_governor(),
        authority_verifier=lambda _item: True,
    )
    with pytest.raises(SuccessorAdmissionError) as terminal:
        restarted.admit("n4-original")
    assert terminal.value.code == "SUCCESSOR_ALREADY_TERMINAL"
    assert len(mailbox.successor_records()) == 1


def test_n4_repeated_terminal_admission_replay_never_allocates_generation(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    _processed_for_admission(mailbox, _message(message_id="n4-replay-100"))
    bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    original = bridge.admit("n4-replay-100")
    mailbox.set_successor_lifecycle(original.package_id, SuccessorLifecycle.TERMINAL)
    replay = MailboxGovernorBridge(
        mailbox=AgentMailbox(tmp_path, project_id="project-atlas"),
        governor=_governor(),
        authority_verifier=lambda _item: True,
    )
    for _ in range(100):
        with pytest.raises(SuccessorAdmissionError) as terminal:
            replay.admit("n4-replay-100")
        assert terminal.value.code == "SUCCESSOR_ALREADY_TERMINAL"
    assert len(mailbox.successor_records()) == 1


def test_n4_unauthorized_retry_is_rejected_and_authorized_retry_replays(
    tmp_path: Path,
) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="n4-prior")
    second = _message(message_id="n4-next")
    second["attempt_id"] = "attempt-2"
    second["payload"]["result_envelope"]["task"]["attempt"] = 2
    second["payload_digest"] = payload_sha256(second["payload"])
    _processed_for_admission(mailbox, first)
    _processed_for_admission(mailbox, second)
    initial_bridge = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    prior = initial_bridge.admit("n4-prior")
    mailbox.set_successor_lifecycle(prior.package_id, SuccessorLifecycle.TERMINAL)

    denied_bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _item: True,
        retry_verifier=lambda _message, _retry_id, _prior: False,
    )
    with pytest.raises(SuccessorAdmissionError) as denied:
        denied_bridge.admit(
            "n4-next",
            retry_id="retry-denied",
            prior_successor_id=prior.package_id,
            prior_generation=1,
        )
    assert denied.value.code == "SUCCESSOR_RETRY_NOT_AUTHORIZED"
    assert len(mailbox.successor_records()) == 1

    allowed_bridge = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=_governor(),
        authority_verifier=lambda _item: True,
        retry_verifier=lambda _message, retry_id, _prior: retry_id == "retry-approved",
    )
    successor = allowed_bridge.admit(
        "n4-next",
        retry_id="retry-approved",
        prior_successor_id=prior.package_id,
        prior_generation=1,
    )
    assert len(mailbox.successor_records()) == 2
    again = allowed_bridge.admit(
        "n4-next",
        retry_id="retry-approved",
        prior_successor_id=prior.package_id,
        prior_generation=1,
    )
    assert again.package_id == successor.package_id
    assert len(mailbox.successor_records()) == 2
    with pytest.raises(SuccessorAdmissionError) as old_admission:
        initial_bridge.admit("n4-prior")
    assert old_admission.value.code == "SUCCESSOR_ALREADY_TERMINAL"
    assert len(mailbox.successor_records()) == 2


def test_n4_retry_after_source_pin_moves_fails_current_pin_gate(tmp_path: Path) -> None:
    mailbox = AgentMailbox(tmp_path, project_id="project-atlas")
    first = _message(message_id="n4-moved-prior")
    second = _message(message_id="n4-moved-next")
    second["attempt_id"] = "attempt-2"
    second["payload"]["result_envelope"]["task"]["attempt"] = 2
    second["payload_digest"] = payload_sha256(second["payload"])
    _processed_for_admission(mailbox, first)
    _processed_for_admission(mailbox, second)
    initial = MailboxGovernorBridge(
        mailbox=mailbox, governor=_governor(), authority_verifier=lambda _item: True
    )
    prior = initial.admit("n4-moved-prior")
    mailbox.set_successor_lifecycle(prior.package_id, SuccessorLifecycle.TERMINAL)
    moved = _governor()
    moved._current_main = OTHER_HEAD
    retry = MailboxGovernorBridge(
        mailbox=mailbox,
        governor=moved,
        authority_verifier=lambda _item: True,
        retry_verifier=lambda _message, _retry_id, _prior: True,
    )
    with pytest.raises(SuccessorAdmissionError) as stale:
        retry.admit(
            "n4-moved-next",
            retry_id="retry-moved-source",
            prior_successor_id=prior.package_id,
            prior_generation=1,
        )
    assert stale.value.code == "SUCCESSOR_SOURCE_PIN_STALE"
    assert len(mailbox.successor_records()) == 1
