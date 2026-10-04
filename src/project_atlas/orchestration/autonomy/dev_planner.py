"""Planner coordination step for the development loop (AS-DEVLOOP-001, slice 2).

The planner role (VPS3 in the current deployment mapping) selects the next admissible task
(``dev_queue``), materializes a sealed ``WorkItem``, publishes it, turns results into verification
requests for a DIFFERENT identity, and turns verdicts into: integration-ready, bounded repair,
owner-required, or blocked. One blocked lineage never blocks the program.

Not a merge actor: INTEGRATION_READY means "verified candidate exists"; governance/merge admission
(merge gate, owner policy) is a separate, later, owner-bound step. This module never grants a gate.

Write-scope admission (ATLAS-DEVQ-0006): ``select`` skips tasks that are in flight, and
``dispatch`` always refuses a new lineage whose sealed ``allowed_paths`` overlap those of a
lineage that still holds its scope (``SCOPE_HOLDING``); there is no switch to turn the check
off. This is admission control, not authority: it grants nothing.

Coordination journal (ATLAS-DEVQ-0007): every state change of the planner is ONE event in an
append-only journal, and the planner's in-memory state is only a replica obtained by replaying
it. The journal is the single source of truth for lineage phase and scope ownership; there is no
second record of it. Properties:
  * write-ahead: the event is appended (and, for ``DirJournal``, fsynced) and acknowledged
    before the matching record is published; ``recover`` re-publishes what a crash left
    unpublished (publishing is idempotent) and, on a transport that keeps claimed records
    (``claimed_records``: the spool), re-feeds records this identity had claimed but not yet
    journalled;
  * continuity before admission and before publication (the ownership invariant): loss or
    replacement of ACKNOWLEDGED history must not become permission to admit or publish
    conflicting work. An event is acknowledged once its digest is in the journal's anchor
    (``DirJournal``: one exclusive file per event in a separate directory); that happens after
    the append and before anything is published for the event, and a writer makes sure the
    event it builds on is acknowledged before it appends. Every replay, and so every operation that
    decides or publishes, checks first that the HEAD event this planner holds is still stored
    unchanged, that each event it applies matches its acknowledgement, that only the newest
    event is unacknowledged, and that the journal does not end below an acknowledged event. A
    commit checks the previous head once more after its append and reads its own event back
    before acknowledging it. Where any of this fails the operation raises (``JournalCorrupt``,
    or an ``OSError`` when journal or anchor cannot be read or written), nothing is published
    for the event that failed, and the replica does not advance past the last event that
    passed the checks (a replay applies the events before the failing one; a ``pump`` may have
    published for records it handled earlier in the same pass). So, with the anchor intact: a
    planner that starts on a journal that lost or had replaced any acknowledged event does not
    start; a running planner stops at its next replay when the head it holds is lost or
    replaced, and inside a commit when its new event is lost or replaced before the read-back
    or the previous head before the re-check;
  * linearisable admission: event ``n`` exists only by EXCLUSIVE CREATION of its name
    (``os.link``, the primitive the spool uses); a writer first replays everything up to
    ``n - 1``, decides against that state, and loses with no effect if another writer created
    ``n`` first, after which it replays and decides again (a bounded number of times, then
    ``JOURNAL_CONTENDED``: under contention a compatible dispatch can be refused that way and
    must be retried by its caller). Planners sharing the journal directory therefore cannot
    both admit colliding scopes;
  * reconstructable: a new ``Planner`` on the same journal replays to the same lineages, scope
    holders, issued requests, completed and blocked sets; ``fleet_status`` derives the fleet
    view from the journal alone, without a planner object;
  * tamper-evident, fail-closed: events are hash-chained (``prev``) and carry the sealed records
    they refer to; a gap, a broken chain, a bad seal or an event that ``_transition`` does not
    accept makes replay raise, and the planner then does nothing. Replay re-checks phases,
    scope, that a result answers the lineage's current work, that a verdict is the assigned
    verifier's on the outstanding request and artifact, and that a repair is the materialised
    one; of a journalled verification request it checks only the result seal, the task id and
    that the verifier is not the executor. It does not know a planner's verifier list, and,
    like every seal here, the chain is unkeyed: it detects corruption and accidents, it does
    not authenticate a writer. Whoever can write the journal directory can write a well-formed
    history.
Limits (what this is NOT):
  * the default ``MemoryJournal`` is per-process and volatile; durability and cross-process
    admission exist only with a ``DirJournal`` on a directory that offers atomic ``link``;
  * storage / failure model of the continuity check: event files may be lost or replaced (a
    deleted tail, a restored older copy of the journal directory, a rival history written
    after such a loss) while the anchor survives, and a planner process may die at any point
    (operating-system crash and power loss are not tested). Within that model, what is NOT
    prevented, precisely:
      - a RUNNING planner re-checks only its head. If an acknowledged event BELOW its head is
        lost or replaced, it keeps deciding against its replica, which is still the complete
        acknowledged history, so it admits nothing conflicting; but it goes on appending to and
        publishing from a journal that no new planner can open any more;
      - the publication windows of a commit, after its last check and before the publish. The
        NEW event lost or replaced after its read-back: the record is published (unless a
        rival's acknowledgement landed first, which raises); the next operation of every
        planner stops. The PREVIOUS head lost or replaced after the re-check: the record is
        published and the writer continues under the limit above, while other planners stop.
        In neither case was a conflicting admission possible afterwards;
      - ``recover``, and ``pump`` for a deferred record that turned out to be journalled,
        check once and then publish the pending record of every live lineage;
      - an event appended but never acknowledged (its writer died or its commit failed) is not
        acknowledged history: if it is lost, nothing was published for it; if it is still
        stored, the next replay by any planner, including the one whose commit failed,
        validates and applies it, whoever wrote it; before that planner appends or publishes
        it acknowledges the event with an ``ADOPTED`` repair record; the event's record is
        then published by ``recover``, or by ``pump`` when the record that led to it is still
        deferred.
    Outside the model, where conflicting work CAN be admitted: journal and anchor lost or
    rolled back TOGETHER, in whole or in part, or a journal of at most one event with its
    anchor lost. A new planner then accepts the shorter history. A planner that was running
    stops only if the head it holds is in the lost part; one whose head is at or below what
    survived continues, and with a partly lost anchor it can admit over a lost lineage that a
    new planner would refuse (a running planner probes only the next acknowledgement, a full
    open lists the anchor). The default anchor is a sibling directory, so a rollback of the
    common parent is such a case. Also outside: the anchor alone lost under a running planner
    on a plain ``DirJournal`` (a planner at the head continues against its complete replica;
    one event behind it adopts and re-anchors that event; two or more behind it fails closed;
    new planners refuse a journal of two or more events; an anchor that cannot be written
    raises ``OSError`` after the event was linked, with nothing published); a writer who
    rewrites journal and anchor. A ``StoreJournal`` (below) turns a missing, re-created or
    foreign anchor into a refusal for every planner, running or new, at any journal length;
  * loss of acknowledged continuity stays observable. The one repair done here is of the
    newest event's acknowledgement, when the event itself is still stored unchanged: by a
    planner that had seen it acknowledged (``RESTORED``, a definite loss) or, before it
    appends or publishes, by a planner that finds the newest event unacknowledged after a
    short wait (``ADOPTED``: its writer died between append and acknowledgement, or the
    acknowledgement was lost; not knowable). Either way a repair record is written to the
    anchor BEFORE the acknowledgement and is never removed here; ``repairs()`` lists them and
    a coordinator's status is ``DEGRADED`` from then on. An acknowledgement therefore either
    comes from the commit that appended its event or has a repair record next to it.
    Everything else fails closed: there is no other repair or re-anchoring tool, and nothing
    here clears a repair record; an operator has to restore the journal or judge the record;
  * ``recover`` must be called by whoever restarts a planner; ``Coordinator.tick`` does;
  * a full open lists the journal directory and reads every event; the directory fsync after
    an append is best effort (not available on Windows);
  * path overlap only: no semantic conflict detection (a generated file two lineages both
    rewrite, a whole-suite acceptance command);
  * the planner cannot observe a merge: an INTEGRATION_READY lineage holds its scope until
    ``release_scope`` is called with the merge revision, which the CALLER asserts; the planner
    does not verify it. Releasing a scope grants nothing;
  * no leases, heartbeats or executor assignment: a holder whose executor died stays a holder
    until its lineage reaches a releasing phase. A phase, a timeout or a caller-supplied
    revision is not proof that an old executor can no longer write or that integration
    happened: ``fail_execution`` and ``release_scope`` release on the caller's word;
  * the quarantine list is evidence in memory only and is not journalled;
  * it does not lift the fabric adapter's serial-dispatch rule, and no live run has exercised
    two lineages.

Store identity and coordinator (ATLAS-DEVQ-0008): ``StoreJournal`` is a ``DirJournal`` whose
journal and anchor directories are created once, carry one store id, and are afterwards only
attached, never created. ``Coordinator`` runs recover, pump and the preparation of further
compatible lineages as one ``tick`` on such a store and writes a journal-derived status file.
It refuses a journal without store identity and a transport that does not keep its records,
checks that every published WORK record is known to the journal (a witness that does not
depend on the anchor), and never dispatches an executor or hands a scope over. See both
classes for what they do not establish: in particular, no continuity boundary adequate for
live conflicting work is established here; a different filesystem for the anchor is evidence
against one failure mode, not proof of independent storage.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from project_atlas.orchestration.autonomy.dev_contracts import (
    ContractError,
    ResultRecord,
    Role,
    Verdict,
    VerdictRecord,
    VerificationRequest,
    WorkItem,
    make_verification_request,
    make_work,
    materialize_repair,
    same_identity,
    validate_identity,
    works_collide,
)
from project_atlas.orchestration.autonomy.dev_queue import (
    QueueItem,
    Selection,
    select_next,
    validate,
)
from project_atlas.orchestration.autonomy.dev_transport import (
    Channel,
    DevTransport,
    Record,
    decode,
    encode,
)


class Phase(StrEnum):
    DISPATCHED = "DISPATCHED"
    VERIFYING = "VERIFYING"
    REPAIR_DISPATCHED = "REPAIR_DISPATCHED"
    INTEGRATION_READY = "INTEGRATION_READY"
    OWNER_REQUIRED = "OWNER_REQUIRED"
    BLOCKED = "BLOCKED"


TERMINAL = frozenset({Phase.INTEGRATION_READY, Phase.OWNER_REQUIRED, Phase.BLOCKED})

SCOPE_HOLDING = frozenset(
    {Phase.DISPATCHED, Phase.VERIFYING, Phase.REPAIR_DISPATCHED, Phase.INTEGRATION_READY}
)
"""Phases in which a lineage still holds its write scope: every non-terminal phase, plus
INTEGRATION_READY. INTEGRATION_READY is terminal for the planner, but it means "a verified,
UNMERGED candidate exists"; releasing its scope there would admit a second lineage over the same
paths and move the conflict back to merge time. OWNER_REQUIRED and BLOCKED do not hold scope."""


@dataclass
class LineageState:
    lineage_root: str
    phase: Phase
    work: WorkItem
    result: ResultRecord | None = None
    history: list[str] = field(default_factory=list)
    reason: str = ""
    scope_released: str = ""  # merge revision the caller asserted in ``release_scope``
    dispatched_by: str = ""  # planner identity that journalled the DISPATCH
    last_seq: int = 0  # journal sequence number of the lineage's latest event


class PlannerError(ContractError):
    code = "DEV_PLANNER_REFUSED"


class JournalCorrupt(PlannerError):
    """The journal does not replay (gap, broken chain, bad record, illegal transition)."""


class JournalContended(PlannerError):
    """A commit lost the race for its sequence number too often; nothing was written."""


MAX_QUARANTINE = 1000  # bounded evidence: a flooding publisher cannot grow memory without limit
MAX_RAISES_PER_PASS = 64  # a transport that keeps raising without consuming must not loop forever
_REPAIR_SUFFIX = re.compile(r"-R[0-9]+$")  # reserved for planner-materialised repair tasks


JOURNAL_VERSION = 1
MAX_COMMIT_RETRIES = 16  # lost exclusive-create races before a commit gives up (never spins)
_EVENT_FILE = re.compile(r"[0-9]{12}\.json")
_ACK_FILE = re.compile(r"[0-9]{12}\.ack")
_REPAIR_FILE = re.compile(r"[0-9]{12}\.repair")
# An acknowledgement written by anyone but the commit that appended the event is a REPAIR of
# acknowledgement continuity and leaves a durable record:
RESTORED = "RESTORED"  # this planner had seen the acknowledgement; it was gone: a definite loss
ADOPTED = "ADOPTED"  # no acknowledgement appeared for the newest event: its writer died between
#                      append and acknowledgement, or the acknowledgement was lost (not knowable)
ADOPT_GRACE_TRIES = 5  # how long a live writer gets to acknowledge its own event before
ADOPT_GRACE_STEP = 0.01  # another planner adopts it (seconds per try)
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_EXECUTING = frozenset({Phase.DISPATCHED, Phase.REPAIR_DISPATCHED})
_LIVE = _EXECUTING | {Phase.VERIFYING}  # executing or being verified


class Journal(Protocol):
    def read(self, after: int) -> list[bytes]:
        """Raw events with sequence number > ``after``, contiguous and in order."""

    def append(self, seq: int, data: bytes) -> bool:
        """Create event ``seq`` iff it is the next one; False when it already exists."""

    def acknowledge(self, seq: int, digest: str) -> None:
        """Record that event ``seq`` with this sha256 is acknowledged history.

        Raises ``JournalCorrupt`` when the stored event is not that event (any more) or when
        ``seq`` is already acknowledged with a different digest; an ``OSError`` when the anchor
        cannot be written. Idempotent otherwise.
        """

    def acknowledged(self, seq: int) -> str | None:
        """The digest event ``seq`` was acknowledged with, or None when it is not acknowledged."""

    def high_water(self, at_least: int, *, full: bool) -> int:
        """A sequence number that is acknowledged and > ``at_least``, or ``at_least``.

        ``full`` asks for the highest acknowledged number (a listing); otherwise only
        ``at_least + 1`` is probed.
        """

    def check_head(self, seq: int, digest: str) -> None:
        """Raise ``JournalCorrupt`` unless stored event ``seq`` still has this digest."""

    def record_repair(self, seq: int, digest: str, kind: str, by: str) -> None:
        """Durably record, BEFORE the acknowledgement is written, that it is a repair.

        Never overwritten and never removed here: the first record for ``seq`` stands.
        """

    def repairs(self) -> tuple[dict[str, Any], ...]:
        """Every repair record, ordered by sequence number (empty when there are none)."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _drop_tmp(tmp: str) -> None:
    """Remove a temp name after its content was linked (or not) under the final name.

    Best effort: where a file cannot be unlinked while another handle on it is open (Windows,
    a reader of the final name), a leftover ``.tmp-`` name is harmless: listings ignore it.
    """
    for _ in range(5):
        try:
            os.unlink(tmp)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.002)


def _sync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):  # directory fsync is best effort (not on Windows)
        dfd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


class MemoryJournal:
    """Volatile, single-process journal (the default). Same contract, no durability.

    Its events live in this object and cannot be lost separately from it, so acknowledgement
    is the event list itself.
    """

    def __init__(self) -> None:
        self._events: list[bytes] = []

    def read(self, after: int) -> list[bytes]:
        return self._events[after:]

    def append(self, seq: int, data: bytes) -> bool:
        if seq != len(self._events) + 1:
            return False
        self._events.append(data)
        return True

    def acknowledge(self, seq: int, digest: str) -> None:
        self.check_head(seq, digest)

    def acknowledged(self, seq: int) -> str | None:
        return _sha(self._events[seq - 1]) if 1 <= seq <= len(self._events) else None

    def high_water(self, at_least: int, *, full: bool) -> int:
        return max(at_least, len(self._events))

    def check_head(self, seq: int, digest: str) -> None:
        if self.acknowledged(seq) != digest:
            raise JournalCorrupt(
                f"JOURNAL_DIVERGED:event {seq} is not the event this planner holds"
            )

    def record_repair(self, seq: int, digest: str, kind: str, by: str) -> None:
        raise JournalCorrupt("a memory journal has no separate acknowledgement to repair")

    def repairs(self) -> tuple[dict[str, Any], ...]:
        return ()


class DirJournal:
    """Durable journal: one immutable file per event, ``<root>/<seq:012d>.json``.

    ``append`` writes a temp file, fsyncs it and creates the final name with ``os.link``, which
    never overwrites: of several processes appending the same sequence number exactly one
    succeeds. Needs a directory with atomic ``link`` (local disk, a mounted volume).

    Acknowledgement anchor: ``<anchor>/<seq:012d>.ack`` holds the sha256 of event ``seq`` and is
    created the same way (exclusive, never overwritten) once the event is in the journal;
    ``<anchor>/<seq:012d>.repair`` records that the acknowledgement of ``seq`` was not written
    by the event's own commit (see ``record_repair``). The
    anchor is the witness that survives a loss of journal files: a journal that ends below the
    highest acknowledged number, or whose event differs from its acknowledgement, does not
    replay. ``anchor`` defaults to the sibling directory ``<root>.ack``; put it on storage that
    does not fail together with ``root`` to cover more than the loss of event files. Every
    planner on a journal must use the same anchor. The constructor creates a missing anchor
    directory. On a full open (a new planner, ``fleet_status``) a journal of two or more
    events with an empty or foreign anchor does not replay (``JOURNAL_UNANCHORED``); with at
    most one event it is indistinguishable from "nothing acknowledged yet". A planner that is
    already at the head does not notice an emptied anchor. IO errors on the anchor surface as
    ``OSError``.
    """

    def __init__(self, root: Path, *, anchor: Path | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.anchor = (
            self.root.with_name(self.root.name + ".ack") if anchor is None else Path(anchor)
        )
        self.anchor.mkdir(parents=True, exist_ok=True)

    _ignored: frozenset[str] = frozenset()  # names in either directory that are not events/acks

    def _names(self, directory: Path) -> list[str]:
        return [
            p.name
            for p in directory.iterdir()
            if not p.name.startswith(".tmp-") and p.name not in self._ignored
        ]

    def _path(self, seq: int) -> Path:
        return self.root / f"{seq:012d}.json"

    def _ack(self, seq: int) -> Path:
        return self.anchor / f"{seq:012d}.ack"

    def acknowledged(self, seq: int) -> str | None:
        try:
            digest = self._ack(seq).read_text(encoding="ascii")
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise JournalCorrupt(f"JOURNAL_CORRUPT:acknowledgement {seq} is unreadable") from exc
        if not _DIGEST.fullmatch(digest):
            raise JournalCorrupt(f"JOURNAL_CORRUPT:acknowledgement {seq} is not a digest")
        return digest

    def check_head(self, seq: int, digest: str) -> None:
        try:
            stored = _sha(self._path(seq).read_bytes())
        except FileNotFoundError:
            raise JournalCorrupt(
                f"JOURNAL_DIVERGED:event {seq}, which this planner holds, is gone"
            ) from None
        if stored != digest:
            raise JournalCorrupt(
                f"JOURNAL_DIVERGED:event {seq} is not the event this planner holds"
            )

    def acknowledge(self, seq: int, digest: str) -> None:
        self.check_head(seq, digest)  # read back: the stored event is (still) this event
        known = self.acknowledged(seq)
        if known is None:
            fd, tmp = tempfile.mkstemp(dir=self.anchor, prefix=".tmp-")
            try:
                with os.fdopen(fd, "w", encoding="ascii") as fh:
                    fh.write(digest)
                    fh.flush()
                    os.fsync(fh.fileno())
                with contextlib.suppress(FileExistsError):
                    os.link(tmp, self._ack(seq))  # exclusive: the first acknowledgement stands
            finally:
                _drop_tmp(tmp)
            _sync_dir(self.anchor)
            known = self.acknowledged(seq)
        if known != digest:
            raise JournalCorrupt(
                f"JOURNAL_DIVERGED:event {seq} is acknowledged as a different event"
            )

    def high_water(self, at_least: int, *, full: bool) -> int:
        if not full:
            return at_least + 1 if self._ack(at_least + 1).exists() else at_least
        names = self._names(self.anchor)
        acks = [n for n in names if _ACK_FILE.fullmatch(n)]
        odd = sorted(
            n
            for n in names
            if not (_ACK_FILE.fullmatch(n) or _REPAIR_FILE.fullmatch(n)) or int(n[:12]) < 1
        )
        if odd:
            raise JournalCorrupt(f"JOURNAL_CORRUPT:anchor holds something else: {odd[:3]}")
        return max((int(n[:12]) for n in acks), default=at_least)

    def record_repair(self, seq: int, digest: str, kind: str, by: str) -> None:
        if kind not in (RESTORED, ADOPTED) or not _DIGEST.fullmatch(digest):
            raise PlannerError("a repair record needs a kind and the event digest")
        body = json.dumps(
            {"v": 1, "seq": seq, "digest": digest, "kind": kind, "by": by}, sort_keys=True
        )
        fd, tmp = tempfile.mkstemp(dir=self.anchor, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            with contextlib.suppress(FileExistsError):  # exclusive: the first record stands
                os.link(tmp, self.anchor / f"{seq:012d}.repair")
        finally:
            _drop_tmp(tmp)
        _sync_dir(self.anchor)
        if not (self.anchor / f"{seq:012d}.repair").is_file():
            raise JournalCorrupt(f"JOURNAL_CORRUPT:repair record {seq} could not be written")

    def repairs(self) -> tuple[dict[str, Any], ...]:
        out: list[dict[str, Any]] = []
        for name in sorted(n for n in self._names(self.anchor) if _REPAIR_FILE.fullmatch(n)):
            try:
                rec = json.loads((self.anchor / name).read_text(encoding="utf-8"))
                if (
                    not isinstance(rec, dict)
                    or rec.get("seq") != int(name[:12])
                    or rec.get("kind") not in (RESTORED, ADOPTED)
                    or not isinstance(rec.get("by"), str)
                    or not _DIGEST.fullmatch(str(rec.get("digest")))
                ):
                    raise ValueError("not a repair record")
            except (OSError, ValueError) as exc:
                raise JournalCorrupt(f"JOURNAL_CORRUPT:repair record {name}: {exc}") from exc
            out.append({k: rec[k] for k in ("seq", "kind", "by", "digest")})
        return tuple(out)

    def read(self, after: int) -> list[bytes]:
        last = 0
        if after == 0:  # full open: nothing but event files, and (below) no gap before the last
            names = sorted(self._names(self.root))
            odd = [n for n in names if not _EVENT_FILE.fullmatch(n) or int(n[:12]) < 1]
            if odd:
                raise JournalCorrupt(f"JOURNAL_CORRUPT:directory is not events 1..k: {odd[:3]}")
            last = max((int(n[:12]) for n in names), default=0)
        out: list[bytes] = []
        seq = after + 1
        while True:
            try:
                out.append(self._path(seq).read_bytes())
            except FileNotFoundError:
                break
            seq += 1
        # events are never removed, so every event below one the listing saw must be readable;
        # checking it this way stays correct while other writers append during the listing
        if seq <= last:
            raise JournalCorrupt(f"JOURNAL_CORRUPT:directory is not events 1..k: missing {seq}")
        return out

    def append(self, seq: int, data: bytes) -> bool:
        final = self._path(seq)
        if final.exists() or (seq > 1 and not self._path(seq - 1).exists()):
            return False
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, final)  # atomic, refuses to overwrite: the linearisation point
            except FileExistsError:
                return False
        finally:
            _drop_tmp(tmp)
        _sync_dir(self.root)
        return True


STORE_MARKER = "STORE.json"
STORE_VERSION = 1
SEPARATE_FILESYSTEM = "SEPARATE_FILESYSTEM"
SAME_FILESYSTEM = "SAME_FILESYSTEM"


class StoreJournal(DirJournal):
    """A ``DirJournal`` whose journal and anchor directories carry one explicit store identity.

    A coordination store is CREATED once (``create``: both directories absent or empty; each
    gets a ``STORE.json`` marker with the same random store id) and afterwards only ATTACHED
    (``attach``: both directories and both markers must exist and agree). Nothing here creates
    a directory or a marker implicitly, so a journal or anchor that went missing is never
    mistaken for, or re-created as, a fresh start:
      * a missing or foreign anchor fails at attach and at every later read or
        acknowledgement (``STORE_IDENTITY``), also for a journal of zero or one event and
        also for a planner that is already running;
      * an anchor of another store, or the plain ``DirJournal`` default sibling, cannot be
        attached by accident.
    The identity is checked on every ``read`` (so on every replay), before every append and
    before every acknowledgement. An identity lost between a commit's append and its
    acknowledgement leaves the event linked, unacknowledged and unpublished. A store whose
    creation was interrupted after the first marker can be neither attached nor re-created;
    an operator has to remove it. Like every seal here the marker is unkeyed: it guards
    against loss, mix-up and re-creation, not against a writer who copies the marker, and it
    says nothing about the acknowledgement FILES: with the marker intact and the ``.ack``
    files gone, the plain ``DirJournal`` rules apply.

    ``boundary`` reports whether the two directories are on different filesystems
    (``st_dev``). Same filesystem means one snapshot or rollback of a common parent can take
    journal and anchor back together, which the continuity check cannot see. Different
    filesystems is evidence against that one failure, not proof of independent storage.
    """

    _ignored = frozenset({STORE_MARKER})

    def __init__(self, root: Path, anchor: Path, store_id: str) -> None:
        # deliberately not DirJournal.__init__: nothing is created here
        self.root = Path(root)
        self.anchor = Path(anchor)
        self.store_id = store_id
        self.check_store()

    @staticmethod
    def _marker(directory: Path, role: str) -> str:
        try:
            raw = json.loads((directory / STORE_MARKER).read_text(encoding="utf-8"))
            if raw["v"] != STORE_VERSION or raw["role"] != role:
                raise ValueError(f"marker is not a v{STORE_VERSION} {role} marker")
            store_id = raw["store"]
            if not isinstance(store_id, str) or not re.fullmatch(r"[0-9a-f]{32}", store_id):
                raise ValueError("store id is not 32 hex digits")
        except FileNotFoundError:
            raise JournalCorrupt(
                f"STORE_IDENTITY:{role} directory {directory} has no store marker"
            ) from None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise JournalCorrupt(
                f"STORE_IDENTITY:{role} marker in {directory} is unusable: {exc}"
            ) from exc
        return store_id

    def check_store(self) -> None:
        """Both directories still carry this store's marker; otherwise ``JournalCorrupt``."""
        for directory, role in ((self.root, "journal"), (self.anchor, "anchor")):
            found = self._marker(directory, role)
            if found != self.store_id:
                raise JournalCorrupt(
                    f"STORE_IDENTITY:{role} directory belongs to store {found}, not {self.store_id}"
                )

    @classmethod
    def create(cls, root: Path, anchor: Path) -> StoreJournal:
        """Create a NEW, empty store. Refuses anything that already holds something."""
        root, anchor = Path(root), Path(anchor)
        a, b = root.resolve(), anchor.resolve()
        if a == b or a.is_relative_to(b) or b.is_relative_to(a):
            raise PlannerError("STORE_LAYOUT:journal and anchor must be separate directories")
        for directory in (root, anchor):
            if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
                raise PlannerError(
                    f"STORE_EXISTS:{directory} is not empty; a store is created once and then "
                    "attached, never re-created over what is there"
                )
        store_id = uuid.uuid4().hex
        for directory, role in ((root, "journal"), (anchor, "anchor")):
            directory.mkdir(parents=True, exist_ok=True)
            body = json.dumps({"v": STORE_VERSION, "role": role, "store": store_id}, sort_keys=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(body)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.link(tmp, directory / STORE_MARKER)  # exclusive: never overwrites a marker
            finally:
                _drop_tmp(tmp)
            _sync_dir(directory)
        return cls(root, anchor, store_id)

    @classmethod
    def attach(cls, root: Path, anchor: Path) -> StoreJournal:
        """Attach to an EXISTING store. Creates nothing; both markers must exist and agree."""
        root, anchor = Path(root), Path(anchor)
        return cls(root, anchor, cls._marker(root, "journal"))

    @property
    def boundary(self) -> str:
        try:
            same = os.stat(self.root).st_dev == os.stat(self.anchor).st_dev
        except OSError as exc:
            raise JournalCorrupt(
                f"STORE_IDENTITY:store directories are not readable: {exc}"
            ) from exc
        return SAME_FILESYSTEM if same else SEPARATE_FILESYSTEM

    def read(self, after: int) -> list[bytes]:
        self.check_store()
        return super().read(after)

    def append(self, seq: int, data: bytes) -> bool:
        self.check_store()
        return super().append(seq, data)

    def acknowledge(self, seq: int, digest: str) -> None:
        self.check_store()
        super().acknowledge(seq, digest)


@dataclass
class FleetState:
    """Replica of the journal: everything here is derived by replay, nothing is authoritative."""

    seq: int = 0
    head: str = ""  # sha256 of the latest event's bytes ("" before the first event)
    lineages: dict[str, LineageState] = field(default_factory=dict)
    by_task: dict[str, str] = field(default_factory=dict)  # task_id -> lineage_root
    works: dict[str, WorkItem] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    blocked: dict[str, str] = field(default_factory=dict)
    issued: dict[str, VerificationRequest] = field(default_factory=dict)
    seals: set[str] = field(default_factory=set)  # result/verdict seals already journalled
    acked: int = 0  # highest sequence number this replica has SEEN acknowledged


def holds_scope(st: LineageState) -> bool:
    return st.phase in SCOPE_HOLDING and not st.scope_released


def _record[T: Record](ev: dict[str, Any], key: str, kind: type[T]) -> T:
    rec = decode(ev[key])  # verifies the seal
    if not isinstance(rec, kind):
        raise PlannerError(f"journal field {key} is not a {kind.__name__}")
    return rec


def _transition(state: FleetState, ev: dict[str, Any], raw: bytes) -> Callable[[], None]:
    """Validate one journal event against ``state``; return the thunk that applies it.

    Everything is checked BEFORE the thunk exists, so a refused event changes nothing. Used both
    for replay and, before the append, for the event a live planner is about to write: an event
    that could not be replayed is never written.
    """
    if ev.get("v") != JOURNAL_VERSION:
        raise PlannerError(f"unsupported journal version {ev.get('v')!r}")
    if type(ev.get("seq")) is not int or ev["seq"] != state.seq + 1 or ev.get("prev") != state.head:
        raise PlannerError("journal sequence or hash chain is broken")
    kind, root, planner = ev["event"], ev["root"], ev["planner"]
    if not isinstance(root, str) or not isinstance(planner, str):
        raise PlannerError("journal event root/planner must be strings")
    seq, head = state.seq + 1, _sha(raw)
    steps: list[Callable[[], None]] = []
    st = state.lineages.get(root)

    def note(line: str) -> None:
        assert st is not None
        st.history.append(line)
        st.last_seq = seq

    if kind == "DISPATCH":
        work = _record(ev, "work", WorkItem)
        if work.lineage_root != root or work.task_id != root:
            raise PlannerError("DISPATCH work is not the root work of its lineage")
        if root in state.lineages or root in state.by_task:
            raise PlannerError("lineage/task id already in use")
        if _REPAIR_SUFFIX.search(root):
            raise PlannerError("task id suffix -R<n> is reserved for repair tasks")
        for other in sorted(state.lineages):
            holder = state.lineages[other]
            if holds_scope(holder):
                pairs = works_collide(work, holder.work)
                if pairs:
                    raise PlannerError(
                        f"SCOPE_COLLISION:{other}:" + ",".join(f"{a}|{b}" for a, b in pairs)
                    )

        def do_dispatch() -> None:
            state.lineages[root] = LineageState(
                root,
                Phase.DISPATCHED,
                work,
                history=[f"DISPATCHED:{work.task_id}"],
                dispatched_by=planner,
                last_seq=seq,
            )
            state.by_task[work.task_id] = root
            state.works[work.task_id] = work

        steps.append(do_dispatch)
    elif st is None:
        raise PlannerError(f"{kind} for an unknown lineage {root}")
    elif kind == "RESULT":
        res = _record(ev, "result", ResultRecord)
        req = _record(ev, "request", VerificationRequest)
        w = st.work
        if st.phase not in _EXECUTING or res.work_seal != w.seal:
            raise PlannerError("RESULT does not answer the lineage's dispatched work")
        if (res.task_id, res.execution_id, res.repository, res.base_revision) != (
            w.task_id,
            w.execution_id,
            w.repository,
            w.base_revision,
        ):
            raise PlannerError("RESULT identity/repository/base does not match the work")
        if req.result_seal != res.seal or req.task_id != res.task_id:
            raise PlannerError("RESULT request does not cover the journalled result")
        if same_identity(req.verifier_identity, res.executor_identity):
            raise PlannerError("RESULT request assigns the executor as its own verifier")

        def do_result() -> None:
            state.issued[res.task_id] = req
            state.seals.add(res.seal)
            st.result = res
            st.phase = Phase.VERIFYING
            note(f"VERIFYING:{res.task_id}:{req.verifier_identity}")

        steps.append(do_result)
    elif kind in ("READY", "REPAIR"):
        ver = _record(ev, "verdict", VerdictRecord)
        issued = state.issued.get(ver.task_id)
        held = st.result
        if (
            st.phase is not Phase.VERIFYING
            or held is None
            or issued is None
            or ver.task_id != st.work.task_id
            or ver.request_seal != issued.seal
        ):
            raise PlannerError(f"{kind} without the outstanding verification it answers")
        if (ver.verifier_identity, ver.execution_id, ver.result_revision, ver.result_tree) != (
            issued.verifier_identity,
            issued.execution_id,
            held.result_revision,
            held.result_tree,
        ):
            raise PlannerError(f"{kind} verdict is not the assigned verifier's on this artifact")
        if kind == "READY":
            if ver.verdict is not Verdict.PASS:
                raise PlannerError("READY needs a PASS verdict")

            def do_ready() -> None:
                state.seals.add(ver.seal)
                st.phase = Phase.INTEGRATION_READY
                note(f"INTEGRATION_READY:{ver.task_id}")
                state.completed.add(root)

            steps.append(do_ready)
        else:
            rw = _record(ev, "work", WorkItem)
            if rw.lineage_root != root or rw.task_id in state.by_task:
                raise PlannerError("REPAIR work is foreign or its task id is already in use")
            if (rw.repository, rw.allowed_paths) != (st.work.repository, st.work.allowed_paths):
                raise PlannerError("REPAIR work may not change the lineage's repository or scope")
            if ver.verdict is Verdict.PASS:
                raise PlannerError("REPAIR needs a non-PASS verdict")
            expected = materialize_repair(st.work, ver, result=held).repair_work
            if expected is None or expected.seal != rw.seal:
                raise PlannerError("REPAIR work is not the repair this verdict materialises")

            def do_repair() -> None:
                state.seals.add(ver.seal)
                state.works[rw.task_id] = rw
                state.by_task[rw.task_id] = root
                st.work, st.result = rw, None
                st.phase = Phase.REPAIR_DISPATCHED
                note(f"REPAIR_DISPATCHED:{rw.task_id}")

            steps.append(do_repair)
    elif kind == "TERMINAL":
        phase, reason = Phase(ev["phase"]), ev["reason"]
        if phase not in (Phase.BLOCKED, Phase.OWNER_REQUIRED) or not isinstance(reason, str):
            raise PlannerError("TERMINAL must be BLOCKED or OWNER_REQUIRED with a reason")
        if st.phase in TERMINAL:
            raise PlannerError(f"lineage {root} is already terminal ({st.phase.value})")
        evidence = ev.get("evidence", "")
        if not isinstance(evidence, str):
            raise PlannerError("TERMINAL evidence must be a string")

        def do_terminal() -> None:
            if evidence:
                state.seals.add(evidence)
            st.phase, st.reason = phase, reason
            note(f"{phase.value}:{reason}")
            state.blocked[root] = f"{phase.value}:{reason}"

        steps.append(do_terminal)
    elif kind == "RELEASE":
        revision = ev["evidence"]
        if st.phase is not Phase.INTEGRATION_READY or st.scope_released:
            raise PlannerError("only an unreleased INTEGRATION_READY lineage can release scope")
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise PlannerError("RELEASE needs the 40-hex merge revision as evidence")

        def do_release() -> None:
            st.scope_released = revision
            note(f"SCOPE_RELEASED:{revision}")

        steps.append(do_release)
    else:
        raise PlannerError(f"unknown journal event {kind!r}")

    def apply() -> None:
        for step in steps:
            step()
        state.seq, state.head = seq, head

    return apply


def replay(journal: Journal, state: FleetState) -> int:
    """Apply every journal event ``state`` has not seen; returns how many. Fail-closed.

    Continuity is checked on every call, before the caller may decide or publish anything: the
    head check before anything is applied, the others per event as it is applied, the
    truncation check after the batch. Events that passed stay applied when a later one fails:
      * the event ``state`` already holds as its HEAD must still be stored with the same digest
        (events below the head are not re-read by a planner that already applied them);
      * every event applied must match its acknowledgement where one exists, and every applied
        event that has a successor must HAVE one (a writer acknowledges all it replayed before
        it appends, so only the newest event can be unacknowledged; a journal whose anchor is
        missing, empty or someone else's stops at the first event that has a successor);
      * the journal may not end below an acknowledged sequence number: the highest one on a
        full open (a listing of the anchor), the next one otherwise.
    Replay writes nothing. It records in ``state.acked`` the highest event it saw acknowledged;
    acknowledging the newest event when its writer did not is a repair and is done, with a
    durable record, by ``Planner._anchor_head`` before that planner appends or publishes.
    """
    full = state.seq == 0
    if not full:
        journal.check_head(state.seq, state.head)
    n = 0
    while True:
        batch = journal.read(state.seq)
        for i, raw in enumerate(batch):
            seq = state.seq + 1
            try:
                ev = json.loads(raw)
                if not isinstance(ev, dict):
                    raise PlannerError("event is not an object")
                apply = _transition(state, ev, raw)
            except (ContractError, ValueError, KeyError, TypeError, RecursionError) as exc:
                raise JournalCorrupt(f"JOURNAL_CORRUPT:event {seq}: {exc}") from exc
            digest = _sha(raw)
            known = journal.acknowledged(seq)
            if known is None and i + 1 < len(batch):
                raise JournalCorrupt(
                    f"JOURNAL_UNANCHORED:event {seq} has a successor but no acknowledgement"
                )
            if known not in (None, digest):
                raise JournalCorrupt(
                    f"JOURNAL_DIVERGED:event {seq} is acknowledged as a different event"
                )
            apply()
            if known is not None:
                state.acked = seq
            n += 1
        mark = journal.high_water(state.seq, full=full)
        if mark <= state.seq:
            return n
        if not journal.read(state.seq):
            # acknowledged beyond the journal and nothing more to read: the tail is gone
            raise JournalCorrupt(
                f"JOURNAL_TRUNCATED:event {mark} is acknowledged but the journal ends at "
                f"{state.seq}"
            )
        # otherwise a writer got ahead while we read (event before acknowledgement): go on


def fleet_status(journal: Journal) -> tuple[dict[str, Any], ...]:
    """Fleet view derived from the journal alone: one row per lineage, ordered by root.

    A read-only projection for observation. It is not authority and not a scheduler; a row's
    ``holds_scope`` is what ``dispatch`` will compare a new work item against.
    """
    state = FleetState()
    replay(journal, state)
    return tuple(
        {
            "lineage_root": root,
            "phase": st.phase.value,
            "reason": st.reason,
            "task_id": st.work.task_id,
            "execution_id": st.work.execution_id,
            "work_seal": st.work.seal,
            "repository": st.work.repository,
            "base_revision": st.work.base_revision,
            "allowed_paths": st.work.allowed_paths,
            "holds_scope": holds_scope(st),
            "scope_released": st.scope_released,
            "dispatched_by": st.dispatched_by,
            "last_seq": st.last_seq,
        }
        for root, st in sorted(state.lineages.items())
    )


class Planner:
    def __init__(
        self,
        transport: DevTransport,
        *,
        identity: str,
        verifier_identities: tuple[str, ...],
        journal: Journal | None = None,
    ) -> None:
        if not verifier_identities or not isinstance(verifier_identities, tuple | list):
            raise PlannerError("at least one verifier identity is required (as a tuple)")
        if not isinstance(identity, str) or not all(
            isinstance(v, str) for v in verifier_identities
        ):
            raise PlannerError("identities must be strings")
        try:
            validate_identity(identity)
            for v in verifier_identities:
                validate_identity(v)
        except ValueError as exc:
            raise PlannerError(f"non-canonical identity: {exc}") from exc
        canon = [v.strip().casefold() for v in verifier_identities]
        if len(set(canon)) != len(canon):
            raise PlannerError("duplicate (or case-variant) verifier identities")
        if any(same_identity(identity, v) for v in verifier_identities):
            raise PlannerError("the planner may not be one of its own verifiers")
        self.transport = transport
        self.identity = identity
        self.verifiers = tuple(verifier_identities)  # own copy: later mutation cannot bypass checks
        self.journal: Journal = MemoryJournal() if journal is None else journal
        self.state = FleetState()
        # the replica's containers, under their established names (same objects, never rebound)
        self.lineages = self.state.lineages
        self._by_task = self.state.by_task
        self._works = self.state.works
        self.completed = self.state.completed
        self.blocked = self.state.blocked
        self.issued = self.state.issued  # task_id -> the request issued for it
        self.quarantined: list[tuple[str, str, str]] = []  # (channel, seal, reason)
        # records already claimed from the transport that could not be decided (contention,
        # journal IO error, a journal that does not replay): retried by a later pump once the
        # cause is gone, never quarantined for that
        self.deferred: list[tuple[Channel, Any]] = []
        self.sync()  # a restarted planner starts from what the journal says, or not at all

    # -- journal ---------------------------------------------------------------------------
    def sync(self) -> int:
        """Replay journal events written since the last look (by this or another planner).

        If the acknowledgement of this planner's head has DISAPPEARED (it had seen it), that is
        a definite loss of acknowledged continuity: the event itself is still stored unchanged
        (replay checked that), so continuity can be re-established, but only with a durable
        ``RESTORED`` repair record written first. The loss stays observable.
        """
        n = replay(self.journal, self.state)
        st = self.state
        if st.seq and st.acked >= st.seq and self.journal.acknowledged(st.seq) is None:
            self.journal.record_repair(st.seq, st.head, RESTORED, self.identity)
            self.journal.acknowledge(st.seq, st.head)
        return n

    def _anchor_head(self) -> None:
        """Make sure the head is acknowledged before this planner appends or publishes.

        An event is acknowledged by the commit that appended it. If the newest event has no
        acknowledgement, its writer may be about to write it (wait briefly), may have died
        between append and acknowledgement, or the acknowledgement may have been lost: this
        planner cannot tell the last two apart. It adopts the event (it validated it on
        replay) and writes an ``ADOPTED`` repair record first, so that an acknowledgement
        which did not come from the event's own commit never looks like one that did.
        """
        st = self.state
        if not st.seq or st.acked >= st.seq:
            return
        known = self.journal.acknowledged(st.seq)
        for _ in range(ADOPT_GRACE_TRIES):
            if known is not None:
                break
            time.sleep(ADOPT_GRACE_STEP)
            known = self.journal.acknowledged(st.seq)
        if known is None:
            self.journal.record_repair(st.seq, st.head, ADOPTED, self.identity)
            self.journal.acknowledge(st.seq, st.head)
        elif known != st.head:
            raise JournalCorrupt(
                f"JOURNAL_DIVERGED:event {st.seq} is acknowledged as a different event"
            )
        st.acked = st.seq

    def _commit(self, decide: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Decide against the current journal state and append the ONE resulting event.

        ``decide`` runs after a fresh replay and raises to refuse. If another writer takes the
        sequence number first, nothing was written: replay and decide again, a bounded number
        of times. After the append the previous head is checked again and the new event is
        acknowledged (read back and anchored) before the in-memory state changes and before
        this returns, so the caller publishes only for an event that is acknowledged history.
        If that fails this raises: nothing is published and the replica does not advance. The
        event file itself may already exist then, unacknowledged; a later replay applies it if
        it is still a legal transition, or the journal stops replaying.
        """
        for _ in range(MAX_COMMIT_RETRIES):
            self.sync()
            self._anchor_head()  # a writer acknowledges what it builds on before it appends
            ev = {
                "v": JOURNAL_VERSION,
                "seq": self.state.seq + 1,
                "prev": self.state.head,
                "planner": self.identity,
                **decide(),
            }
            raw = json.dumps(ev, sort_keys=True).encode()
            apply = _transition(self.state, ev, raw)
            if self.journal.append(ev["seq"], raw):
                if self.state.seq:  # the event this one was decided on is still that event
                    self.journal.check_head(self.state.seq, self.state.head)
                self.journal.acknowledge(ev["seq"], _sha(raw))
                apply()
                self.state.acked = self.state.seq
                return ev
        raise JournalContended("JOURNAL_CONTENDED: could not append after bounded retries")

    def fleet_status(self) -> tuple[dict[str, Any], ...]:
        """``fleet_status`` of this planner's journal (derived from the journal, not from self)."""
        return fleet_status(self.journal)

    def recover(self) -> list[str]:
        """After a restart: finish what a crash may have left between journal and transport.

        Re-publishes the current work of every executing lineage and the issued request of every
        verifying one (idempotent: an already published record is a no-op), then re-feeds RESULT
        and VERDICT records this identity had claimed from a transport that keeps them
        (``claimed_records``) but that are not in the journal yet. Returns what it did.
        """
        done = self._republish()
        claimed = getattr(self.transport, "claimed_records", None)
        if claimed is not None:
            handlers: tuple[tuple[Channel, Callable[[Any], None]], ...] = (
                (Channel.RESULT, self._on_result),
                (Channel.VERDICT, self._on_verdict),
            )
            for channel, handler in handlers:
                for rec in claimed(channel, identity=self.identity):
                    if rec.seal in self.state.seals:
                        continue
                    before = self.state.seq
                    self._guarded(channel.value, rec.seal, handler, rec)
                    if self.state.seq != before:
                        done.append(f"REPLAYED:{channel.value}:{rec.seal}")
        return done

    def _republish(self) -> list[str]:
        """Publish the pending record of every live lineage (idempotent). Replays first."""
        self.sync()  # continuity first: nothing is published from a history that does not replay
        self._anchor_head()  # and nothing is published for an event that is not acknowledged
        done: list[str] = []
        for root, st in sorted(self.lineages.items()):
            rec: Record | None = None
            if st.phase in _EXECUTING:
                rec = st.work
            elif st.phase is Phase.VERIFYING:
                rec = self.issued.get(st.work.task_id)
            if rec is not None and self.transport.publish(rec):
                done.append(f"REPUBLISHED:{root}:{rec.KIND.value}")
        return done

    # -- selection / dispatch -------------------------------------------------------------
    def in_flight(self) -> frozenset[str]:
        """Lineage roots (task ids) whose phase is scope-holding (see ``SCOPE_HOLDING``)."""
        return frozenset(r for r, st in self.lineages.items() if holds_scope(st))

    def scope_holders(self) -> tuple[WorkItem, ...]:
        """The CURRENT sealed work of every scope-holding lineage, ordered by lineage root.

        For a lineage in repair this is its current repair work, which carries the lineage's
        original ``allowed_paths`` (a journalled repair may not change scope). The replica as of
        the last ``sync``.
        """
        return tuple(st.work for _, st in sorted(self.lineages.items()) if holds_scope(st))

    def select(self, items: list[QueueItem]) -> Selection:
        """Next ranked admissible item, skipping tasks that are in flight (``IN_FLIGHT``).

        Selection does not look at write scopes (a ``QueueItem`` carries none); a selected item
        can still be refused by ``dispatch`` with ``SCOPE_COLLISION``.
        """
        self.sync()
        return select_next(
            items, completed=self.completed, blocked=self.blocked, in_flight=self.in_flight()
        )

    def dispatch(
        self,
        item: QueueItem,
        *,
        execution_ordinal: int = 1,
        live_limit: int | None = None,
        retain_terminal_scopes: bool = False,
        **work_fields: object,
    ) -> WorkItem:
        """Materialize + publish the sealed work for an admissible queue item.

        Before anything is published or recorded the sealed work is compared with the current
        work of every scope holder (``works_collide``). On an overlap this raises ``PlannerError``
        ``SCOPE_COLLISION:<holder lineage root>:<new>|<held>[,<new>|<held>...]`` for the first
        colliding holder in lineage-root order, with all of its colliding pairs as normalised
        comparison keys in sorted order. Nothing is published, no state changes, and the task id
        stays usable for a later dispatch. A holder whose seal no longer verifies is an error.
        ``live_limit`` (optional) refuses with ``LIVE_LIMIT`` when that many lineages are
        already executing or being verified. ``retain_terminal_scopes`` (optional) also
        refuses, with ``SCOPE_RETAINED``, a work item that overlaps the last work of ANY
        lineage that no longer holds its scope: BLOCKED, OWNER_REQUIRED, or INTEGRATION_READY
        and released with ``release_scope``. Those end the lineage or record a caller's
        assertion; none is evidence that an executor stopped writing, that a result branch is
        gone or that a candidate was merged.
        The decision is taken against the journal and committed as its next event, so it also
        holds against other planners on the same journal. The DISPATCH event is written before
        the work is published; if publishing fails the lineage is DISPATCHED and ``recover``
        publishes it. See the module docstring for what this check does not cover.
        """
        if execution_ordinal < 1:
            raise PlannerError("execution_ordinal must be >= 1")
        if _REPAIR_SUFFIX.search(item.task_id):
            raise PlannerError("task id suffix -R<n> is reserved for repair tasks")
        reserved = {
            "task_id",
            "execution_id",
            "lineage_root",
            "required_role",
            "max_attempts",
        } & set(work_fields)
        if reserved:
            raise PlannerError(f"work_fields may not override reserved keys: {sorted(reserved)}")

        def decide() -> dict[str, Any]:
            # no in-flight filter here, so a repeated dispatch of a live task id keeps its
            # existing refusal below
            sel = select_next([item], completed=self.completed, blocked=self.blocked)
            if sel.selected is None:
                raise PlannerError(f"task not admissible: {sel.skipped}")
            if item.task_id in self.lineages or item.task_id in self._by_task:
                raise PlannerError("lineage/task id already in use")
            if live_limit is not None:
                # decided against the journal like the scope check, so the bound holds across
                # every planner on this journal, not only for this object's view
                live = sum(1 for st in self.lineages.values() if st.phase in _LIVE)
                if live >= live_limit:
                    raise PlannerError(f"LIVE_LIMIT:{live} lineages are live, limit {live_limit}")
            work = make_work(
                task_id=item.task_id,
                execution_id=f"{item.task_id}-E{execution_ordinal}",
                lineage_root=item.task_id,
                required_role=Role.IMPLEMENTER,
                max_attempts=item.max_attempts,
                **work_fields,
            )
            for holder in self.scope_holders():
                pairs = works_collide(work, holder)
                if pairs:
                    raise PlannerError(
                        f"SCOPE_COLLISION:{holder.lineage_root}:"
                        + ",".join(f"{a}|{b}" for a, b in pairs)
                    )
            if retain_terminal_scopes:
                for root, st in sorted(self.lineages.items()):
                    if not holds_scope(st):  # BLOCKED, OWNER_REQUIRED, or released
                        pairs = works_collide(work, st.work)
                        if pairs:
                            raise PlannerError(
                                f"SCOPE_RETAINED:{root}:" + ",".join(f"{a}|{b}" for a, b in pairs)
                            )
            return {"event": "DISPATCH", "root": item.task_id, "work": encode(work)}

        self._commit(decide)
        work = self.lineages[item.task_id].work
        self.transport.publish(work)
        return work

    # -- pump -------------------------------------------------------------------------------
    def pump(self) -> int:
        """Consume the available results and verdicts once each; returns records processed.

        Starts with a journal replay. Raises ``JournalCorrupt`` when the journal does not replay
        or continuity cannot be established, there or later in the pass, and re-raises an
        ``OSError`` from the journal or from publishing; a record claimed by then and not yet
        journalled is kept in ``deferred``. An error from the transport's ``claim`` is
        quarantined instead, at most ``MAX_RAISES_PER_PASS`` times per channel and pass.
        """
        self.sync()
        n = 0
        handlers: dict[Channel, Callable[[Any], None]] = {
            Channel.RESULT: self._on_result,
            Channel.VERDICT: self._on_verdict,
        }
        for _ in range(len(self.deferred)):  # claimed earlier, not journalled then: first
            channel, rec = self.deferred.pop(0)  # one at a time: a raise keeps the rest
            if rec.seal not in self.state.seals:
                self._guarded(channel.value, rec.seal, handlers[channel], rec)
                n += 1
            else:  # journalled after all (the append raised after it had linked the event)
                self._republish()
        for channel, handler in handlers.items():
            raises = 0  # per channel: a flooded RESULT channel must not starve VERDICT
            while True:
                try:
                    rec = self.transport.claim(channel, role=Role.PLANNER, identity=self.identity)
                except (ContractError, OSError) as exc:  # poisoned wire: rejected once / IO trouble
                    self._quarantine(channel.value, "UNDECODABLE", str(exc))
                    n += 1
                    raises += 1
                    if raises >= MAX_RAISES_PER_PASS:
                        break  # resume on the next pump; never spin on a non-consuming transport
                    continue
                if rec is None:
                    break
                self._guarded(channel.value, rec.seal, handler, rec)
                n += 1
        return n

    def _guarded(self, channel: str, seal: str, fn: Callable[[Any], None], rec: Any) -> None:
        """A record the planner REFUSES is quarantined and the pass goes on to the next record.

        A record that could not be decided (the commit lost the append race too often, an
        ``OSError`` was raised before its event was journalled, or the journal no longer
        replays) is kept in ``deferred`` for a later pump instead of being quarantined. An
        ``OSError`` or ``JournalCorrupt`` is re-raised after that and ends the pass.
        ``deferred`` is in memory only.
        """
        try:
            fn(rec)
        except JournalContended:
            self.deferred.append((Channel(channel), rec))
        except JournalCorrupt:  # the journal stopped replaying mid-pass: stop, keep the record
            self.deferred.append((Channel(channel), rec))
            raise
        except ContractError as exc:
            self._quarantine(channel, seal, str(exc))
        except OSError:
            if seal not in self.state.seals:  # not journalled: the record is still undecided
                self.deferred.append((Channel(channel), rec))
            raise

    def _quarantine(self, channel: str, seal: str, reason: str) -> None:
        self.quarantined.append((channel, seal, reason))
        if len(self.quarantined) > MAX_QUARANTINE:
            del self.quarantined[: len(self.quarantined) - MAX_QUARANTINE]

    def _lineage_for(self, task_id: str) -> LineageState:
        root = self._by_task.get(task_id)
        if root is None:
            raise PlannerError(f"unknown task {task_id}")
        return self.lineages[root]

    def _on_result(self, res: ResultRecord) -> None:
        def decide() -> dict[str, Any]:
            st = self._lineage_for(res.task_id)
            work = st.work
            if st.phase not in _EXECUTING or res.task_id != work.task_id:
                raise PlannerError("result is not expected in this phase / for the current work")
            if res.work_seal != work.seal:
                raise PlannerError("result does not answer the dispatched work")
            if (res.execution_id, res.repository, res.base_revision) != (
                work.execution_id,
                work.repository,
                work.base_revision,
            ):
                raise PlannerError(
                    "result identity/repository/base does not match the dispatched work"
                )
            others = [v for v in self.verifiers if not same_identity(v, res.executor_identity)]
            if not others:
                return self._terminal(st, Phase.BLOCKED, "NO_INDEPENDENT_VERIFIER", res.seal)
            # deterministic (no PYTHONHASHSEED dependence): stable index from the task id digest
            verifier = others[
                int(hashlib.sha256(res.task_id.encode()).hexdigest(), 16) % len(others)
            ]
            req = make_verification_request(work, res, verifier_identity=verifier)
            return {
                "event": "RESULT",
                "root": st.lineage_root,
                "result": encode(res),
                "request": encode(req),
            }

        ev = self._commit(decide)
        if ev["event"] == "RESULT":
            self.transport.publish(self.issued[res.task_id])

    def _on_verdict(self, ver: VerdictRecord) -> None:
        def decide() -> dict[str, Any]:
            st = self._lineage_for(ver.task_id)
            work = self._works[ver.task_id]
            res = st.result
            if res is None or st.phase is not Phase.VERIFYING:
                raise PlannerError("verdict without an outstanding verification")
            if ver.task_id != st.work.task_id:
                raise PlannerError("verdict is for a superseded work item of this lineage")
            req = self.issued.get(ver.task_id)
            if req is None or ver.request_seal != req.seal or req.result_seal != res.seal:
                raise PlannerError("verdict does not answer the issued verification request")
            if ver.verifier_identity != req.verifier_identity or same_identity(
                ver.verifier_identity, res.executor_identity
            ):
                raise PlannerError("verdict is not from the assigned independent verifier")
            if ver.execution_id != req.execution_id:
                raise PlannerError("verdict execution identity mismatch")
            if ver.result_revision != res.result_revision or ver.result_tree != res.result_tree:
                raise PlannerError("verdict judged a different artifact")
            root = st.lineage_root
            if ver.verdict is Verdict.PASS:
                return {"event": "READY", "root": root, "verdict": encode(ver)}
            decision = materialize_repair(work, ver, result=res)
            if decision.action == "REPAIR":
                assert decision.repair_work is not None
                rw = decision.repair_work
                if rw.task_id in self._by_task:
                    return self._terminal(st, Phase.BLOCKED, "REPAIR_TASK_ID_COLLISION", ver.seal)
                return {
                    "event": "REPAIR",
                    "root": root,
                    "verdict": encode(ver),
                    "work": encode(rw),
                }
            phase = Phase.OWNER_REQUIRED if decision.action == "OWNER_REQUIRED" else Phase.BLOCKED
            return self._terminal(st, phase, decision.reason, ver.seal)

        ev = self._commit(decide)
        if ev["event"] == "REPAIR":
            self.transport.publish(self.lineages[ev["root"]].work)

    def fail_execution(self, task_id: str, reason: str) -> None:
        """The caller reports that the remote execution failed (no result): block that lineage.

        Only for the CURRENT work of a lineage that is executing (DISPATCHED or
        REPAIR_DISPATCHED). A lineage that already has a result, is INTEGRATION_READY or is
        terminal is refused, and so is a superseded task id: a wrong-phase or stale failure
        report does not release a scope. That is all this guards. The planner does not confirm
        that the remote executor has stopped or can no longer write; the scope is released on
        the caller's word, so a caller must have established that (or fenced the executor)
        before it calls this.
        """

        def decide() -> dict[str, Any]:
            st = self._lineage_for(task_id)
            if st.phase not in _EXECUTING or task_id != st.work.task_id:
                raise PlannerError(
                    f"fail_execution refused: {task_id} is not the executing work of its "
                    f"lineage (phase {st.phase.value}, current work {st.work.task_id})"
                )
            return self._terminal(st, Phase.BLOCKED, f"EXECUTION_FAILED:{reason}")

        self._commit(decide)

    def release_scope(self, lineage_root: str, *, merged_revision: str) -> None:
        """Release the scope of an INTEGRATION_READY lineage on the caller's merge assertion.

        ``merged_revision`` (40-hex) is recorded as the evidence the CALLER asserts; the planner
        cannot observe a merge and does not verify it, so the revision is not proof that
        integration occurred. This is not a merge, grants nothing and changes no phase: it only
        stops the lineage from holding its write scope.
        """

        def decide() -> dict[str, Any]:
            if lineage_root not in self.lineages:
                raise PlannerError(f"unknown lineage {lineage_root}")
            return {"event": "RELEASE", "root": lineage_root, "evidence": merged_revision}

        self._commit(decide)

    @staticmethod
    def _terminal(
        st: LineageState, phase: Phase, reason: str, evidence: str = ""
    ) -> dict[str, Any]:
        return {
            "event": "TERMINAL",
            "root": st.lineage_root,
            "phase": phase.value,
            "reason": reason,
            "evidence": evidence,
        }


MAX_LIVE_DEFAULT = 2
STATUS_VERSION = 1


class Coordinator:
    """One recoverable coordination step over a durable store: recover, track, prepare.

    ``tick`` (1) establishes continuity: it lists the WORK records the transport holds, replays
    the journal, requires every listed record to be a work the journal knows, and re-reads the
    whole journal against the anchor; (2) finishes what a crash left (``Planner.recover``);
    (3) consumes results and verdicts (``pump``); (4) PREPARES further lineages: it dispatches,
    into the transport, the next admissible candidates whose scope is compatible with every
    scope holder and with every other lineage the journal has ever recorded, while fewer than
    ``max_live`` lineages are
    executing or being verified in the store; (5) writes a status file derived from the
    journal. Several coordinators may tick on one store: scope admission and the ``max_live``
    bound are decided in the journal commit, not by this object (coordinators configured with
    different limits each enforce their own).

    What it is NOT, and never does:
      * it does not dispatch an executor: publishing a WORK record makes it available to an
        implementer role; causing a workflow run is the fabric adapter's separate, authority
        bound step. Nothing here holds or consumes a dispatch grant;
      * recovery is not a new attempt: ``recover`` only re-publishes the SAME sealed record
        (same seal, same execution id, same attempt number). A transport that still has the
        record, pending or claimed, ignores it. If the transport lost it, the record is
        published again, and whether that leads to a second workflow dispatch is decided by
        the adapter's own ledger (the crosswalk), which refuses a seal it already dispatched:
        that protection lasts exactly as long as that ledger does;
      * it never hands a scope over: it does not call ``fail_execution`` or ``release_scope``,
        and it admits nothing that overlaps the last work of a lineage that gave up its scope
        (``SCOPE_RETAINED``): BLOCKED or OWNER_REQUIRED, which ``pump`` or another caller's
        ``fail_execution`` produces, and INTEGRATION_READY released by another caller's
        ``release_scope`` on a revision it merely asserts. None of these is evidence that an
        executor stopped writing, that a result branch is gone or that a merge happened.
        Until a verified release exists, a path any lineage of this journal has claimed in
        the same repository stays closed to this coordinator (the repository is compared as
        in ``works_collide``: a ``.git`` or URL spelling of the same repository counts as a
        different one). A planner used directly, without this option, still admits over such
        a scope;
      * it does not fall back: it refuses a journal without store identity (``MemoryJournal``,
        a plain ``DirJournal``) and a transport that does not keep its records, and it refuses
        a store whose journal and anchor share a filesystem unless the caller explicitly
        accepts that. Either way the status file says that an adequate continuity boundary
        for live conflicting work is NOT established: a different filesystem is one ``st_dev``
        comparison, taken once at construction, not proof of independent storage.
    Limits:
      * the witness covers WORK records only and only what the transport still holds: it
        detects a journal that no longer knows a published work (a lost DISPATCH or REPAIR
        event, including journal and anchor rolled back together). It does not detect a lost
        later event of a lineage (the lineage then replays to an earlier, still scope-holding
        phase), a lineage that was journalled but never published, or a rollback that took
        the transport back as well. A bare ``Planner`` has no witness;
      * the witness trusts the transport directory as much as the journal: anyone who can
        write a WORK record there can stop every coordinator;
      * one tick is not atomic. A crash between its steps is finished by the next tick;
        history lost after the continuity step is noticed by the next operation or tick, not
        necessarily before this tick's remaining dispatches;
      * candidates are supplied by the caller (no mission decomposition) and are validated
        before anything else happens. A dependency must be in the same list (otherwise the
        list is invalid and the tick raises); a candidate with ``depends_on`` is not selected
        until its dependency is completed and is then reported as refused, because
        ``Planner.dispatch`` validates an item on its own: dependencies are not supported;
      * status ``state`` is ``OK``, ``DEGRADED`` (the anchor holds repair records: some
        acknowledgement was restored or adopted; the tick still ran) or ``HALTED`` (continuity
        failed in the constructor or during a tick; written best effort). A failure of any
        other kind leaves the previous status in place;
      * the status file is last-writer-wins and names no coordinator: one that fails to start
        for a reason local to it (a wrong transport or store) overwrites a shared status with
        ``HALTED`` until a healthy tick rewrites it;
      * no leases and no executor assignment.
    """

    def __init__(
        self,
        journal: Journal,
        transport: DevTransport,
        *,
        identity: str,
        verifier_identities: tuple[str, ...],
        status_path: Path,
        max_live: int = MAX_LIVE_DEFAULT,
        accept_same_filesystem: bool = False,
    ) -> None:
        if not isinstance(journal, StoreJournal):
            raise PlannerError(
                "COORDINATOR_NEEDS_STORE:a coordinator runs only on an attached StoreJournal "
                f"(got {type(journal).__name__}); there is no in-memory or unanchored fallback"
            )
        for needed in ("claimed_records", "published"):
            if not callable(getattr(transport, needed, None)):
                raise PlannerError(
                    f"COORDINATOR_NEEDS_DURABLE_TRANSPORT:transport has no {needed}()"
                )
        if not isinstance(max_live, int) or isinstance(max_live, bool) or max_live < 1:
            raise PlannerError("max_live must be an integer >= 1")
        self.status_path = Path(status_path)
        where = self.status_path.resolve()
        owned = [journal.root, journal.anchor, getattr(transport, "root", None)]
        for directory in owned:
            if directory is not None and where.is_relative_to(Path(directory).resolve()):
                raise PlannerError(
                    f"STATUS_PATH:{self.status_path} is inside {directory}; the status file "
                    "must live outside the journal, the anchor and the transport"
                )
        self.journal = journal
        try:
            self.boundary = journal.boundary
        except JournalCorrupt as exc:
            self.boundary = "UNKNOWN"
            self._halted(exc)
            raise
        if self.boundary != SEPARATE_FILESYSTEM and not accept_same_filesystem:
            raise PlannerError(
                "STORE_BOUNDARY:journal and anchor are on the same filesystem; one rollback of "
                "it takes both back together, which the continuity check cannot detect. Put "
                "the anchor on a different filesystem or accept the reduced boundary "
                "explicitly (accept_same_filesystem=True)"
            )
        self.max_live = max_live
        if self.status_path.is_dir():
            raise PlannerError(f"STATUS_PATH:{self.status_path} is a directory")
        try:
            self.planner = Planner(
                transport,
                identity=identity,
                verifier_identities=verifier_identities,
                journal=journal,
            )
            self._continuity()
        except JournalCorrupt as exc:
            self._halted(exc)
            raise

    def _continuity(self) -> None:
        """Replay, witness the transport, and re-read the whole journal against the anchor.

        The transport is listed BEFORE the replay: a record is published only after its event
        was acknowledged, so every work listed is in a journal read afterwards, and a work that
        is still unknown then means the journal no longer has its event (or this is another
        store's transport), never that another coordinator was merely faster. The full re-read
        (``fleet_status``) makes a coordinator notice a lost or replaced event below its head,
        which a planner's own replay does not look at.
        """
        published = self.planner.transport.published(Channel.WORK)  # type: ignore[attr-defined]
        self.planner.sync()  # restores a lost head acknowledgement, with a repair record
        known = {w.seal for w in self.planner.state.works.values()}
        unknown = sorted(r.seal for r in published if r.seal not in known)
        if unknown:
            raise JournalCorrupt(
                f"JOURNAL_BEHIND_TRANSPORT:{len(unknown)} published work record(s) are unknown "
                f"to the journal, first {unknown[0]}"
            )
        fleet_status(self.journal)

    def live(self) -> frozenset[str]:
        """Lineage roots that are executing or being verified (what ``max_live`` bounds)."""
        return frozenset(r for r, st in self.planner.lineages.items() if st.phase in _LIVE)

    def tick(
        self, candidates: Sequence[tuple[QueueItem, Mapping[str, Any]]] = ()
    ) -> dict[str, Any]:
        """Run one coordination step and return the status that was written.

        ``candidates`` are (queue item, sealed work fields) pairs the caller proposes. They are
        validated first; a malformed list raises before anything is read or written. A
        candidate refused for a scope collision, a retained terminal scope, contention or its
        own invalidity is reported under ``deferred`` and may be proposed again.

        Raises ``JournalCorrupt`` when continuity cannot be established or is lost during the
        tick; a ``HALTED`` status naming the reason is written first if the status file can be
        written. Continuity is checked before recover, pump and the first dispatch; if it
        fails there, no event was appended and nothing was published by this tick (an
        acknowledgement repair, with its record, may have been written). An ``OSError`` from
        the journal, the anchor, a publish or the status file is raised as it is; one from the
        transport's ``claim`` is quarantined by ``pump``.
        """
        items = [self._candidate(c) for c in candidates]
        validate(item for item, _ in items)  # duplicate ids, cycles, malformed items
        fields = dict((item.task_id, (item, work)) for item, work in items)
        p = self.planner
        try:
            self._continuity()
            recovered = p.recover()
            processed = p.pump()
            admitted: list[str] = []
            deferred: list[tuple[str, str]] = []
            tried: set[str] = set()
            while len(tried) < len(items) and len(self.live()) < self.max_live:
                p.sync()
                sel = select_next(
                    [item for item, _ in items],
                    completed=p.completed,
                    blocked=p.blocked,
                    in_flight=p.in_flight() | tried,
                )
                if sel.selected is None:
                    break
                item, work = fields[sel.selected.task_id]
                tried.add(item.task_id)
                try:
                    p.dispatch(item, live_limit=self.max_live, retain_terminal_scopes=True, **work)
                except JournalContended:
                    deferred.append((item.task_id, "JOURNAL_CONTENDED"))
                    break  # the journal is busy: leave the rest for the next tick
                except JournalCorrupt:
                    raise
                except (PlannerError, ValueError) as exc:  # incl. ContractError, QueueError
                    reason = str(exc)
                    if reason.startswith("LIVE_LIMIT:"):
                        break  # another coordinator filled the capacity since we looked
                    kept = reason.startswith(("SCOPE_COLLISION:", "SCOPE_RETAINED:"))
                    deferred.append((item.task_id, reason if kept else f"REFUSED:{reason}"))
                    continue
                admitted.append(item.task_id)
            rows = fleet_status(self.journal)
            repairs = [dict(r) for r in self.journal.repairs()]
        except JournalCorrupt as exc:
            self._halted(exc)
            raise
        # acknowledgement continuity that had to be repaired stays visible: never plain OK
        status = self._header("DEGRADED" if repairs else "OK") | {
            "acknowledgement_continuity": "REPAIRED" if repairs else "INTACT",
            "repairs": repairs,
            "journal": {"seq": p.state.seq, "head": p.state.head},
            "max_live": self.max_live,
            "live": sorted(self.live()),
            "admitted": admitted,
            "deferred": [list(d) for d in deferred],
            "recovered": recovered,
            "records_processed": processed,
            "records_deferred": len(p.deferred),
            "records_quarantined": len(p.quarantined),
            "lineages": [dict(r) for r in rows],
        }
        self._write_status(status)
        return status

    _RESERVED_WORK_KEYS = frozenset(
        {"self", "item", "live_limit", "retain_terminal_scopes", "execution_ordinal"}
    )

    def _halted(self, exc: JournalCorrupt) -> None:
        status = self._header("HALTED") | {"reason": str(exc)}
        with contextlib.suppress(OSError, JournalCorrupt):  # best effort: repairs seen so far
            status["repairs"] = [dict(r) for r in self.journal.repairs()]
        with contextlib.suppress(OSError):  # the continuity failure is what must surface
            self._write_status(status)

    def _candidate(self, c: object) -> tuple[QueueItem, dict[str, Any]]:
        if not isinstance(c, tuple) or len(c) != 2:
            raise PlannerError("a candidate is a (QueueItem, work fields) pair")
        item, work = c
        if not isinstance(item, QueueItem) or not isinstance(work, Mapping):
            raise PlannerError("a candidate is a (QueueItem, work fields) pair")
        if not all(isinstance(k, str) for k in work):
            raise PlannerError("work field names must be strings")
        clash = sorted(self._RESERVED_WORK_KEYS & set(work))
        if clash:
            raise PlannerError(f"work fields may not set {clash}")
        return item, dict(work)

    def _header(self, state: str) -> dict[str, Any]:
        return {
            "v": STATUS_VERSION,
            "state": state,
            "store": self.journal.store_id,
            "continuity_boundary": self.boundary,
            # one st_dev comparison is not an established boundary; nothing here claims one
            "live_conflicting_work_boundary": "NOT_ESTABLISHED",
        }

    def _write_status(self, status: dict[str, Any]) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.status_path.parent, prefix=".tmp-status-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(status, sort_keys=True, indent=2) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.status_path)  # atomic: a reader never sees a torn status
        finally:
            _drop_tmp(tmp)
