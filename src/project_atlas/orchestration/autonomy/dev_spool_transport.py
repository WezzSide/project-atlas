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
consume-once across concurrent claimers (``os.rename`` into ``claimed/`` succeeds for exactly
one); seal re-check on every claim (tamper => ``ContractError``, record left in place, never
consumed); same role/channel and executor-vs-verifier rules as the reference backend.
"""

from __future__ import annotations

import json
import os
import tempfile
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
                wire = path.read_text(encoding="utf-8")
                rec = decode(wire)
                if rec.seal != path.stem:
                    bad = "spool file name does not match record seal"
                elif CHANNEL_FOR_KIND[rec.KIND] is not channel:
                    bad = "record kind does not belong to this channel"
                else:
                    bad = ""
            except FileNotFoundError:
                continue  # another claimer consumed it between listing and reading
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
            try:
                os.rename(path, d / _CLAIMED / path.name)  # exactly one claimer wins
            except FileNotFoundError:
                continue  # lost the race to another claimer
            meta = d / _CLAIMED / f"{rec.seal}.claim.json"
            tmp = meta.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"identity": identity, "role": role.value}, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, meta)  # atomic: never a torn meta
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
