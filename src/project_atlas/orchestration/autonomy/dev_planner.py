"""Planner coordination step for the development loop (AS-DEVLOOP-001, slice 2).

The planner role (VPS3 in the current deployment mapping) selects the next admissible task
(``dev_queue``), materializes a sealed ``WorkItem``, publishes it, turns results into verification
requests for a DIFFERENT identity, and turns verdicts into: integration-ready, bounded repair,
owner-required, or blocked. One blocked lineage never blocks the program.

Not a merge actor: INTEGRATION_READY means "verified candidate exists"; governance/merge admission
(merge gate, owner policy) is a separate, later, owner-bound step. This module never grants a gate.
"""

from __future__ import annotations

import hashlib
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


class Planner:
    def __init__(
        self,
        transport: DevTransport,
        *,
        identity: str,
        verifier_identities: tuple[str, ...],
    ) -> None:
        if not verifier_identities:
            raise PlannerError("at least one verifier identity is required")
        self.transport = transport
        self.identity = identity
        self.verifiers = verifier_identities
        self.lineages: dict[str, LineageState] = {}
        self._by_task: dict[str, str] = {}  # task_id -> lineage_root
        self._works: dict[str, WorkItem] = {}
        self.completed: set[str] = set()
        self.blocked: dict[str, str] = {}
        self.issued: dict[str, VerificationRequest] = {}  # task_id -> the request we issued
        self.quarantined: list[tuple[str, str, str]] = []  # (channel, seal, reason)

    # -- selection / dispatch -------------------------------------------------------------
    def select(self, items: list[QueueItem]) -> Selection:
        return select_next(items, completed=self.completed, blocked=self.blocked)

    def dispatch(self, item: QueueItem, **work_fields: object) -> WorkItem:
        """Materialize + publish the sealed work for an admissible queue item."""
        sel = self.select([item])
        if sel.selected is None:
            raise PlannerError(f"task not admissible: {sel.skipped}")
        if item.task_id in self.lineages:
            raise PlannerError("lineage already dispatched")
        work = make_work(
            task_id=item.task_id,
            execution_id=f"{item.task_id}-E1",
            lineage_root=item.task_id,
            required_role=Role.IMPLEMENTER,
            max_attempts=item.max_attempts,
            **work_fields,
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
        while rec := self.transport.claim(
            Channel.RESULT, role=Role.PLANNER, identity=self.identity
        ):
            assert isinstance(rec, ResultRecord)
            self._guarded("RESULT", rec.seal, self._on_result, rec)
            n += 1
        while rec := self.transport.claim(
            Channel.VERDICT, role=Role.PLANNER, identity=self.identity
        ):
            assert isinstance(rec, VerdictRecord)
            self._guarded("VERDICT", rec.seal, self._on_verdict, rec)
            n += 1
        return n

    def _guarded(self, channel: str, seal: str, fn: Callable[[Any], None], rec: Any) -> None:
        """One bad record is quarantined; it never aborts the pass or blocks other lineages."""
        try:
            fn(rec)
        except ContractError as exc:
            self.quarantined.append((channel, seal, str(exc)))

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
        req = self.issued.get(ver.task_id)
        if req is None or ver.request_seal != req.seal:
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

    def _terminal(self, st: LineageState, phase: Phase, reason: str) -> None:
        st.phase, st.reason = phase, reason
        st.history.append(f"{phase.value}:{reason}")
        self.blocked[st.lineage_root] = f"{phase.value}:{reason}"
