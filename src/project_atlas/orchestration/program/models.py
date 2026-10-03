"""AS-ORCH-PROGRAM-SUPERVISOR-001 typed contracts for approved work programs.

The owner approves a *program* once. The supervisor executes its eligible
tasks. Nothing in this module grants authority:

  PROGRAM_APPROVAL != MERGE_AUTHORIZATION
  WORKER_REPORTED_COMPLETION != ACCEPTANCE
  ACCEPTANCE != INDEPENDENT_VERIFICATION
  TASK_COMPLETE != PROGRAM_COMPLETE
  ADAPTER_EXIT_ZERO != ACCEPTANCE_PASSED
  ESTIMATED_COST != BILLED_SPEND

Governance vocabulary is *not* redefined here. ``ProgramTask.to_work_node()``
projects a task onto the existing ``orchestration.autonomy.models.WorkNode``
so the existing DAG, eligibility, lease and owner-gate code operates on it
unchanged.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_atlas.orchestration.autonomy.models import (
    AgentCapability,
    ExecutionHostClass,
    IvRequirements,
    MutationSurface,
    NodeState,
    OwnerGateKind,
    RetryPolicy,
    WorkNode,
)
from project_atlas.orchestration.sdk.external_observers import ObserverType

PACKAGE_ID: Final[Literal["AS-ORCH-PROGRAM-SUPERVISOR-001"]] = (
    "AS-ORCH-PROGRAM-SUPERVISOR-001"
)
DIRECTIVE_ID: Final[Literal["ATLAS-CONTINUOUS-EXECUTION-SUPERVISOR-001"]] = (
    "ATLAS-CONTINUOUS-EXECUTION-SUPERVISOR-001"
)
TRUTH_BOUNDARY: Final[str] = (
    "PROGRAM_APPROVAL != MERGE_AUTHORIZATION / "
    "WORKER_REPORTED_COMPLETION != ACCEPTANCE / "
    "ACCEPTANCE != INDEPENDENT_VERIFICATION / "
    "TASK_COMPLETE != PROGRAM_COMPLETE / "
    "ADAPTER_EXIT_ZERO != ACCEPTANCE_PASSED / "
    "ESTIMATED_COST != BILLED_SPEND"
)

#: Task, program and profile identifiers. Deliberately the same shape the
#: existing ``WorkNode.package_id`` accepts (``atlas_contracts.versions
#: .ID_PATTERN``) so a task id can be projected onto a node without
#: rewriting, but validated here too so a bad id fails at the program
#: boundary rather than deep inside the governor.
_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REL_PATH_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_PIN_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_SEMANTIC_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class ProgramError(ValueError):
    """Any refusal raised by this package. Carries a stable machine code."""

    code = "PROGRAM_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class AuthorityExpansionError(ProgramError):
    """A task override tried to widen authority beyond its approved scope."""

    code = "AUTHORITY_EXPANSION_FORBIDDEN"


class ExecutionConfidence(StrEnum):
    """Is the *outcome* of an adapter invocation known?

    ``CONFIRMED`` and ``FAILED`` are both certain: the adapter ran to a
    terminal state this process observed. ``UNCERTAIN`` means an external
    effect may have occurred with no recorded outcome -- an interrupted
    invocation, a killed process, a lost result. It is never silently
    upgraded and never blindly retried.
    """

    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


class FailureClass(StrEnum):
    """Why an attempt did not succeed. Retry policy keys on this, not on text."""

    #: Infrastructure hiccup: spawn failure, transient network, disk. Retryable.
    TRANSIENT_INFRASTRUCTURE = "TRANSIENT_INFRASTRUCTURE"
    #: The task itself is malformed (bad argv, missing workspace). Never retried.
    INVALID_TASK_INPUT = "INVALID_TASK_INPUT"
    #: The runtime refused on policy grounds (permission denied, tool blocked).
    POLICY_REFUSAL = "POLICY_REFUSAL"
    #: Credentials missing/rejected, or the account is out of quota or credit.
    #: Never retried in a loop and never worked around by changing provider.
    QUOTA_OR_CREDENTIAL = "QUOTA_OR_CREDENTIAL"
    #: The worker ran and produced a result, but acceptance did not pass.
    ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"
    #: The invocation's outcome is unknown. Requires reconciliation.
    UNCERTAIN_OUTCOME = "UNCERTAIN_OUTCOME"
    #: Attempts ran, nothing observable changed. Bounded, then stops.
    NO_PROGRESS = "NO_PROGRESS"
    #: Wall clock, launch count or attempt count exhausted.
    LIMIT_EXHAUSTED = "LIMIT_EXHAUSTED"
    #: Operator or program cancellation.
    CANCELLED = "CANCELLED"


#: Failure classes a bounded retry may follow. Everything else stops the task.
RETRYABLE_FAILURES: Final[frozenset[FailureClass]] = frozenset(
    {FailureClass.TRANSIENT_INFRASTRUCTURE, FailureClass.ACCEPTANCE_FAILED}
)

#: Failure classes that must never be retried, however many attempts remain.
#: ``QUOTA_OR_CREDENTIAL`` is here deliberately: retrying a rejected credential
#: or an exhausted account is never progress, and switching provider to evade
#: an account limit is not an option this package offers.
PERMANENT_FAILURES: Final[frozenset[FailureClass]] = frozenset(
    {
        FailureClass.INVALID_TASK_INPUT,
        FailureClass.POLICY_REFUSAL,
        FailureClass.QUOTA_OR_CREDENTIAL,
        FailureClass.NO_PROGRESS,
        FailureClass.LIMIT_EXHAUSTED,
        FailureClass.CANCELLED,
    }
)


class ProgramStopReason(StrEnum):
    """Why a supervisor cycle stopped selecting work.

    Deliberately a separate vocabulary from ``autonomy.models.StopReason``,
    which this package still consumes verbatim from ``select_next``. The two
    are mapped explicitly in ``supervisor._map_stop_reason`` rather than
    conflated, because ``StopReason`` has no member for program completion,
    cancellation, or an uncertain outcome awaiting reconciliation.
    """

    PROGRAM_COMPLETE = "PROGRAM_COMPLETE"
    OWNER_DECISION_REQUIRED = "OWNER_DECISION_REQUIRED"
    #: Deliberately not CANCELLED. A pause stops NEW work and lets workers
    #: already running finish, so nothing becomes UNCERTAIN just because an
    #: operator wanted a breather. Cancel is the one that interrupts.
    PAUSED = "PAUSED"
    WAITING_ON_EXTERNAL_EVENT = "WAITING_ON_EXTERNAL_EVENT"
    AWAITING_INDEPENDENT_VERIFICATION = "AWAITING_INDEPENDENT_VERIFICATION"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    NO_ELIGIBLE_WORK = "NO_ELIGIBLE_WORK"
    HARD_BLOCKER = "HARD_BLOCKER"
    LIMIT_REACHED = "LIMIT_REACHED"
    CANCELLED = "CANCELLED"
    CYCLE_BUDGET_REACHED = "CYCLE_BUDGET_REACHED"


class AttemptPhase(StrEnum):
    """Durable checkpoint phases for one attempt at one task.

    Written BEFORE the effect each names, never after, so a crash between two
    phases is legible rather than invisible. ``INTENT_RECORDED`` is the
    ambiguity window: the supervisor decided to launch and may or may not have
    reached the spawn. ``recovery.classify_attempt`` resolves it with evidence
    (a runtime session probe) rather than an assumption.
    """

    INTENT_RECORDED = "INTENT_RECORDED"
    ADAPTER_INVOKED = "ADAPTER_INVOKED"
    ADAPTER_RETURNED = "ADAPTER_RETURNED"
    ACCEPTANCE_EVALUATED = "ACCEPTANCE_EVALUATED"
    TERMINAL = "TERMINAL"


class AcceptanceKind(StrEnum):
    """How one acceptance condition is checked, locally and by this process.

    Every kind is observed by the supervisor itself. None of them consults the
    worker's own report of what it did.
    """

    #: Run a fixed argv in the workspace; exit status 0 is the pass condition.
    COMMAND = "COMMAND"
    #: A workspace-relative path must exist.
    FILE_EXISTS = "FILE_EXISTS"
    #: A workspace-relative file must match a regular expression.
    FILE_MATCHES = "FILE_MATCHES"
    #: `git diff --quiet` against the recorded base must report a change.
    GIT_TREE_CHANGED = "GIT_TREE_CHANGED"


class AcceptanceCheck(BaseModel):
    """One locally observed acceptance condition.

    ``COMMAND`` takes an argv list, never a shell string: worker prose can
    never become part of a command line, because acceptance commands come
    from the approved program file and nowhere else.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=1, max_length=128)
    kind: AcceptanceKind
    description: str = Field(min_length=1, max_length=512)
    argv: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    path: str | None = None
    pattern: str | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=7200)

    @field_validator("check_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("check_id must be a safe identifier")
        return value

    @field_validator("path")
    @classmethod
    def _path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _REL_PATH_RE.fullmatch(value) or ".." in value.split("/"):
            raise ValueError("acceptance path must be a safe workspace-relative path")
        return value

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            re.compile(value)
        except re.error as exc:  # pragma: no cover - message varies by version
            raise ValueError(f"acceptance pattern is not a valid regex: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _kind_requirements(self) -> AcceptanceCheck:
        if self.kind is AcceptanceKind.COMMAND and not self.argv:
            raise ValueError("COMMAND acceptance requires a non-empty argv")
        if self.kind is not AcceptanceKind.COMMAND and self.argv:
            raise ValueError("argv is only meaningful for COMMAND acceptance")
        needs_path = {AcceptanceKind.FILE_EXISTS, AcceptanceKind.FILE_MATCHES}
        if self.kind in needs_path and self.path is None:
            raise ValueError(f"{self.kind.value} acceptance requires a path")
        if self.kind is AcceptanceKind.FILE_MATCHES and self.pattern is None:
            raise ValueError("FILE_MATCHES acceptance requires a pattern")
        return self


class ExternalPrecondition(BaseModel):
    """An external event a task waits for before it becomes eligible.

    Backed by ``orchestration.sdk.external_observers``: a durable observer with
    exponential backoff and once-only terminal-event consumption. The probe is
    a fixed argv whose exit status is the signal, so "wait for CI", "wait for a
    file to appear" and "wait for a controlled test fixture" are the same
    mechanism with different argv.

    ``observer_type`` is the existing ``ObserverType`` alias itself, imported
    rather than restated: a copied literal list is a copy that can drift from
    the vocabulary the registry actually accepts, and the first symptom would
    be a validation error deep inside a poll rather than at load time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    precondition_id: str = Field(min_length=1, max_length=128)
    observer_type: ObserverType = "LOCAL_WORKER"
    external_id: str = Field(min_length=1, max_length=256)
    probe_argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    #: Exit status meaning "the event has happened and passed".
    pass_exit_code: int = Field(default=0, ge=0, le=255)
    #: Exit status meaning "the event has happened and failed" -- terminal, not
    #: a reason to keep polling. ``None`` means only ``pass_exit_code`` is
    #: terminal and every other status is "still pending".
    fail_exit_code: int | None = Field(default=None, ge=0, le=255)
    poll_interval_seconds: float = Field(default=15.0, ge=0.0, le=3600.0)
    probe_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    max_wait_seconds: float = Field(default=3600.0, ge=1.0, le=604800.0)

    @field_validator("precondition_id")
    @classmethod
    def _pid(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("precondition_id must be a safe identifier")
        return value

    @model_validator(mode="after")
    def _distinct_codes(self) -> ExternalPrecondition:
        if self.fail_exit_code is not None and self.fail_exit_code == self.pass_exit_code:
            raise ValueError("pass_exit_code and fail_exit_code must differ")
        return self


class ProgramLimits(BaseModel):
    """Bounds the owner approves along with the objective.

    Every field here is enforced by this package against something it can
    actually count. ``max_estimated_cost_usd`` is the exception and is named
    honestly: it is forwarded to the runtime where the runtime supports a
    budget flag, and otherwise reported. It is not an account spending limit.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Total adapter launches across the whole program, all tasks, all attempts.
    max_task_launches: int = Field(default=20, ge=1, le=10_000)
    #: Attempts at any single task before it is declared blocked.
    max_attempts_per_task: int = Field(default=3, ge=1, le=20)
    #: Wall clock for one adapter invocation.
    max_task_seconds: int = Field(default=1800, ge=1, le=86_400)
    #: Wall clock for one `start` invocation of the supervisor.
    max_program_seconds: int = Field(default=14_400, ge=1, le=604_800)
    #: Supervisor cycles per `start` invocation. A cycle is one pass of the
    #: loop, dispatching at most one task.
    max_cycles: int = Field(default=200, ge=1, le=100_000)
    #: Consecutive cycles with no observable progress before the supervisor
    #: stops. Progress means changed task state, new evidence, or a resolved
    #: external event -- never "more messages" or "more commits".
    max_idle_cycles: int = Field(default=5, ge=1, le=1_000)
    #: Forwarded to a runtime that supports a per-launch budget flag. Client-
    #: side estimate, not enforced spend. ``None`` forwards nothing.
    max_estimated_cost_usd: float | None = Field(default=None, gt=0.0, le=10_000.0)
    #: Seconds to sleep between cycles when nothing is dispatchable but an
    #: external wait is outstanding.
    idle_sleep_seconds: float = Field(default=5.0, ge=0.0, le=600.0)
    #: How many workers may run at once. Defaults to 1: sequential execution
    #: is the proven case, and a program gets concurrency because it asked for
    #: it, not because a default changed underneath it.
    #:
    #: Raising it does not relax any other gate. The existing surface-overlap
    #: gate still refuses to run two tasks that touch the same paths or share
    #: a mutation semantic, the durable lease projection still refuses a second
    #: active lease for one task or one agent, and owner gates still hold. What
    #: this number bounds is how many *non-conflicting* tasks may be in flight.
    max_concurrent_workers: int = Field(default=1, ge=1, le=16)


class ProgramTask(BaseModel):
    """One bounded unit of work inside an approved program."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    #: The instruction handed to the worker. Data for the worker, never an
    #: instruction to Atlas: nothing a worker reads or writes can enlarge the
    #: program, change a profile, or grant a gate.
    instruction: str = Field(min_length=1, max_length=32_768)
    profile_ref: str = Field(min_length=1, max_length=128)
    depends_on: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    #: Workspace-relative paths this task may mutate. Projected onto
    #: ``WorkNode.mutation_surface`` so the existing overlap gate applies.
    mutation_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    surface_id: str = Field(min_length=1, max_length=128)
    surface_semantic: str = Field(min_length=1, max_length=64)
    capabilities_required: tuple[AgentCapability, ...] = Field(
        default=(AgentCapability.IMPLEMENT,), min_length=1, max_length=8
    )
    acceptance: tuple[AcceptanceCheck, ...] = Field(min_length=1, max_length=32)
    external_precondition: ExternalPrecondition | None = None
    #: When set, the supervisor never dispatches this task. Owner gates are
    #: not grantable from inside this package; see ``autonomy.owner_gates``.
    owner_gate: OwnerGateKind | None = None
    #: When true, this task's own worker can never satisfy its verification
    #: gate; a distinct agent with VERIFY capability must certify it.
    requires_independent_verification: bool = False
    #: Profile id of the verifier. Must differ from the implementer's profile
    #: and its resolved agent id. ``None`` leaves verification to a human.
    verifier_profile_ref: str | None = None
    #: Narrowing-only overrides applied on top of the referenced profile.
    profile_override: dict[str, object] = Field(default_factory=dict)
    #: If the supervisor crashed in the ``INTENT_RECORDED`` window and the
    #: runtime reports no evidence the worker ever started, may the *same*
    #: attempt be launched again? Only ever true for a task whose effect is
    #: idempotent by construction. Default is to stop and require reconcile.
    retry_safe_when_no_launch_evidence: bool = False

    @field_validator("task_id", "profile_ref", "surface_id")
    @classmethod
    def _ident(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("identifier must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        return value

    @field_validator("verifier_profile_ref")
    @classmethod
    def _opt_ident(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            raise ValueError("verifier_profile_ref must be a safe identifier")
        return value

    @field_validator("surface_semantic")
    @classmethod
    def _semantic(cls, value: str) -> str:
        if not _SEMANTIC_RE.fullmatch(value):
            raise ValueError("surface_semantic must be an uppercase identifier")
        return value

    @field_validator("mutation_paths")
    @classmethod
    def _paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REL_PATH_RE.fullmatch(item) or ".." in item.split("/"):
                raise ValueError("mutation paths must be safe relative paths")
        return value

    @field_validator("depends_on")
    @classmethod
    def _deps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _ID_RE.fullmatch(item):
                raise ValueError("dependency ids must be safe identifiers")
        if len(set(value)) != len(value):
            raise ValueError("duplicate dependency")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> ProgramTask:
        if self.task_id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        if self.verifier_profile_ref is not None:
            if not self.requires_independent_verification:
                raise ValueError(
                    "verifier_profile_ref is only meaningful when "
                    "requires_independent_verification is true"
                )
            if self.verifier_profile_ref == self.profile_ref:
                raise AuthorityExpansionError(
                    "the implementer's own profile cannot satisfy an "
                    "independent verification gate",
                    code="IMPLEMENTER_CANNOT_VERIFY",
                )
        return self

    def to_work_node(self, *, base_pin: str) -> WorkNode:
        """Project onto the existing governance node type.

        The DAG, ``select_next``, ``grant_lease``, the overlap gate and the
        owner-gate evaluator all operate on ``WorkNode``. Producing one here
        is what makes those reusable without a second scheduling engine.
        """
        return WorkNode(
            package_id=self.task_id,
            objective=self.title[:512],
            base_pin=base_pin,
            dependencies=self.depends_on,
            mutation_surface=MutationSurface(
                surface_id=self.surface_id,
                paths=self.mutation_paths,
                semantic=self.surface_semantic,
            ),
            execution_host_class=ExecutionHostClass.LOCAL_PROCESS,
            agent_capabilities_required=self.capabilities_required,
            acceptance_criteria=tuple(
                check.description[:512] for check in self.acceptance[:16]
            ),
            iv_requirements=IvRequirements(
                certification_required=self.requires_independent_verification,
                implementer_cannot_verify=True,
            ),
            owner_gate=self.owner_gate,
            retry_policy=RetryPolicy(),
            state=NodeState.DISCOVERED,
        )


class WorkProgram(BaseModel):
    """An objective the owner approves once, with everything it may do.

    ``approved_by`` / ``approval_reference`` are a record of who approved the
    program and where that approval is written down. They are provenance, not
    a credential: this package never treats their presence as authorization
    for anything an owner gate would otherwise hold.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    package_id: Literal["AS-ORCH-PROGRAM-SUPERVISOR-001"] = PACKAGE_ID
    program_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=2048)
    approved_by: str = Field(min_length=1, max_length=256)
    approval_reference: str = Field(min_length=1, max_length=512)
    #: Absolute or program-file-relative path to the repository the workers
    #: operate in. Resolved and containment-checked by the loader.
    workspace_root: str = Field(min_length=1, max_length=4096)
    #: The commit the program was approved against. Recorded on every lease
    #: and every idempotency key so a moved base is visible, not silent.
    base_pin: str = Field(min_length=40, max_length=40)
    tasks: tuple[ProgramTask, ...] = Field(min_length=1, max_length=256)
    limits: ProgramLimits = Field(default_factory=ProgramLimits)
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False

    @field_validator("program_id")
    @classmethod
    def _pid(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("program_id must be a safe identifier")
        return value

    @field_validator("base_pin")
    @classmethod
    def _pin(cls, value: str) -> str:
        if not _PIN_RE.fullmatch(value):
            raise ValueError("base_pin must be a 40-char lowercase git SHA")
        return value

    @model_validator(mode="after")
    def _graph_is_sound(self) -> WorkProgram:
        ids = [task.task_id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task_id in program")
        known = set(ids)
        for task in self.tasks:
            unknown = sorted(set(task.depends_on) - known)
            if unknown:
                raise ValueError(
                    f"task {task.task_id} depends on unknown task(s): "
                    f"{', '.join(unknown)}"
                )
        _reject_cycles(self.tasks)
        return self

    def task(self, task_id: str) -> ProgramTask:
        for candidate in self.tasks:
            if candidate.task_id == task_id:
                return candidate
        raise ProgramError(f"unknown task {task_id}", code="UNKNOWN_TASK")


def _reject_cycles(tasks: tuple[ProgramTask, ...]) -> None:
    """Fail closed on a dependency cycle.

    A cycle is not merely unschedulable, it is undetectable at run time from
    ``select_next``'s point of view: every node in the cycle simply never
    becomes eligible, and the supervisor would report NO_ELIGIBLE_WORK
    forever without ever saying why. Reject it at the program boundary.
    """
    edges = {task.task_id: set(task.depends_on) for task in tasks}
    permanent: set[str] = set()
    visiting: list[str] = []

    def visit(node: str) -> None:
        if node in permanent:
            return
        if node in visiting:
            cycle = " -> ".join([*visiting[visiting.index(node) :], node])
            raise ValueError(f"dependency cycle: {cycle}")
        visiting.append(node)
        for dep in sorted(edges.get(node, set())):
            visit(dep)
        visiting.pop()
        permanent.add(node)

    for task_id in sorted(edges):
        visit(task_id)
