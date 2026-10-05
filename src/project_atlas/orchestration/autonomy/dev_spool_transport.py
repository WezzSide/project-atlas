"""Durable, hash-verified spool backend for ``DevTransport`` (AS-DEVLOOP-001, slice 3).

Unlike ``InMemoryTransport`` this backend survives process death and can be shared by several
processes/hosts through any directory that offers atomic ``link``/``rename`` (local disk, a
checked-out git working tree that is synchronised by a fleet mechanism, a mounted volume). It is the
mechanical deployment seam for the fleet channel: cross-host proof still requires a real
synchronisation medium and role identities (``FLEET_TRANSPORT_INTEGRATION_READY``, not
``LOOP_ACTIVE``).

Layout::

    <root>/<CHANNEL>/<seal>.json                 published, unclaimed
    <root>/<CHANNEL>/<seal>.to                    addressee of the record, if it has one
    <root>/<CHANNEL>/withdrawn/<seal>.json        tombstone written by ``withdraw``
    <root>/<CHANNEL>/rejected/<name>              a file ``claim`` could not hand out, parked
    <root>/<CHANNEL>/claimed/<seal>.json          consumed (content kept as durable evidence)
    <root>/<CHANNEL>/claimed/<seal>.claim.json    who claimed it

Guarantees: atomic publish (temp + ``os.link``: never overwrites, never half-written);
consume-once across concurrent claimers: the ownership transition is the EXCLUSIVE CREATION of
the ``claimed/<seal>.json`` name with ``os.link`` (it fails with ``FileExistsError`` for every
claimer but one, on POSIX and on Windows). ``os.rename`` is deliberately NOT the ownership
primitive: on Windows it opens the source by name and renames by handle, so several claimers that
opened the same file before the first rename completed can each report success. Removing the
pending name afterwards is mere cleanup of the winner's own record and never decides ownership.
Seal re-check on every claim (tamper => ``TransportError``, a ``ContractError``; the file is
moved to ``rejected/`` once and never handed out);
same role/channel and executor-vs-verifier rules as the reference backend.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
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
    check_address,
    decode,
    encode,
)

_CLAIMED = "claimed"
_WITHDRAWN = "withdrawn"
MAX_ADDRESS_BYTES = 256

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
    the delete fail transiently, so retry boundedly. After a claim, a name that still cannot
    be removed is inert: the record is in ``claimed/`` (ownership is already decided) and
    every later claimer loses the exclusive create. ``withdraw`` and ``publish`` call this
    too, for a record nobody claimed; there a leftover name stays in the spool and it is the
    tombstone that keeps ``claim`` from handing it out.
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
    addressed = True  # ``publish(record, to=identity)`` delivers only to that identity

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        for ch in Channel:
            (self.root / ch.value / _CLAIMED).mkdir(parents=True, exist_ok=True)

    def _dir(self, channel: Channel) -> Path:
        return self.root / channel.value

    def _addressee(self, channel: Channel, seal: str) -> str | None:
        """Who a record is addressed to; ``None`` when it has no address file.

        An address that is not a regular file (a symbolic link, a directory, a FIFO), is
        longer than ``MAX_ADDRESS_BYTES``, cannot be read or does not hold exactly an
        identity of at most ``MAX_IDENTITY`` characters raises ``TransportError``: an
        unreadable address is never "addressed to nobody in particular". A MISSING address
        file does mean "not addressed"; the spool directory is trusted for that. The check
        for a regular file and the read are two steps, not one.
        """
        path = self._dir(channel) / f"{seal}.to"
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise ValueError("not a regular file")
            with open(path, "rb") as fh:
                raw = fh.read(MAX_ADDRESS_BYTES + 1)
            if len(raw) > MAX_ADDRESS_BYTES:
                raise ValueError("too long")
            who = raw.decode("utf-8")
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise TransportError(f"unreadable address of record {seal}: {exc}") from exc
        check_address(who, who)
        return who

    def publish(self, record: Record, *, to: str | None = None) -> bool:
        """Publish; with ``to`` the record is delivered only to that identity's ``claim``.

        The address is written BEFORE the record (exclusive creation, never overwritten), so
        a record this call publishes is never claimable without its address. It is delivery
        metadata in the spool directory, outside the record's seal: whoever can write the
        spool can write, replace or remove an address, and a missing address file means "not
        addressed". Publishing a record again with another address, or with none after it
        had one, is refused when the publishes are sequential; a concurrent UNADDRESSED
        publish of the same seal can win the record name first (the planner never publishes
        one seal both ways). ``claim`` and ``claimed_records`` honour the address.
        A seal that was withdrawn (``withdraw``) is not published again: this returns False
        for it, before and after creating the record's name.
        """
        channel = CHANNEL_FOR_KIND[record.KIND]
        wire = encode(record)  # verifies the seal
        d = self._dir(channel)
        final = d / f"{record.seal}.json"
        tomb = d / _WITHDRAWN / f"{record.seal}.json"
        if tomb.exists():
            return False  # taken back by its publisher: it stays back, whatever the address
        exists = final.exists() or (d / _CLAIMED / f"{record.seal}.json").exists()
        standing = self._addressee(channel, record.seal)
        check_address(to, standing if exists or standing is not None else to)
        if exists:
            return False
        if to is not None and standing is None:
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(to)
                    fh.flush()
                    os.fsync(fh.fileno())
                with contextlib.suppress(FileExistsError):
                    os.link(tmp, d / f"{record.seal}.to")  # exclusive: the first address stands
            finally:
                os.unlink(tmp)
            check_address(to, self._addressee(channel, record.seal))  # a rival's address won?
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
        if tomb.exists():  # withdrawn while this publish was on its way: take the name back
            _release_pending(final)
            return False
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
            if (d / _WITHDRAWN / path.name).exists():
                continue  # taken back by its publisher; the name is a leftover, never delivered
            try:
                addressee = self._addressee(channel, rec.seal)
            except TransportError:
                continue  # unreadable address: nobody receives it; the record stays pending
            if addressee is not None and not same_identity(identity, addressee):
                continue  # addressed to another identity: leave it for that one
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

    def withdraw(self, record: Record) -> bool:
        """Take a record back for good unless somebody claimed it.

        True when this call wrote the tombstone and saw no claim; False when the record is
        claimed, when the seal already had a tombstone, or when the spool cannot be written.

        A tombstone ``withdrawn/<seal>.json`` is created first (exclusively; it holds the
        record as evidence), then the pending name, if there is one, is removed. The
        tombstone is written also when the record is not in the spool at all, so that a
        ``publish`` of that seal which is still on its way is refused: ``publish`` looks for
        the tombstone before it starts and again after it created the record's name, and
        removes its own name when it finds one. A record that a claimer already has stays
        the claimer's: this returns False and removes the tombstone again. Returns False as
        well when the seal was withdrawn before. Best effort: it does not raise for a spool
        it cannot write, and a pending name it cannot remove stays in the spool next to its
        tombstone; ``claim`` skips a pending record whose seal has a tombstone, and
        ``published`` does not list it. Two windows remain, both between two steps of
        different calls: a claimer that linked the record between ``publish`` creating the
        name and ``publish`` seeing the tombstone keeps it, and so does a claimer that looked
        for the tombstone before this call wrote it and links a name this call could not
        remove after this call looked for a claim; in the second case this call has returned
        True. Neither makes the claimed execution's result acceptable: that is decided by the
        journal, not here.
        """
        channel = CHANNEL_FOR_KIND[record.KIND]
        wire = encode(record)
        d = self._dir(channel)
        name = f"{record.seal}.json"
        tomb = d / _WITHDRAWN / name
        if (d / _CLAIMED / name).exists():
            with contextlib.suppress(OSError):
                tomb.unlink()  # a tombstone next to a claimed record says nothing
            return False
        fresh = False
        try:
            tomb.parent.mkdir(exist_ok=True)
            if not tomb.exists():
                fd, tmp = tempfile.mkstemp(dir=tomb.parent, prefix=".tmp-")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(wire)
                        fh.flush()
                        os.fsync(fh.fileno())
                    with contextlib.suppress(FileExistsError):
                        os.link(tmp, tomb)
                        fresh = True
                finally:
                    os.unlink(tmp)
        except OSError:
            return False
        _release_pending(d / name)
        if (d / _CLAIMED / name).exists():  # a claimer linked it meanwhile: it is the claimer's
            with contextlib.suppress(OSError):
                tomb.unlink()
            return False
        return fresh

    def published(self, channel: Channel) -> list[Record]:
        """Every decodable record this spool holds on ``channel``, pending or claimed.

        Read-only: what the spool directory holds now. Records are kept after a claim, so
        absent loss this is what was published and neither rejected nor withdrawn (a pending
        record whose seal has a tombstone is not listed; a claimed one is, tombstone or not,
        which matters only inside the windows ``withdraw`` documents); a file that cannot be
        decoded, or whose name or channel does not match its record, is skipped (it would be
        parked on a claim, never handed out).
        """
        d = self._dir(channel)
        out: dict[str, Record] = {}
        for folder in (d, d / _CLAIMED):
            for path in sorted(folder.glob("*.json")):
                if path.name.endswith(".claim.json") or path.name.startswith("."):
                    continue
                if folder is d and (d / _WITHDRAWN / path.name).exists():
                    continue  # a leftover name of a withdrawn record
                try:
                    rec = decode(_read_wire(path))
                except (OSError, ValueError, ContractError, RecursionError):
                    continue
                if rec.seal == path.stem and CHANNEL_FOR_KIND[rec.KIND] is channel:
                    out[rec.seal] = rec
        return [out[k] for k in sorted(out)]

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
                addressee = self._addressee(channel, seal)
                if addressee is not None and not same_identity(identity, addressee):
                    continue  # addressed to another identity: never re-adopted by this one
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
