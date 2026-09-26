"""Versioned inbox messages and fail-closed persistence records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas_contracts.versions import HASH_PATTERN, ID_PATTERN
from project_atlas.orchestration.models import (
    ResultProducer,
)
from project_atlas.secrets import scan_text

_PIN_RE = re.compile(r"^[0-9a-f]{40}$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_PAYLOAD_BYTES = 256 * 1024


class MailboxError(ValueError):
    """Durable inbox error with a stable failure code."""

    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


class InboxMessageKind(StrEnum):
    AGENT_RESULT = "AGENT_RESULT"
    BLOCKED = "BLOCKED"
    PROGRESS = "PROGRESS"
    VERIFY_REQUEST = "VERIFY_REQUEST"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    CONTINUATION = "CONTINUATION"
    OWNER_REQUIRED = "OWNER_REQUIRED"


class MailboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    QUARANTINED = "QUARANTINED"


class SuccessorLifecycle(StrEnum):
    PREPARED = "PREPARED"
    MATERIALIZING = "MATERIALIZING"
    READY = "READY"
    LEASED = "LEASED"
    ACTIVE = "ACTIVE"
    VERIFYING = "VERIFYING"
    REMEDIATING = "REMEDIATING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    WAIT_RECONCILIATION = "WAIT_RECONCILIATION"
    TERMINAL = "TERMINAL"


ACTIVE_SUCCESSOR_LIFECYCLES = frozenset(
    {
        SuccessorLifecycle.PREPARED,
        SuccessorLifecycle.MATERIALIZING,
        SuccessorLifecycle.READY,
        SuccessorLifecycle.LEASED,
        SuccessorLifecycle.ACTIVE,
        SuccessorLifecycle.VERIFYING,
        SuccessorLifecycle.REMEDIATING,
        SuccessorLifecycle.WAITING_EXTERNAL,
        SuccessorLifecycle.WAIT_RECONCILIATION,
    }
)


def canonical_json(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("payload must be canonical JSON data") from exc


def payload_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def record_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


class AgentInboxMessage(BaseModel):
    """Transport envelope. Producer claims are input, never authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    message_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    project_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    task_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    run_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    dispatch_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    attempt_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    correlation_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    causation_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    requester_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    authority_reference: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    producer: ResultProducer
    message_kind: InboxMessageKind
    task_class: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    reason_code: str | None = Field(default=None, max_length=64)
    trusted_head: str = Field(min_length=40, max_length=40)
    trusted_tree: str = Field(min_length=40, max_length=40)
    payload: dict[str, Any] = Field(max_length=64)
    payload_digest: str = Field(pattern=HASH_PATTERN)
    created_at: datetime
    expires_at: datetime | None = None
    retryable: bool = False
    owner_required: bool = False
    authority_claimed: Literal[False] = False
    execution_authorized: Literal[False] = False
    merge_authorized: Literal[False] = False

    @field_validator("trusted_head", "trusted_tree")
    @classmethod
    def _trusted_pin(cls, value: str) -> str:
        if not _PIN_RE.fullmatch(value):
            raise ValueError("trusted pins must be full lowercase Git object IDs")
        return value

    @field_validator("reason_code")
    @classmethod
    def _reason_code(cls, value: str | None) -> str | None:
        if value is not None and not _CODE_RE.fullmatch(value):
            raise ValueError("reason_code must be an uppercase identifier")
        return value

    @field_validator("created_at", "expires_at")
    @classmethod
    def _aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("message timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _payload_integrity_and_bounds(self) -> AgentInboxMessage:
        payload_bytes = canonical_json(self.payload)
        if len(payload_bytes) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds bounded inbox size")
        if scan_text(canonical_json(self.model_dump(mode="json")).decode("utf-8")):
            raise ValueError("secret-shaped message content is not accepted for persistence")
        if hashlib.sha256(payload_bytes).hexdigest() != self.payload_digest:
            raise ValueError("payload_digest does not match canonical payload")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        return self

    def incident_id(self) -> str:
        """Stable per-task/source/failure correlation; producer identity is excluded."""
        material = {
            "project_id": self.project_id,
            "requester_id": self.requester_id,
            "task_id": self.task_id,
            "task_class": self.task_class,
            "trusted_head": self.trusted_head,
            "trusted_tree": self.trusted_tree,
            "reason_code": self.reason_code,
        }
        return "inc-" + record_sha256(material)[:32]

    def message_sha256(self) -> str:
        return record_sha256(self.model_dump(mode="json"))

    def idempotency_sha256(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("message_id")
        payload.pop("created_at")
        return record_sha256(payload)


class MailboxRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: AgentInboxMessage
    status: MailboxStatus = MailboxStatus.PENDING
    incident_id: str
    routing: dict[str, Any] | None = None
    quarantine_code: str | None = None
    duplicate_of: str | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QuarantineReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(pattern=HASH_PATTERN)
    code: str = Field(pattern=_CODE_RE.pattern)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MailboxSuccessorBindingV1(BaseModel):
    """Durable correlation from a validated inbox route to a governor WorkNode."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    package_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    message_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    incident_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    source_task_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    source_attempt_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    source_result_digest: str = Field(pattern=HASH_PATTERN)
    route_digest: str = Field(pattern=HASH_PATTERN)
    correlation_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    causation_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)
    requester_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    trusted_main: str = Field(min_length=40, max_length=40)
    trusted_tree: str = Field(min_length=40, max_length=40)
    candidate_head: str | None = Field(default=None, min_length=40, max_length=40)
    candidate_tree: str | None = Field(default=None, min_length=40, max_length=40)
    task_type: Literal["candidate_verification", "recertification", "program_reconciliation"]
    transition: str = Field(min_length=1, max_length=64, pattern=_CODE_RE.pattern)
    execution_host_class: Literal["EXTERNAL_AGENT"] = "EXTERNAL_AGENT"
    required_capabilities: tuple[Literal["DISCOVER", "VERIFY"], ...] = Field(
        min_length=1, max_length=2
    )
    authority_reference: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    owner_gate: Literal[False] = False
    execution_authorized: Literal[False] = False
    merge_authorized: Literal[False] = False
    production_mutation_authorized: Literal[False] = False
    binding_digest: str = Field(pattern=HASH_PATTERN)

    @field_validator("trusted_main", "trusted_tree", "candidate_head", "candidate_tree")
    @classmethod
    def _binding_pin(cls, value: str | None) -> str | None:
        if value is not None and not _PIN_RE.fullmatch(value):
            raise ValueError("binding pins must be full lowercase Git object IDs")
        return value

    def unsigned_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload.pop("binding_digest")
        return payload

    def seal(self) -> MailboxSuccessorBindingV1:
        return self.model_copy(update={"binding_digest": record_sha256(self.unsigned_payload())})

    def verify(self) -> MailboxSuccessorBindingV1:
        if self.binding_digest != record_sha256(self.unsigned_payload()):
            raise ValueError("successor binding digest mismatch")
        if self.owner_gate or self.execution_authorized or self.merge_authorized:
            raise ValueError("successor binding cannot carry authority")
        if self.production_mutation_authorized:
            raise ValueError("successor binding cannot authorize production mutation")
        return self


class MailboxSuccessorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: MailboxSuccessorBindingV1
    work_node: dict[str, Any]
    work_node_digest: str = Field(pattern=HASH_PATTERN)
    lifecycle: SuccessorLifecycle = SuccessorLifecycle.PREPARED
    generation: int = Field(default=1, ge=1)
    supersedes_package_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)

    @model_validator(mode="after")
    def _node_digest(self) -> MailboxSuccessorRecord:
        if record_sha256(self.work_node) != self.work_node_digest:
            raise ValueError("successor WorkNode digest mismatch")
        self.binding.verify()
        if self.work_node.get("package_id") != self.binding.package_id:
            raise ValueError("successor WorkNode package binding mismatch")
        return self


class MailboxState(BaseModel):
    """Single-project atomic snapshot. Digest detects edits/corruption."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = 3
    project_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    records: dict[str, MailboxRecord] = Field(default_factory=dict)
    quarantined: dict[str, QuarantineReceipt] = Field(default_factory=dict)
    successors: dict[str, MailboxSuccessorRecord] = Field(default_factory=dict)
    state_digest: str = Field(pattern=HASH_PATTERN)

    def unsigned_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload.pop("state_digest")
        return payload

    def verify(self) -> MailboxState:
        if record_sha256(self.unsigned_payload()) != self.state_digest:
            raise MailboxError("mailbox state digest mismatch", code="STORE_CORRUPT")
        if any(key != record.message.message_id for key, record in self.records.items()):
            raise MailboxError("mailbox record index mismatch", code="STORE_CORRUPT")
        if any(record.message.project_id != self.project_id for record in self.records.values()):
            raise MailboxError("cross-project mailbox row", code="STORE_CORRUPT")
        if any(key != item.binding.package_id for key, item in self.successors.items()):
            raise MailboxError("successor index mismatch", code="STORE_CORRUPT")
        active_incidents: set[str] = set()
        for item in self.successors.values():
            if item.lifecycle not in ACTIVE_SUCCESSOR_LIFECYCLES:
                continue
            incident_id = item.binding.incident_id
            if incident_id in active_incidents:
                raise MailboxError("multiple active successors for incident", code="STORE_CORRUPT")
            active_incidents.add(incident_id)
        return self

    def seal(self) -> MailboxState:
        body = self.model_dump(mode="json")
        body.pop("state_digest")
        return self.model_copy(update={"state_digest": record_sha256(body)})


class EnqueueReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MailboxStatus
    message_id: str | None = None
    incident_id: str | None = None
    duplicate: bool = False
    duplicate_of: str | None = None
    quarantine_code: str | None = None
    authority_claimed: Literal[False] = False


class InboxRoutingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Literal[
        "CONTINUATION_ELIGIBLE",
        "TASK_DIRECTIVE_READY",
        "OWNER_REQUIRED",
        "WAIT_EXTERNAL",
        "FAIL_TERMINAL",
        "QUARANTINE",
    ]
    decision: dict[str, Any] | None = None
    route: dict[str, Any] | None = None
    reason_code: str | None = None
    execution_authorized: Literal[False] = False
    authority_granted: Literal[False] = False

    @model_validator(mode="after")
    def _no_authority(self) -> InboxRoutingResult:
        if self.execution_authorized or self.authority_granted:
            raise ValueError("Inbox routing cannot carry authority")
        return self
