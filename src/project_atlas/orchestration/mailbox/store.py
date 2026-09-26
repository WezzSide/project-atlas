"""Atomic, project-isolated mailbox snapshot store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import ValidationError

from atlas_contracts.versions import ID_PATTERN
from project_atlas.orchestration.mailbox.models import (
    MAX_PAYLOAD_BYTES,
    AgentInboxMessage,
    EnqueueReceipt,
    InboxRoutingResult,
    MailboxError,
    MailboxRecord,
    MailboxState,
    MailboxStatus,
    MailboxSuccessorBindingV1,
    MailboxSuccessorRecord,
    QuarantineReceipt,
    canonical_json,
    record_sha256,
)
from project_atlas.source_identity import IdentityLockError, ProjectIdentityLock

STATE_RELATIVE = Path(".atlas") / "orchestration" / "inbox"
STATE_NAME = "state.json"
LOCK_NAME = ".inbox.lock"
T = TypeVar("T")


class _InboxRouter(Protocol):
    def classify(self, message: AgentInboxMessage) -> InboxRoutingResult: ...


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        if os.name != "nt":
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        tmp_path.unlink(missing_ok=True)


class AgentMailbox:
    """One project's durable ingress journal; not a task queue or authority ledger."""

    def __init__(self, root: Path, *, project_id: str) -> None:
        resolved = root.expanduser().resolve()
        if (
            not resolved.is_dir()
            or resolved == Path(resolved.anchor)
            or resolved == Path.home().resolve()
        ):
            raise MailboxError("mailbox root is invalid or too broad", code="PATH_UNSAFE")
        if not re.fullmatch(ID_PATTERN, project_id):
            raise MailboxError("project id is unsafe", code="PROJECT_INVALID")
        self.root = resolved
        self.project_id = project_id
        self.store_dir = (resolved / STATE_RELATIVE / project_id).resolve()
        if not _inside(resolved, self.store_dir):
            raise MailboxError("mailbox path escapes root", code="PATH_UNSAFE")
        self.state_path = self.store_dir / STATE_NAME
        self.lock_path = self.store_dir / LOCK_NAME

    def enqueue(self, raw_message: object) -> EnqueueReceipt:
        try:
            raw_bytes = canonical_json(raw_message)
        except ValueError:
            return self._quarantine(raw_message, "MESSAGE_INVALID")
        if len(raw_bytes) > MAX_PAYLOAD_BYTES + 64 * 1024:
            return self._quarantine_bytes(raw_bytes, "MESSAGE_TOO_LARGE")
        try:
            message = AgentInboxMessage.model_validate(raw_message)
        except (ValidationError, TypeError, ValueError):
            return self._quarantine(raw_message, "MESSAGE_INVALID")
        if message.project_id != self.project_id:
            return self._quarantine(raw_message, "PROJECT_MISMATCH")
        return self._locked_update(lambda state: self._enqueue_valid(state, message))

    def records(self) -> tuple[MailboxRecord, ...]:
        state = self._load_state()
        return tuple(
            sorted(
                state.records.values(),
                key=lambda item: (item.message.created_at, item.message.message_id),
            )
        )

    def incident_message_ids(self, incident_id: str) -> tuple[str, ...]:
        return tuple(
            record.message.message_id
            for record in self.records()
            if record.incident_id == incident_id
        )

    def get_record(self, message_id: str) -> MailboxRecord | None:
        return self._load_state().records.get(message_id)

    def successor_records(self) -> tuple[MailboxSuccessorRecord, ...]:
        state = self._load_state()
        return tuple(state.successors[key] for key in sorted(state.successors))

    def persist_successor(
        self,
        *,
        message_id: str,
        binding: MailboxSuccessorBindingV1,
        work_node: dict[str, object],
    ) -> tuple[MailboxSuccessorRecord, bool]:
        """Persist one routed successor identity before governor materialization."""
        binding.verify()

        def operation(state: MailboxState) -> tuple[MailboxSuccessorRecord, bool]:
            source = state.records.get(message_id)
            if source is None or source.status != MailboxStatus.PROCESSED:
                raise MailboxError(
                    "source message is not processed", code="SUCCESSOR_SOURCE_INVALID"
                )
            routing = source.routing or {}
            if routing.get("classification") not in {
                "CONTINUATION_ELIGIBLE",
                "TASK_DIRECTIVE_READY",
            }:
                raise MailboxError(
                    "source route is not continuation-eligible", code="ROUTE_NOT_READY"
                )
            if (
                binding.message_id != message_id
                or binding.incident_id != source.incident_id
                or binding.source_task_id != source.message.task_id
                or binding.source_attempt_id != source.message.attempt_id
                or binding.requester_id != source.message.requester_id
                or binding.correlation_id != source.message.correlation_id
                or binding.causation_id != source.message.causation_id
                or binding.source_result_digest != source.message.payload_digest
                or (binding.trusted_main, binding.trusted_tree)
                != (source.message.trusted_head, source.message.trusted_tree)
            ):
                raise MailboxError(
                    "successor source binding mismatch", code="SUCCESSOR_BINDING_MISMATCH"
                )
            candidate = MailboxSuccessorRecord(
                binding=binding,
                work_node=work_node,
                work_node_digest=record_sha256(work_node),
            )
            prior = state.successors.get(binding.package_id)
            if prior is not None:
                stable_prior = prior.binding.model_dump(mode="json")
                stable_candidate = binding.model_dump(mode="json")
                for field in (
                    "message_id",
                    "source_result_digest",
                    "source_attempt_id",
                    "causation_id",
                    "correlation_id",
                    "binding_digest",
                ):
                    stable_prior.pop(field, None)
                    stable_candidate.pop(field, None)
                if (
                    stable_prior != stable_candidate
                    or prior.work_node_digest != candidate.work_node_digest
                ):
                    raise MailboxError(
                        "successor identity collision", code="SUCCESSOR_ID_COLLISION"
                    )
                return prior, True
            state.successors[binding.package_id] = candidate
            self._save_state(state)
            return candidate, False

        return self._locked_update(operation)

    def process_next(self, router: _InboxRouter) -> MailboxRecord | None:
        """Persist one deterministic classification; never dispatches a task."""

        def operation(state: MailboxState) -> MailboxRecord | None:
            pending = sorted(
                (item for item in state.records.values() if item.status == MailboxStatus.PENDING),
                key=lambda item: (item.message.created_at, item.message.message_id),
            )
            if not pending:
                return None
            current = pending[0]
            try:
                outcome: InboxRoutingResult = router.classify(current.message)
            except Exception as exc:  # fail closed; do not lose the durable message
                raise MailboxError(
                    "mailbox route failed; message remains pending", code="ROUTE_FAILED"
                ) from exc
            if outcome.classification == "QUARANTINE":
                updated = current.model_copy(
                    update={
                        "status": MailboxStatus.QUARANTINED,
                        "quarantine_code": outcome.reason_code,
                    }
                )
            else:
                updated = current.model_copy(
                    update={
                        "status": MailboxStatus.PROCESSED,
                        "routing": outcome.model_dump(mode="json"),
                    }
                )
            state.records[current.message.message_id] = updated
            self._save_state(state)
            return updated

        return self._locked_update(operation)

    def _enqueue_valid(self, state: MailboxState, message: AgentInboxMessage) -> EnqueueReceipt:
        prior = state.records.get(message.message_id)
        if prior is not None:
            if prior.message.message_sha256() == message.message_sha256():
                return EnqueueReceipt(
                    status=prior.status,
                    message_id=message.message_id,
                    incident_id=prior.incident_id,
                    duplicate=True,
                    duplicate_of=prior.duplicate_of,
                )
            return self._quarantine_locked(
                state, message.model_dump(mode="json"), "MESSAGE_ID_COLLISION"
            )

        for item in state.records.values():
            if item.message.idempotency_key == message.idempotency_key:
                if item.message.idempotency_sha256() != message.idempotency_sha256():
                    return self._quarantine_locked(
                        state, message.model_dump(mode="json"), "IDEMPOTENCY_KEY_COLLISION"
                    )
                return self._record_duplicate(state, message, item.message.message_id)

        result_kinds = {"AGENT_RESULT", "BLOCKED", "VERIFICATION_RESULT"}
        duplicate_of = None
        if message.message_kind.value in result_kinds and message.dispatch_id is not None:
            for item in state.records.values():
                if (
                    item.message.message_kind == message.message_kind
                    and item.message.task_id == message.task_id
                    and item.message.run_id == message.run_id
                    and item.message.dispatch_id == message.dispatch_id
                    and item.message.payload_digest == message.payload_digest
                ):
                    duplicate_of = item.message.message_id
                    break
        incident_id = message.incident_id()
        if duplicate_of is not None:
            return self._record_duplicate(state, message, duplicate_of)
        state.records[message.message_id] = MailboxRecord(
            message=message, status=MailboxStatus.PENDING, incident_id=incident_id
        )
        self._save_state(state)
        return EnqueueReceipt(
            status=MailboxStatus.PENDING,
            message_id=message.message_id,
            incident_id=incident_id,
        )

    def _record_duplicate(
        self, state: MailboxState, message: AgentInboxMessage, duplicate_of: str
    ) -> EnqueueReceipt:
        incident_id = message.incident_id()
        state.records[message.message_id] = MailboxRecord(
            message=message,
            status=MailboxStatus.PROCESSED,
            incident_id=incident_id,
            duplicate_of=duplicate_of,
            routing={"classification": "DUPLICATE_MESSAGE", "duplicate_of": duplicate_of},
        )
        self._save_state(state)
        return EnqueueReceipt(
            status=MailboxStatus.PROCESSED,
            message_id=message.message_id,
            incident_id=incident_id,
            duplicate=True,
            duplicate_of=duplicate_of,
        )

    def _quarantine(self, raw: object, code: str) -> EnqueueReceipt:
        try:
            raw_bytes = canonical_json(raw)
        except ValueError:
            raw_bytes = repr(type(raw).__name__).encode("utf-8")
        return self._quarantine_bytes(raw_bytes, code)

    def _quarantine_bytes(self, raw_bytes: bytes, code: str) -> EnqueueReceipt:
        receipt_id = hashlib.sha256(raw_bytes).hexdigest()
        return self._locked_update(
            lambda state: self._quarantine_locked(state, {"digest": receipt_id}, code)
        )

    def _quarantine_locked(self, state: MailboxState, raw: object, code: str) -> EnqueueReceipt:
        digest = (
            str(raw.get("digest"))
            if isinstance(raw, dict) and set(raw) == {"digest"}
            else hashlib.sha256(canonical_json(raw)).hexdigest()
        )
        if digest not in state.quarantined:
            state.quarantined[digest] = QuarantineReceipt(receipt_id=digest, code=code)
            self._save_state(state)
        return EnqueueReceipt(
            status=MailboxStatus.QUARANTINED,
            quarantine_code=code,
        )

    def _locked_update(self, operation: Callable[[MailboxState], T]) -> T:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        try:
            with ProjectIdentityLock(self.lock_path, wait_seconds=2.0, stale_seconds=30.0):
                return operation(self._load_state())
        except IdentityLockError as exc:
            raise MailboxError("mailbox lock is unavailable", code="STORE_LOCKED") from exc
        except MailboxError:
            raise
        except OSError as exc:
            raise MailboxError("mailbox persistence failed", code="STORE_WRITE_FAILED") from exc

    def _load_state(self) -> MailboxState:
        if not self.state_path.is_file():
            return MailboxState(
                project_id=self.project_id,
                state_digest="0" * 64,
            ).seal()
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state root is not an object")
            if raw.get("schema_version") == 1:
                legacy_digest = raw.get("state_digest")
                legacy_unsigned = dict(raw)
                legacy_unsigned.pop("state_digest", None)
                if (
                    not isinstance(legacy_digest, str)
                    or record_sha256(legacy_unsigned) != legacy_digest
                ):
                    raise MailboxError("legacy mailbox digest mismatch", code="STORE_CORRUPT")
                legacy_records = raw.get("records", {})
                legacy_quarantine = raw.get("quarantined", {})
                if not isinstance(legacy_records, dict) or not isinstance(legacy_quarantine, dict):
                    raise MailboxError(
                        "legacy mailbox collections are invalid", code="STORE_CORRUPT"
                    )
                state = MailboxState(
                    project_id=raw["project_id"],
                    records={
                        str(key): MailboxRecord.model_validate(value)
                        for key, value in legacy_records.items()
                    },
                    quarantined={
                        str(key): QuarantineReceipt.model_validate(value)
                        for key, value in legacy_quarantine.items()
                    },
                    successors={},
                    state_digest="0" * 64,
                ).seal()
            else:
                state = MailboxState.model_validate(raw).verify()
        except MailboxError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise MailboxError(
                "mailbox store is corrupt or unreadable", code="STORE_CORRUPT"
            ) from exc
        if state.project_id != self.project_id:
            raise MailboxError("mailbox store project mismatch", code="PROJECT_MISMATCH")
        return state

    def _save_state(self, state: MailboxState) -> None:
        sealed = state.seal()
        sealed.verify()
        try:
            encoded = (
                json.dumps(sealed.model_dump(mode="json"), sort_keys=True, indent=2).encode("utf-8")
                + b"\n"
            )
            _atomic_replace(self.state_path, encoded)
        except OSError as exc:
            raise MailboxError("mailbox state persist failed", code="STORE_WRITE_FAILED") from exc
