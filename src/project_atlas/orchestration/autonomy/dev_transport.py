"""Transport-neutral carrier interface for the development loop (AS-DEVLOOP-001, slice 2).

``DevTransport`` is the seam a real fleet transport implements (GitHub-as-bus, a remote
``DispatchPort`` adapter, a hash-verified file spool). ``InMemoryTransport`` is a TEST / REFERENCE
backend only: it proves the contract semantics deterministically; it is not a control plane and
never proves a cross-host cycle.

Semantics every backend must provide:
  * records travel as plain JSON and are re-validated (seal included) on claim: tamper => error;
  * a record type is only published to / claimable from its own channel (RESULT != VERDICT);
  * consume-once: a claimed record is never handed out again; re-publishing an identical sealed
    record is an idempotent no-op;
  * a role may claim only from the channels its role owns; the VERIFICATION channel additionally
    refuses the executor's own identity (IMPLEMENTER != VERIFIER).

TRUST BOUNDARY (explicit non-guarantee): seals are unkeyed content digests. They detect
corruption, tampering-in-flight and cross-record confusion; they do NOT authenticate the
publisher. Anyone who can publish to a channel can mint a well-formed sealed record. Publisher
authentication is the transport's responsibility (e.g. the authenticated GitHub actor / the fabric
adapter that derives verdicts from independent GitHub evidence) and must be enforced by the
deployment mapping. The planner therefore binds every record to what it issued (request seal,
assigned verifier, execution id, current work item) but cannot, alone, prove who published it.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from enum import StrEnum
from typing import Any, Protocol

from project_atlas.orchestration.autonomy.dev_contracts import (
    ContractError,
    RecordKind,
    ResultRecord,
    Role,
    VerdictRecord,
    VerificationRequest,
    WorkItem,
    same_identity,
)

_MODEL_FOR_KIND: dict[RecordKind, Any] = {
    RecordKind.WORK: WorkItem,
    RecordKind.RESULT: ResultRecord,
    RecordKind.VERIFICATION_REQUEST: VerificationRequest,
    RecordKind.VERDICT: VerdictRecord,
}


class Channel(StrEnum):
    WORK = "WORK"  # planner -> implementer
    RESULT = "RESULT"  # implementer -> planner
    VERIFICATION = "VERIFICATION"  # planner -> verifier
    VERDICT = "VERDICT"  # verifier -> planner


CHANNEL_FOR_KIND: dict[RecordKind, Channel] = {
    RecordKind.WORK: Channel.WORK,
    RecordKind.RESULT: Channel.RESULT,
    RecordKind.VERIFICATION_REQUEST: Channel.VERIFICATION,
    RecordKind.VERDICT: Channel.VERDICT,
}
ROLE_FOR_CHANNEL: dict[Channel, Role] = {
    Channel.WORK: Role.IMPLEMENTER,
    Channel.RESULT: Role.PLANNER,
    Channel.VERIFICATION: Role.VERIFIER,
    Channel.VERDICT: Role.PLANNER,
}
Record = WorkItem | ResultRecord | VerificationRequest | VerdictRecord


MAX_REJECTED = 1000


class TransportError(ContractError):
    code = "DEV_TRANSPORT_REFUSED"


class DevTransport(Protocol):
    def publish(self, record: Record) -> bool:
        """Publish a sealed record; False when an identical record was already published."""

    def claim(self, channel: Channel, *, role: Role, identity: str) -> Record | None:
        """Consume-once claim of the next record on ``channel`` for ``role``; None when empty."""


def encode(record: Record) -> str:
    record.verify_seal()
    return json.dumps(
        {"kind": record.KIND.value, "body": record.model_dump(mode="json")}, sort_keys=True
    )


def decode(wire: str) -> Record:
    try:
        env = json.loads(wire)
        kind = RecordKind(env["kind"])
        rec: Record = _MODEL_FOR_KIND[kind].model_validate(env["body"])
    except (ValueError, KeyError, TypeError) as exc:
        raise TransportError(f"undecodable record: {exc}") from exc
    rec.verify_seal()
    return rec


class InMemoryTransport:
    """Reference/test backend. Not a production transport."""

    def __init__(self) -> None:
        self._queues: dict[Channel, deque[str]] = defaultdict(deque)
        self._seen: set[tuple[Channel, str]] = set()
        self.claims: list[tuple[Channel, str, str]] = []  # (channel, seal, claimer identity)
        # undecodable/tampered wires, kept as bounded evidence (newest MAX_REJECTED)
        self.rejected: list[tuple[Channel, str]] = []

    def publish(self, record: Record) -> bool:
        channel = CHANNEL_FOR_KIND[record.KIND]
        wire = encode(record)  # verifies the seal
        key = (channel, record.seal)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._queues[channel].append(wire)
        return True

    def claim(self, channel: Channel, *, role: Role, identity: str) -> Record | None:
        if ROLE_FOR_CHANNEL[channel] is not role:
            raise TransportError(f"role {role.value} may not claim from {channel.value}")
        q = self._queues[channel]
        for idx, wire in enumerate(q):
            try:
                rec = decode(wire)
            except ContractError:
                del q[idx]  # a poisoned wire must not block the channel: reject it exactly once
                self.rejected.append((channel, wire))
                del self.rejected[: max(0, len(self.rejected) - MAX_REJECTED)]
                raise
            if channel is Channel.VERIFICATION:
                if not isinstance(rec, VerificationRequest):
                    raise TransportError("record kind does not belong to this channel")
                if not same_identity(identity, rec.verifier_identity):
                    continue  # addressed to a different verifier; leave it queued
                if same_identity(identity, rec.executor_identity):
                    raise TransportError("executor identity may not claim its own verification")
            del q[idx]
            self.claims.append((channel, rec.seal, identity))
            return rec
        return None

    # test hook: simulate wire tampering of the next queued record
    def _tamper_next(self, channel: Channel, replace: tuple[str, str]) -> None:
        q = self._queues[channel]
        q[0] = q[0].replace(*replace, 1)
