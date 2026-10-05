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

Package binding (ATLAS-DEVQ-0005): ``dispatch_package`` (and ``dispatch`` when the adapter is
constructed with ``package_source``) sends only the payload rebuilt from the sealed work item
after ``dev_package.bind_package_to_work`` proved that the rendered package has the expected
``package_sha256`` and that its identity, scope, contract and payload are that work item's;
the ledger DISPATCH record then also carries ``package_sha256``. Binding is not a grant: it
neither issues, consumes nor verifies an owner dispatch grant, never overrides a classifier or
platform denial, and the recorded ``package_sha256`` states what was bound, not that anyone
approved it.

Crash safety: DISPATCH is written ahead to the Crosswalk ledger, so a work item is dispatched at
most once; run discovery is fail-closed on ambiguity.

OPERATING ASSUMPTIONS (documented residuals, enforced by deployment, not by this module):
  * the execute workflow has no ``run-name``, so a run cannot be tied to a dispatch except by
    time window + serialisation; humans must not dispatch ``atlas-agent-execute.yml`` by hand
    while the adapter has an unbound dispatch (the adapter refuses to guess on ambiguity);
  * evidence PRs must be opened with a PAT/App token (not ``GITHUB_TOKEN``) or task CI will not
    trigger and no verdict is ever produced (fail-closed, never a PASS);
  * a manual re-run of the executor run is refused (attempt guard) before and after ingestion;
  * single writer: exactly one adapter process per (ledger, pending dir, executor identity); two
    concurrent adapters would each hold their own in-memory ledger view and could double-adopt.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    repository_key,
    same_identity,
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


class DispatchRefused(AdapterError):
    """A dispatch precondition failed BEFORE anything was sent (stale base, unknown repair base).

    Terminal for this work item (the planner must replan); never a reason to stall other works.
    """


class PackageBindingRefused(DispatchRefused):
    """The rendered package could not be bound to the sealed work item (nothing written/sent).

    ``reason`` is the stable ``PackageSpecError`` reason. Terminal for this work item like any
    ``DispatchRefused``. The absence of this error is not a grant and permits nothing.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"package binding refused: {reason}: {detail}")
        self.reason = reason


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
MAX_ACCEPT_PER_TICK = 64  # per-tick ceiling on channel claims (hostile spool entries)
CLOCK_SKEW = timedelta(seconds=120)
DISPATCH_DEADLINE = timedelta(minutes=15)
_OK = frozenset({"success", "skipped", "neutral"})


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _park(f: Path, suffix: str = ".rejected") -> None:
    """Move a bad pending file aside (best effort, portable); never raise out of housekeeping."""
    with contextlib.suppress(OSError):
        os.replace(f, f.with_suffix(suffix))


def _parse(iso: str) -> datetime:
    try:
        return _aware(datetime.fromisoformat(iso.replace("Z", "+00:00")))
    except ValueError as exc:
        raise AdapterError(f"bad timestamp {iso!r}") from exc


def _aware(t: datetime) -> datetime:
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _shift(iso: str, delta: timedelta) -> str:
    t = _aware(datetime.fromisoformat(iso.replace("Z", "+00:00"))) - delta
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


class ResultInBaseObserver:
    """Read-only observation for the planner's verified scope handover (ATLAS-DEVQ-0009).

    ``result_in_base`` reports evidence only when one ``compare`` of the port shows that
    ``result_revision`` is an ancestor of ``base_revision`` (their merge base is the result
    revision itself), for the one repository this observer was constructed for (the caller
    must pass a port that is bound to that repository; that is not checked here). The
    repository is compared by ``repository_key``: any supported spelling of it matches, and
    the constructor raises ``ContractError`` (not ``AdapterError``) for a non-empty one
    that cannot be keyed. Anything else is ``None``: another
    repository or an unsupported spelling, a revision that is not 40 lowercase hex digits, a
    merge base that differs. A port error (``AdapterError``, including a truncated compare)
    is raised and the planner treats it as "not established". It calls nothing but
    ``compare``; it dispatches, merges and writes nothing. What it does NOT establish: that
    the base is on the default branch, that the result was merged by anyone in particular, or
    anything about an executor. Structurally a ``dev_planner.HandoverObserver``.
    """

    def __init__(self, port: GitHubPort, *, repository: str, identity: str) -> None:
        if not repository or not identity:
            raise AdapterError("a handover observer needs a repository and an identity")
        self.port, self.repository, self.identity = port, repository, identity
        self._key = repository_key(repository)  # an unsupported identity is refused here

    def result_in_base(
        self, *, repository: str, result_revision: str, base_revision: str
    ) -> dict[str, str] | None:
        try:
            if repository_key(repository) != self._key:  # as works_collide compares it
                return None
        except ContractError:
            return None
        if not _SHA.fullmatch(result_revision) or not _SHA.fullmatch(base_revision):
            return None
        merge_base = self.port.compare(base_revision, result_revision).merge_base
        if merge_base != result_revision:
            return None
        return {"compare": f"{base_revision}...{result_revision}", "merge_base": merge_base}


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


# Name of the execute workflow's sealed-base input (atlas-agent-execute.yml). Single definition:
# ``dev_package`` re-exports this constant.
BASE_REVISION_INPUT = "base_revision"


def build_dispatch_payload(
    work: WorkItem, *, base_branch: str, task_statement: str, acceptance_commands: tuple[str, ...]
) -> DispatchPayload:
    """Legacy three-input payload (no ``base_revision``), without secret values.

    Kept only so the frozen ``dev_first_run`` module keeps reproducing the committed, already
    executed ATLAS-DEVQ-0001 package (JSON-identical content). Nothing else may dispatch or
    package this shape: use ``build_sealed_dispatch_payload``. A test restricts its callers.
    """
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


def build_sealed_dispatch_payload(
    work: WorkItem, *, base_branch: str, task_statement: str, acceptance_commands: tuple[str, ...]
) -> DispatchPayload:
    """The ONE canonical dispatch payload for a sealed work item (no secret values).

    ``dev_package.build_package`` records exactly this as ``workflow_inputs`` and
    ``FabricAdapter.dispatch`` sends exactly this, so for the same work item, branch, statement
    and commands a package's ``workflow_inputs_sha256`` equals the ledger ``payload_sha256``.

    The inputs always carry the SEALED ``base_revision`` (from the work item, never from a
    branch read). ``base_branch`` is a movable name; the execute workflow asserts its
    checked-out HEAD against this input and fails closed before the agent runs if they differ.
    """
    legacy = build_dispatch_payload(
        work,
        base_branch=base_branch,
        task_statement=task_statement,
        acceptance_commands=acceptance_commands,
    )
    return DispatchPayload(
        workflow=legacy.workflow,
        ref=legacy.ref,
        inputs={**legacy.inputs, BASE_REVISION_INPUT: work.base_revision},
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
        task_statement: Callable[[WorkItem], tuple[str, tuple[str, ...]]] | None = None,
        package_source: Callable[[WorkItem], tuple[str, str]] | None = None,
        executor_identity: str = EXECUTOR_IDENTITY,
        verifier_identity: str = VERIFIER_IDENTITY,
        required_checks: frozenset[str] = DEFAULT_REQUIRED_CHECKS,
        dispatch_deadline: timedelta = DISPATCH_DEADLINE,
    ) -> None:
        self.dispatch_deadline = dispatch_deadline
        # Exactly one dispatch source. With ``package_source`` (rendered package text and its
        # expected package sha256 per work item) every dispatch goes through package binding.
        if (task_statement is None) == (package_source is None):
            raise AdapterError("exactly one of task_statement and package_source is required")
        if not required_checks:
            raise AdapterError("a non-empty required check set is mandatory")
        self.required_checks = required_checks
        self.port, self.transport, self.xw = port, transport, crosswalk
        self.pending = Path(pending_dir)
        self.pending.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.task_statement = task_statement
        self.package_source = package_source
        self.executor_identity = executor_identity
        self.verifier_identity = verifier_identity
        self.works: dict[str, WorkItem] = {}  # seal -> work (in-flight in this process)
        self._load_works()

    # durable in-flight set: works claimed from the WORK channel are persisted before dispatch
    def _work_file(self, seal: str) -> Path:
        return self.pending / f"work-{seal}.json"

    def _load_works(self) -> None:
        for f in sorted(self.pending.glob("work-*.json")):
            try:
                w = decode(f.read_text(encoding="utf-8"))
                if not isinstance(w, WorkItem):
                    raise ContractError("not a work item")
                self.xw.bind_work(w)  # idempotent; closes the persist-before-bind crash window
            except (ContractError, OSError, ValueError, RecursionError):
                _park(f)  # never loaded, never dispatched
                continue
            self.works[w.seal] = w

    # -- implementer side --------------------------------------------------------------------
    def accept_work(self) -> WorkItem | None:
        w = self.transport.claim(
            Channel.WORK, role=Role.IMPLEMENTER, identity=self.executor_identity
        )
        if w is None:
            return None
        if not isinstance(w, WorkItem):
            raise ContractError("claimed record is not a work item")
        _atomic_write(self._work_file(w.seal), encode(w))
        self.works[w.seal] = w
        self.xw.bind_work(w)
        return w

    def dispatch(self, work: WorkItem) -> DispatchPayload:
        if self.package_source is not None:
            # Package mode: only a bound package can be dispatched. A source that raises or
            # returns anything but (rendered text, expected sha) fails closed before any write.
            try:
                sourced = self.package_source(work)
            except Exception as exc:
                raise DispatchRefused(
                    f"package source failed: {type(exc).__name__}: {exc}"
                ) from exc
            if (
                not isinstance(sourced, tuple)
                or len(sourced) != 2
                or not all(isinstance(x, str) for x in sourced)
            ):
                raise DispatchRefused("package source did not return (rendered, package_sha256)")
            return self.dispatch_package(work, sourced[0], expected_package_sha256=sourced[1])
        if self.task_statement is None:  # unreachable: the constructor requires one source
            raise AdapterError("no dispatch source configured")
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
                raise DispatchRefused("repair base revision has no known result branch")
            base_branch = found
        if self.port.branch_head(base_branch) != work.base_revision:
            raise DispatchRefused(f"{base_branch} is not at the sealed base revision")
        statement, commands = self.task_statement(work)
        # HARDEN-DEVLOOP-002/003: base_branch is a movable name and the branch-head check above
        # is only a pre-dispatch guard. The canonical payload carries the SEALED revision (from
        # the work item, never from a branch read), so the execute workflow's sealed-base
        # assertion fails closed if the branch moves before checkout. This one payload object
        # is hashed into the ledger and returned; the port receives a copy of its inputs, so a
        # port cannot alter what the ledger hash describes.
        payload = build_sealed_dispatch_payload(
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
            self.port.dispatch_workflow(payload.workflow, payload.ref, dict(payload.inputs))
        except AdapterError as exc:  # write-ahead already recorded: never retried, surfaced
            raise RemoteExecutionFailed(f"dispatch failed: {exc}") from exc
        return payload

    def dispatch_package(
        self, work: WorkItem, rendered: str, *, expected_package_sha256: str
    ) -> DispatchPayload:
        """Dispatch exactly the payload of a rendered package bound to ``work``, at most once.

        Order: (1) the pure binder (``dev_package.bind_package_to_work``); a refusal raises
        ``PackageBindingRefused`` and writes and sends nothing; (2) bind the work, refuse a
        second dispatch, defer behind an unbound dispatch; (3) the branch comes from the
        PACKAGE, constrained by its kind and never taken from ``work.attempt``: an
        implementation package dispatches on the default branch, a repair package on exactly
        the result branch the ledger knows for the sealed base revision; (4) that branch's head
        must be the sealed base revision; (5) write-ahead DISPATCH with ``payload_sha256`` and
        ``package_sha256``; (6) send a copy of the rebuilt payload's inputs.

        Binding is not a grant: this neither issues, consumes nor verifies an owner dispatch
        grant, the package's ``grant_required`` stays as it is, and passing these checks never
        overrides a classifier or platform denial. The ledger ``package_sha256`` records what
        was bound, not approval.
        """
        # Imported here: ``dev_package`` imports this module at import time.
        from project_atlas.orchestration.autonomy.dev_package import (
            PackageSpecError,
            bind_package_to_work,
        )

        try:
            binding = bind_package_to_work(
                rendered, expected_package_sha256=expected_package_sha256, work=work
            )
        except PackageSpecError as exc:
            raise PackageBindingRefused(exc.reason, exc.detail) from None
        payload = binding.payload
        self.xw.bind_work(work)
        if self.xw.hop(work.seal, "DISPATCH") is not None:
            raise AdapterError("work item already dispatched; refusing to dispatch twice")
        if self._unbound_dispatches(exclude=work.seal):
            raise DispatchDeferred("another dispatch has not been bound to a run yet")
        base_branch = binding.base_branch
        if binding.attempt_kind == "repair":
            found = self.xw.branch_for_revision(work.base_revision)
            if found is None:
                raise DispatchRefused("repair base revision has no known result branch")
            if found != base_branch:
                raise DispatchRefused(
                    "repair package names a branch that is not the known result branch of the "
                    "sealed base revision"
                )
        elif binding.attempt_kind != "implementation" or base_branch != DEFAULT_BRANCH:
            raise DispatchRefused(f"an implementation package dispatches on {DEFAULT_BRANCH}")
        if payload.inputs.get("base_branch") != base_branch:
            raise DispatchRefused("bound payload does not check out the package branch")
        if self.port.branch_head(base_branch) != work.base_revision:
            raise DispatchRefused(f"{base_branch} is not at the sealed base revision")
        # write-ahead: from here on this work item can never be dispatched again
        self.xw.bind_dispatch(
            work.seal,
            dispatched_at=self.clock(),
            payload_sha256=payload.sha256(),
            package_sha256=binding.package_sha256,
        )
        try:
            self.port.dispatch_workflow(payload.workflow, payload.ref, dict(payload.inputs))
        except AdapterError as exc:  # write-ahead already recorded: never retried, surfaced
            raise RemoteExecutionFailed(f"dispatch failed: {exc}") from exc
        return payload

    def _unbound_dispatches(self, *, exclude: str = "") -> list[str]:
        """Dispatches without a bound run, taken from the DURABLE ledger (not from live works).

        A dropped lineage whose dispatch may still surface a late run keeps blocking further
        dispatches until the dispatch deadline has elapsed, so its run can never be adopted by
        the next work item.
        """
        now = _parse(self.clock())
        return [
            seal
            for seal, at in self.xw.unbound_dispatches()
            if seal != exclude and now - _parse(at) <= self.dispatch_deadline
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
        bound = self.xw.bound_run_ids()  # every run ever bound, from the durable ledger
        runs = [r for r in runs if r.run_id not in bound]
        if len(runs) > 1:
            raise AmbiguousRun("ambiguous run correlation; refusing to guess")
        if not runs:
            if _parse(self.clock()) - _parse(str(disp["dispatched_at"])) > self.dispatch_deadline:
                raise RemoteExecutionFailed("no workflow run appeared within the dispatch deadline")
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
        if run is None:
            return False
        hop = self.xw.hop(work.seal, "RUN")
        assert hop is not None
        if run.attempt != int(hop["run_attempt"]):
            raise RemoteExecutionFailed(
                f"run {run.run_id} was re-run (attempt {run.attempt} != bound "
                f"{hop['run_attempt']}); the bound branch/evidence no longer match"
            )
        if run.status != "completed":
            return False
        if run.conclusion != "success":
            raise RemoteExecutionFailed(f"run {run.run_id} concluded {run.conclusion}")
        branch = str(hop["branch"])
        if not AGENT_BRANCH.match(branch):
            raise RemoteExecutionFailed("unexpected result branch shape")
        head = self.port.branch_head(branch)
        if head is None:
            raise RemoteExecutionFailed("no result branch: the executor produced no change")
        cmp = self.port.compare(work.base_revision, _sha(head))
        if cmp.merge_base != work.base_revision:
            raise RemoteExecutionFailed("result branch does not descend from the sealed base")
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
        out.extend(self._readopt_claimed())
        for f in sorted(self.pending.glob("result-*.json")):
            try:
                rec = decode(f.read_text(encoding="utf-8"))
                if not isinstance(rec, ResultRecord):
                    raise ContractError("not a result record")
                if self.xw.hop(rec.work_seal, "RESULT") is None:
                    self.xw.bind_result(rec)
            except (
                ContractError,
                OSError,
                ValueError,
                RecursionError,
            ) as exc:  # stray/forged: park, go on
                _park(f)
                out.append(f"RESULT_FILE_REJECTED:{f.name}:{exc}")
                continue
            if self.transport.publish(rec):
                out.append(f"RESULT_REPUBLISHED:{rec.task_id}")
            f.unlink()  # delivered (published now, or already queued/consumed in the spool)
        for f in sorted(self.pending.glob("verdict-*.json")):
            try:
                v = decode(f.read_text(encoding="utf-8"))
                if not isinstance(v, VerdictRecord):
                    raise ContractError("not a verdict record")
                row = self.xw.resolve("execution_id", v.execution_id)
                hop = self.xw.hop(str(row["work_seal"]), "VERDICT")
            except (
                ContractError,
                OSError,
                ValueError,
                RecursionError,
            ) as exc:  # unknown execution / forged
                _park(f)
                out.append(f"VERDICT_FILE_REJECTED:{f.name}:{exc}")
                continue
            if hop is None or hop["verdict_seal"] != v.seal:
                f.unlink()  # never validated against the ledger: must not be published
                out.append(f"VERDICT_DISCARDED:{v.task_id}")
                continue
            if self.transport.publish(v):
                out.append(f"VERDICT_REPUBLISHED:{v.task_id}")
            f.unlink()
        return out

    def _refused_marker(self, seal: str) -> Path:
        return self.pending / f"refused-{seal}.marker"

    def _readopt_claimed(self) -> list[str]:
        """Claim-before-persist crash window: re-adopt records we claimed but never persisted.

        The transport decides what it returns: the spool transport leaves out a record that
        was withdrawn meanwhile.
        """
        lister = getattr(self.transport, "claimed_records", None)
        if lister is None:
            return []
        out: list[str] = []
        for rec in lister(Channel.WORK, identity=self.executor_identity):
            if (
                not isinstance(rec, WorkItem)
                or rec.seal in self.works
                or self.xw.knows_work(rec.seal)
                or self._refused_marker(rec.seal).exists()
            ):
                continue
            try:
                _atomic_write(self._work_file(rec.seal), encode(rec))  # persist FIRST
            except OSError as exc:  # stays claimed; retried next tick unless withdrawn meanwhile
                out.append(f"WORK_READOPT_DEFERRED:{rec.task_id}:{exc}")
                continue
            try:
                self.xw.bind_work(rec)  # uniqueness check; a refusal tombstones + unpersists
            except ContractError as exc:
                self._work_file(rec.seal).unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    self._refused_marker(rec.seal).write_text(str(exc), encoding="utf-8")
                out.append(f"WORK_READOPT_REFUSED:{rec.task_id}:{exc}")
                continue
            self.works[rec.seal] = rec
            out.append(f"WORK_READOPTED:{rec.task_id}")
        for rec in lister(Channel.VERIFICATION, identity=self.verifier_identity):
            if (
                not isinstance(rec, VerificationRequest)
                or not same_identity(rec.verifier_identity, self.verifier_identity)
                or self._verify_file(rec).exists()
            ):
                continue
            try:
                ws = str(self.xw.resolve("execution_id", rec.execution_id)["work_seal"])
            except CrosswalkError:
                continue
            if self.xw.hop(ws, "VERDICT") is None:
                try:
                    _atomic_write(self._verify_file(rec), encode(rec))
                except OSError as exc:
                    out.append(f"VERIFICATION_READOPT_FAILED:{rec.task_id}:{exc}")
                    continue
                out.append(f"VERIFICATION_READOPTED:{rec.task_id}")
        return out

    # -- verifier side ------------------------------------------------------------------------
    def accept_verification(self) -> VerificationRequest | None:
        req = self.transport.claim(
            Channel.VERIFICATION, role=Role.VERIFIER, identity=self.verifier_identity
        )
        if req is None:
            return None
        if not isinstance(req, VerificationRequest):
            raise ContractError("claimed record is not a verification request")
        _atomic_write(self.pending / f"verify-{req.seal}.json", encode(req))
        return req

    def pending_verifications(self) -> list[VerificationRequest]:
        out: list[VerificationRequest] = []
        for f in sorted(self.pending.glob("verify-*.json")):
            try:
                r = decode(f.read_text(encoding="utf-8"))
                if not isinstance(r, VerificationRequest):
                    raise ContractError("not a verification request")
            except (ContractError, OSError, ValueError, RecursionError):
                _park(f)  # a garbled pending file must not block the other verifications
                continue
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
            self._verify_file(req).unlink(missing_ok=True)
            return False  # already decided; nothing more to do for this request
        source_run = int(run_hop["run_id"])
        live = self.port.get_run(source_run)
        if live.attempt != int(run_hop["run_attempt"]):
            raise AdapterError(
                "executor run was re-run after ingestion; evidence no longer matches the result"
            )
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
        for c in sorted(candidates, key=lambda r: (_parse(r.created_at), r.run_id)):
            try:
                rep = self.port.read_json_artifact(
                    c.run_id, REPORT_ARTIFACT, "verification-report.json"
                )
            except Exception:
                continue  # that run has no (usable) report; it may belong to another lineage
            try:
                same = int(str(rep.get("source_run_id"))) == source_run
            except (ValueError, AttributeError):
                same = False
            if same:
                matches.append(rep)
        # a re-run of the verifier supersedes earlier reports: the LATEST report decides
        return matches[-1] if matches else None

    # -- one scheduling tick (idempotent; state lives in crosswalk + spool + pending dir) ----
    def tick(self) -> list[str]:
        events: list[str] = []
        try:
            events.extend(self.recover())
        except Exception as exc:
            events.append(f"RECOVER_ERROR:{type(exc).__name__}:{exc}")
        for _ in range(MAX_ACCEPT_PER_TICK):  # bounded: a hostile channel cannot spin the tick
            try:
                w = self.accept_work()
            except (ContractError, RecursionError) as exc:  # e.g. duplicate execution id
                events.append(f"ACCEPT_REFUSED:{exc}")
                continue
            except OSError as exc:  # do not spin on a failing disk (see _readopt_claimed)
                events.append(f"ACCEPT_REFUSED:{exc}")
                break
            except Exception as exc:
                events.append(f"ACCEPT_UNEXPECTED:{type(exc).__name__}:{exc}")
                continue  # bounded by MAX_ACCEPT_PER_TICK
            if w is None:
                break
            events.append(f"ACCEPTED:{w.task_id}")
        for seal, w in list(self.works.items()):
            try:
                if self.xw.hop(seal, "DISPATCH") is None:
                    try:
                        self.dispatch(w)
                    except DispatchDeferred:
                        continue  # correlation is serialised; retry next tick
                    events.append(f"DISPATCHED:{w.task_id}")
                    continue  # never correlate a run in the tick that created it
                if self.xw.hop(seal, "RESULT") is None and self.collect_result(w):
                    events.append(f"RESULT:{w.task_id}")
            except (
                RemoteExecutionFailed,
                DispatchRefused,
                CrosswalkError,
                AmbiguousRun,
            ) as exc:  # terminal for this lineage only
                events.append(f"EXECUTION_FAILED:{w.task_id}:{exc}")
                self._work_file(seal).unlink(missing_ok=True)
                del self.works[seal]
            except AdapterError as exc:  # transient port problem: keep the work, retry next tick
                events.append(f"TRANSIENT:{w.task_id}:{exc}")
            except Exception as exc:
                events.append(f"UNEXPECTED:{w.task_id}:{type(exc).__name__}:{exc}")
        for _ in range(MAX_ACCEPT_PER_TICK):
            try:
                if self.accept_verification() is None:
                    break
            except (ContractError, RecursionError) as exc:  # poisoned request: parked once
                events.append(f"VERIFICATION_REFUSED:{exc}")
                continue
            except OSError as exc:
                events.append(f"VERIFICATION_REFUSED:{exc}")
                break
            except Exception as exc:
                events.append(f"VERIFICATION_UNEXPECTED:{type(exc).__name__}:{exc}")
                continue  # bounded by MAX_ACCEPT_PER_TICK
            events.append("VERIFICATION_ACCEPTED")
        for req in self.pending_verifications():
            work = next((x for x in self.works.values() if x.task_id == req.task_id), None)
            if work is None:
                continue
            try:
                if self.collect_verdict(work, req):
                    events.append(f"VERDICT:{req.task_id}")
            except (AdapterError, CrosswalkError) as exc:  # integrity problem: report, keep going
                events.append(f"VERDICT_ERROR:{req.task_id}:{exc}")
            except Exception as exc:
                events.append(f"VERDICT_UNEXPECTED:{req.task_id}:{type(exc).__name__}:{exc}")
        return events
