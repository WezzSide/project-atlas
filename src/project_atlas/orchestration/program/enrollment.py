"""Agent enrollment: one supported way to register an agent and give it work.

Four identities, kept genuinely separate, because conflating any two of them is
how a supervisor ends up doing something nobody authorized:

  AGENT IDENTITY AND PERMISSIONS  ``EnrolledAgent.agent_id`` plus the profile
      narrowing it carries. Durable. Outlives every run, every session, every
      supervisor process. This is the principal that "the implementer cannot
      verify its own work" is checked against.

  RUNTIME / SESSION IDENTITY  ``AttemptRecord.runtime_session_id``. Per
      attempt. Minted by the runtime or assigned by the supervisor depending on
      the adapter's ``accepts_assigned_session``. Says nothing about who is
      allowed to do what.

  TASK OWNERSHIP  the ``AgentLease`` and its durable projection row. Per task,
      held while work is in flight, released or transitioned when it ends. This
      is what stops two workers writing the same surface.

  SUPERVISOR PROCESS LIFECYCLE  the singleton lock, its instance id and pid.
      Per process. A supervisor dying does not release task ownership and does
      not end an agent's enrollment; those are different facts with different
      lifetimes.

`ENROLLMENT != AUTHORIZATION`. Enrolling an agent records that it exists, what
runtime it is, where it works and what it may narrow. It grants nothing. The
approved program still decides what work exists, the profile still decides what
the worker may do, and every owner gate still fails closed.

`NEVER SILENTLY ADOPT`. An existing session becomes this supervisor's business
only through an explicit, recorded act -- ``supervisor.enroll_session`` for a
stored session, and nothing at all for a live process. There is no code path
here that discovers a running agent and takes it over.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from project_atlas.orchestration.program.loader import LoadedProgram, load_program
from project_atlas.orchestration.program.models import (
    PACKAGE_ID,
    AuthorityExpansionError,
    ProgramError,
)
from project_atlas.orchestration.program.profiles import (
    AdapterKind,
    AgentProfile,
    resolve_effective_profile,
)
from project_atlas.orchestration.program.store import _write_atomic, state_dir

REGISTRY_NAME: Final[str] = "agents.json"
_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class EnrollmentError(ProgramError):
    code = "ENROLLMENT_ERROR"


class AgentStatus(StrEnum):
    """Whether an enrolled agent may currently be given work."""

    ACTIVE = "ACTIVE"
    #: Temporarily not dispatchable. Its enrollment, ownership and history all
    #: survive; only new dispatch is withheld.
    SUSPENDED = "SUSPENDED"
    #: Permanently done. Kept in the registry so its past work stays
    #: attributable to a principal that still exists.
    RETIRED = "RETIRED"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EnrolledAgent(BaseModel):
    """One registered agent. Identity and permissions, not a session."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=128)
    #: The program-profile name this agent fills. A program declares roles;
    #: enrollment says who plays them.
    role: str = Field(min_length=1, max_length=128)
    #: Which runtime this agent *is*. A program written for one runtime is not
    #: silently run by another; see ``bind``.
    adapter: AdapterKind
    #: Absolute path to the repository this agent works in.
    workspace_root: str = Field(min_length=1, max_length=4096)
    description: str = Field(default="", max_length=512)
    #: Narrowing-only overrides layered on top of the program's own profile for
    #: this role. Checked by the same rules a task override is: an enrollment
    #: can tighten what an agent may do and can never widen it.
    profile_narrowing: dict[str, Any] = Field(default_factory=dict)
    status: AgentStatus = AgentStatus.ACTIVE
    enrolled_by: str = Field(min_length=1, max_length=256)
    enrolled_at: str = Field(default_factory=_utc_now)
    #: Durable record that an operator authorized this agent to run a role
    #: written for a different runtime. Stored rather than passed as a flag at
    #: launch time: an authorization that lives only in the argv of whichever
    #: command happened to run is not an authorization anyone can audit, and a
    #: later launch through a different entry point would not see it.
    runtime_substitution_authorized: bool = False
    runtime_substitution_authorized_by: str | None = None
    #: Path to the approved program this agent is assigned to, if any.
    assigned_program: str | None = None
    assigned_by: str | None = None
    assigned_at: str | None = None
    merge_authorized: Literal[False] = False

    @field_validator("agent_id", "role")
    @classmethod
    def _ident(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("identifier must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        return value


class AgentRegistry(BaseModel):
    """The durable roster. Evidence and routing only, never authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    package_id: Literal["AS-ORCH-PROGRAM-SUPERVISOR-001"] = PACKAGE_ID
    agents: dict[str, EnrolledAgent] = Field(default_factory=dict)
    merge_authorized: Literal[False] = False


def registry_path(root: Path) -> Path:
    return state_dir(root) / REGISTRY_NAME


def load_registry(root: Path) -> AgentRegistry:
    path = registry_path(root)
    if not path.is_file():
        return AgentRegistry()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnrollmentError(
            f"agent registry at {path} is unreadable: {exc}",
            code="REGISTRY_UNREADABLE",
        ) from exc
    return AgentRegistry.model_validate(raw)


def persist_registry(root: Path, registry: AgentRegistry) -> Path:
    path = registry_path(root)
    _write_atomic(
        path,
        json.dumps(registry.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
    )
    return path


def enroll(
    root: Path,
    *,
    agent_id: str,
    role: str,
    adapter: AdapterKind | str,
    workspace_root: Path,
    enrolled_by: str,
    description: str = "",
    profile_narrowing: dict[str, Any] | None = None,
    replace: bool = False,
) -> EnrolledAgent:
    """Register an agent. Records that it exists; authorizes nothing."""
    registry = load_registry(root)
    existing = registry.agents.get(agent_id)
    if existing is not None and not replace:
        raise EnrollmentError(
            f"agent {agent_id} is already enrolled (as role {existing.role!r}); "
            "pass replace to change it",
            code="AGENT_ALREADY_ENROLLED",
        )
    workspace = workspace_root.expanduser().resolve()
    if not workspace.is_dir():
        raise EnrollmentError(
            f"workspace {workspace} is not a directory", code="WORKSPACE_MISSING"
        )
    agent = EnrolledAgent(
        agent_id=agent_id,
        role=role,
        adapter=AdapterKind(adapter),
        workspace_root=str(workspace),
        description=description,
        profile_narrowing=dict(profile_narrowing or {}),
        enrolled_by=enrolled_by,
        # A replacement keeps whatever assignment the agent already had, so
        # correcting a description does not silently unassign its work.
        assigned_program=existing.assigned_program if existing else None,
        assigned_by=existing.assigned_by if existing else None,
        assigned_at=existing.assigned_at if existing else None,
        # A replacement never inherits a runtime-substitution grant. The grant
        # was given for a specific agent record; re-enrolling changes that
        # record, and silently carrying an authorization across is how a grant
        # outlives the thing it was granted for.
        runtime_substitution_authorized=False,
        runtime_substitution_authorized_by=None,
    )
    registry.agents[agent_id] = agent
    persist_registry(root, registry)
    return agent


def set_status(
    root: Path, *, agent_id: str, status: AgentStatus | str
) -> EnrolledAgent:
    """Suspend, retire or reactivate an agent.

    Deliberately does not touch ownership: an agent suspended mid-task still
    holds its lease, because dropping ownership of a surface somebody may still
    be writing is worse than pausing dispatch. Use ``reconcile`` for the lease.
    """
    registry = load_registry(root)
    agent = registry.agents.get(agent_id)
    if agent is None:
        raise EnrollmentError(f"unknown agent {agent_id}", code="UNKNOWN_AGENT")
    agent.status = AgentStatus(status)
    persist_registry(root, registry)
    return agent


def assign(
    root: Path,
    *,
    agent_id: str,
    program_path: Path,
    assigned_by: str,
    allow_runtime_substitution: bool = False,
) -> tuple[EnrolledAgent, LoadedProgram]:
    """Assign an approved program to an enrolled agent.

    The program is fully validated first, and the binding is checked before it
    is recorded: an assignment that could not actually run is refused now
    rather than at the moment a worker would have started.
    """
    registry = load_registry(root)
    agent = registry.agents.get(agent_id)
    if agent is None:
        raise EnrollmentError(f"unknown agent {agent_id}", code="UNKNOWN_AGENT")
    if agent.status is not AgentStatus.ACTIVE:
        raise EnrollmentError(
            f"agent {agent_id} is {agent.status.value} and cannot be assigned work",
            code="AGENT_NOT_ACTIVE",
        )
    loaded = load_program(program_path)
    # Prove the binding resolves before recording it.
    bind(agent, loaded, allow_runtime_substitution=allow_runtime_substitution)

    agent.assigned_program = str(program_path.expanduser().resolve())
    agent.assigned_by = assigned_by
    agent.assigned_at = _utc_now()
    agent.runtime_substitution_authorized = allow_runtime_substitution
    agent.runtime_substitution_authorized_by = (
        assigned_by if allow_runtime_substitution else None
    )
    persist_registry(root, registry)
    return agent, loaded


def bind(
    agent: EnrolledAgent,
    loaded: LoadedProgram,
    *,
    allow_runtime_substitution: bool = False,
) -> AgentProfile:
    """Resolve the profile this agent actually runs under, or refuse.

    Layering, in one direction only:

        program profile for the role
          ⊕ the agent's own narrowing   (same checker as a task override)
          ⊕ the agent's identity        (agent_id is the principal, not a permission)

    The agent's ``agent_id`` replaces the program's placeholder because the
    principal is a property of who is doing the work, not of the work. Every
    permission still comes from the program's profile, narrowed.
    """
    profile = loaded.profiles.profiles.get(agent.role)
    if profile is None:
        raise EnrollmentError(
            f"program {loaded.program.program_id} declares no role "
            f"{agent.role!r}; it has: {', '.join(sorted(loaded.profiles.profiles))}",
            code="ROLE_NOT_IN_PROGRAM",
        )
    if profile.adapter is not agent.adapter and not allow_runtime_substitution:
        raise EnrollmentError(
            f"agent {agent.agent_id} is a {agent.adapter.value} agent but role "
            f"{agent.role!r} is written for {profile.adapter.value}. Running it "
            "on a different runtime is a decision, not a default: pass "
            "allow_runtime_substitution if that is what you mean",
            code="RUNTIME_SUBSTITUTION_NOT_AUTHORIZED",
        )
    try:
        narrowed = resolve_effective_profile(
            loaded.profiles, profile_ref=agent.role, override=agent.profile_narrowing
        )
    except AuthorityExpansionError as exc:
        raise EnrollmentError(
            f"agent {agent.agent_id}'s enrollment tries to widen role "
            f"{agent.role!r}: {exc}",
            code=str(getattr(exc, "code", "AUTHORITY_EXPANSION_FORBIDDEN")),
        ) from exc
    updates: dict[str, Any] = {"agent_id": agent.agent_id}
    if allow_runtime_substitution and profile.adapter is not agent.adapter:
        updates["adapter"] = agent.adapter
    return narrowed.model_copy(update=updates)


def identity_view(
    agent: EnrolledAgent, loaded: LoadedProgram | None = None
) -> dict[str, Any]:
    """The four identities, side by side, each labelled with its lifetime.

    Exists because "who is this" has four different answers depending on which
    question is being asked, and a status view that blurs them is how an
    operator ends up believing a dead supervisor means an unowned task.
    """
    effective = None
    if loaded is not None:
        try:
            effective = bind(
                agent,
                loaded,
                allow_runtime_substitution=agent.runtime_substitution_authorized,
            )
        except EnrollmentError:
            effective = None
    return {
        "agent_identity": {
            "agent_id": agent.agent_id,
            "role": agent.role,
            "adapter": agent.adapter.value,
            "status": agent.status.value,
            "workspace_root": agent.workspace_root,
            "enrolled_by": agent.enrolled_by,
            "enrolled_at": agent.enrolled_at,
            "lifetime": "durable; outlives every run, session and supervisor",
            "permissions_from": (
                f"the program's {agent.role!r} profile, narrowed by this "
                "enrollment; never widened by it"
            ),
            "effective_profile_id": (
                effective.profile_id if effective is not None else None
            ),
        },
        "runtime_session_identity": {
            "lifetime": "one attempt",
            "note": (
                "recorded per attempt as runtime_session_id; assigned by the "
                "supervisor only when the adapter accepts an assigned session, "
                "otherwise minted and reported by the runtime. Carries no "
                "permission of its own"
            ),
        },
        "task_ownership": {
            "lifetime": "one task, while work is in flight",
            "note": (
                "an AgentLease and its durable projection row. Survives the "
                "supervisor process; released or transitioned when the task "
                "ends, never when a process dies"
            ),
        },
        "supervisor_process": {
            "lifetime": "one process",
            "note": (
                "the singleton lock, its instance id and pid. A supervisor "
                "exiting ends none of the three above"
            ),
        },
        "assignment": {
            "assigned_program": agent.assigned_program,
            "assigned_by": agent.assigned_by,
            "assigned_at": agent.assigned_at,
        },
        "merge_authorized": False,
    }
