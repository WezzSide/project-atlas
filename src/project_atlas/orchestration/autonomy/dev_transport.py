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
    validate_identity,
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
    def publish(self, record: Record, *, to: str | None = None) -> bool:
        """Publish a sealed record; False when an identical record was already published.

        ``to`` (optional, ATLAS-DEVQ-0011) addresses the record to one identity: only that
        identity's ``claim`` receives it. The address is delivery metadata next to the
        record, not part of its seal. A backend that implements it says so with the class
        attribute ``addressed = True`` (callers read it with ``getattr``). Such a backend may also
        offer
        ``withdraw(record) -> bool``: take a published record back if nobody claimed it.
        """

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
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise TransportError(f"undecodable record: {exc}") from exc
    rec.verify_seal()
    return rec


MAX_IDENTITY = 200  # an executor identity / addressee, in characters (identities are ASCII)


def check_address(to: str | None, standing: str | None) -> None:
    """Refuse an address that is not an identity, or that differs from the one that stands.

    A record is addressed once: publishing it again with another address, or with none after
    it was addressed (or the reverse), would silently change who may receive it.
    """
    if to is not None:
        try:
            if not isinstance(to, str):
                raise ValueError("not a string")
            validate_identity(to)
            if len(to) > MAX_IDENTITY:
                raise ValueError("too long")
        except ValueError as exc:
            raise TransportError(f"invalid addressee: {exc}") from exc
    if (to is None) != (standing is None) or (
        to is not None and standing is not None and not same_identity(to, standing)
    ):
        raise TransportError("record is already published with a different address")


class InMemoryTransport:
    """Reference/test backend. Not a production transport."""

    addressed = True

    def __init__(self) -> None:
        self._to: dict[tuple[Channel, str], str] = {}  # (channel, seal) -> addressee
        self._queues: dict[Channel, deque[str]] = defaultdict(deque)
        self._seen: set[tuple[Channel, str]] = set()
        self.claims: list[tuple[Channel, str, str]] = []  # (channel, seal, claimer identity)
        # undecodable/tampered wires, kept as bounded evidence (newest MAX_REJECTED)
        self.rejected: list[tuple[Channel, str]] = []

    def publish(self, record: Record, *, to: str | None = None) -> bool:
        channel = CHANNEL_FOR_KIND[record.KIND]
        wire = encode(record)  # verifies the seal
        key = (channel, record.seal)
        check_address(to, self._to.get(key) if key in self._seen else to)
        if key in self._seen:
            return False
        if to is not None:
            self._to[key] = to
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
            addressee = self._to.get((channel, rec.seal))
            if addressee is not None and not same_identity(identity, addressee):
                continue  # addressed to another identity; leave it queued
            del q[idx]
            self.claims.append((channel, rec.seal, identity))
            return rec
        return None

    def withdraw(self, record: Record) -> bool:
        """Remove a published, unclaimed record; False when it is not pending."""
        channel = CHANNEL_FOR_KIND[record.KIND]
        wire = encode(record)
        q = self._queues[channel]
        if wire not in q:
            return False
        q.remove(wire)
        return True

    # test hook: simulate wire tampering of the next queued record
    def _tamper_next(self, channel: Channel, replace: tuple[str, str]) -> None:
        q = self._queues[channel]
        q[0] = q[0].replace(*replace, 1)
