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
off. This is admission control, not authority: it grants nothing and it is not a concurrency
guarantee. Limits:
  * in-memory only: a planner restart forgets every holder;
  * one planner process: nothing is guaranteed across processes or hosts;
  * path overlap only: no semantic conflict detection (a generated file two lineages both rewrite,
    a whole-suite acceptance command);
  * the planner cannot observe a merge, so an INTEGRATION_READY lineage holds its scope for the
    lifetime of this planner object; nothing here releases it;
  * it does not lift the fabric adapter's serial-dispatch rule.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

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
from project_atlas.orchestration.autonomy.dev_transport import Channel, DevTransport


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


class PlannerError(ContractError):
    code = "DEV_PLANNER_REFUSED"


MAX_QUARANTINE = 1000  # bounded evidence: a flooding publisher cannot grow memory without limit
MAX_RAISES_PER_PASS = 64  # a transport that keeps raising without consuming must not loop forever
_REPAIR_SUFFIX = re.compile(r"-R[0-9]+$")  # reserved for planner-materialised repair tasks


class Planner:
    def __init__(
        self,
        transport: DevTransport,
        *,
        identity: str,
        verifier_identities: tuple[str, ...],
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
        self.lineages: dict[str, LineageState] = {}
        self._by_task: dict[str, str] = {}  # task_id -> lineage_root
        self._works: dict[str, WorkItem] = {}
        self.completed: set[str] = set()
        self.blocked: dict[str, str] = {}
        self.issued: dict[str, VerificationRequest] = {}  # task_id -> the request we issued
        self.quarantined: list[tuple[str, str, str]] = []  # (channel, seal, reason)

    # -- selection / dispatch -------------------------------------------------------------
    def in_flight(self) -> frozenset[str]:
        """Lineage roots (task ids) whose phase is scope-holding (see ``SCOPE_HOLDING``)."""
        return frozenset(r for r, st in self.lineages.items() if st.phase in SCOPE_HOLDING)

    def scope_holders(self) -> tuple[WorkItem, ...]:
        """The CURRENT sealed work of every scope-holding lineage, ordered by lineage root.

        For a lineage in repair this is its current repair work, which carries the lineage's
        original ``allowed_paths`` (``materialize_repair`` never changes scope). In-memory view
        of this planner object only.
        """
        return tuple(
            st.work for _, st in sorted(self.lineages.items()) if st.phase in SCOPE_HOLDING
        )

    def select(self, items: list[QueueItem]) -> Selection:
        """Next ranked admissible item, skipping tasks that are in flight (``IN_FLIGHT``).

        Selection does not look at write scopes (a ``QueueItem`` carries none); a selected item
        can still be refused by ``dispatch`` with ``SCOPE_COLLISION``.
        """
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
        See the module docstring for what this check does not cover.
        """
        # admission as before ATLAS-DEVQ-0006 (no in-flight filter here), so a repeated dispatch
        # of a live task id keeps its existing refusal below
        sel = select_next([item], completed=self.completed, blocked=self.blocked)
        if sel.selected is None:
            raise PlannerError(f"task not admissible: {sel.skipped}")
        if execution_ordinal < 1:
            raise PlannerError("execution_ordinal must be >= 1")
        if item.task_id in self.lineages or item.task_id in self._by_task:
            raise PlannerError("lineage/task id already in use")
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
        self.transport.publish(work)
        self.lineages[item.task_id] = LineageState(
            item.task_id, Phase.DISPATCHED, work, history=[f"DISPATCHED:{work.task_id}"]
        )
        self._by_task[work.task_id] = item.task_id
        self._works[work.task_id] = work
        return work

    # -- pump -------------------------------------------------------------------------------
    def pump(self) -> int:
        """Consume every available result and verdict once; returns records processed."""
        n = 0
        for channel, handler in (
            (Channel.RESULT, self._on_result),
            (Channel.VERDICT, self._on_verdict),
        ):
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
        """One bad record is quarantined; it never aborts the pass or blocks other lineages."""
        try:
            fn(rec)
        except ContractError as exc:
            self._quarantine(channel, seal, str(exc))

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
        st = self._lineage_for(res.task_id)
        work = st.work
        if (
            st.phase not in (Phase.DISPATCHED, Phase.REPAIR_DISPATCHED)
            or res.task_id != work.task_id
        ):
            raise PlannerError("result is not expected in this phase / for the current work")
        if res.work_seal != work.seal:
            raise PlannerError("result does not answer the dispatched work")
        if (res.execution_id, res.repository, res.base_revision) != (
            work.execution_id,
            work.repository,
            work.base_revision,
        ):
            raise PlannerError("result identity/repository/base does not match the dispatched work")
        others = [v for v in self.verifiers if not same_identity(v, res.executor_identity)]
        if not others:
            self._terminal(st, Phase.BLOCKED, "NO_INDEPENDENT_VERIFIER")
            return
        # deterministic (no PYTHONHASHSEED dependence): stable index from the task id digest
        verifier = others[int(hashlib.sha256(res.task_id.encode()).hexdigest(), 16) % len(others)]
        req = make_verification_request(work, res, verifier_identity=verifier)
        self.transport.publish(req)
        self.issued[res.task_id] = req
        st.result = res
        st.phase = Phase.VERIFYING
        st.history.append(f"VERIFYING:{res.task_id}:{verifier}")

    def _on_verdict(self, ver: VerdictRecord) -> None:
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
        if ver.verdict is Verdict.PASS:
            st.phase = Phase.INTEGRATION_READY
            st.history.append(f"INTEGRATION_READY:{ver.task_id}")
            self.completed.add(st.lineage_root)
            return
        decision = materialize_repair(work, ver, result=res)
        if decision.action == "REPAIR":
            assert decision.repair_work is not None
            rw = decision.repair_work
            if rw.task_id in self._by_task:
                self._terminal(st, Phase.BLOCKED, "REPAIR_TASK_ID_COLLISION")
                return
            self.transport.publish(rw)
            self._works[rw.task_id] = rw
            self._by_task[rw.task_id] = st.lineage_root
            st.work, st.result = rw, None
            st.phase = Phase.REPAIR_DISPATCHED
            st.history.append(f"REPAIR_DISPATCHED:{rw.task_id}")
        elif decision.action == "OWNER_REQUIRED":
            self._terminal(st, Phase.OWNER_REQUIRED, decision.reason)
        else:
            self._terminal(st, Phase.BLOCKED, decision.reason)

    def fail_execution(self, task_id: str, reason: str) -> None:
        """The remote execution itself failed (no result): block that lineage only."""
        st = self._lineage_for(task_id)
        self._terminal(st, Phase.BLOCKED, f"EXECUTION_FAILED:{reason}")

    def _terminal(self, st: LineageState, phase: Phase, reason: str) -> None:
        st.phase, st.reason = phase, reason
        st.history.append(f"{phase.value}:{reason}")
        self.blocked[st.lineage_root] = f"{phase.value}:{reason}"
