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
    on a plain ``DirJournal`` (a planner at the head continues against its complete replica
    and restores its head's acknowledgement with a ``RESTORED`` record; one event behind it
    adopts that event with an ``ADOPTED`` record; two or more behind it fails closed; new
    planners refuse a journal of two or more events; an anchor that cannot be written raises
    ``OSError`` with nothing published, before the append when it is the repair record that
    cannot be written, after it when it is the new event's acknowledgement); a writer who
    rewrites journal and anchor. A ``StoreJournal`` (below) turns a missing, re-created or
    foreign anchor into a refusal for every planner, running or new, at any journal length;
  * loss of acknowledged continuity stays observable. The one repair done here is of the
    newest event's acknowledgement, when the event itself is still stored unchanged: by a
    planner that had seen it acknowledged (``RESTORED``, a definite loss) or, before it
    appends or publishes, by a planner that finds the newest event unacknowledged after a
    wait (``ADOPTED``: its writer died between append and acknowledgement, the
    acknowledgement was lost, or a live writer took longer than the wait; not knowable). Either
    way a repair record is written to the
    anchor BEFORE the acknowledgement and is never removed here; ``repairs()`` lists them and
    a coordinator's healthy tick reports ``DEGRADED`` while a record exists. An acknowledgement
    therefore either comes from the commit that appended its event or has a repair record
    next to it (the record that stands must be a well-formed record of the same event; one
    record per sequence number, so a second loss of the same acknowledgement adds none).
    Everything else fails closed: there is no other repair or re-anchoring tool, and nothing
    here clears a repair record; an operator has to restore the journal or judge the record;
  * ``recover`` must be called by whoever restarts a planner; ``Coordinator.tick`` does;
  * a full open lists the journal directory and reads every event; the directory fsync after
    an append is best effort (not available on Windows);
  * path overlap only: no semantic conflict detection (a generated file two lineages both
    rewrite, a whole-suite acceptance command);
  * the planner cannot observe a merge. ``release_scope`` records the merge revision the
    CALLER asserts and ``fail_execution`` records a caller's failure report; since
    ATLAS-DEVQ-0009 neither opens a scope for other work (see "Verified scope handover");
  * no heartbeats, lease durations or liveness detection: nothing here notices that an
    executor died. Ownership of an execution and its safe replacement are journalled (see
    "Executor ownership"), but WHEN to replace is the caller's decision, and the paths of a
    BLOCKED or OWNER_REQUIRED lineage stay closed;
  * the quarantine list is evidence in memory only and is not journalled;
  * it does not lift the fabric adapter's serial-dispatch rule, and no live run has exercised
    two lineages.

Verified scope handover (ATLAS-DEVQ-0009): a work item that overlaps the last work of ANY
earlier lineage in the same repository (by ``dev_contracts.repository_key``, ATLAS-DEVQ-0010:
every supported spelling of a repository is that repository, and a DISPATCH whose repository
identity cannot be keyed is refused, ``REPOSITORY_UNSUPPORTED``, also on replay; the key
follows no rename or redirect) is admitted only when the DISPATCH event carries a
verified handover of that lineage's scope. This is a rule of the journal (``_transition``), so
it holds for every planner and on replay; there is no switch. A phase, a timeout, a failure
report (``fail_execution``) or an asserted merge revision (``release_scope``) never opens a
scope: such lineages are refused with ``SCOPE_RETAINED`` instead of ``SCOPE_COLLISION``, and
that is the only difference. The one basis for a handover is ``RESULT_IN_BASE``: the earlier
lineage is INTEGRATION_READY and its verified result revision is an ancestor of the new
work's ``base_revision``. The new work then starts from history that contains the verified
result revision, so it cannot conflict with that revision. The earlier lineage is terminal
and the journal accepts no further result or verdict for it; its executor and its result
branch are not fenced by anything else. The
journal checks that an entry names exactly that lineage's result and exactly the new work's
base. That the ancestry was OBSERVED is the statement of a ``HandoverObserver`` given to the
planner at construction, called inside the dispatch decision and journalled with its identity
and evidence; ``dispatch`` has no parameter through which a caller could pass such evidence.
What this does not establish: the observer is trusted like the journal directory (whoever
can construct a planner can construct a lying observer, and the entry is unkeyed); ancestry
says nothing about the default branch or about who merged, and a handover does not mean the
result was integrated: a base equal to, or built on, the unmerged result branch satisfies
it; commits pushed to the earlier result branch AFTER the verified revision are not covered
(only the verified revision is); a journal written before this rule that admitted work over
a retained scope no longer replays (fail closed), and an event that carries a handover is
written as journal version 2, which the previous code refuses, so a journal with a handover
does not replay there either; events without one are still version 1; and the scope of
a BLOCKED or OWNER_REQUIRED lineage, or of an INTEGRATION_READY lineage whose result is not
an ancestor of the base a new work is given, cannot be handed over at all yet.

Executor ownership (ATLAS-DEVQ-0011): which executor owns a lineage's execution is a fact of
the journal, and replacing it leaves at most one execution whose result the journal accepts.
One model, three parts:
  * assignment: a DISPATCH may name the executor that owns the execution (chosen from a pool
    inside the dispatch decision); a REPAIR keeps it. The work record is published ADDRESSED
    to that executor, and the journal accepts a RESULT for the lineage only from it;
  * the fencing token is the execution id, which is part of the sealed work item. A result
    carries the seal and the execution id of the work it answers, and the journal accepts a
    result only for the lineage's CURRENT work;
  * reassignment (``Planner.reassign``, event REASSIGN): the current work is re-materialised
    under its next execution id and a (new or the same) executor becomes the owner. From that
    event on the earlier execution cannot produce an accepted result, whether its executor
    stopped, is slow, or never learns of it. So at every point of the journal at most one
    (execution id, executor) pair can have a result accepted for an assigned lineage, and
    only while that lineage is executing (the owner is matched as ``same_identity`` does:
    case and surrounding whitespace do not distinguish identities).
A lease in this model is that ownership record: it has no clock. A timeout, a missed
heartbeat or a report is a reason somebody may have for calling ``reassign``; it is recorded
as the reason and is not what makes the replacement safe. Events that name an executor are
journal version 3, which earlier code refuses.
What this does NOT establish: it does not stop, signal or observe the earlier executor, which
may keep running and pushing to its own result branch (nothing here integrates a branch; only
a verified result of the current execution can become INTEGRATION_READY). A fenced work
record that nobody claimed yet is withdrawn from a transport that supports it (best effort,
repeated on recovery), so the earlier executor does not START it afterwards; one it already
claimed runs on; identities are
unauthenticated strings and the address of a work record is metadata in the transport, so
this fences stale or slow executors, not one that forges another's identity; a reassignment
IS a new execution that the new owner's adapter dispatches (it is bounded per work item, so
per lineage by that bound times its attempts, and it is not recovery, which only re-publishes
the same record); an executor is "busy" while it owns a
lineage that is executing or being verified, a count over the journal, not a measurement;
and a lineage dispatched without a pool is not assigned and accepts any implementer's
result, as before.

Store identity and coordinator (ATLAS-DEVQ-0008): ``StoreJournal`` is a ``DirJournal`` whose
journal and anchor directories are created once, carry one store id, and are afterwards only
attached, never created. ``Coordinator`` runs recover, pump and the preparation of further
compatible lineages as one ``tick`` on such a store and writes a journal-derived status file.
It refuses a journal without store identity and a transport without the record-listing methods,
checks that every published WORK record is known to the journal (a witness that does not
depend on the anchor), and never dispatches an executor; it hands a scope over only through
the verified handover above. See both
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
    repository_key,
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
    TransportError,
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
paths and move the conflict back to merge time. OWNER_REQUIRED and BLOCKED are not holders, but
their paths stay closed to other work all the same (``SCOPE_RETAINED``, ATLAS-DEVQ-0009)."""


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
    handover: str = ""  # base revision of the first work admitted over this scope (journalled)
    executor: str = ""  # identity that owns the CURRENT execution ("" = not assigned)
    epoch: int = 0  # how often the current work was reassigned (0 = as first materialised)
    handed_over_to: list[str] = field(default_factory=list)  # lineage roots admitted over it


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
# An event that carries a scope handover is written as version 2, so that code from before
# ATLAS-DEVQ-0009 (which would ignore the entry) refuses the journal instead of replaying it.
HANDOVER_VERSION = 2
# An event that names an executor (ATLAS-DEVQ-0011: an assignment on DISPATCH / REPAIR, or a
# REASSIGN) is version 3, so that code from before the ownership model, which would accept a
# result from any executor, refuses the journal instead of replaying it.
OWNERSHIP_VERSION = 3
MAX_REASSIGNMENTS = 3  # per work item: further replacement needs a decision about the lineage
MAX_REASON = 200
_EPOCH_SUFFIX = re.compile(r"-X[0-9]+$")
MAX_OBSERVER_IDENTITY = 200
MAX_COMMIT_RETRIES = 16  # lost exclusive-create races before a commit gives up (never spins)
_EVENT_FILE = re.compile(r"[0-9]{12}\.json")
_ACK_FILE = re.compile(r"[0-9]{12}\.ack")
_REPAIR_FILE = re.compile(r"[0-9]{12}\.repair")
# An acknowledgement written by anyone but the commit that appended the event is a REPAIR of
# acknowledgement continuity and leaves a durable record:
RESTORED = "RESTORED"  # this planner had seen the acknowledgement; it was gone: a definite loss
ADOPTED = "ADOPTED"  # no acknowledgement appeared for the newest event within the wait: its
#                      writer died, the acknowledgement was lost, or the writer is slower
#                      than the wait (not knowable)
ADOPT_GRACE_TRIES = 40  # how long a live writer gets to acknowledge its own event before
ADOPT_GRACE_STEP = 0.05  # another planner adopts it: tries x seconds, 2 s. A live writer
#                          that needs longer than this between append and acknowledgement
#                          is adopted too and leaves an ADOPTED record
_DIGEST = re.compile(r"[0-9a-f]{64}")
# Verified scope handover (ATLAS-DEVQ-0009). The only basis: the verified result of the lineage
# that owned the scope is an ancestor of the base revision the new work starts from.
RESULT_IN_BASE = "RESULT_IN_BASE"
MAX_HANDOVERS = 64  # lineages one DISPATCH may take a scope over from
MAX_EVIDENCE_KEYS = 8
MAX_EVIDENCE_CHARS = 256  # per key + value
_HANDOVER_KEYS = frozenset(
    {"root", "basis", "result_revision", "base_revision", "observer", "evidence"}
)
_REVISION = re.compile(r"[0-9a-f]{40}")
_EXECUTING = frozenset({Phase.DISPATCHED, Phase.REPAIR_DISPATCHED})
_LIVE = _EXECUTING | {Phase.VERIFYING}  # executing or being verified


class HandoverObserver(Protocol):
    """Observes, outside the journal, whether a scope can be handed over. Read-only.

    ``result_in_base`` returns evidence (a small ``str -> str`` mapping, journalled with the
    DISPATCH) when it OBSERVED that ``result_revision`` is an ancestor of ``base_revision`` in
    ``repository``, and ``None`` when it did not or could not. ``identity`` names the source
    of the observation. A planner without an observer hands no scope over.
    """

    identity: str

    def result_in_base(
        self, *, repository: str, result_revision: str, base_revision: str
    ) -> Mapping[str, str] | None: ...


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
    already at the head notices only that its head's acknowledgement is gone, and restores
    it with a repair record. IO errors on the anchor surface as ``OSError``.
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
        # the record that stands (this one, or an earlier one) must be a record of THIS event:
        # a file that merely has the name is not evidence, and no acknowledgement follows it
        if self._repair(f"{seq:012d}.repair")["digest"] != digest:
            raise JournalCorrupt(f"JOURNAL_CORRUPT:repair record {seq} is for a different event")

    def _repair(self, name: str) -> dict[str, Any]:
        """One repair record, shape-checked (not compared with the journal)."""
        try:
            rec = json.loads((self.anchor / name).read_text(encoding="utf-8"))
            if (
                not isinstance(rec, dict)
                or type(rec.get("seq")) is not int
                or rec["seq"] != int(name[:12])
                or rec.get("kind") not in (RESTORED, ADOPTED)
                or not isinstance(rec.get("by"), str)
                or not isinstance(rec.get("digest"), str)
                or not _DIGEST.fullmatch(rec["digest"])
            ):
                raise ValueError("not a repair record")
        except (OSError, ValueError, RecursionError) as exc:
            raise JournalCorrupt(f"JOURNAL_CORRUPT:repair record {name}: {exc}") from exc
        return {k: rec[k] for k in ("seq", "kind", "by", "digest")}

    def repairs(self) -> tuple[dict[str, Any], ...]:
        names = sorted(n for n in self._names(self.anchor) if _REPAIR_FILE.fullmatch(n))
        return tuple(self._repair(name) for name in names)

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
      * a missing or foreign anchor fails at attach and at every later read, append,
        acknowledgement, repair record and repair listing (``STORE_IDENTITY``), also for a journal
        of zero or one event and
        also for a planner that is already running;
      * an anchor of another store, or the plain ``DirJournal`` default sibling, cannot be
        attached by accident.
    The identity is checked on every ``read`` (so on every replay), before every append,
    before every acknowledgement, before a repair record is written and when the repair
    records are listed. An identity lost between a commit's append and its
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

    def record_repair(self, seq: int, digest: str, kind: str, by: str) -> None:
        self.check_store()  # checked immediately before the write, not atomically with it
        super().record_repair(seq, digest, kind, by)

    def repairs(self) -> tuple[dict[str, Any], ...]:
        self.check_store()
        return super().repairs()


@dataclass
class FleetState:
    """Replica of the journal: everything here is derived by replay, nothing is authoritative."""

    seq: int = 0
    head: str = ""  # sha256 of the latest event's bytes ("" before the first event)
    lineages: dict[str, LineageState] = field(default_factory=dict)
    by_task: dict[str, str] = field(default_factory=dict)  # task_id -> lineage_root
    works: dict[str, WorkItem] = field(default_factory=dict)
    superseded: dict[str, WorkItem] = field(default_factory=dict)  # fenced seal -> its work
    completed: set[str] = field(default_factory=set)
    blocked: dict[str, str] = field(default_factory=dict)
    issued: dict[str, VerificationRequest] = field(default_factory=dict)
    seals: set[str] = field(default_factory=set)  # result/verdict seals already journalled
    acked: int = 0  # highest sequence number this replica has SEEN acknowledged


def _event_version(ev: Mapping[str, Any]) -> int:
    if "executor" in ev or ev.get("event") == "REASSIGN":
        return OWNERSHIP_VERSION
    return HANDOVER_VERSION if "handover" in ev else JOURNAL_VERSION


def _executor_of(ev: dict[str, Any]) -> str:
    """The executor an event assigns ("" when it assigns none); validated."""
    if "executor" not in ev:
        return ""
    executor = ev["executor"]
    try:
        if not isinstance(executor, str):
            raise ValueError("not a string")
        validate_identity(executor)
    except ValueError as exc:
        raise PlannerError(f"event names an invalid executor identity: {exc}") from exc
    return executor


def next_epoch_work(work: WorkItem, epoch: int) -> WorkItem:
    """``work`` re-materialised for reassignment number ``epoch`` (>= 1): a new execution id.

    Everything else is the work item's own: task, lineage, repository, base, scope, contract,
    attempt. The execution id is the fencing token: it is part of the seal, a result has to
    carry the seal and the execution id of the work it answers, and the journal accepts a
    result only for the CURRENT work of a lineage. So a result produced under an earlier
    epoch is not accepted once this work is the current one, PROVIDED the execution id
    changed: the journal refuses a root work whose execution id already carries an epoch
    suffix and a REASSIGN whose work has the seal of the work it replaces.
    """
    if type(epoch) is not int or epoch < 1:
        raise PlannerError("an epoch is an integer >= 1")
    stem = _EPOCH_SUFFIX.sub("", work.execution_id)
    fresh: WorkItem = work.model_copy(
        update={"execution_id": f"{stem}-X{epoch}", "seal": ""}
    ).sealed()
    return fresh


def holds_scope(st: LineageState) -> bool:
    return st.phase in SCOPE_HOLDING and not st.scope_released and not st.handover


def _collisions(pairs: tuple[tuple[str, str], ...]) -> str:
    return ",".join(f"{a}|{b}" for a, b in pairs)


def _handover_entries(ev: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The ``handover`` entries of a DISPATCH event by lineage root; shape-checked only."""
    raw = ev.get("handover", [])
    if not isinstance(raw, list) or len(raw) > MAX_HANDOVERS:
        raise PlannerError("DISPATCH handover must be a bounded list")
    out: dict[str, dict[str, Any]] = {}
    for h in raw:
        if not isinstance(h, dict) or set(h) != _HANDOVER_KEYS:
            raise PlannerError("DISPATCH handover entry has the wrong fields")
        evidence = h["evidence"]
        if (
            not all(isinstance(h[k], str) and h[k] for k in _HANDOVER_KEYS - {"evidence"})
            or not _evidence_ok(evidence)
            or not _observer_identity_ok(h["observer"])
            or h["root"] in out
        ):
            raise PlannerError("DISPATCH handover entry is malformed or repeated")
        out[h["root"]] = h
    if [h["root"] for h in raw] != sorted(out):
        raise PlannerError("DISPATCH handover entries must be ordered by lineage root")
    return out


def _observer_identity_ok(identity: object) -> bool:
    return (
        isinstance(identity, str)
        and 0 < len(identity) <= MAX_OBSERVER_IDENTITY
        and identity == identity.strip()
        and identity.isprintable()
    )


def _evidence_ok(evidence: object) -> bool:
    return (
        isinstance(evidence, dict)
        and 0 < len(evidence) <= MAX_EVIDENCE_KEYS
        and all(
            isinstance(k, str) and isinstance(v, str) and 0 < len(k) + len(v) <= MAX_EVIDENCE_CHARS
            for k, v in evidence.items()
        )
    )


def _verified_release(holder: LineageState, h: dict[str, Any], work: WorkItem) -> bool:
    """Whether ``h`` is a handover of ``holder``'s scope to ``work`` that the journal accepts.

    Only one basis exists: the holder's verified result is contained in the base the new work
    starts from. The journal checks that the entry names exactly that result and exactly that
    base; that the containment was OBSERVED is the observer's statement, recorded with it.
    """
    res = holder.result
    return (
        holder.phase is Phase.INTEGRATION_READY
        and res is not None
        and h["basis"] == RESULT_IN_BASE
        and h["result_revision"] == res.result_revision
        and h["base_revision"] == work.base_revision
    )


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
    if type(ev.get("v")) is not int or ev["v"] != _event_version(ev):
        raise PlannerError(f"unsupported journal version {ev.get('v')!r}")
    if "handover" in ev and ev.get("event") != "DISPATCH":
        raise PlannerError("only a DISPATCH carries a handover")
    if "executor" in ev and ev.get("event") not in ("DISPATCH", "REPAIR", "REASSIGN"):
        raise PlannerError("only a DISPATCH, REPAIR or REASSIGN names an executor")
    assigned = _executor_of(ev)
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
        if _EPOCH_SUFFIX.search(work.execution_id):
            raise PlannerError("execution id suffix -X<n> is reserved for reassigned executions")
        try:  # also with no earlier lineage: a journal that replays holds no unkeyable identity
            repository_key(work.repository)
        except ContractError as exc:
            raise PlannerError(f"REPOSITORY_UNSUPPORTED:{exc}") from exc
        # Scope ownership. A work that overlaps the last work of ANY earlier lineage in the
        # same repository is admitted only with a verified handover of that lineage's scope.
        # Holders are reported first (SCOPE_COLLISION), then lineages that no longer hold
        # their scope (SCOPE_RETAINED): BLOCKED, OWNER_REQUIRED, released on an assertion, or
        # already handed over. Neither a phase nor an assertion opens a scope.
        handed = _handover_entries(ev)
        taken: list[tuple[LineageState, str]] = []
        for holding in (True, False):
            for other in sorted(state.lineages):
                holder = state.lineages[other]
                if holds_scope(holder) is not holding:
                    continue
                pairs = works_collide(work, holder.work)
                h = handed.get(other)
                if not pairs:
                    continue
                if h is None:
                    code = "SCOPE_COLLISION" if holding else "SCOPE_RETAINED"
                    raise PlannerError(f"{code}:{other}:{_collisions(pairs)}")
                if not _verified_release(holder, h, work):
                    raise PlannerError(f"handover of {other} is not a verified release")
                taken.append((holder, h["base_revision"]))
        if len(taken) != len(handed):
            raise PlannerError("handover for a lineage the work does not collide with")

        def do_dispatch() -> None:
            for prior, base in taken:
                prior.handover = prior.handover or base
                prior.handed_over_to.append(root)
                prior.history.append(f"SCOPE_HANDED_OVER:{root}:{base}")
                prior.last_seq = seq
            state.lineages[root] = LineageState(
                root,
                Phase.DISPATCHED,
                work,
                history=[f"DISPATCHED:{work.task_id}"],
                dispatched_by=planner,
                last_seq=seq,
                executor=assigned,
            )
            if assigned:
                state.lineages[root].history.append(f"ASSIGNED:{work.execution_id}:{assigned}")
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
        if st.executor and not same_identity(res.executor_identity, st.executor):
            raise PlannerError("RESULT is not from the executor that owns this execution")
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
            if assigned != st.executor:
                raise PlannerError("REPAIR must keep the lineage's executor assignment")

            def do_repair() -> None:
                state.seals.add(ver.seal)
                state.works[rw.task_id] = rw
                state.by_task[rw.task_id] = root
                st.work, st.result = rw, None
                st.epoch = 0
                st.phase = Phase.REPAIR_DISPATCHED
                note(f"REPAIR_DISPATCHED:{rw.task_id}")

            steps.append(do_repair)
    elif kind == "REASSIGN":
        # Replacement of the executor of an EXECUTING lineage. The new work is the current
        # work under the next execution id (the fencing token); from here on only a result
        # that answers THIS work, from THIS executor, is accepted. Whatever caused the
        # replacement (a timeout, a report, an operator) is recorded as its reason and is
        # not what makes it safe: the earlier execution can no longer produce an accepted
        # result, whether or not its executor stopped.
        nw = _record(ev, "work", WorkItem)
        why = ev["reason"]
        if st.phase not in _EXECUTING:
            raise PlannerError("only an executing lineage can be reassigned")
        if not assigned:
            raise PlannerError("REASSIGN needs the executor that takes the execution over")
        if st.epoch >= MAX_REASSIGNMENTS:
            raise PlannerError(f"REASSIGN_LIMIT:{st.work.task_id} was reassigned {st.epoch} times")
        if nw.seal != next_epoch_work(st.work, st.epoch + 1).seal or nw.seal == st.work.seal:
            raise PlannerError("REASSIGN work is not the current work under its next epoch")
        if not isinstance(why, str) or not 0 < len(why) <= MAX_REASON or not why.isprintable():
            raise PlannerError("REASSIGN needs a short printable reason")
        fenced, was = st.work, st.executor

        def do_reassign() -> None:
            state.superseded[fenced.seal] = fenced
            state.works[nw.task_id] = nw
            st.work, st.executor = nw, assigned
            st.epoch += 1
            note(f"FENCED:{fenced.execution_id}:{was or '-'}")
            note(f"REASSIGNED:{nw.execution_id}:{assigned}:{why}")

        steps.append(do_reassign)
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
        event that has a successor must HAVE one (a writer makes sure its head is acknowledged
        before it appends, so only the newest event can be unacknowledged; a journal whose anchor is
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
            "repository_key": repository_key(st.work.repository),
            "base_revision": st.work.base_revision,
            "allowed_paths": st.work.allowed_paths,
            "holds_scope": holds_scope(st),
            "scope_released": st.scope_released,
            "handover": st.handover,
            "handed_over_to": tuple(st.handed_over_to),
            "executor": st.executor,
            "epoch": st.epoch,
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
        observer: HandoverObserver | None = None,
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
        if observer is not None and not _observer_identity_ok(getattr(observer, "identity", None)):
            raise PlannerError("a handover observer needs a non-empty identity")
        self.observer = observer  # None: this planner never admits over an earlier scope
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
        if not st.seq:
            return
        known = self.journal.acknowledged(st.seq)  # the file, not this replica's memory
        if known is None and st.acked >= st.seq:
            # it had been acknowledged and is gone again since the replay: a definite loss
            self.journal.record_repair(st.seq, st.head, RESTORED, self.identity)
            self.journal.acknowledge(st.seq, st.head)
            return
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
            decided = decide()
            ev = {
                "v": _event_version(decided),
                "seq": self.state.seq + 1,
                "prev": self.state.head,
                "planner": self.identity,
                **decided,
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
        for seal in sorted(self.state.superseded):  # a crash between REASSIGN and withdrawal
            if self._withdraw(self.state.superseded[seal]):
                done.append(f"WITHDRAWN:{self.state.superseded[seal].lineage_root}:WORK")
        for root, st in sorted(self.lineages.items()):
            rec: Record | None = None
            if st.phase in _EXECUTING:
                rec = st.work
            elif st.phase is Phase.VERIFYING:
                rec = self.issued.get(st.work.task_id)
            if rec is None:
                continue
            fresh = (
                self._publish_work(st) if st.phase in _EXECUTING else self.transport.publish(rec)
            )
            if fresh:
                done.append(f"REPUBLISHED:{root}:{rec.KIND.value}")
        return done

    def _publish_work(self, st: LineageState) -> bool:
        """Publish a lineage's current work, addressed to its executor when it has one."""
        if st.executor:
            return self.transport.publish(st.work, to=st.executor)
        return self.transport.publish(st.work)

    def _withdraw(self, fenced: WorkItem) -> bool:
        """Take a fenced work record back if nobody claimed it yet (best effort).

        A courtesy to the earlier executor, not the fence: a record that was already claimed
        stays claimed and its execution may run on; its result is refused either way. A
        transport without ``withdraw`` leaves the record deliverable.
        """
        withdraw = getattr(self.transport, "withdraw", None)
        return bool(withdraw(fenced)) if callable(withdraw) else False

    def _needs_addressing(self, pool: tuple[str, ...]) -> None:
        if pool and getattr(self.transport, "addressed", False) is not True:
            raise PlannerError(
                "EXECUTORS_NEED_ADDRESSED_TRANSPORT:an execution is assigned only over a "
                "transport that delivers a work record to the executor it is addressed to"
            )

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
        executor_pool: Sequence[str] = (),
        executor_limit: int = 1,
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
        already executing or being verified.
        A work item that overlaps the last work of a lineage that no longer holds its scope
        (BLOCKED, OWNER_REQUIRED, INTEGRATION_READY released with ``release_scope``, or
        already handed over) is refused with ``SCOPE_RETAINED`` unless the verified handover
        below applies; for a BLOCKED or OWNER_REQUIRED lineage it never does. Those phases
        end a lineage or record a caller's assertion, and none is evidence that an executor
        stopped writing, that a result branch is gone or that a candidate was merged.
        VERIFIED HANDOVER is the one way a scope passes on: when the overlapped lineage is
        INTEGRATION_READY and this planner's ``observer`` reports that the lineage's verified
        result revision is an ancestor of the new work's ``base_revision``, the DISPATCH event
        carries that observation and the earlier lineage stops holding its scope. The new
        work then starts from a base that contains the verified result revision, so it cannot
        conflict with that revision. No caller can pass such evidence in; without an observer,
        or when it reports nothing or raises ``ValueError`` (every ``ContractError``),
        ``OSError`` or ``TypeError``, the refusal stands. Any other exception from the
        observer propagates and nothing is written.
        EXECUTOR ASSIGNMENT (ATLAS-DEVQ-0011): with a non-empty ``executor_pool`` the DISPATCH
        names the executor that owns the execution: the pool member that owns the fewest
        lineages that are executing or being verified, the first by name among equals. If
        every member already owns ``executor_limit`` of them the dispatch is refused with
        ``EXECUTOR_BUSY``. The work is published addressed to that executor, the journal
        accepts a result for the lineage only from it, and a repair stays with it. Without a
        pool the lineage is not assigned and any implementer's result is accepted, as before.
        The decision is taken against the journal and committed as its next event, so it also
        holds against other planners on the same journal. The DISPATCH event is written before
        the work is published; if publishing fails the lineage is DISPATCHED and ``recover``
        publishes it. See the module docstring for what this check does not cover.
        """
        if execution_ordinal < 1:
            raise PlannerError("execution_ordinal must be >= 1")
        pool = self._pool(executor_pool, executor_limit)
        self._needs_addressing(pool)
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
            try:
                repository_key(work.repository)
            except ContractError as exc:
                raise PlannerError(f"REPOSITORY_UNSUPPORTED:{exc}") from exc
            handover: list[dict[str, Any]] = []
            for holding in (True, False):  # holders first, as the journal rule reports them
                for root, st in sorted(self.lineages.items()):
                    if holds_scope(st) is not holding:
                        continue
                    pairs = works_collide(work, st.work)
                    if not pairs:
                        continue
                    entry = self._observe_handover(st, work, observed)
                    if entry is None:
                        code = "SCOPE_COLLISION" if holding else "SCOPE_RETAINED"
                        raise PlannerError(f"{code}:{root}:{_collisions(pairs)}")
                    handover.append(entry)
            ev: dict[str, Any] = {"event": "DISPATCH", "root": item.task_id, "work": encode(work)}
            if pool:
                ev["executor"] = self._free_executor(pool, executor_limit)
            if handover:
                ev["handover"] = sorted(handover, key=lambda h: str(h["root"]))
            return ev

        observed: dict[tuple[str, str], Mapping[str, str] | None] = {}  # one look per dispatch

        self._commit(decide)
        st = self.lineages[item.task_id]
        self._publish_work(st)
        return st.work

    # -- executor ownership ---------------------------------------------------------------
    @staticmethod
    def _pool(executor_pool: Sequence[str], executor_limit: int) -> tuple[str, ...]:
        if isinstance(executor_pool, str) or not isinstance(executor_limit, int):
            raise PlannerError("executor_pool is a sequence of identities, the limit an integer")
        if isinstance(executor_limit, bool) or executor_limit < 1:
            raise PlannerError("executor_limit must be an integer >= 1")
        pool = tuple(executor_pool)
        try:
            for e in pool:
                if not isinstance(e, str):
                    raise ValueError("not a string")
                validate_identity(e)
        except ValueError as exc:
            raise PlannerError(f"invalid executor identity in the pool: {exc}") from exc
        if len({e.strip().casefold() for e in pool}) != len(pool):
            raise PlannerError("duplicate (or case-variant) executor identities in the pool")
        return pool

    def executor_load(self) -> dict[str, int]:
        """Lineages each executor owns that are executing or being verified (the replica)."""
        load: dict[str, int] = {}
        for st in self.lineages.values():
            if st.executor and st.phase in _LIVE:
                key = st.executor.strip().casefold()
                load[key] = load.get(key, 0) + 1
        return load

    def _free_executor(self, pool: tuple[str, ...], limit: int, *, besides: str = "") -> str:
        """The least loaded pool member below ``limit``; ``EXECUTOR_BUSY`` when there is none.

        ``besides`` is a lineage root whose own execution does not count (it is the one being
        moved). Called inside a commit decision, so the choice is made against the journal.
        """
        load = self.executor_load()
        if besides:
            owner = self.lineages[besides].executor.strip().casefold()
            if owner and self.lineages[besides].phase in _LIVE:
                load[owner] -= 1
        ranked = sorted(pool, key=lambda e: (load.get(e.strip().casefold(), 0), e))
        if not ranked or load.get(ranked[0].strip().casefold(), 0) >= limit:
            raise PlannerError(f"EXECUTOR_BUSY:every executor of the pool owns {limit} lineage(s)")
        return ranked[0]

    def reassign(
        self,
        task_id: str,
        *,
        executor_pool: Sequence[str],
        reason: str,
        executor_limit: int = 1,
    ) -> WorkItem:
        """Replace the executor of an executing lineage without creating a second valid writer.

        Journals a REASSIGN: the lineage's current work is re-materialised under its next
        execution id (see ``next_epoch_work``), the executor chosen from ``executor_pool``
        (as in ``dispatch``; the lineage's own execution does not count against its present
        owner, so a pool of that one executor restarts it under a new epoch) becomes the
        owner, and the new work is published addressed to it. From that event on the journal
        accepts a result only for the new work and only from the new owner. A result of the
        earlier execution, whenever it arrives and whoever sends it, is refused: it answers a
        work seal that is no longer the lineage's current work (the journal refuses a
        REASSIGN that would keep the seal). The fenced record is withdrawn from the transport
        if nobody claimed it yet.

        That refusal, not the caller's ``reason``, is what makes the replacement safe. A
        timeout, an expired lease or a report that an executor died is a reason to call this;
        none of them is evidence that the earlier executor stopped, and none is needed. What
        this does NOT do: it does not stop the earlier executor, which may go on running and
        pushing to its own result branch; it does not tell the earlier executor anything; and
        it IS a new execution (a new sealed work item with a new execution id, which an
        adapter will dispatch as such), not a recovery. It is bounded by
        ``MAX_REASSIGNMENTS`` per work item. Only for the CURRENT work of a lineage that is
        executing; a lineage that has a result, is INTEGRATION_READY or terminal is refused.
        """
        pool = self._pool(executor_pool, executor_limit)
        if not pool:
            raise PlannerError("reassign needs a non-empty executor pool")
        self._needs_addressing(pool)

        def decide() -> dict[str, Any]:
            st = self._lineage_for(task_id)
            if st.phase not in _EXECUTING or task_id != st.work.task_id:
                raise PlannerError(
                    f"reassign refused: {task_id} is not the executing work of its lineage "
                    f"(phase {st.phase.value}, current work {st.work.task_id})"
                )
            if st.epoch >= MAX_REASSIGNMENTS:
                raise PlannerError(f"REASSIGN_LIMIT:{task_id} was reassigned {st.epoch} times")
            return {
                "event": "REASSIGN",
                "root": st.lineage_root,
                "work": encode(next_epoch_work(st.work, st.epoch + 1)),
                "executor": self._free_executor(pool, executor_limit, besides=st.lineage_root),
                "reason": reason,
            }

        ev = self._commit(decide)
        st = self.lineages[ev["root"]]
        self._publish_work(st)
        for seal in sorted(self.state.superseded):  # idempotent; only unclaimed records move
            self._withdraw(self.state.superseded[seal])
        return st.work

    def _observe_handover(
        self,
        st: LineageState,
        work: WorkItem,
        observed: dict[tuple[str, str], Mapping[str, str] | None],
    ) -> dict[str, Any] | None:
        """A handover entry for ``st``'s scope if the observer establishes one, else ``None``.

        Fail-closed: no observer, a lineage that is not INTEGRATION_READY, an observer that
        reports nothing, raises ``ValueError`` / ``OSError`` / ``TypeError``, or returns
        anything but a mapping that is bounded evidence all give ``None``, and the caller
        refuses the dispatch. Another exception type propagates.
        """
        res = st.result
        if self.observer is None or st.phase is not Phase.INTEGRATION_READY or res is None:
            return None
        key = (res.result_revision, work.base_revision)
        if key not in observed:
            found: dict[str, str] | None = None
            try:
                seen = self.observer.result_in_base(
                    repository=work.repository,
                    result_revision=res.result_revision,
                    base_revision=work.base_revision,
                )
                if isinstance(seen, Mapping) and _evidence_ok(dict(seen)):
                    found = dict(seen)
            except (ValueError, OSError, TypeError):  # incl. ContractError: not established
                found = None
            observed[key] = found
        evidence = observed[key]
        if evidence is None:
            return None
        return {
            "root": st.lineage_root,
            "basis": RESULT_IN_BASE,
            "result_revision": res.result_revision,
            "base_revision": work.base_revision,
            "observer": self.observer.identity,
            "evidence": dict(sorted(evidence.items())),
        }

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
            if st.executor and not same_identity(res.executor_identity, st.executor):
                raise PlannerError(
                    f"result is from {res.executor_identity}, not from {st.executor}, the "
                    "executor that owns this execution"
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
                repair: dict[str, Any] = {
                    "event": "REPAIR",
                    "root": root,
                    "verdict": encode(ver),
                    "work": encode(rw),
                }
                if st.executor:
                    repair["executor"] = st.executor  # the lineage stays with its executor
                return repair
            phase = Phase.OWNER_REQUIRED if decision.action == "OWNER_REQUIRED" else Phase.BLOCKED
            return self._terminal(st, phase, decision.reason, ver.seal)

        ev = self._commit(decide)
        if ev["event"] == "REPAIR":
            self._publish_work(self.lineages[ev["root"]])

    def fail_execution(self, task_id: str, reason: str) -> None:
        """The caller reports that the remote execution failed (no result): block that lineage.

        Only for the CURRENT work of a lineage that is executing (DISPATCHED or
        REPAIR_DISPATCHED). A lineage that already has a result, is INTEGRATION_READY or is
        terminal is refused, and so is a superseded task id: a wrong-phase or stale failure
        report does not release a scope. That is all this guards. The planner does not confirm
        that the remote executor has stopped or can no longer write. For that reason the
        report opens nothing: the lineage is BLOCKED and its paths stay closed to other work
        (``SCOPE_RETAINED``); there is no handover of a BLOCKED lineage's scope yet.
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
        """Record the caller's merge assertion for an INTEGRATION_READY lineage.

        ``merged_revision`` (40-hex) is recorded as the evidence the CALLER asserts; the planner
        cannot observe a merge and does not verify it, so the revision is not proof that
        integration occurred. This is not a merge, grants nothing and changes no phase. The
        lineage stops counting as a scope HOLDER (``in_flight``, ``scope_holders``), but its
        paths stay closed to other work: an overlapping dispatch is refused with
        ``SCOPE_RETAINED`` instead of ``SCOPE_COLLISION`` until a verified handover (see
        ``dispatch``), for which this assertion is neither needed nor sufficient.
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
      * it hands a scope over only on a verified release (ATLAS-DEVQ-0009), which is the
        journal's rule for every planner, not an option of this class: it does not call
        ``fail_execution`` or ``release_scope``, and nothing that overlaps the last work of an
        earlier lineage is admitted because of a phase, a timeout or an assertion. BLOCKED
        and OWNER_REQUIRED lineages, and INTEGRATION_READY ones released by a caller's
        ``release_scope``, keep their paths closed (``SCOPE_RETAINED``). The one handover:
        the ``observer`` given to the constructor reports that an INTEGRATION_READY lineage's
        verified result is an ancestor of the candidate's base revision (see
        ``Planner.dispatch``). Without an observer this coordinator hands no scope over, and
        its status says so (the lineage rows still show handovers other planners made). The scope
        of a BLOCKED or OWNER_REQUIRED lineage cannot be handed
        over at all yet: that needs evidence about its executor, which nothing here has
        (repositories are compared by ``repository_key``, as in ``works_collide``);
      * it does not fall back: it refuses a journal without store identity (``MemoryJournal``,
        a plain ``DirJournal``) and a transport that lacks ``claimed_records`` and
        ``published`` (a check of two method names, not of durability), and it refuses
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
      * status ``state`` is ``OK``, ``DEGRADED`` (the anchor holds repair records; the tick
        still ran) or ``HALTED`` (continuity failed, or an ``OSError`` from the store or the
        transport stopped the constructor or a tick, reason ``IO_ERROR:...``; written best
        effort). Any other failure, including a constructor refused for its configuration,
        leaves the previous status in place;
      * the status file is last-writer-wins and names no coordinator: one that fails to start
        for a reason local to it (a wrong transport or store) overwrites a shared status with
        ``HALTED`` until a healthy tick rewrites it;
      * executors are assigned only when the constructor is given ``executors``: each
        candidate then goes to the pool member that owns the fewest live lineages, and this
        coordinator assigns to no member that already owns ``executor_limit``
        (``EXECUTOR_BUSY``; another coordinator's limit or a ``reassign`` can exceed it). The
        status lists the live lineages each executor owns, from the journal. That needs a
        transport whose class attribute ``addressed`` is ``True`` (an attribute check, not a
        test of delivery). The coordinator never calls ``reassign``: it detects no dead
        executor and replaces none. A transport that refuses a record (``TransportError``,
        e.g. an address that cannot be read) stops the tick with a ``HALTED`` status.
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
        observer: HandoverObserver | None = None,
        executors: Sequence[str] = (),
        executor_limit: int = 1,
    ) -> None:
        if observer is not None and not _observer_identity_ok(getattr(observer, "identity", None)):
            raise PlannerError("a handover observer needs a non-empty identity")
        self.observer = observer
        self.executors = Planner._pool(executors, executor_limit)
        self.executor_limit = executor_limit
        if self.executors and getattr(transport, "addressed", False) is not True:
            raise PlannerError(
                "COORDINATOR_NEEDS_ADDRESSED_TRANSPORT:executors are assigned only over a "
                "transport that delivers a work record to the executor it is addressed to"
            )
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
                observer=observer,
            )
            self._continuity()
        except JournalCorrupt as exc:
            self._halted(exc)
            raise
        except OSError as exc:  # the store or the transport could not be read: never a stale OK
            self._halted(JournalCorrupt(f"IO_ERROR:{type(exc).__name__}: {exc}"))
            raise
        except TransportError as exc:
            self._halted(JournalCorrupt(f"TRANSPORT_ERROR:{exc}"))
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
        self.journal.repairs()  # unreadable repair evidence stops the tick before it acts
        self.planner.sync()  # restores a lost head acknowledgement, with a repair record
        state = self.planner.state  # a work fenced by a reassignment is still a known work
        known = {w.seal for w in state.works.values()} | set(state.superseded)
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
        the journal, the anchor, the transport listing or a publish is raised as it is, after
        a ``HALTED`` status naming it (``IO_ERROR``) was written if it can be written, so a
        tick stopped by a ``JournalCorrupt`` or an ``OSError`` replaces an earlier ``OK``
        whenever the status file can be written. A tick that raises anything else (an invalid
        candidate list, an exception of another type) leaves the previous status in place.
        An ``OSError`` from the status file itself is raised as it is; one from the
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
                    p.dispatch(
                        item,
                        live_limit=self.max_live,
                        executor_pool=self.executors,
                        executor_limit=self.executor_limit,
                        **work,
                    )
                except JournalContended:
                    deferred.append((item.task_id, "JOURNAL_CONTENDED"))
                    break  # the journal is busy: leave the rest for the next tick
                except (JournalCorrupt, TransportError):
                    raise  # a transport that refused a journalled record is not a refusal
                except (PlannerError, ValueError) as exc:  # incl. ContractError, QueueError
                    reason = str(exc)
                    if reason.startswith("LIVE_LIMIT:"):
                        break  # another coordinator filled the capacity since we looked
                    if reason.startswith("EXECUTOR_BUSY:"):
                        deferred.append((item.task_id, "EXECUTOR_BUSY"))
                        break  # no executor is free: the rest waits for the next tick
                    kept = reason.startswith(("SCOPE_COLLISION:", "SCOPE_RETAINED:"))
                    deferred.append((item.task_id, reason if kept else f"REFUSED:{reason}"))
                    continue
                admitted.append(item.task_id)
            rows = fleet_status(self.journal)
            repairs = [dict(r) for r in self.journal.repairs()]
        except JournalCorrupt as exc:
            self._halted(exc)
            raise
        except OSError as exc:
            self._halted(JournalCorrupt(f"IO_ERROR:{type(exc).__name__}: {exc}"))
            raise
        except TransportError as exc:  # e.g. a work record whose address cannot be read
            self._halted(JournalCorrupt(f"TRANSPORT_ERROR:{exc}"))
            raise
        # acknowledgement continuity that had to be repaired stays visible: never plain OK
        status = self._header("DEGRADED" if repairs else "OK") | {
            "acknowledgement_continuity": "REPAIRED" if repairs else "INTACT",
            "repairs": repairs,
            "journal": {"seq": p.state.seq, "head": p.state.head},
            "max_live": self.max_live,
            "executor_limit": self.executor_limit,
            # who owns what, from the journal: every executor of this coordinator's pool and
            # every executor a live lineage is assigned to, with the lineage roots it owns
            "executors": self._ownership(),
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
        {"self", "item", "live_limit", "execution_ordinal", "executor_pool", "executor_limit"}
    )

    def _ownership(self) -> dict[str, list[str]]:
        owned: dict[str, list[str]] = {e: [] for e in self.executors}
        by_key = {e.strip().casefold(): e for e in self.executors}
        for root, st in sorted(self.planner.lineages.items()):
            if st.executor and st.phase in _LIVE:
                name = by_key.setdefault(st.executor.strip().casefold(), st.executor)
                owned.setdefault(name, []).append(root)
        return dict(sorted(owned.items()))

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
            # who observes for a verified scope handover; NONE: this coordinator hands none over
            "scope_handover_observer": "NONE" if self.observer is None else self.observer.identity,
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
