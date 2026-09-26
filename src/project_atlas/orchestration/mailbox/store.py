"""Atomic, project-isolated mailbox snapshot store."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Protocol, TypeVar, cast

from pydantic import ValidationError

from atlas_contracts.versions import ID_PATTERN
from project_atlas.orchestration.mailbox.models import (
    ACTIVE_SUCCESSOR_LIFECYCLES,
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
    SuccessorLifecycle,
    canonical_json,
    record_sha256,
)

STATE_RELATIVE = Path(".atlas") / "orchestration" / "inbox"
STATE_NAME = "state.json"
LOCK_NAME = ".inbox.lock"
T = TypeVar("T")


class _MailboxFileLock(AbstractContextManager["_MailboxFileLock"]):
    """Kernel-owned exclusive lock; lock-file age never revokes a live owner."""

    def __init__(self, path: Path, *, wait_seconds: float = 2.0) -> None:
        self.path = path
        self.wait_seconds = wait_seconds
        self._handle: BinaryIO | None = None
        self._locked = False

    def __enter__(self) -> _MailboxFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if os.name == "nt":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt = cast(Any, importlib.import_module("msvcrt"))
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                self._locked = True
                return self
            except OSError as exc:
                if isinstance(exc, OSError) and exc.errno not in {
                    11,
                    13,
                    35,
                    36,
                    33,
                }:
                    handle.close()
                    raise
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError("mailbox lock wait expired") from exc
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        if handle is None or not self._locked:
            return None
        self._locked = False
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt = cast(Any, importlib.import_module("msvcrt"))
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None
        return None


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
        generation: int = 1,
        supersedes_package_id: str | None = None,
        retry_id: str | None = None,
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
            if routing.get("classification") == "DUPLICATE_MESSAGE":
                if not source.duplicate_of:
                    raise MailboxError(
                        "duplicate source reference is missing", code="SUCCESSOR_SOURCE_INVALID"
                    )
                duplicate_source = state.records.get(source.duplicate_of)
                if (
                    duplicate_source is None
                    or duplicate_source.status != MailboxStatus.PROCESSED
                    or duplicate_source.incident_id != source.incident_id
                ):
                    raise MailboxError(
                        "duplicate source reference is invalid", code="SUCCESSOR_SOURCE_INVALID"
                    )
                routing = duplicate_source.routing or {}
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
                generation=generation,
                supersedes_package_id=supersedes_package_id,
                retry_id=retry_id,
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
                    or prior.retry_id != candidate.retry_id
                    or prior.generation != candidate.generation
                    or prior.supersedes_package_id != candidate.supersedes_package_id
                ):
                    if prior.lifecycle in ACTIVE_SUCCESSOR_LIFECYCLES:
                        raise MailboxError(
                            "logical incident already has an active successor",
                            code="DUPLICATE_ACTIVE_SUCCESSOR",
                        )
                    raise MailboxError(
                        "successor identity collision", code="SUCCESSOR_ID_COLLISION"
                    )
                return prior, True
            same_incident = [
                item
                for item in state.successors.values()
                if item.binding.incident_id == binding.incident_id
            ]
            active = [
                item for item in same_incident if item.lifecycle in ACTIVE_SUCCESSOR_LIFECYCLES
            ]
            if active:
                raise MailboxError(
                    "logical incident already has an active successor",
                    code="DUPLICATE_ACTIVE_SUCCESSOR",
                )
            if same_incident:
                latest = max(same_incident, key=lambda item: item.generation)
                allowed_retries = {
                    "AUTONOMOUS_RECONCILE",
                    "RECERTIFY_REQUIRED",
                    "REMEDIATION_REQUIRED",
                }
                if (
                    latest.lifecycle != SuccessorLifecycle.TERMINAL
                    or supersedes_package_id != latest.binding.package_id
                    or generation != latest.generation + 1
                    or retry_id is None
                    or binding.transition not in allowed_retries
                ):
                    raise MailboxError(
                        "successor replacement requires explicit terminal supersession",
                        code="SUCCESSOR_SUPERSESSION_REQUIRED",
                    )
            elif supersedes_package_id is not None or generation != 1 or retry_id is not None:
                raise MailboxError(
                    "successor generation has no prior terminal record",
                    code="SUCCESSOR_SUPERSESSION_INVALID",
                )
            state.successors[binding.package_id] = candidate
            self._save_state(state)
            return candidate, False

        return self._locked_update(operation)

    def set_successor_lifecycle(
        self, package_id: str, lifecycle: SuccessorLifecycle
    ) -> MailboxSuccessorRecord:
        """Persist lifecycle observed from the governor; this method grants no authority."""

        def operation(state: MailboxState) -> MailboxSuccessorRecord:
            item = state.successors.get(package_id)
            if item is None:
                raise MailboxError("successor is not recorded", code="SUCCESSOR_NOT_FOUND")
            if lifecycle == SuccessorLifecycle.MATERIALIZING:
                raise MailboxError(
                    "materialization requires an atomic claim", code="MATERIALIZATION_CAS_REQUIRED"
                )
            if item.lifecycle == SuccessorLifecycle.MATERIALIZING:
                raise MailboxError(
                    "materialization is owned by its durable claimant",
                    code="MATERIALIZATION_OWNER_REQUIRED",
                )
            if item.lifecycle == SuccessorLifecycle.PREPARED and lifecycle not in {
                SuccessorLifecycle.PREPARED,
                SuccessorLifecycle.WAIT_RECONCILIATION,
                SuccessorLifecycle.TERMINAL,
            }:
                raise MailboxError(
                    "prepared successor must be claimed before lifecycle advance",
                    code="MATERIALIZATION_CAS_REQUIRED",
                )
            if item.lifecycle == SuccessorLifecycle.TERMINAL and lifecycle != item.lifecycle:
                raise MailboxError(
                    "terminal successor lifecycle cannot regress",
                    code="SUCCESSOR_LIFECYCLE_REGRESSION",
                )
            updated = item.model_copy(
                update={"lifecycle": lifecycle, "lifecycle_revision": item.lifecycle_revision + 1}
            )
            state.successors[package_id] = updated
            self._save_state(state)
            return updated

        return self._locked_update(operation)

    def claim_materialization(
        self,
        package_id: str,
        *,
        generation: int,
        expected_revision: int,
        owner_token: str,
    ) -> tuple[MailboxSuccessorRecord, bool]:
        """Atomically claim PREPARED -> MATERIALIZING for exactly one owner."""

        if not re.fullmatch(ID_PATTERN, owner_token):
            raise MailboxError("materialization owner token is invalid", code="OWNER_TOKEN_INVALID")

        def operation(state: MailboxState) -> tuple[MailboxSuccessorRecord, bool]:
            item = state.successors.get(package_id)
            if item is None:
                raise MailboxError("successor is not recorded", code="SUCCESSOR_NOT_FOUND")
            if (
                item.generation != generation
                or item.lifecycle_revision != expected_revision
                or item.lifecycle != SuccessorLifecycle.PREPARED
            ):
                return item, False
            claimed = item.model_copy(
                update={
                    "lifecycle": SuccessorLifecycle.MATERIALIZING,
                    "lifecycle_revision": item.lifecycle_revision + 1,
                    "materialization_owner_token": owner_token,
                }
            )
            state.successors[package_id] = claimed
            self._save_state(state)
            return claimed, True

        return self._locked_update(operation)

    def finalize_materialization(
        self,
        package_id: str,
        *,
        generation: int,
        expected_revision: int,
        owner_token: str,
        lifecycle: SuccessorLifecycle,
    ) -> MailboxSuccessorRecord:
        """Finalize only the still-current CAS owner; stale owners cannot commit."""
        if lifecycle in {
            SuccessorLifecycle.PREPARED,
            SuccessorLifecycle.MATERIALIZING,
            SuccessorLifecycle.WAIT_RECONCILIATION,
        }:
            raise MailboxError("invalid materialization final state", code="LIFECYCLE_INVALID")

        def operation(state: MailboxState) -> MailboxSuccessorRecord:
            item = state.successors.get(package_id)
            if item is None:
                raise MailboxError("successor is not recorded", code="SUCCESSOR_NOT_FOUND")
            if (
                item.generation != generation
                or item.lifecycle_revision != expected_revision
                or item.lifecycle != SuccessorLifecycle.MATERIALIZING
                or item.materialization_owner_token != owner_token
            ):
                raise MailboxError(
                    "materialization owner is stale", code="MATERIALIZATION_STALE_OWNER"
                )
            finalized = item.model_copy(
                update={
                    "lifecycle": lifecycle,
                    "lifecycle_revision": item.lifecycle_revision + 1,
                    "materialization_owner_token": None,
                }
            )
            state.successors[package_id] = finalized
            self._save_state(state)
            return finalized

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
                duplicate_of = self._validated_duplicate_of(state, current.message, outcome)
                if duplicate_of is not None:
                    updated = current.model_copy(
                        update={
                            "status": MailboxStatus.PROCESSED,
                            "duplicate_of": duplicate_of,
                            "routing": {
                                "classification": "DUPLICATE_MESSAGE",
                                "duplicate_of": duplicate_of,
                                "validated_routing": outcome.model_dump(mode="json"),
                            },
                        }
                    )
                    state.records[current.message.message_id] = updated
                    self._save_state(state)
                    return updated
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
            if item.status != MailboxStatus.PROCESSED or (item.routing or {}).get(
                "classification"
            ) in {"DUPLICATE_MESSAGE", "QUARANTINE"}:
                continue
            if (
                item.message.idempotency_key == message.idempotency_key
                and item.message.idempotency_sha256() != message.idempotency_sha256()
            ):
                return self._quarantine_locked(
                    state, message.model_dump(mode="json"), "IDEMPOTENCY_KEY_COLLISION"
                )
            # Cross-message replay is decided only after current routing validates.
        incident_id = message.incident_id()
        state.records[message.message_id] = MailboxRecord(
            message=message, status=MailboxStatus.PENDING, incident_id=incident_id
        )
        self._save_state(state)
        return EnqueueReceipt(
            status=MailboxStatus.PENDING,
            message_id=message.message_id,
            incident_id=incident_id,
        )

    @staticmethod
    def _routing_context_digest(message: AgentInboxMessage, routing: InboxRoutingResult) -> str:
        envelope = message.payload.get("result_envelope")
        envelope = envelope if isinstance(envelope, dict) else {}
        envelope_task = envelope.get("task")
        envelope_task = envelope_task if isinstance(envelope_task, dict) else {}
        producer = envelope.get("producer")
        producer = producer if isinstance(producer, dict) else {}
        receipt = envelope.get("receipt")
        receipt = receipt if isinstance(receipt, dict) else {}
        observations = envelope.get("observations")
        observations = observations if isinstance(observations, dict) else {}
        extras = observations.get("extras")
        extras = extras if isinstance(extras, dict) else {}
        blockers = envelope.get("blockers")
        blocker_codes = (
            [item.get("code") for item in blockers if isinstance(item, dict)]
            if isinstance(blockers, list)
            else []
        )
        context = {
            "project_id": message.project_id,
            "task_id": message.task_id,
            "task_class": message.task_class,
            "run_id": message.run_id,
            "dispatch_id": message.dispatch_id,
            "attempt_id": message.attempt_id,
            "requester_id": message.requester_id,
            "authority_reference": message.authority_reference,
            "message_kind": message.message_kind.value,
            "reason_code": message.reason_code,
            "trusted_head": message.trusted_head,
            "trusted_tree": message.trusted_tree,
            "retryable": message.retryable,
            "owner_required": message.owner_required,
            "result": {
                "producer_role": producer.get("role"),
                "task_id": envelope_task.get("id"),
                "task_attempt": envelope_task.get("attempt"),
                "outcome": envelope.get("outcome"),
                "state": envelope.get("state"),
                "requested_transition": envelope.get("requested_transition"),
                "authority_grant": envelope.get("authority_grant"),
                "merge_authorized": envelope.get("merge_authorized"),
                "receipt_status": receipt.get("status"),
                "receipt_event_id": receipt.get("event_id"),
                "target_moved": observations.get("target_moved"),
                "unauthorized_mutations": observations.get("unauthorized_mutations"),
                "retryable": extras.get("retryable"),
                "process_started": extras.get("process_started"),
                "blocker_codes": blocker_codes,
            },
            "routing": routing.model_dump(mode="json"),
        }
        return record_sha256(context)

    def _validated_duplicate_of(
        self,
        state: MailboxState,
        message: AgentInboxMessage,
        routing: InboxRoutingResult,
    ) -> str | None:
        result_kinds = {"AGENT_RESULT", "BLOCKED", "VERIFICATION_RESULT"}
        if message.message_kind.value not in result_kinds or message.dispatch_id is None:
            return None
        candidate = self._routing_context_digest(message, routing)
        for item in state.records.values():
            if (
                item.status != MailboxStatus.PROCESSED
                or item.duplicate_of is not None
                or item.routing is None
                or item.routing.get("classification") in {"QUARANTINE", "DUPLICATE_MESSAGE"}
                or item.message.message_kind.value not in result_kinds
                or item.message.task_id != message.task_id
                or item.message.dispatch_id != message.dispatch_id
            ):
                continue
            try:
                prior_routing = InboxRoutingResult.model_validate(item.routing)
            except (ValidationError, TypeError, ValueError):
                continue
            if self._routing_context_digest(item.message, prior_routing) == candidate:
                return item.message.message_id
        return None

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
            with _MailboxFileLock(self.lock_path, wait_seconds=2.0):
                return operation(self._load_state())
        except TimeoutError as exc:
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
            if raw.get("schema_version") in {1, 2, 3}:
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
                legacy_successors = raw.get("successors", {})
                if (
                    not isinstance(legacy_records, dict)
                    or not isinstance(legacy_quarantine, dict)
                    or not isinstance(legacy_successors, dict)
                ):
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
                    successors={
                        str(key): self._migrate_successor(
                            value, schema_version=int(raw.get("schema_version", 1))
                        )
                        for key, value in legacy_successors.items()
                    },
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

    @staticmethod
    def _migrate_successor(value: object, *, schema_version: int) -> MailboxSuccessorRecord:
        if not isinstance(value, dict):
            raise MailboxError("legacy successor record is invalid", code="STORE_CORRUPT")
        migrated = dict(value)
        lifecycle = migrated.get("lifecycle")
        if schema_version < 3 or lifecycle not in {
            SuccessorLifecycle.PREPARED.value,
            SuccessorLifecycle.TERMINAL.value,
        }:
            migrated["lifecycle"] = SuccessorLifecycle.WAIT_RECONCILIATION.value
            migrated["materialization_owner_token"] = None
        return MailboxSuccessorRecord.model_validate(migrated)

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
