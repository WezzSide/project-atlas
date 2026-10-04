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
    replacement of ACKNOWLEDGED history is never permission to admit or publish. An event is
    acknowledged once its digest is in the journal's anchor (``DirJournal``: one exclusive file
    per event in a separate directory), which happens after the append and before anything is
    published for it. Every replay, and so every operation that decides or publishes, first
    checks that the head this planner holds is still stored unchanged, that each event it
    applies matches its acknowledgement, and that the journal does not end below the highest
    acknowledged event. Where that cannot be established the operation raises
    ``JournalCorrupt`` and nothing is appended or published: a planner whose history was lost
    or replaced under it stops, and a new planner does not start on the shortened journal;
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
    after such a loss) while the anchor survives, and the process may die at any point. NOT
    covered: journal and anchor lost or rolled back TOGETHER (the default anchor is a sibling
    directory, so a rollback of their common parent takes both; a new planner then accepts the
    shorter history, while a planner that was running still stops); a writer who rewrites the
    journal and the anchor; an event lost after the last continuity check of an operation that
    had already been acknowledged (the record is published; every later operation stops);
    loss of an event that was appended but not yet acknowledged, which is by definition not
    acknowledged history and for which nothing was published;
  * failing closed is the whole response: there is no repair or re-anchoring tool here, an
    operator has to restore the journal;
  * ``recover`` must be called by whoever restarts a planner; nothing in ``src`` does that yet;
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
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
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
from project_atlas.orchestration.autonomy.dev_queue import QueueItem, Selection, select_next
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
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_EXECUTING = frozenset({Phase.DISPATCHED, Phase.REPAIR_DISPATCHED})


class Journal(Protocol):
    def read(self, after: int) -> list[bytes]:
        """Raw events with sequence number > ``after``, contiguous and in order."""

    def append(self, seq: int, data: bytes) -> bool:
        """Create event ``seq`` iff it is the next one; False when it already exists."""

    def acknowledge(self, seq: int, digest: str) -> None:
        """Record that event ``seq`` with this sha256 is acknowledged history.

        Raises ``JournalCorrupt`` when the stored event is not that event (any more) or when
        ``seq`` is already acknowledged with a different digest. Idempotent otherwise.
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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


class DirJournal:
    """Durable journal: one immutable file per event, ``<root>/<seq:012d>.json``.

    ``append`` writes a temp file, fsyncs it and creates the final name with ``os.link``, which
    never overwrites: of several processes appending the same sequence number exactly one
    succeeds. Needs a directory with atomic ``link`` (local disk, a mounted volume).

    Acknowledgement anchor: ``<anchor>/<seq:012d>.ack`` holds the sha256 of event ``seq`` and is
    created the same way (exclusive, never overwritten) once the event is in the journal. The
    anchor is the witness that survives a loss of journal files: a journal that ends below the
    highest acknowledged number, or whose event differs from its acknowledgement, does not
    replay. ``anchor`` defaults to the sibling directory ``<root>.ack``; put it on storage that
    does not fail together with ``root`` to cover more than the loss of event files.
    """

    def __init__(self, root: Path, *, anchor: Path | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.anchor = (
            self.root.with_name(self.root.name + ".ack") if anchor is None else Path(anchor)
        )
        self.anchor.mkdir(parents=True, exist_ok=True)

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
                os.unlink(tmp)
            known = self.acknowledged(seq)
        if known != digest:
            raise JournalCorrupt(
                f"JOURNAL_DIVERGED:event {seq} is acknowledged as a different event"
            )

    def high_water(self, at_least: int, *, full: bool) -> int:
        if not full:
            return at_least + 1 if self._ack(at_least + 1).exists() else at_least
        names = [p.name for p in self.anchor.iterdir() if not p.name.startswith(".tmp-")]
        odd = sorted(n for n in names if not _ACK_FILE.fullmatch(n) or int(n[:12]) < 1)
        if odd:
            raise JournalCorrupt(f"JOURNAL_CORRUPT:anchor holds something else: {odd[:3]}")
        return max((int(n[:12]) for n in names), default=at_least)

    def read(self, after: int) -> list[bytes]:
        last = 0
        if after == 0:  # full open: nothing but event files, and (below) no gap before the last
            names = sorted(p.name for p in self.root.iterdir() if not p.name.startswith(".tmp-"))
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
            os.unlink(tmp)
        with contextlib.suppress(OSError):  # directory fsync is best effort (not on Windows)
            dfd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        return True


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


def replay(journal: Journal, state: FleetState, *, witness: bool = False) -> int:
    """Apply every journal event ``state`` has not seen; returns how many. Fail-closed.

    Continuity is established on every call, before anything is applied and before the caller
    may decide or publish anything:
      * the event ``state`` already holds as its head must still be stored with the same digest
        (a planner whose history was lost or replaced under it stops here);
      * every event applied must match its acknowledgement where one exists;
      * the journal may not end below the highest acknowledged sequence number (a journal that
        lost its tail does not replay, however well-formed what is left of it is).
    With ``witness`` (a planner, not a read-only projection) each applied event is acknowledged.
    """
    full = state.seq == 0
    if not full:
        journal.check_head(state.seq, state.head)
    n = 0
    while True:
        batch = journal.read(state.seq)
        for raw in batch:
            seq = state.seq + 1
            try:
                ev = json.loads(raw)
                if not isinstance(ev, dict):
                    raise PlannerError("event is not an object")
                apply = _transition(state, ev, raw)
            except (ContractError, ValueError, KeyError, TypeError, RecursionError) as exc:
                raise JournalCorrupt(f"JOURNAL_CORRUPT:event {seq}: {exc}") from exc
            digest = _sha(raw)
            if journal.acknowledged(seq) not in (None, digest):
                raise JournalCorrupt(
                    f"JOURNAL_DIVERGED:event {seq} is acknowledged as a different event"
                )
            if witness:
                journal.acknowledge(seq, digest)
            apply()
            n += 1
        mark = journal.high_water(state.seq, full=full)
        if mark <= state.seq:
            return n
        if not batch and not journal.read(state.seq):
            # acknowledged beyond the journal and nothing more to read: the tail is gone
            raise JournalCorrupt(
                f"JOURNAL_TRUNCATED:event {mark} is acknowledged but the journal ends at "
                f"{state.seq}"
            )
        full = False  # a writer got ahead while we read (event before acknowledgement): go on


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
        """Replay journal events written since the last look (by this or another planner)."""
        return replay(self.journal, self.state, witness=True)

    def _commit(self, decide: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Decide against the current journal state and append the ONE resulting event.

        ``decide`` runs after a fresh replay and raises to refuse. If another writer takes the
        sequence number first, nothing was written: replay and decide again, a bounded number
        of times. The appended event is acknowledged (read back and anchored) before the
        in-memory state changes and before this returns, so the caller publishes only for an
        event that is acknowledged history; if that fails this raises and nothing is published.
        """
        for _ in range(MAX_COMMIT_RETRIES):
            self.sync()
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
                self.journal.acknowledge(ev["seq"], _sha(raw))
                apply()
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
        self, item: QueueItem, *, execution_ordinal: int = 1, **work_fields: object
    ) -> WorkItem:
        """Materialize + publish the sealed work for an admissible queue item.

        Before anything is published or recorded the sealed work is compared with the current
        work of every scope holder (``works_collide``). On an overlap this raises ``PlannerError``
        ``SCOPE_COLLISION:<holder lineage root>:<new>|<held>[,<new>|<held>...]`` for the first
        colliding holder in lineage-root order, with all of its colliding pairs as normalised
        comparison keys in sorted order. Nothing is published, no state changes, and the task id
        stays usable for a later dispatch. A holder whose seal no longer verifies is an error.
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
        ``OSError`` from the journal or the transport; a record claimed by then and not yet
        journalled is kept in ``deferred``. Bounded per channel by ``MAX_RAISES_PER_PASS``.
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
        """Release the scope of an INTEGRATION_READY lineage whose candidate was merged.

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
