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

from project_atlas.orchestration.autonomy.dev_contracts import Role, VerificationRequest
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
            try:
                wire = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue  # another claimer consumed it between listing and reading
            rec = decode(wire)  # tamper => ContractError, record stays unconsumed
            if rec.seal != path.stem:
                raise TransportError("spool file name does not match record seal")
            if channel is Channel.VERIFICATION:
                assert isinstance(rec, VerificationRequest)
                if identity == rec.executor_identity:
                    raise TransportError("executor identity may not claim its own verification")
                if identity != rec.verifier_identity:
                    continue
            try:
                os.rename(path, d / _CLAIMED / path.name)  # exactly one claimer wins
            except FileNotFoundError:
                continue  # lost the race to another claimer
            (d / _CLAIMED / f"{rec.seal}.claim.json").write_text(
                json.dumps({"identity": identity, "role": role.value}, sort_keys=True),
                encoding="utf-8",
            )
            return rec
        return None
