"""AS-ORCH-CONTINUATION-BROKER-001 — lifecycle continuation, not authority.

Reuses AS-ORCH-001D (single-hop dispatch) and AS-ORCH-001E (loop tick).
Does not create a second governor, DAG, dispatcher, or lease authority.

PRIMARY_CONTINUATION_BACKEND = CURSOR_SDK_DURABLE_AGENT_RUNTIME
STOP_HOOK_BACKEND = CURSOR_STOP_HOOK_FOLLOWUP (fallback / defense-in-depth)

Durable consume-once cycle store remains the hook invocation contract.
The 001C Cursor bridge slot is never read or written here
(PR400 leftover must remain unconsumed).

RESULT != AUTHORITY / FOLLOWUP != DISPATCH / YIELD != OWNER_REQUIRED
WORKER_TERMINAL != DAG_TERMINAL / REQUESTED_TRANSITION != AUTHORIZATION
NEXT_MACHINE_ACTION text alone is not continuation.
RUN_TERMINAL != DAG_END
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from project_atlas.orchestration.autonomy.evidence import hash_payload
from project_atlas.orchestration.autonomy.models import CANONICAL_REPOSITORY_IDENTITY
from project_atlas.orchestration.autonomy.return_gate import (
    AutonomyReturnState,
    load_authoritative_return_state,
    render_stop_hook_dag_continuation,
    stop_hook_terminal_return_allowed,
)
from project_atlas.orchestration.autonomy.trust import (
    repository_identities_match,
    require_full_pin,
)
from project_atlas.source_identity import IdentityLockError, ProjectIdentityLock

PACKAGE_ID: Final[Literal["AS-ORCH-CONTINUATION-BROKER-001"]] = (
    "AS-ORCH-CONTINUATION-BROKER-001"
)
PRIMARY_CONTINUATION_BACKEND: Final[Literal["CURSOR_SDK_DURABLE_AGENT_RUNTIME"]] = (
    "CURSOR_SDK_DURABLE_AGENT_RUNTIME"
)
BACKEND: Final[Literal["CURSOR_STOP_HOOK_FOLLOWUP"]] = "CURSOR_STOP_HOOK_FOLLOWUP"
STOP_HOOK_BACKEND: Final[Literal["CURSOR_STOP_HOOK_FOLLOWUP"]] = BACKEND
HOOK_ADAPTER_VERSION: Final[Literal["D082-1"]] = "D082-1"
STATE_DIR_RELATIVE: Final[Path] = Path(".atlas") / "orchestration" / "continuation-broker"
CURRENT_NAME: Final[str] = "current.json"
LOCK_NAME: Final[str] = ".broker.lock"
TRACE_NAME: Final[str] = "hook-trace.jsonl"
CHECKPOINT_NAME: Final[str] = "checkpoint.json"
BROKER_MARKER: Final[str] = "[ATLAS_CONTINUATION_BROKER]"
PLACEHOLDER_DIGEST: Final[str] = "0" * 64
MAX_IDENTICAL_NO_PROGRESS_CYCLES: Final[int] = 3
_CYCLE_RE = re.compile(r"^[A-Z][A-Z0-9._-]{0,63}$")
_FINGERPRINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRACE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "timestamp",
        "event_type",
        "session_id",
        "cycle_id",
        "loop_count",
        "broker_phase",
        "dag_generation",
        "hook_pid",
        "python_executable",
        "resolved_repo_root",
        "resolved_project_atlas_module_path",
        "module_root_match",
        "followup_returned",
        "successor_consumed",
        "error_code",
        "hook_adapter_version",
        "hook_config_digest",
    }
)


class BrokerError(ValueError):
    """Fail-closed broker error. Not an authority grant."""

    code = "BROKER_FAILED_CLOSED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class SuccessorKind(StrEnum):
    CHECKPOINT_CONTINUE = "CHECKPOINT_CONTINUE"
    RESOURCE_YIELD = "RESOURCE_YIELD"
    WORKER_TERMINAL_DAG_CONTINUES = "WORKER_TERMINAL_DAG_CONTINUES"
    CI_PENDING_WITH_OBSERVER = "CI_PENDING_WITH_OBSERVER"
    IV_PENDING_WITH_OBSERVER = "IV_PENDING_WITH_OBSERVER"
    ADV_PENDING_WITH_OBSERVER = "ADV_PENDING_WITH_OBSERVER"
    REMEDIATION_REQUIRED = "REMEDIATION_REQUIRED"
    RECOVERABLE_EXTERNAL_FAILURE = "RECOVERABLE_EXTERNAL_FAILURE"


class TerminalResultClass(StrEnum):
    WAITING_OWNER = "WAITING_OWNER"
    DAG_COMPLETE = "DAG_COMPLETE"
    SAFETY_STOP = "SAFETY_STOP"
    UNRECOVERABLE_CORRUPTION = "UNRECOVERABLE_CORRUPTION"


class BrokerPhase(StrEnum):
    IDLE = "IDLE"
    QUEUED = "QUEUED"
    FOLLOWUP_EMITTED = "FOLLOWUP_EMITTED"
    CONSUMED = "CONSUMED"
    PARKED_OWNER = "PARKED_OWNER"
    AWAITING_RESULT = "AWAITING_RESULT"


ENQUEUEABLE_RESULT_CLASSES: Final[frozenset[str]] = frozenset(kind.value for kind in SuccessorKind)
TERMINAL_RESULT_CLASSES: Final[frozenset[str]] = frozenset(
    item.value for item in TerminalResultClass
)
FINAL_RESPONSE_SUCCESSOR_STATES: Final[frozenset[str]] = frozenset(
    {
        BrokerPhase.QUEUED.value,
        BrokerPhase.FOLLOWUP_EMITTED.value,
        BrokerPhase.AWAITING_RESULT.value,
    }
)


class BrokerState(BaseModel):
    """Durable continuation identity. Never authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    package_id: Literal["AS-ORCH-CONTINUATION-BROKER-001"] = PACKAGE_ID
    backend: Literal["CURSOR_STOP_HOOK_FOLLOWUP"] = BACKEND
    stop_hook_backend: Literal["CURSOR_STOP_HOOK_FOLLOWUP"] = STOP_HOOK_BACKEND
    repository_identity: str = Field(min_length=1, max_length=256)
    trusted_main: str = Field(min_length=40, max_length=40)
    trusted_tree: str = Field(min_length=40, max_length=40)
    dag_generation: int = Field(ge=0, le=1_000_000)
    cycle_id: str | None = None
    kind: SuccessorKind | None = None
    phase: BrokerPhase
    followup_emitted: bool = False
    consumed: bool = False
    owner_gate_fingerprint: str | None = None
    owner_request_id: str | None = None
    owner_request_already_emitted: bool = False
    primary_governor: Literal[True] = True
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False
    authority_granted: Literal[False] = False
    checkpoint_digest: str | None = None
    next_node_id: str | None = None
    next_action_class: str | None = None
    next_action_digest: str | None = None
    progress_sequence: int = Field(default=0, ge=0, le=1_000_000)
    no_progress_count: int = Field(default=0, ge=0, le=1_000_000)
    lease_id: str | None = None
    external_wait_identity: str | None = None
    active_package: str | None = None
    candidate_head: str | None = None
    ci_state: str | None = None
    iv_state: str | None = None
    adv_state: str | None = None
    followup_digest: str | None = None
    hook_config_digest: str | None = None
    record_digest: str = Field(min_length=64, max_length=64)

    @field_validator("trusted_main", "trusted_tree")
    @classmethod
    def _pin(cls, value: str) -> str:
        return require_full_pin(value, "broker pin")

    @field_validator("cycle_id")
    @classmethod
    def _cycle(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _CYCLE_RE.fullmatch(value):
            raise ValueError("cycle_id must be a safe identifier")
        return value

    @field_validator(
        "owner_gate_fingerprint",
        "owner_request_id",
        "next_node_id",
        "next_action_class",
        "lease_id",
        "external_wait_identity",
        "active_package",
        "ci_state",
        "iv_state",
        "adv_state",
    )
    @classmethod
    def _token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _FINGERPRINT_RE.fullmatch(value):
            raise ValueError("owner token must be a safe identifier")
        return value

    @field_validator("candidate_head")
    @classmethod
    def _optional_pin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_full_pin(value, "candidate head")

    @model_validator(mode="after")
    def _no_authority(self) -> BrokerState:
        if self.merge_authorized or self.execution_authorized or self.authority_granted:
            raise ValueError("broker state cannot carry authority")
        if not repository_identities_match(
            self.repository_identity, CANONICAL_REPOSITORY_IDENTITY
        ):
            raise ValueError("cross-project broker reuse is forbidden")
        if self.consumed and self.phase not in {BrokerPhase.CONSUMED, BrokerPhase.IDLE}:
            raise ValueError("consumed successor must be CONSUMED or IDLE")
        return self

    def unsigned_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload.pop("record_digest")
        return payload


class EnqueueResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    deduped: bool = False
    phase: BrokerPhase
    cycle_id: str | None = None
    kind: SuccessorKind | None = None
    owner_prompt_emitted: bool = False
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False


class FinalizeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_class: str
    successor_enqueued: bool
    phase: BrokerPhase
    cycle_id: str | None = None
    kind: SuccessorKind | None = None
    next_machine_action: str | None = None
    next_machine_action_scheduled: bool = False
    final_response_allowed: bool
    error_code: str | None = None
    no_progress_count: int = 0
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False


class GovernorCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    result_class: str
    trusted_main: str
    trusted_tree: str
    cycle_id: str
    next_action_class: str
    next_node_id: str | None = None
    dag_generation: int = 0
    record_digest: str = Field(min_length=64, max_length=64)

    def unsigned_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload.pop("record_digest")
        return payload


def seal_broker_state(state: BrokerState) -> BrokerState:
    return state.model_copy(update={"record_digest": hash_payload(state.unsigned_payload())})


def verify_broker_state(state: BrokerState) -> BrokerState:
    expected = hash_payload(state.unsigned_payload())
    if state.record_digest != expected:
        raise BrokerError("broker state digest mismatch", code="STATE_CORRUPT")
    return state


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_broker_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise BrokerError("repository root is not a directory", code="PATH_UNSAFE")
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise BrokerError("refusing filesystem root or home as broker root", code="PATH_UNSAFE")
    return resolved


def _store_path(root: Path, relative: str) -> Path:
    if ".." in Path(relative).parts or Path(relative).is_absolute():
        raise BrokerError("broker store path is unsafe", code="PATH_UNSAFE")
    target = (root / relative).resolve()
    if not _inside(root, target):
        raise BrokerError("broker store path escapes root", code="PATH_UNSAFE")
    return target


def broker_store_dir(root: Path) -> Path:
    return resolve_broker_root(root) / STATE_DIR_RELATIVE


def _write_json_atomic(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(encoded + "\n", encoding="utf-8")
    os.replace(tmp, target)


def persist_broker_state(store: Path, state: BrokerState) -> BrokerState:
    sealed = verify_broker_state(seal_broker_state(state))
    root = store.resolve()
    lock_path = _store_path(root, LOCK_NAME)
    try:
        with ProjectIdentityLock(lock_path, wait_seconds=2.0, stale_seconds=30.0):
            _write_json_atomic(
                _store_path(root, CURRENT_NAME),
                sealed.model_dump(mode="json"),
            )
    except IdentityLockError as exc:
        raise BrokerError("broker lock is held", code="CONCURRENT_BROKER") from exc
    return sealed


def load_broker_state(store: Path) -> BrokerState | None:
    path = _store_path(store.resolve(), CURRENT_NAME)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise BrokerError("broker state is schema-invalid", code="STATE_CORRUPT")
        return verify_broker_state(BrokerState.model_validate(payload))
    except BrokerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BrokerError("broker state is unreadable", code="STATE_CORRUPT") from exc


def initial_broker_state(*, trusted_main: str, trusted_tree: str) -> BrokerState:
    return seal_broker_state(
        BrokerState(
            repository_identity=CANONICAL_REPOSITORY_IDENTITY,
            trusted_main=trusted_main,
            trusted_tree=trusted_tree,
            dag_generation=0,
            phase=BrokerPhase.IDLE,
            record_digest=PLACEHOLDER_DIGEST,
        )
    )


def session_exit_does_not_end_dag(*, worker_terminal: bool, dag_terminal: bool) -> bool:
    """Worker/session terminal is not DAG terminal."""
    del worker_terminal
    return not dag_terminal


def _require_identity(repository_identity: str) -> str:
    identity = repository_identity.strip()
    if not repository_identities_match(identity, CANONICAL_REPOSITORY_IDENTITY):
        raise BrokerError("foreign project broker reuse is forbidden", code="FOREIGN_PROJECT")
    return CANONICAL_REPOSITORY_IDENTITY


def _require_token(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not _FINGERPRINT_RE.fullmatch(value):
        raise BrokerError(f"{name} is unsafe", code="UNSAFE_TOKEN")
    return value


def hook_config_digest(root: Path) -> str | None:
    path = resolve_broker_root(root) / ".cursor" / "hooks.json"
    if not path.is_file():
        return None
    return hash_payload({"hooks_json_sha256": _file_sha256(path)})


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_hook_trace(root: Path, event: dict[str, object]) -> None:
    """Append-only safe hook lifecycle trace. Never records prompts or secrets."""
    store = broker_store_dir(root)
    store.mkdir(parents=True, exist_ok=True)
    safe: dict[str, object] = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hook_adapter_version": HOOK_ADAPTER_VERSION,
    }
    for key, value in event.items():
        if key not in _TRACE_KEYS:
            continue
        if key in {"prompt", "prompts", "secret", "token", "environment", "env"}:
            continue
        if isinstance(value, (str, int, bool)) or value is None:
            safe[key] = value
    path = _store_path(store.resolve(), TRACE_NAME)
    lock_path = _store_path(store.resolve(), LOCK_NAME)
    line = json.dumps(safe, sort_keys=True, ensure_ascii=True) + "\n"
    try:
        with (
            ProjectIdentityLock(lock_path, wait_seconds=2.0, stale_seconds=30.0),
            path.open("a", encoding="utf-8") as handle,
        ):
            handle.write(line)
    except (IdentityLockError, OSError):
        return


def progress_fingerprint(
    *,
    trusted_main: str,
    trusted_tree: str,
    candidate_head: str | None,
    ci_state: str | None,
    iv_state: str | None,
    adv_state: str | None,
    dag_generation: int,
    lease_id: str | None,
    next_node_id: str | None,
    next_action_class: str | None,
) -> str:
    return hash_payload(
        {
            "trusted_main": trusted_main,
            "trusted_tree": trusted_tree,
            "candidate_head": candidate_head,
            "ci_state": ci_state,
            "iv_state": iv_state,
            "adv_state": adv_state,
            "dag_generation": dag_generation,
            "lease_id": lease_id,
            "next_node_id": next_node_id,
            "next_action_class": next_action_class,
        }
    )


def _coerce_return_state(return_state: object) -> AutonomyReturnState:
    if isinstance(return_state, AutonomyReturnState):
        return return_state
    return AutonomyReturnState.model_validate(return_state)


def _load_d147_return_state(root: Path) -> AutonomyReturnState | None:
    return load_authoritative_return_state(root)


def _resolve_return_state(
    root: Path,
    return_state: object | None,
    *,
    terminal_path: bool,
) -> object | None:
    if return_state is not None:
        return return_state
    if not terminal_path:
        return None
    return _load_d147_return_state(root)


def _stop_hook_block_terminal_return(
    root: Path,
    *,
    session_id: str | None,
    loop_count: int,
    event_type: str,
    broker_phase: str | None = None,
    cycle_id: str | None = None,
    dag_generation: int | None = None,
) -> dict[str, str]:
    """Emit trusted DAG continuation when final return is forbidden."""
    append_hook_trace(
        root,
        {
            "event_type": event_type,
            "session_id": session_id,
            "cycle_id": cycle_id,
            "loop_count": loop_count,
            "broker_phase": broker_phase,
            "dag_generation": dag_generation,
            "followup_returned": True,
            "final_return_blocked": True,
        },
    )
    return {"followup_message": render_stop_hook_dag_continuation()}


def final_response_allowed(
    *,
    owner_action_required_now: bool,
    safe_dag_work_remains: bool,
    successor_state: str,
    return_state: object | None = None,
) -> bool:
    """Whether the outer session may emit a final response.

    Terminal / owner-wait paths require a mandatory D-146 return gate (fail closed
    when ``return_state`` is omitted). Continuation follow-up may proceed when a
    durable successor exists even while autonomous work remains.
    """
    from project_atlas.orchestration.autonomy.return_gate import (
        may_emit_final_return,
    )

    if owner_action_required_now or not safe_dag_work_remains:
        if return_state is None:
            return False
        return may_emit_final_return(_coerce_return_state(return_state))

    if return_state is not None and not may_emit_final_return(
        _coerce_return_state(return_state)
    ):
        return successor_state in FINAL_RESPONSE_SUCCESSOR_STATES

    return successor_state in FINAL_RESPONSE_SUCCESSOR_STATES


def next_machine_action_receipt_valid(*, executing: bool, scheduled: bool) -> bool:
    return bool(executing or scheduled)


def enqueue_successor(
    root: Path,
    *,
    cycle_id: str,
    kind: SuccessorKind,
    trusted_main: str,
    trusted_tree: str,
    repository_identity: str = CANONICAL_REPOSITORY_IDENTITY,
    dag_generation: int = 0,
    dag_terminal: bool = False,
    worker_terminal: bool = False,
    owner_gate_fingerprint: str | None = None,
    owner_request_id: str | None = None,
    primary_governor: bool = True,
    checkpoint_digest: str | None = None,
    next_node_id: str | None = None,
    next_action_class: str | None = None,
    lease_id: str | None = None,
    external_wait_identity: str | None = None,
    active_package: str | None = None,
    candidate_head: str | None = None,
    ci_state: str | None = None,
    iv_state: str | None = None,
    adv_state: str | None = None,
    progress_sequence: int = 0,
) -> EnqueueResult:
    """Queue exactly one successor for a governor cycle. Consume-once."""
    if not primary_governor:
        raise BrokerError("second governor is forbidden", code="SECOND_GOVERNOR")
    if not _CYCLE_RE.fullmatch(cycle_id):
        raise BrokerError("cycle_id is unsafe", code="UNSAFE_CYCLE_ID")
    _require_identity(repository_identity)
    require_full_pin(trusted_main, "broker main")
    require_full_pin(trusted_tree, "broker tree")
    next_node_id = _require_token(next_node_id, "next_node_id")
    next_action_class = _require_token(next_action_class, "next_action_class")
    lease_id = _require_token(lease_id, "lease_id")
    external_wait_identity = _require_token(external_wait_identity, "external_wait_identity")
    active_package = _require_token(active_package, "active_package")
    ci_state = _require_token(ci_state, "ci_state")
    iv_state = _require_token(iv_state, "iv_state")
    adv_state = _require_token(adv_state, "adv_state")
    if candidate_head is not None:
        require_full_pin(candidate_head, "candidate head")
    if kind == SuccessorKind.WORKER_TERMINAL_DAG_CONTINUES and not session_exit_does_not_end_dag(
        worker_terminal=worker_terminal, dag_terminal=dag_terminal
    ):
        raise BrokerError("DAG is terminal; no successor", code="DAG_TERMINAL")
    if dag_terminal:
        raise BrokerError("DAG is terminal; no successor", code="DAG_TERMINAL")
    store = broker_store_dir(root)
    store.mkdir(parents=True, exist_ok=True)
    existing = load_broker_state(store)
    if existing is None:
        existing = persist_broker_state(
            store, initial_broker_state(trusted_main=trusted_main, trusted_tree=trusted_tree)
        )
    if (
        existing.trusted_main != trusted_main or existing.trusted_tree != trusted_tree
    ) and existing.phase in {BrokerPhase.QUEUED, BrokerPhase.FOLLOWUP_EMITTED}:
        raise BrokerError("stale checkpoint pin does not match", code="STALE_CHECKPOINT")
    if (
        owner_gate_fingerprint
        and existing.owner_gate_fingerprint == owner_gate_fingerprint
        and existing.owner_request_already_emitted
    ):
        return EnqueueResult(
            accepted=False,
            deduped=True,
            phase=existing.phase,
            cycle_id=existing.cycle_id,
            kind=existing.kind,
            owner_prompt_emitted=False,
        )
    incoming_progress = progress_fingerprint(
        trusted_main=trusted_main,
        trusted_tree=trusted_tree,
        candidate_head=candidate_head,
        ci_state=ci_state,
        iv_state=iv_state,
        adv_state=adv_state,
        dag_generation=dag_generation,
        lease_id=lease_id,
        next_node_id=next_node_id,
        next_action_class=next_action_class,
    )
    existing_progress = progress_fingerprint(
        trusted_main=existing.trusted_main,
        trusted_tree=existing.trusted_tree,
        candidate_head=existing.candidate_head,
        ci_state=existing.ci_state,
        iv_state=existing.iv_state,
        adv_state=existing.adv_state,
        dag_generation=existing.dag_generation,
        lease_id=existing.lease_id,
        next_node_id=existing.next_node_id,
        next_action_class=existing.next_action_class,
    )
    no_progress_count = (
        (existing.no_progress_count or 1) + 1
        if incoming_progress == existing_progress and existing.cycle_id is not None
        else 1
    )
    if existing.phase in {BrokerPhase.QUEUED, BrokerPhase.FOLLOWUP_EMITTED}:
        if existing.cycle_id == cycle_id and existing.kind == kind:
            return EnqueueResult(
                accepted=True,
                deduped=True,
                phase=existing.phase,
                cycle_id=existing.cycle_id,
                kind=existing.kind,
            )
        raise BrokerError("duplicate successor is forbidden", code="DUPLICATE_SUCCESSOR")
    if existing.consumed and existing.cycle_id == cycle_id:
        raise BrokerError("cycle already consumed", code="CYCLE_REPLAY")
    if no_progress_count > MAX_IDENTICAL_NO_PROGRESS_CYCLES:
        raise BrokerError("identical no-progress loop detected", code="NO_PROGRESS_LOOP")
    parked = owner_gate_fingerprint is not None
    action_digest = hash_payload(
        {
            "cycle_id": cycle_id,
            "kind": kind.value,
            "next_action_class": next_action_class,
            "next_node_id": next_node_id,
            "trusted_main": trusted_main,
            "trusted_tree": trusted_tree,
        }
    )
    updated = persist_broker_state(
        store,
        existing.model_copy(
            update={
                "trusted_main": trusted_main,
                "trusted_tree": trusted_tree,
                "dag_generation": dag_generation,
                "cycle_id": cycle_id,
                "kind": kind,
                "phase": BrokerPhase.PARKED_OWNER if parked else BrokerPhase.QUEUED,
                "followup_emitted": False,
                "consumed": False,
                "owner_gate_fingerprint": owner_gate_fingerprint,
                "owner_request_id": owner_request_id,
                "owner_request_already_emitted": bool(owner_gate_fingerprint),
                "checkpoint_digest": checkpoint_digest or action_digest,
                "next_node_id": next_node_id,
                "next_action_class": next_action_class,
                "next_action_digest": action_digest,
                "progress_sequence": progress_sequence,
                "no_progress_count": no_progress_count,
                "lease_id": lease_id,
                "external_wait_identity": external_wait_identity,
                "active_package": active_package,
                "candidate_head": candidate_head,
                "ci_state": ci_state,
                "iv_state": iv_state,
                "adv_state": adv_state,
                "followup_digest": None,
                "hook_config_digest": hook_config_digest(root),
            }
        ),
    )
    if updated.phase == BrokerPhase.QUEUED:
        rendered = render_broker_followup(updated)
        if rendered is not None:
            updated = persist_broker_state(
                store,
                updated.model_copy(
                    update={"followup_digest": hash_payload({"followup": rendered})}
                ),
            )
    return EnqueueResult(
        accepted=True,
        phase=updated.phase,
        cycle_id=updated.cycle_id,
        kind=updated.kind,
        owner_prompt_emitted=bool(owner_gate_fingerprint)
        and not existing.owner_request_already_emitted,
    )


def consume_successor(root: Path, cycle_id: str) -> BrokerState:
    """Consume a queued/emitted successor exactly once."""
    if not _CYCLE_RE.fullmatch(cycle_id):
        raise BrokerError("cycle_id is unsafe", code="UNSAFE_CYCLE_ID")
    store = broker_store_dir(root)
    existing = load_broker_state(store)
    if existing is None or existing.cycle_id != cycle_id:
        raise BrokerError("successor cycle is not queued", code="CYCLE_MISSING")
    if existing.consumed or existing.phase == BrokerPhase.CONSUMED:
        raise BrokerError("cycle already consumed", code="CYCLE_REPLAY")
    if existing.phase == BrokerPhase.PARKED_OWNER:
        raise BrokerError("owner-parked successor is not consumable", code="OWNER_PARKED")
    if existing.phase not in {BrokerPhase.QUEUED, BrokerPhase.FOLLOWUP_EMITTED}:
        raise BrokerError("successor is not consumable", code="CYCLE_NOT_READY")
    return persist_broker_state(
        store,
        existing.model_copy(
            update={
                "phase": BrokerPhase.CONSUMED,
                "consumed": True,
            }
        ),
    )


def recover_broker(root: Path) -> BrokerState | None:
    """Crash/restart: return durable state. Never mint a duplicate successor."""
    store = broker_store_dir(root)
    if not (store / CURRENT_NAME).is_file():
        return None
    return load_broker_state(store)


def render_broker_followup(state: BrokerState) -> str | None:
    """Fixed trusted template. Untrusted stop/envelope text never interpolates."""
    if state.phase not in {BrokerPhase.QUEUED, BrokerPhase.FOLLOWUP_EMITTED}:
        return None
    if state.consumed or state.cycle_id is None or state.kind is None:
        return None
    if not _CYCLE_RE.fullmatch(state.cycle_id):
        return None
    next_action = state.next_action_class or state.kind.value
    next_node = state.next_node_id or "PRIMARY_GOVERNOR"
    checkpoint = state.checkpoint_digest or PLACEHOLDER_DIGEST
    return (
        f"{BROKER_MARKER}\n"
        "\n"
        f"Resume primary governor cycle {state.cycle_id}.\n"
        "Consume queued successor.\n"
        "Refresh live DAG.\n"
        "Execute bound next node/action.\n"
        "Do not ask the owner for routine continuation.\n"
        "Worker terminal != DAG terminal.\n"
        "MERGE_AUTHORIZATION=NOT_GRANTED.\n"
        "\n"
        "DO NOT END WITH A ROUTINE QUESTION.\n"
        "DO NOT ONLY REPORT A NEXT ACTION.\n"
        "EXECUTE THE NEXT SAFE ACTION WHEN AUTHORIZED.\n"
        "IF YOUR TURN MUST END AND SAFE DAG WORK REMAINS,\n"
        "VERIFY THAT THE PRIMARY GOVERNOR HAS A DURABLE SUCCESSOR QUEUED.\n"
        "ONLY GENUINE OWNER AUTHORITY MAY STOP THE GOVERNOR DAG.\n"
        "\n"
        f"CYCLE_ID: {state.cycle_id}\n"
        f"KIND: {state.kind.value}\n"
        f"NEXT_ACTION_CLASS: {next_action}\n"
        f"NEXT_NODE_ID: {next_node}\n"
        f"MAIN_SHA: {state.trusted_main}\n"
        f"MAIN_TREE: {state.trusted_tree}\n"
        f"CHECKPOINT_DIGEST: {checkpoint}\n"
        f"DAG_GENERATION: {state.dag_generation}\n"
        f"BACKEND: {BACKEND}\n"
    )


def emit_stop_followup(
    root: Path,
    *,
    loop_count: int = 0,
    session_id: str | None = None,
) -> dict[str, str]:
    """Stop-hook backend. Never reads or writes the 001C bridge slot."""
    try:
        store = broker_store_dir(root)
        existing = load_broker_state(store)
    except BrokerError as exc:
        append_hook_trace(
            root,
            {
                "event_type": "STOP_HOOK_STATE_READ_FAILURE",
                "session_id": session_id,
                "loop_count": loop_count,
                "error_code": exc.code,
                "followup_returned": False,
            },
        )
        if stop_hook_terminal_return_allowed(root):
            return {}
        return _stop_hook_block_terminal_return(
            root,
            session_id=session_id,
            loop_count=loop_count,
            event_type="STOP_HOOK_RETURN_GATE_BLOCKED",
        )
    if existing is None:
        # loop_count=0: broker idle — defer to bridge / non-terminal hook path.
        if loop_count == 0:
            return {}
        if stop_hook_terminal_return_allowed(root):
            append_hook_trace(
                root,
                {
                    "event_type": "STOP_HOOK_NO_BROKER_STATE",
                    "session_id": session_id,
                    "loop_count": loop_count,
                    "broker_phase": BrokerPhase.IDLE.value,
                    "followup_returned": False,
                    "final_return_allowed": True,
                },
            )
            return {}
        return _stop_hook_block_terminal_return(
            root,
            session_id=session_id,
            loop_count=loop_count,
            event_type="STOP_HOOK_NO_BROKER_STATE_BLOCKED",
            broker_phase=BrokerPhase.IDLE.value,
        )
    message = render_broker_followup(existing)
    if message is None:
        if stop_hook_terminal_return_allowed(root):
            append_hook_trace(
                root,
                {
                    "event_type": "STOP_HOOK_NO_FOLLOWUP",
                    "session_id": session_id,
                    "cycle_id": existing.cycle_id,
                    "loop_count": loop_count,
                    "broker_phase": existing.phase.value,
                    "dag_generation": existing.dag_generation,
                    "followup_returned": False,
                    "final_return_allowed": True,
                },
            )
            return {}
        return _stop_hook_block_terminal_return(
            root,
            session_id=session_id,
            loop_count=loop_count,
            event_type="STOP_HOOK_NO_FOLLOWUP_BLOCKED",
            broker_phase=existing.phase.value,
            cycle_id=existing.cycle_id,
            dag_generation=existing.dag_generation,
        )
    if existing.phase == BrokerPhase.QUEUED:
        persist_broker_state(
            store,
            existing.model_copy(
                update={"phase": BrokerPhase.FOLLOWUP_EMITTED, "followup_emitted": True}
            ),
        )
    append_hook_trace(
        root,
        {
            "event_type": "STOP_HOOK_FOLLOWUP_RETURNED",
            "session_id": session_id,
            "cycle_id": existing.cycle_id,
            "loop_count": loop_count,
            "broker_phase": BrokerPhase.FOLLOWUP_EMITTED.value,
            "dag_generation": existing.dag_generation,
            "followup_returned": True,
        },
    )
    return {"followup_message": message}


def _parse_followup_fields(prompt: str) -> dict[str, str] | None:
    normalized = prompt.replace("\r\n", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    if BROKER_MARKER not in lines:
        return None
    fields: dict[str, str] = {}
    for line in lines:
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        if key in {
            "CYCLE_ID",
            "KIND",
            "NEXT_ACTION_CLASS",
            "NEXT_NODE_ID",
            "MAIN_SHA",
            "MAIN_TREE",
            "CHECKPOINT_DIGEST",
            "DAG_GENERATION",
            "BACKEND",
        }:
            fields[key] = value.strip()
    return fields


def consume_trusted_followup(root: Path, prompt: str) -> BrokerState:
    """Consume only an exact trusted broker follow-up. Untrusted text cannot."""
    fields = _parse_followup_fields(prompt)
    if fields is None:
        raise BrokerError("trusted broker marker is absent", code="UNTRUSTED_PROMPT")
    cycle_id = fields.get("CYCLE_ID")
    main_sha = fields.get("MAIN_SHA")
    main_tree = fields.get("MAIN_TREE")
    if cycle_id is None or main_sha is None or main_tree is None:
        raise BrokerError("trusted follow-up fields are incomplete", code="UNTRUSTED_PROMPT")
    if not _CYCLE_RE.fullmatch(cycle_id):
        raise BrokerError("cycle_id is unsafe", code="UNSAFE_CYCLE_ID")
    store = broker_store_dir(root)
    existing = load_broker_state(store)
    if existing is None or existing.cycle_id != cycle_id:
        raise BrokerError("successor cycle is not queued", code="CYCLE_MISSING")
    if existing.trusted_main != main_sha or existing.trusted_tree != main_tree:
        raise BrokerError("follow-up pin does not match queued state", code="STALE_CHECKPOINT")
    if existing.followup_digest is not None:
        expected = render_broker_followup(existing)
        if expected is None or expected not in prompt.replace("\r\n", "\n"):
            raise BrokerError("follow-up text is not the trusted template", code="UNTRUSTED_PROMPT")
        if hash_payload({"followup": expected}) != existing.followup_digest:
            raise BrokerError("follow-up digest mismatch", code="UNTRUSTED_PROMPT")
    return consume_successor(root, cycle_id)


def handle_before_submit_event(payload: object, *, root: Path) -> dict[str, bool]:
    """Thin beforeSubmit handshake. No routing, merge, or owner policy."""
    prompt = ""
    session_id: str | None = None
    if isinstance(payload, dict):
        raw_prompt = payload.get("prompt")
        if isinstance(raw_prompt, str):
            prompt = raw_prompt
        raw_session = payload.get("conversation_id")
        if isinstance(raw_session, str):
            session_id = raw_session[:256]
    if BROKER_MARKER not in prompt:
        append_hook_trace(
            root,
            {
                "event_type": "BEFORE_SUBMIT_IGNORED",
                "session_id": session_id,
                "successor_consumed": False,
            },
        )
        return {"continue": True}
    try:
        consumed = consume_trusted_followup(root, prompt)
    except BrokerError as exc:
        append_hook_trace(
            root,
            {
                "event_type": "BEFORE_SUBMIT_CONSUME_REJECTED",
                "session_id": session_id,
                "successor_consumed": False,
                "error_code": exc.code,
            },
        )
        return {"continue": True}
    append_hook_trace(
        root,
        {
            "event_type": "BEFORE_SUBMIT_SUCCESSOR_CONSUMED",
            "session_id": session_id,
            "cycle_id": consumed.cycle_id,
            "broker_phase": consumed.phase.value,
            "dag_generation": consumed.dag_generation,
            "successor_consumed": True,
        },
    )
    return {"continue": True}


def _persist_checkpoint(root: Path, checkpoint: GovernorCheckpoint) -> GovernorCheckpoint:
    sealed = checkpoint.model_copy(
        update={"record_digest": hash_payload(checkpoint.unsigned_payload())}
    )
    store = broker_store_dir(root)
    store.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(
        _store_path(store.resolve(), CHECKPOINT_NAME),
        sealed.model_dump(mode="json"),
    )
    return sealed


def finalize_governor_checkpoint(
    root: Path,
    *,
    result_class: str,
    cycle_id: str,
    trusted_main: str,
    trusted_tree: str,
    next_action_class: str,
    next_node_id: str | None = None,
    repository_identity: str = CANONICAL_REPOSITORY_IDENTITY,
    dag_generation: int = 0,
    dag_terminal: bool = False,
    worker_terminal: bool = False,
    owner_gate_fingerprint: str | None = None,
    owner_request_id: str | None = None,
    primary_governor: bool = True,
    lease_id: str | None = None,
    external_wait_identity: str | None = None,
    active_package: str | None = None,
    candidate_head: str | None = None,
    ci_state: str | None = None,
    iv_state: str | None = None,
    adv_state: str | None = None,
    progress_sequence: int = 0,
    owner_action_required_now: bool = False,
    safe_dag_work_remains: bool | None = None,
    return_state: object | None = None,
) -> FinalizeResult:
    """Authoritative checkpoint path. CHECKPOINT_CONTINUE implies successor queued."""
    terminal_path = (
        result_class in TERMINAL_RESULT_CLASSES or owner_action_required_now
    )
    return_state = _resolve_return_state(
        root,
        return_state,
        terminal_path=terminal_path,
    )
    if result_class in TERMINAL_RESULT_CLASSES or owner_action_required_now:
        phase = (
            BrokerPhase.PARKED_OWNER
            if result_class == TerminalResultClass.WAITING_OWNER or owner_action_required_now
            else BrokerPhase.IDLE
        )
        remains = False if safe_dag_work_remains is None else safe_dag_work_remains
        return FinalizeResult(
            result_class=result_class,
            successor_enqueued=False,
            phase=phase,
            next_machine_action=None,
            next_machine_action_scheduled=False,
            final_response_allowed=final_response_allowed(
                owner_action_required_now=owner_action_required_now or result_class
                == TerminalResultClass.WAITING_OWNER,
                safe_dag_work_remains=remains,
                successor_state=phase.value,
                return_state=return_state,
            ),
        )
    if result_class not in ENQUEUEABLE_RESULT_CLASSES:
        return FinalizeResult(
            result_class="CONTINUATION_ENQUEUE_FAILED",
            successor_enqueued=False,
            phase=BrokerPhase.IDLE,
            next_machine_action=None,
            next_machine_action_scheduled=False,
            final_response_allowed=False,
            error_code="UNKNOWN_RESULT_CLASS",
        )
    try:
        checkpoint = _persist_checkpoint(
            root,
            GovernorCheckpoint(
                result_class=result_class,
                trusted_main=trusted_main,
                trusted_tree=trusted_tree,
                cycle_id=cycle_id,
                next_action_class=next_action_class,
                next_node_id=next_node_id,
                dag_generation=dag_generation,
                record_digest=PLACEHOLDER_DIGEST,
            ),
        )
        enqueue_successor(
            root,
            cycle_id=cycle_id,
            kind=SuccessorKind(result_class),
            trusted_main=trusted_main,
            trusted_tree=trusted_tree,
            repository_identity=repository_identity,
            dag_generation=dag_generation,
            dag_terminal=dag_terminal,
            worker_terminal=worker_terminal,
            owner_gate_fingerprint=owner_gate_fingerprint,
            owner_request_id=owner_request_id,
            primary_governor=primary_governor,
            checkpoint_digest=checkpoint.record_digest,
            next_node_id=next_node_id,
            next_action_class=next_action_class,
            lease_id=lease_id,
            external_wait_identity=external_wait_identity,
            active_package=active_package,
            candidate_head=candidate_head,
            ci_state=ci_state,
            iv_state=iv_state,
            adv_state=adv_state,
            progress_sequence=progress_sequence,
        )
        verified = recover_broker(root)
        if (
            verified is None
            or verified.phase != BrokerPhase.QUEUED
            or verified.cycle_id != cycle_id
        ):
            raise BrokerError(
                "queued successor was not durable",
                code="CONTINUATION_ENQUEUE_FAILED",
            )
        remains = True if safe_dag_work_remains is None else safe_dag_work_remains
        return FinalizeResult(
            result_class=result_class,
            successor_enqueued=True,
            phase=verified.phase,
            cycle_id=verified.cycle_id,
            kind=verified.kind,
            next_machine_action=next_action_class,
            next_machine_action_scheduled=next_machine_action_receipt_valid(
                executing=False, scheduled=True
            ),
            final_response_allowed=final_response_allowed(
                owner_action_required_now=False,
                safe_dag_work_remains=remains,
                successor_state=verified.phase.value,
                return_state=return_state,
            ),
            no_progress_count=verified.no_progress_count,
        )
    except BrokerError as exc:
        if exc.code == "NO_PROGRESS_LOOP":
            store = broker_store_dir(root)
            existing = load_broker_state(store)
            if existing is not None:
                parked = persist_broker_state(
                    store,
                    existing.model_copy(update={"phase": BrokerPhase.AWAITING_RESULT}),
                )
                return FinalizeResult(
                    result_class="NO_PROGRESS_LOOP",
                    successor_enqueued=False,
                    phase=parked.phase,
                    cycle_id=parked.cycle_id,
                    kind=parked.kind,
                    next_machine_action=next_action_class,
                    next_machine_action_scheduled=True,
                    final_response_allowed=final_response_allowed(
                        owner_action_required_now=False,
                        safe_dag_work_remains=True,
                        successor_state=BrokerPhase.AWAITING_RESULT.value,
                        return_state=return_state,
                    ),
                    error_code=exc.code,
                    no_progress_count=parked.no_progress_count,
                )
        return FinalizeResult(
            result_class="CONTINUATION_ENQUEUE_FAILED",
            successor_enqueued=False,
            phase=BrokerPhase.IDLE,
            next_machine_action=next_action_class,
            next_machine_action_scheduled=False,
            final_response_allowed=False,
            error_code=exc.code,
        )


def status_report(root: Path) -> dict[str, object]:
    try:
        existing = recover_broker(root)
    except BrokerError as exc:
        return {
            "ok": False,
            "error": exc.code,
            "package_id": PACKAGE_ID,
            "execution_authorized": False,
            "merge_authorized": False,
        }
    if existing is None:
        return {
            "ok": True,
            "package_id": PACKAGE_ID,
            "backend": BACKEND,
            "phase": BrokerPhase.IDLE.value,
            "execution_authorized": False,
            "merge_authorized": False,
        }
    return {
        "ok": True,
        "package_id": PACKAGE_ID,
        "backend": BACKEND,
        "phase": existing.phase.value,
        "cycle_id": existing.cycle_id,
        "kind": existing.kind.value if existing.kind else None,
        "next_action_class": existing.next_action_class,
        "next_node_id": existing.next_node_id,
        "followup_emitted": existing.followup_emitted,
        "consumed": existing.consumed,
        "no_progress_count": existing.no_progress_count,
        "owner_request_already_emitted": existing.owner_request_already_emitted,
        "execution_authorized": False,
        "merge_authorized": False,
        "authority_granted": False,
        "python_executable": sys.executable,
        "primary_continuation_backend": PRIMARY_CONTINUATION_BACKEND,
        "stop_hook_backend": STOP_HOOK_BACKEND,
    }


def supersede_legacy_hook_successor(
    root: Path,
    *,
    cycle_id: str,
    reason: str = "SUPERSEDED_BY_SDK_SUPERVISOR",
) -> BrokerState:
    """Park a stranded stop-hook successor without treating it as consumed."""
    if not _CYCLE_RE.fullmatch(cycle_id):
        raise BrokerError("cycle_id is unsafe", code="UNSAFE_CYCLE_ID")
    if not _FINGERPRINT_RE.fullmatch(reason):
        raise BrokerError("supersede reason is unsafe", code="UNSAFE_TOKEN")
    store = broker_store_dir(root)
    existing = load_broker_state(store)
    if existing is None or existing.cycle_id != cycle_id:
        raise BrokerError("legacy successor is not queued", code="CYCLE_MISSING")
    if existing.consumed:
        raise BrokerError("cycle already consumed", code="CYCLE_REPLAY")
    updated = persist_broker_state(
        store,
        existing.model_copy(
            update={
                "phase": BrokerPhase.AWAITING_RESULT,
                "followup_emitted": False,
                "external_wait_identity": reason,
            }
        ),
    )
    append_hook_trace(
        root,
        {
            "event_type": "LEGACY_SUCCESSOR_SUPERSEDED",
            "cycle_id": cycle_id,
            "broker_phase": updated.phase.value,
            "error_code": reason,
        },
    )
    return updated
