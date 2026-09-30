"""GitHub/runner-fabric adapter for the development loop (AS-DEVLOOP-001, slice 3 glue).

Connects the sealed loop contracts to the EXISTING execution path and adds no scheduler, queue,
result plane or verifier protocol:

    WorkItem --(workflow_dispatch)--> atlas-agent-execute.yml on the self-hosted executor
      --> dedicated atlas/agent-<run>-<attempt> branch + evidence artifact
      --> exact result discovery --> ingest_report (Crosswalk-bound ResultRecord)
      --> existing independent GitHub-hosted verifier (atlas-runner-verify.yml, workflow_run)
      --> verdict = evidence VERIFIED  AND  task CI green on the result head  --> VerdictRecord

Truth boundaries: workflow success != result; executor success != verified; verifier evidence
VERIFIED alone != code acceptance (the result PR's CI on the exact head is required as well);
nothing here merges, and nothing here reads or needs any secret value. All GitHub access goes
through the injected ``GitHubPort`` (a fake in tests, ``GitHubRestPort`` live).

Crash safety: DISPATCH is written ahead to the Crosswalk ledger, so a work item is dispatched at
most once; run discovery is fail-closed on ambiguity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from project_atlas.orchestration.autonomy.dev_contracts import (
    ContractError,
    Finding,
    FindingCategory,
    ResultRecord,
    Role,
    Verdict,
    VerdictRecord,
    VerificationRequest,
    WorkItem,
    make_verdict,
)
from project_atlas.orchestration.autonomy.dev_crosswalk import (
    Crosswalk,
    CrosswalkError,
    RemoteExecutionReport,
    ingest_report,
)
from project_atlas.orchestration.autonomy.dev_transport import (
    Channel,
    DevTransport,
    decode,
    encode,
)

EXECUTE_WORKFLOW = "atlas-agent-execute.yml"
VERIFY_WORKFLOW = "atlas-runner-verify.yml"
EVIDENCE_ARTIFACT = "atlas-agent-execute-evidence"
REPORT_ARTIFACT = "atlas-verification-report"
DEFAULT_BRANCH = "main"
AGENT_BRANCH = re.compile(r"^atlas/agent-([0-9]+)-([0-9]+)$")
EXECUTOR_IDENTITY = "fabric:vps-02-executor"
VERIFIER_IDENTITY = "github-hosted:atlas-runner-verify"
_SHA = re.compile(r"^[0-9a-f]{40}$")


class AdapterError(ContractError):
    code = "DEV_FABRIC_REFUSED"


class RemoteExecutionFailed(AdapterError):
    code = "DEV_FABRIC_EXECUTION_FAILED"


@dataclass(frozen=True)
class RunInfo:
    run_id: int
    attempt: int
    workflow: str
    event: str
    status: str  # queued | in_progress | completed
    conclusion: str | None
    head_branch: str
    created_at: str


@dataclass(frozen=True)
class CheckRun:
    name: str
    status: str
    conclusion: str | None


class DispatchDeferred(AdapterError):
    code = "DEV_FABRIC_DEFERRED"


class AmbiguousRun(AdapterError):
    code = "DEV_FABRIC_AMBIGUOUS_RUN"


DEFAULT_REQUIRED_CHECKS = frozenset(
    {
        "control-plane",
        "quality (ubuntu-latest, 3.12, full)",
        "quality (ubuntu-latest, 3.13, compat)",
        "quality (windows-latest, 3.12, windows)",
    }
)
CLOCK_SKEW = timedelta(seconds=120)
_OK = frozenset({"success", "skipped", "neutral"})


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _shift(iso: str, delta: timedelta) -> str:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")) - delta
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class CompareInfo:
    merge_base: str
    files: tuple[str, ...]


class GitHubPort(Protocol):
    def dispatch_workflow(self, workflow: str, ref: str, inputs: dict[str, str]) -> None: ...
    def list_runs(self, workflow: str, *, event: str, created_after: str) -> list[RunInfo]: ...
    def get_run(self, run_id: int) -> RunInfo: ...
    def branch_head(self, branch: str) -> str | None: ...
    def commit_tree(self, sha: str) -> str: ...
    def compare(self, base: str, head: str) -> CompareInfo: ...
    def artifact_digests(self, run_id: int, name: str) -> tuple[str, ...]: ...
    def read_json_artifact(self, run_id: int, name: str, member: str) -> dict[str, object]: ...
    def check_runs(self, sha: str) -> list[CheckRun]: ...
    def ensure_draft_pr(self, head_branch: str, base: str, title: str, body: str) -> int: ...


# -- exact dispatch payload ----------------------------------------------------------------


@dataclass(frozen=True)
class DispatchPayload:
    workflow: str
    ref: str
    inputs: dict[str, str]

    def sha256(self) -> str:
        blob = json.dumps(
            {"workflow": self.workflow, "ref": self.ref, "inputs": self.inputs}, sort_keys=True
        )
        return hashlib.sha256(blob.encode()).hexdigest()


def build_dispatch_payload(
    work: WorkItem, *, base_branch: str, task_statement: str, acceptance_commands: tuple[str, ...]
) -> DispatchPayload:
    """Deterministic workflow inputs for one sealed work item (no secret values)."""
    work.verify_seal()
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", base_branch) or ".." in base_branch:
        raise AdapterError("unsafe base_branch")
    lines = [
        f"Atlas dev-loop task {work.task_id} | execution {work.execution_id} | seal {work.seal}",
        f"Repository {work.repository}; base revision {work.base_revision}; attempt {work.attempt}"
        f"/{work.max_attempts}; authority {work.authority_ref}.",
        task_statement,
        f"ONLY modify paths under: {', '.join(work.allowed_paths)}.",
        f"NEVER modify: {', '.join(work.forbidden_paths) or '(none)'}.",
        "Acceptance (task-specific, all required; run them and keep them passing):",
        *[f"  - {c}" for c in work.acceptance_contract],
        *[f"  - run: {c}" for c in acceptance_commands],
        "New tests must fail on the base revision and pass after your change. The infra runner "
        "tests alone are NOT sufficient acceptance for this task.",
    ]
    return DispatchPayload(
        workflow=EXECUTE_WORKFLOW,
        ref=DEFAULT_BRANCH,
        inputs={
            "task_prompt": "\n".join(lines),
            "base_branch": base_branch,
            "agent_type": "claude",
        },
    )


def _sha(v: str | None) -> str:
    if not v or not _SHA.match(v):
        raise AdapterError("expected a 40-hex sha")
    return v


# -- adapter -------------------------------------------------------------------------------


class FabricAdapter:
    """Implementer-side and verifier-side proxy of the remote path over ``DevTransport``."""

    def __init__(
        self,
        port: GitHubPort,
        transport: DevTransport,
        crosswalk: Crosswalk,
        *,
        pending_dir: Path,
        clock: Callable[[], str],
        task_statement: Callable[[WorkItem], tuple[str, tuple[str, ...]]],
        executor_identity: str = EXECUTOR_IDENTITY,
        verifier_identity: str = VERIFIER_IDENTITY,
        required_checks: frozenset[str] = DEFAULT_REQUIRED_CHECKS,
    ) -> None:
        if not required_checks:
            raise AdapterError("a non-empty required check set is mandatory")
        self.required_checks = required_checks
        self.port, self.transport, self.xw = port, transport, crosswalk
        self.pending = Path(pending_dir)
        self.pending.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.task_statement = task_statement
        self.executor_identity = executor_identity
        self.verifier_identity = verifier_identity
        self.works: dict[str, WorkItem] = {}  # seal -> work (in-flight in this process)
        self._load_works()

    # durable in-flight set: works claimed from the WORK channel are persisted before dispatch
    def _work_file(self, seal: str) -> Path:
        return self.pending / f"work-{seal}.json"

    def _load_works(self) -> None:
        for f in sorted(self.pending.glob("work-*.json")):
            w = decode(f.read_text(encoding="utf-8"))
            assert isinstance(w, WorkItem)
            self.works[w.seal] = w

    # -- implementer side --------------------------------------------------------------------
    def accept_work(self) -> WorkItem | None:
        w = self.transport.claim(
            Channel.WORK, role=Role.IMPLEMENTER, identity=self.executor_identity
        )
        if w is None:
            return None
        assert isinstance(w, WorkItem)
        _atomic_write(self._work_file(w.seal), encode(w))
        self.works[w.seal] = w
        self.xw.bind_work(w)
        return w

    def dispatch(self, work: WorkItem) -> DispatchPayload:
        work.verify_seal()
        self.xw.bind_work(work)
        if self.xw.hop(work.seal, "DISPATCH") is not None:
            raise AdapterError("work item already dispatched; refusing to dispatch twice")
        if self._unbound_dispatches(exclude=work.seal):
            raise DispatchDeferred("another dispatch has not been bound to a run yet")
        if work.attempt == 1:
            base_branch = DEFAULT_BRANCH
        else:
            found = self.xw.branch_for_revision(work.base_revision)
            if found is None:
                raise AdapterError("repair base revision has no known result branch")
            base_branch = found
        if self.port.branch_head(base_branch) != work.base_revision:
            raise AdapterError(f"{base_branch} is not at the sealed base revision")
        statement, commands = self.task_statement(work)
        payload = build_dispatch_payload(
            work,
            base_branch=base_branch,
            task_statement=statement,
            acceptance_commands=commands,
        )
        # write-ahead: from here on this work item can never be dispatched again
        self.xw.bind_dispatch(
            work.seal, dispatched_at=self.clock(), payload_sha256=payload.sha256()
        )
        try:
            self.port.dispatch_workflow(payload.workflow, payload.ref, payload.inputs)
        except AdapterError as exc:  # write-ahead already recorded: never retried, surfaced
            raise RemoteExecutionFailed(f"dispatch failed: {exc}") from exc
        return payload

    def _unbound_dispatches(self, *, exclude: str = "") -> list[str]:
        """Seals dispatched but not yet bound to a run: correlation is serialised through these."""
        return [
            seal
            for seal in self.works
            if seal != exclude
            and self.xw.hop(seal, "DISPATCH") is not None
            and self.xw.hop(seal, "RUN") is None
        ]

    def locate_run(self, work: WorkItem) -> RunInfo | None:
        """The single workflow_dispatch run created after our dispatch; ambiguity => refuse."""
        if (h := self.xw.hop(work.seal, "RUN")) is not None:
            return self.port.get_run(int(h["run_id"]))
        disp = self.xw.hop(work.seal, "DISPATCH")
        if disp is None:
            raise AdapterError("work item was not dispatched")
        runs = [
            r
            for r in self.port.list_runs(
                EXECUTE_WORKFLOW,
                event="workflow_dispatch",
                created_after=_shift(str(disp["dispatched_at"]), CLOCK_SKEW),
            )
            if r.head_branch == DEFAULT_BRANCH
        ]
        already = {
            int(h["run_id"])
            for row in self.works
            for h in [self.xw.hop(row, "RUN")]
            if h is not None
        }
        runs = [r for r in runs if r.run_id not in already]
        if len(runs) > 1:
            raise AmbiguousRun("ambiguous run correlation; refusing to guess")
        if not runs:
            return None
        r = runs[0]
        self.xw.bind_run(
            work.seal,
            run_id=r.run_id,
            run_attempt=r.attempt,
            branch=f"atlas/agent-{r.run_id}-{r.attempt}",
        )
        return r

    def collect_result(self, work: WorkItem) -> bool:
        """Discover + ingest the exact result. False while still pending; raises on failure."""
        if self.xw.hop(work.seal, "RESULT") is not None:
            return True
        run = self.locate_run(work)
        if run is None or run.status != "completed":
            return False
        if run.conclusion != "success":
            raise RemoteExecutionFailed(f"run {run.run_id} concluded {run.conclusion}")
        hop = self.xw.hop(work.seal, "RUN")
        assert hop is not None
        branch = str(hop["branch"])
        if not AGENT_BRANCH.match(branch):
            raise AdapterError("unexpected result branch shape")
        head = self.port.branch_head(branch)
        if head is None:
            raise RemoteExecutionFailed("no result branch: the executor produced no change")
        cmp = self.port.compare(work.base_revision, _sha(head))
        if cmp.merge_base != work.base_revision:
            raise AdapterError("result branch does not descend from the sealed base revision")
        report = RemoteExecutionReport(
            workflow_conclusion=run.conclusion,
            task_id=work.task_id,
            execution_id=work.execution_id,
            base_revision=work.base_revision,
            executor_identity=self.executor_identity,
            result_revision=head,
            result_tree=self.port.commit_tree(head),
            artifact_digests=self.port.artifact_digests(run.run_id, EVIDENCE_ARTIFACT),
            changed_paths=cmp.files,
        )
        res = ingest_report(report, work, self.xw, bind=False)
        # crash-safe order: durable copy -> ledger -> publish (recover() finishes any prefix)
        _atomic_write(self._result_file(res.work_seal), encode(res))
        self.xw.bind_result(res)
        self.transport.publish(res)
        return True

    def _result_file(self, seal: str) -> Path:
        return self.pending / f"result-{seal}.json"

    def recover(self) -> list[str]:
        """Finish interrupted hops: bind + (idempotently) publish results/verdicts on disk."""
        out: list[str] = []
        for f in sorted(self.pending.glob("result-*.json")):
            rec = decode(f.read_text(encoding="utf-8"))
            assert isinstance(rec, ResultRecord)
            if self.xw.hop(rec.work_seal, "RESULT") is None:
                self.xw.bind_result(rec)
            if self.transport.publish(rec):
                out.append(f"RESULT_REPUBLISHED:{rec.task_id}")
        for f in sorted(self.pending.glob("verdict-*.json")):
            v = decode(f.read_text(encoding="utf-8"))
            assert isinstance(v, VerdictRecord)
            if self.transport.publish(v):
                out.append(f"VERDICT_REPUBLISHED:{v.task_id}")
        return out

    # -- verifier side ------------------------------------------------------------------------
    def accept_verification(self) -> VerificationRequest | None:
        req = self.transport.claim(
            Channel.VERIFICATION, role=Role.VERIFIER, identity=self.verifier_identity
        )
        if req is None:
            return None
        assert isinstance(req, VerificationRequest)
        _atomic_write(self.pending / f"verify-{req.seal}.json", encode(req))
        return req

    def pending_verifications(self) -> list[VerificationRequest]:
        out: list[VerificationRequest] = []
        for f in sorted(self.pending.glob("verify-*.json")):
            r = decode(f.read_text(encoding="utf-8"))
            assert isinstance(r, VerificationRequest)
            out.append(r)
        return out

    def ensure_result_pr(self, work: WorkItem, req: VerificationRequest) -> int:
        hop = self.xw.hop(work.seal, "RUN")
        assert hop is not None
        return self.port.ensure_draft_pr(
            str(hop["branch"]),
            DEFAULT_BRANCH,
            f"{work.task_id}: dev-loop result (draft, not for merge)",
            f"Result of sealed work {work.seal} at {req.result_revision}. Draft evidence PR so "
            "task CI runs on the exact head; not merge authority.",
        )

    def collect_verdict(self, work: WorkItem, req: VerificationRequest) -> bool:
        """Publish a verdict once BOTH the independent verifier and result CI are conclusive."""
        run_hop = self.xw.hop(work.seal, "RUN")
        res_hop = self.xw.hop(work.seal, "RESULT")
        assert run_hop is not None
        if res_hop is None or (
            req.result_seal != res_hop["result_seal"]
            or req.result_revision != res_hop["result_revision"]
            or req.result_tree != res_hop["result_tree"]
        ):
            raise AdapterError("verification request does not match the crosswalked result")
        if self.xw.hop(work.seal, "VERDICT") is not None:
            return True  # already decided (recover() republishes if needed)
        source_run = int(run_hop["run_id"])
        head = self.port.branch_head(str(run_hop["branch"]))
        if head != req.result_revision:
            raise AdapterError("result branch moved after ingestion; artifact identity changed")
        self.ensure_result_pr(work, req)
        checks = self.port.check_runs(req.result_revision)
        names = {c.name for c in checks}
        if not self.required_checks <= names or any(c.status != "completed" for c in checks):
            return False  # required task CI not (fully) reported/completed yet
        report = self._verification_report(source_run)
        if report is None:
            return False
        verdict_str = str(report.get("verdict"))
        if verdict_str == "UNESTABLISHED":
            return False  # mandatory evidence missing: no verdict, never a PASS
        findings: list[Finding] = []
        if verdict_str != "VERIFIED":
            findings.append(
                Finding(
                    finding_id="VERIFY-REJECTED",
                    category=FindingCategory.DEFECT,
                    message=f"independent verifier verdict {verdict_str} for run {source_run}",
                )
            )
        for c in sorted(checks, key=lambda x: (x.name, str(x.conclusion))):
            ok = (
                c.conclusion == "success"
                if c.name in self.required_checks
                else (c.conclusion in _OK)
            )
            if not ok:
                fid = re.sub(r"[^A-Za-z0-9]+", "-", c.name).strip("-")
                findings.append(
                    Finding(
                        finding_id=f"CI-{fid}",
                        category=FindingCategory.DEFECT,
                        message=f"check {c.name!r} concluded {c.conclusion} on the exact head",
                    )
                )
        unique = list({f.finding_id: f for f in findings}.values())
        v = make_verdict(
            req,
            verdict=Verdict.FAIL if unique else Verdict.PASS,
            findings=tuple(unique),
        )
        # crash-safe order: durable copy -> ledger (validates against the RESULT hop) -> publish
        _atomic_write(self.pending / f"verdict-{v.seal}.json", encode(v))
        self.xw.bind_verification(work.seal, req)
        self.xw.bind_verdict(work.seal, v)
        self.transport.publish(v)
        self._verify_file(req).unlink(missing_ok=True)
        return True

    def _verify_file(self, req: VerificationRequest) -> Path:
        return self.pending / f"verify-{req.seal}.json"

    def _verification_report(self, source_run: int) -> dict[str, object] | None:
        run = self.port.get_run(source_run)
        candidates = self.port.list_runs(
            VERIFY_WORKFLOW, event="workflow_run", created_after=_shift(run.created_at, CLOCK_SKEW)
        )
        matches: list[dict[str, object]] = []
        for c in candidates:
            try:
                rep = self.port.read_json_artifact(
                    c.run_id, REPORT_ARTIFACT, "verification-report.json"
                )
            except AdapterError:
                continue  # that run has no report (yet); it may belong to another lineage
            try:
                same = int(str(rep.get("source_run_id"))) == source_run
            except ValueError:
                same = False
            if same:
                matches.append(rep)
        if len({str(m.get("verdict")) for m in matches}) > 1:
            raise AdapterError("conflicting verifier reports for one source run")
        return matches[-1] if matches else None

    # -- one scheduling tick (idempotent; state lives in crosswalk + spool + pending dir) ----
    def tick(self) -> list[str]:
        events: list[str] = list(self.recover())
        while (w := self.accept_work()) is not None:
            events.append(f"ACCEPTED:{w.task_id}")
        for seal, w in list(self.works.items()):
            try:
                if self.xw.hop(seal, "DISPATCH") is None:
                    try:
                        self.dispatch(w)
                    except DispatchDeferred:
                        continue  # correlation is serialised; retry next tick
                    events.append(f"DISPATCHED:{w.task_id}")
                if self.xw.hop(seal, "RESULT") is None and self.collect_result(w):
                    events.append(f"RESULT:{w.task_id}")
            except (
                RemoteExecutionFailed,
                CrosswalkError,
                AmbiguousRun,
            ) as exc:  # this lineage only
                events.append(f"EXECUTION_FAILED:{w.task_id}:{exc}")
                self._work_file(seal).unlink(missing_ok=True)
                del self.works[seal]
        while self.accept_verification() is not None:
            events.append("VERIFICATION_ACCEPTED")
        for req in self.pending_verifications():
            work = next((x for x in self.works.values() if x.task_id == req.task_id), None)
            if work is not None and self.collect_verdict(work, req):
                events.append(f"VERDICT:{req.task_id}")
        return events
