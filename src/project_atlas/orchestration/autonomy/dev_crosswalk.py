"""Identity crosswalk, verifier profiles and result ingestion (AS-DEVLOOP-001, slice 3).

The transport/adaptation layer owns the mapping between the identifiers different subsystems use
(loop: task_id/execution_id/lineage; orchestration: dispatch_id/lease_id; fabric: execution
evidence with base/result revisions). Properties: deterministic (dispatch/lease ids are derived from
the work seal), durable (append-only JSONL ledger, replayed and re-validated on open), auditable
(every hop records its seal), one-to-one where required, fail-closed on any ambiguity/conflict.

``ingest_report`` is the result path: a remote report is never "verified" by workflow success. A
success conclusion is necessary, not sufficient: the exact result revision/tree, the executor
identity and at least one artifact digest must be bound to the crosswalked work, otherwise refuse.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from project_atlas.orchestration.autonomy.dev_contracts import (
    ContractError,
    ResultRecord,
    VerdictRecord,
    VerificationRequest,
    WorkItem,
    make_result,
)


class CrosswalkError(ContractError):
    code = "DEV_CROSSWALK_REFUSED"


def _h(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:16]


def derive_dispatch_id(work: WorkItem) -> str:
    return "DSP-" + _h("dispatch", work.seal)


def derive_lease_id(work: WorkItem) -> str:
    return "LSE-" + _h("lease", work.seal)


_UNIQUE = ("execution_id", "dispatch_id", "lease_id", "work_seal")


class Crosswalk:
    """Append-only durable ledger keyed by work seal; replayed and validated on open."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._rows: dict[str, dict[str, Any]] = {}
        self._index: dict[tuple[str, str], str] = {}
        if self.path.exists():
            for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    ev = json.loads(line)
                    self._apply(ev)
                except (ValueError, KeyError, TypeError) as exc:
                    raise CrosswalkError(f"corrupt crosswalk ledger line {n}: {exc}") from exc

    # -- ledger ---------------------------------------------------------------------------
    def _append(self, ev: dict[str, Any]) -> None:
        self._apply(ev)  # validate first: a refused event is never written
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, sort_keys=True) + "\n")
            fh.flush()

    def _apply(self, ev: dict[str, Any]) -> None:
        kind, ws = ev["event"], ev["work_seal"]
        if kind == "WORK":
            row = {k: ev[k] for k in (*_UNIQUE, "task_id", "lineage_root", "attempt")}
            row.update(base_revision=ev["base_revision"], hops=[])
            if ws in self._rows:
                if {k: self._rows[ws][k] for k in row if k != "hops"} != {
                    k: v for k, v in row.items() if k != "hops"
                }:
                    raise CrosswalkError("work seal already bound to different identities")
                return
            for k in _UNIQUE:
                other = self._index.get((k, row[k]))
                if other is not None and other != ws:
                    raise CrosswalkError(f"{k} {row[k]} already bound to another work item")
            self._rows[ws] = row
            for k in _UNIQUE:
                self._index[(k, row[k])] = ws
            return
        cur = self._rows.get(ws)
        if cur is None:
            raise CrosswalkError(f"{kind} for an unbound work seal")
        hop = {k: v for k, v in ev.items() if k not in ("work_seal",)}
        same = [h for h in cur["hops"] if h["event"] == kind]
        if kind in ("RESULT", "VERIFICATION", "DISPATCH", "RUN"):
            if same:
                if same[0] != hop:
                    raise CrosswalkError(f"conflicting second {kind} for one work item")
                return
        elif kind == "VERDICT":
            if any(h == hop for h in same):
                return
            res = next((h for h in cur["hops"] if h["event"] == "RESULT"), None)
            if res is None or (res["result_revision"], res["result_tree"]) != (
                hop["result_revision"],
                hop["result_tree"],
            ):
                raise CrosswalkError("verdict does not judge the crosswalked result artifact")
            if same:
                raise CrosswalkError("conflicting second VERDICT for one work item")
        else:
            raise CrosswalkError(f"unknown event {kind}")
        cur["hops"].append(hop)

    # -- binding --------------------------------------------------------------------------
    def bind_work(self, work: WorkItem) -> tuple[str, str]:
        work.verify_seal()
        d, ls = derive_dispatch_id(work), derive_lease_id(work)
        self._append(
            {
                "event": "WORK",
                "work_seal": work.seal,
                "task_id": work.task_id,
                "execution_id": work.execution_id,
                "lineage_root": work.lineage_root,
                "attempt": work.attempt,
                "base_revision": work.base_revision,
                "dispatch_id": d,
                "lease_id": ls,
            }
        )
        return d, ls

    def bind_result(self, res: ResultRecord) -> None:
        res.verify_seal()
        row = self._rows.get(res.work_seal)
        if row is None or row["execution_id"] != res.execution_id:
            raise CrosswalkError("result does not match any crosswalked dispatch")
        self._append(
            {
                "event": "RESULT",
                "work_seal": res.work_seal,
                "result_seal": res.seal,
                "result_revision": res.result_revision,
                "result_tree": res.result_tree,
                "executor_identity": res.executor_identity,
            }
        )

    def bind_verification(self, work_seal: str, req: VerificationRequest) -> None:
        req.verify_seal()
        self._append(
            {
                "event": "VERIFICATION",
                "work_seal": work_seal,
                "verification_seal": req.seal,
                "result_seal": req.result_seal,
                "verifier_identity": req.verifier_identity,
            }
        )

    def bind_verdict(self, work_seal: str, ver: VerdictRecord) -> None:
        ver.verify_seal()
        self._append(
            {
                "event": "VERDICT",
                "work_seal": work_seal,
                "verdict_seal": ver.seal,
                "verdict": ver.verdict.value,
                "result_revision": ver.result_revision,
                "result_tree": ver.result_tree,
            }
        )

    def bind_dispatch(self, work_seal: str, *, dispatched_at: str, payload_sha256: str) -> None:
        """Write-ahead record: a work item is dispatched at most once (never re-dispatched)."""
        self._append(
            {
                "event": "DISPATCH",
                "work_seal": work_seal,
                "dispatched_at": dispatched_at,
                "payload_sha256": payload_sha256,
            }
        )

    def bind_run(self, work_seal: str, *, run_id: int, run_attempt: int, branch: str) -> None:
        self._append(
            {
                "event": "RUN",
                "work_seal": work_seal,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "branch": branch,
            }
        )

    def hop(self, work_seal: str, kind: str) -> dict[str, Any] | None:
        row = self._rows.get(work_seal)
        if row is None:
            raise CrosswalkError("unbound work seal")
        return next((h for h in row["hops"] if h["event"] == kind), None)

    def branch_for_revision(self, revision: str) -> str | None:
        """Result branch that produced ``revision`` (used as repair base); ambiguity => refuse."""
        found = {
            run["branch"]
            for row in self._rows.values()
            for res in row["hops"]
            if res["event"] == "RESULT" and res["result_revision"] == revision
            for run in row["hops"]
            if run["event"] == "RUN"
        }
        if len(found) > 1:
            raise CrosswalkError(f"revision {revision} maps to several branches")
        return next(iter(found), None)

    # -- resolution -----------------------------------------------------------------------
    def resolve(self, key: str, value: str) -> dict[str, Any]:
        """Resolve by execution_id / dispatch_id / lease_id / work_seal; unknown => refuse."""
        ws = self._index.get((key, value))
        if ws is None:
            raise CrosswalkError(f"unresolvable {key}={value}")
        return self._rows[ws]

    def lineage(self, lineage_root: str) -> list[dict[str, Any]]:
        rows = [r for r in self._rows.values() if r["lineage_root"] == lineage_root]
        return sorted(rows, key=lambda r: r["attempt"])


# -- verifier profiles ---------------------------------------------------------------------

PROFILES = ("github_hosted", "vps2_independent")


@dataclass(frozen=True)
class VerifierProfile:
    """Deployment mapping of the VERIFIER role; contracts are identical across profiles."""

    name: str
    identities: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.name not in PROFILES:
            raise CrosswalkError(f"unknown verifier profile {self.name}")
        if not self.identities:
            raise CrosswalkError("verifier profile has no identities")

    def for_executor(self, executor_identity: str) -> tuple[str, ...]:
        return tuple(i for i in self.identities if i != executor_identity)


# -- result ingestion ----------------------------------------------------------------------


class RemoteExecutionReport(BaseModel):
    """What a remote executor/fabric reports. Not a verdict, not proof by itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    workflow_conclusion: str
    task_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    base_revision: str
    executor_identity: str = Field(min_length=1)
    result_revision: str | None = None
    result_tree: str | None = None
    artifact_digests: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    test_evidence_digests: tuple[str, ...] = ()


def ingest_report(report: RemoteExecutionReport, work: WorkItem, xw: Crosswalk) -> ResultRecord:
    """Turn a remote report into a bound ResultRecord or refuse. Workflow success != result."""
    work.verify_seal()
    row = xw.resolve("work_seal", work.seal)
    if report.workflow_conclusion != "success":
        raise CrosswalkError(f"remote execution did not succeed: {report.workflow_conclusion}")
    if (report.task_id, report.execution_id) != (work.task_id, work.execution_id):
        raise CrosswalkError("report identity does not match the dispatched work")
    if report.execution_id != row["execution_id"]:
        raise CrosswalkError("report execution_id is not the crosswalked execution")
    if report.base_revision != work.base_revision:
        raise CrosswalkError("report base revision differs from the sealed work base")
    if not report.result_revision or not report.result_tree:
        raise CrosswalkError("success without an exact result revision/tree is not a result")
    if report.result_revision == work.base_revision:
        raise CrosswalkError("result revision equals base: no artifact was produced")
    if not report.artifact_digests:
        raise CrosswalkError("no artifact digest bound to the result")
    forbidden = [
        p for p in report.changed_paths if any(p.startswith(f) for f in work.forbidden_paths)
    ]
    if forbidden:
        raise CrosswalkError(f"result touches forbidden paths: {forbidden}")
    if work.allowed_paths:
        outside = [
            p for p in report.changed_paths if not any(p.startswith(a) for a in work.allowed_paths)
        ]
        if outside:
            raise CrosswalkError(f"result touches paths outside allowed_paths: {outside}")
    res = make_result(
        work,
        executor_identity=report.executor_identity,
        result_revision=report.result_revision,
        result_tree=report.result_tree,
        changed_paths=report.changed_paths,
        test_evidence_digests=report.test_evidence_digests,
        artifact_digests=report.artifact_digests,
    )
    xw.bind_result(res)
    return res
