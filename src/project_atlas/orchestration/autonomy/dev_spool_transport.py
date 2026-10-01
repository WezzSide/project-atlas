"""Durable, hash-verified spool backend for ``DevTransport`` (AS-DEVLOOP-001, slice 3).

Unlike ``InMemoryTransport`` this backend survives process death and can be shared by several
processes/hosts through any directory that offers atomic ``link``/``rename`` (local disk, a
checked-out git working tree that is synchronised by a fleet mechanism, a mounted volume). It is the
mechanical deployment seam for the fleet channel: cross-host proof still requires a real
synchronisation medium and role identities (``FLEET_TRANSPORT_INTEGRATION_READY``, not
``LOOP_ACTIVE``).

Layout::

    <root>/<CHANNEL>/<seal>.json                 published, unclaimed
    <root>/<CHANNEL>/claimed/<seal>.json          consumed (content kept as durable evidence)
    <root>/<CHANNEL>/claimed/<seal>.claim.json    who claimed it

Guarantees: atomic publish (temp + ``os.link``: never overwrites, never half-written);
consume-once across concurrent claimers: the ownership transition is the EXCLUSIVE CREATION of
the ``claimed/<seal>.json`` name with ``os.link`` (it fails with ``FileExistsError`` for every
claimer but one, on POSIX and on Windows). ``os.rename`` is deliberately NOT the ownership
primitive: on Windows it opens the source by name and renames by handle, so several claimers that
opened the same file before the first rename completed can each report success. Removing the
pending name afterwards is mere cleanup of the winner's own record and never decides ownership.
Seal re-check on every claim (tamper => ``ContractError``, record left in place, never consumed);
same role/channel and executor-vs-verifier rules as the reference backend.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from project_atlas.orchestration.autonomy.dev_contracts import (
    ContractError,
    Role,
    VerificationRequest,
    same_identity,
)
from project_atlas.orchestration.autonomy.dev_transport import (
    CHANNEL_FOR_KIND,
    ROLE_FOR_CHANNEL,
    Channel,
    Record,
    TransportError,
    decode,
    encode,
)

_CLAIMED = "claimed"

# A concurrent claimer's open/rename can make a read or rename fail TRANSIENTLY with
# ``PermissionError`` (Windows sharing violation: files are opened without FILE_SHARE_DELETE).
# That must never be mistaken for a hostile record: retry a bounded number of times with a short
# deterministic backoff, and only then treat the file as genuinely unusable.
_TRANSIENT_TRIES = 12
_TRANSIENT_BACKOFF_S = (0.002, 0.004, 0.008, 0.016, 0.032)  # the last step repeats


def _retry_transient[T](op: Callable[[], T], *, still_valid: Callable[[], bool]) -> T:
    """Run ``op``; retry only ``PermissionError`` while ``still_valid()`` (bounded, no spin)."""
    for attempt in range(_TRANSIENT_TRIES):
        try:
            return op()
        except PermissionError:
            if attempt == _TRANSIENT_TRIES - 1 or not still_valid():
                raise
            time.sleep(_TRANSIENT_BACKOFF_S[min(attempt, len(_TRANSIENT_BACKOFF_S) - 1)])
    raise AssertionError("unreachable")  # pragma: no cover - loop always returns or raises


def _read_wire(path: Path) -> str:
    return _retry_transient(lambda: path.read_text(encoding="utf-8"), still_valid=path.exists)


def _claim_link(src: Path, dest: Path) -> None:
    """Exclusive-create ``dest`` as a hard link of ``src``: the one atomic ownership transition.

    Exactly one caller can create the name; everyone else gets ``FileExistsError`` (or
    ``FileNotFoundError`` once the pending name is gone). A transient ``PermissionError`` is
    retried only while the source still exists and the destination slot is still free.
    """
    _retry_transient(
        lambda: os.link(src, dest), still_valid=lambda: src.exists() and not dest.exists()
    )


def _release_pending(path: Path) -> None:
    """Best-effort removal of the winner's own pending name (never decides ownership).

    A concurrent claimer may still hold the pending file open for reading; on Windows that makes
    the delete fail transiently, so retry boundedly. If it still cannot be removed, the record
    stays in ``claimed/`` (ownership is already decided) and the leftover pending name is inert:
    every later claimer loses the exclusive create.
    """
    with contextlib.suppress(OSError):
        _retry_transient(path.unlink, still_valid=path.exists)


def _lost_race(path: Path, dest: Path) -> bool:
    """After ``FileExistsError``: is ``dest`` this record's claim, not a blocked slot?"""
    if not path.exists():
        return True  # the winner already removed the pending name
    try:
        return dest.is_file() and os.path.samefile(path, dest)
    except FileNotFoundError:
        return True  # vanished between the checks: the winner finished
    except OSError:
        return False


class SpoolTransport:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        for ch in Channel:
            (self.root / ch.value / _CLAIMED).mkdir(parents=True, exist_ok=True)

    def _dir(self, channel: Channel) -> Path:
        return self.root / channel.value

    def publish(self, record: Record) -> bool:
        channel = CHANNEL_FOR_KIND[record.KIND]
        wire = encode(record)  # verifies the seal
        d = self._dir(channel)
        final = d / f"{record.seal}.json"
        if final.exists() or (d / _CLAIMED / f"{record.seal}.json").exists():
            return False
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(wire)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, final)  # atomic, refuses to overwrite
            except FileExistsError:
                return False
        finally:
            os.unlink(tmp)
        return True

    def claim(self, channel: Channel, *, role: Role, identity: str) -> Record | None:
        if ROLE_FOR_CHANNEL[channel] is not role:
            raise TransportError(f"role {role.value} may not claim from {channel.value}")
        d = self._dir(channel)
        for path in sorted(p for p in d.glob("*.json") if not p.name.startswith(".")):
            if not path.is_file() or path.is_symlink():
                continue  # directories/symlinks/devices are never records; skip, never spin
            try:
                wire = _read_wire(path)
                rec = decode(wire)
                if rec.seal != path.stem:
                    bad = "spool file name does not match record seal"
                elif CHANNEL_FOR_KIND[rec.KIND] is not channel:
                    bad = "record kind does not belong to this channel"
                else:
                    bad = ""
            except FileNotFoundError:
                continue  # another claimer consumed it between listing and reading
            except PermissionError:
                continue  # persistent contention is never evidence of a hostile record: leave it
            except (OSError, ValueError, ContractError, RecursionError) as exc:
                bad = f"unreadable or undecodable spool file: {type(exc).__name__}"
            if bad:
                # reject exactly once, keep the bytes as evidence, never wedge the channel
                if self._park(d, path):
                    raise TransportError(bad)
                continue  # could not even park it: skip it instead of raising forever
            if channel is Channel.VERIFICATION:
                if not isinstance(rec, VerificationRequest):  # unreachable: kind/channel checked
                    raise TransportError("record kind does not belong to this channel")
                if not same_identity(identity, rec.verifier_identity):
                    continue  # addressed to someone else: leave it, never wedge this claimer
                if same_identity(identity, rec.executor_identity):
                    raise TransportError("executor identity may not claim its own verification")
            dest = d / _CLAIMED / path.name
            try:
                _claim_link(path, dest)  # THE linearization point: exactly one claimer creates it
            except FileNotFoundError:
                continue  # lost the race: the pending name is already gone
            except (FileExistsError, PermissionError) as exc:
                if not isinstance(exc, FileExistsError) and not dest.exists():
                    continue  # persistent contention: the record stays pending, nothing rejected
                if _lost_race(path, dest):
                    continue  # another claimer owns it; a lost race never becomes a second claim
                # the claimed/ slot holds something that is not this record's claim: blocked slot
                if self._park(d, path):
                    raise TransportError("record could not be claimed and was parked") from None
                continue
            except OSError:
                # the claimed/ slot is unusable (e.g. no hard links): never retry it forever
                if self._park(d, path):
                    raise TransportError("record could not be claimed and was parked") from None
                continue
            _release_pending(path)  # cleanup only; ownership was decided by the link above
            meta = d / _CLAIMED / f"{rec.seal}.claim.json"
            tmp = meta.with_suffix(".tmp")
            try:
                tmp.write_text(
                    json.dumps({"identity": identity, "role": role.value}, sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(tmp, meta)  # atomic: never a torn meta
            except OSError:
                pass  # best effort: the claim already happened; a missing meta means 'unowned'
            return rec
        return None

    @staticmethod
    def _park(d: Path, path: Path) -> bool:
        rej = d / "rejected"
        try:
            rej.mkdir(exist_ok=True)
            os.replace(path, rej / path.name)
        except OSError:
            return False
        return True

    def claimed_records(self, channel: Channel, *, identity: str) -> list[Record]:
        """Records this identity claimed earlier (crash recovery: claim-before-persist window)."""
        d = self._dir(channel) / _CLAIMED
        out: list[Record] = []
        for rec_path in sorted(p for p in d.glob("*.json") if not p.name.endswith(".claim.json")):
            seal = rec_path.stem
            meta = d / f"{seal}.claim.json"
            try:
                who = _claimer(meta)
                if who is not None and not same_identity(who, identity):
                    continue  # owned by another identity: only its claimer may re-adopt it
                # no meta => crash between rename and meta write: the caller must check that the
                # record is addressed to it (the adapter does, for VERIFICATION)
                rec = decode(rec_path.read_text(encoding="utf-8"))
                if rec.seal != seal:
                    continue
                out.append(rec)
            except (OSError, ValueError, ContractError, RecursionError):
                continue  # unreadable claim evidence is ignored, never trusted
        return out


def _claimer(meta: Path) -> str | None:
    """Identity recorded in a claim meta; a missing/torn/garbled meta counts as 'unowned'."""
    try:
        who = json.loads(meta.read_text(encoding="utf-8")).get("identity")
    except (OSError, ValueError, AttributeError, RecursionError):
        return None
    return who if isinstance(who, str) else None
