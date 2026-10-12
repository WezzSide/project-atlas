"""Operator surface for AS-ORCH-PROGRAM-SUPERVISOR-001.

Invoked as a module, mirroring ``orchestration.autonomy.cli``'s standalone
parser:

    python -m project_atlas.orchestration.program.cli <command> [options]

Deliberately NOT wired into ``src/project_atlas/cli.py``. That file is under a
structural guard (``tests/unit/test_atlas3_demo_isolation_001.py::
test_cli_mutation_is_additive_only``) that requires every diff to it to add an
Atlas 3 parser hook; a supervisor subcommand is not that, and working around
the guard is not this package's business. ``register_program_parser()`` below
is the whole wiring, so an owner who wants ``atlas program ...`` gets it in one
additive call whenever that grant exists.

Every command prints one JSON object on stdout. Exit codes follow the
repository convention: 0 success, 1 operational error, 2 usage error.

None of these commands merge, grant a gate, or widen a program.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from project_atlas.orchestration.program import control, service
from project_atlas.orchestration.program.credentials import credential_report
from project_atlas.orchestration.program.enrollment import (
    AgentStatus,
    EnrollmentError,
    assign,
    bind,
    enroll,
    identity_view,
    load_registry,
    set_status,
)
from project_atlas.orchestration.program.loader import (
    LoadedProgram,
    ProgramLoadError,
    load_program,
    profile_digest,
)
from project_atlas.orchestration.program.models import ProgramError
from project_atlas.orchestration.program.profiles import (
    UNENFORCED_MODES,
    AdapterKind,
)
from project_atlas.orchestration.program.runtimes import (
    NO_GENERIC_ADAPTER,
    UNIVERSALLY_UNSUPPORTED,
    capability_matrix,
    describe_all,
    inventory_unimplemented,
)
from project_atlas.orchestration.program.store import read_events
from project_atlas.orchestration.program.supervisor import ProgramSupervisor

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _state_root(args: argparse.Namespace, loaded: LoadedProgram) -> Path:
    explicit = getattr(args, "state_root", None)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return loaded.source_path.parent


def _enrolled_for(
    args: argparse.Namespace, loaded: LoadedProgram
) -> tuple[tuple[Any, ...], Path | None]:
    """Every ACTIVE enrolled agent whose role this program declares.

    Only present when the operator passes `--registry`, so a program can still
    be run without a roster. Binding here is what makes enrolment actually
    govern a run: the agent's identity becomes the principal on the lease, its
    narrowing applies, and its authority is re-read before every dispatch.

    A SUSPENDED or RETIRED agent is skipped rather than bound. Binding it and
    then refusing every dispatch would be the same outcome reached noisily.
    """
    registry_arg = getattr(args, "registry", None)
    if not registry_arg:
        return (), None
    root = Path(registry_arg).expanduser().resolve()
    registry = load_registry(root)
    roles = set(loaded.profiles.profiles)
    agents = tuple(
        agent
        for agent in sorted(registry.agents.values(), key=lambda item: item.agent_id)
        if agent.role in roles and agent.status is AgentStatus.ACTIVE
    )
    return agents, root


def _supervisor(args: argparse.Namespace) -> ProgramSupervisor:
    loaded = load_program(Path(args.program))
    agents, registry_root = _enrolled_for(args, loaded)
    return ProgramSupervisor(
        loaded,
        state_root=_state_root(args, loaded),
        enrolled_agents=agents,
        registry_root=registry_root,
    )


def run_validate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Validate a program file without running anything.

    Reports the enforcement picture as well as the validity: which permission
    boundaries the runtime will actually apply, and which fields are Atlas's
    own declaration. A program that validates is not thereby a safe one, and
    saying so here is cheaper than discovering it later.
    """
    loaded = load_program(Path(args.program))
    tasks: list[dict[str, Any]] = []
    warnings: list[str] = []
    for task in loaded.program.tasks:
        profile = loaded.effective_profile(task.task_id)
        if profile.permission_mode in UNENFORCED_MODES:
            warnings.append(
                f"task {task.task_id} runs under permission mode "
                f"{profile.permission_mode}, under which the runtime enforces "
                "nothing; only the OS boundary remains"
            )
        if profile.workspace.additional_dirs and not profile.workspace.restricted:
            warnings.append(
                f"profile {profile.profile_id} names additional_dirs without "
                "restricted; those directories are documentation, not a "
                "boundary, and a worker holding Bash can read outside them"
            )
        if task.requires_independent_verification and task.verifier_profile_ref is None:
            warnings.append(
                f"task {task.task_id} requires independent verification but "
                "configures no verifier profile; it will stop at that gate and "
                "wait for a person"
            )
        tasks.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "depends_on": list(task.depends_on),
                "profile_id": profile.profile_id,
                "agent_id": profile.agent_id,
                "adapter": profile.adapter.value,
                "permission_mode": profile.permission_mode,
                "effective_profile_sha256": profile_digest(profile),
                "acceptance_checks": [check.check_id for check in task.acceptance],
                "owner_gate": task.owner_gate.value if task.owner_gate else None,
                "external_precondition": (
                    task.external_precondition.precondition_id
                    if task.external_precondition
                    else None
                ),
                "requires_independent_verification": (
                    task.requires_independent_verification
                ),
                "verifier_profile_ref": task.verifier_profile_ref,
            }
        )
    return (
        {
            "valid": True,
            "program_id": loaded.program.program_id,
            "program_sha256": loaded.digest,
            "workspace": str(loaded.workspace),
            "base_pin": loaded.program.base_pin,
            "approved_by": loaded.program.approved_by,
            "approval_reference": loaded.program.approval_reference,
            "limits": loaded.program.limits.model_dump(mode="json"),
            "tasks": tasks,
            "warnings": warnings,
            "merge_authorized": False,
            "execution_authorized": False,
        },
        EXIT_OK,
    )


def run_start(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    supervisor = _supervisor(args)
    report = supervisor.start()
    payload = report.to_public_dict()
    payload["enrolled_agents_bound"] = [
        agent.agent_id for agent in supervisor.enrolled_agents
    ]
    return payload, EXIT_OK


def run_status(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    supervisor = _supervisor(args)
    return supervisor.status(), EXIT_OK


def run_cancel(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    supervisor = _supervisor(args)
    return supervisor.request_cancel(), EXIT_OK


def run_reconcile(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    supervisor = _supervisor(args)
    return (
        supervisor.reconcile(
            resolve_uncertain=getattr(args, "resolve_uncertain", None)
        ),
        EXIT_OK,
    )


def run_runtimes(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Report which runtimes this machine supports, and what they cannot do.

    Takes no program: it is a property of the host, and it is the answer to
    "can I write a program against this runtime" asked before writing one.
    """
    _ = args
    return (
        {
            "runtimes": [support.to_public_dict() for support in describe_all()],
            "inventoried_not_implemented": [
                row.to_public_dict() for row in inventory_unimplemented()
            ],
            "no_generic_adapter": NO_GENERIC_ADAPTER,
            "universally_unsupported": list(UNIVERSALLY_UNSUPPORTED),
            "authentication_checked": False,
            "authentication_note": (
                "not probed: a probe costs a model call. A credential or quota "
                "problem surfaces at first dispatch as QUOTA_OR_CREDENTIAL"
            ),
        },
        EXIT_OK,
    )


def run_credentials(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Which account each profile will actually authenticate as. No values."""
    return credential_report(load_program(Path(args.program))), EXIT_OK


def run_capabilities(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """The three-tier capability matrix. Takes no program; runs no model."""
    _ = args
    return capability_matrix(), EXIT_OK


def run_handoff(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    supervisor = _supervisor(args)
    return (
        supervisor.enroll_session(
            task_id=str(args.task),
            session_id=str(args.session_id),
            enrolled_by=str(args.enrolled_by),
            note=str(getattr(args, "note", "") or ""),
        ),
        EXIT_OK,
    )


def run_events(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    loaded = load_program(Path(args.program))
    root = _state_root(args, loaded)
    rows = read_events(root, limit=int(getattr(args, "limit", 50) or 50))
    return {"program_id": loaded.program.program_id, "events": rows}, EXIT_OK


# ------------------------------------------------------------------ control


def run_control(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """The stable read-only contract, or one governed action request."""
    loaded = load_program(Path(args.program))
    root = _state_root(args, loaded)
    action = getattr(args, "action", None)
    if not action:
        return (
            control.control_view(
                root, loaded, event_limit=int(getattr(args, "events", 25) or 25)
            ),
            EXIT_OK,
        )
    return (
        control.request_action(
            root,
            loaded,
            action=str(action),
            requested_by=str(args.requested_by),
            attempt_id=getattr(args, "attempt_id", None),
        ),
        EXIT_OK,
    )


# ------------------------------------------------------------------ service


def run_service(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    action = str(getattr(args, "service_action", ""))
    program_path = Path(args.program)
    loaded = load_program(program_path)
    root = _state_root(args, loaded)
    registry = getattr(args, "registry", None)
    registry_root = Path(registry).expanduser().resolve() if registry else None
    allow_unregistered = bool(getattr(args, "allow_unregistered", False))
    if action == "install":
        return service.install(root, program_path, registry_root=registry_root), EXIT_OK
    if action == "start":
        return (
            service.start(
                root,
                program_path,
                registry_root=registry_root,
                allow_unregistered=allow_unregistered,
            ),
            EXIT_OK,
        )
    if action == "stop":
        return service.stop(root), EXIT_OK
    if action == "status":
        return service.status(root, program_path, registry_root=registry_root), EXIT_OK
    if action == "run":
        return (
            service.run(
                root,
                program_path,
                poll_seconds=float(getattr(args, "poll_seconds", 30.0)),
                max_rounds=getattr(args, "max_rounds", None),
                registry_root=registry_root,
                allow_unregistered=allow_unregistered,
            ),
            EXIT_OK,
        )
    return {"error": "unknown service action"}, EXIT_USAGE


# --------------------------------------------------------------- enrollment


def _registry_root(args: argparse.Namespace) -> Path:
    return Path(args.registry).expanduser().resolve()


def run_agent_enroll(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    narrowing: dict[str, Any] = {}
    raw = getattr(args, "narrow", None)
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EnrollmentError(
                f"--narrow is not valid JSON: {exc}", code="NARROWING_MALFORMED"
            ) from exc
        if not isinstance(parsed, dict):
            raise EnrollmentError(
                "--narrow must be a JSON object", code="NARROWING_MALFORMED"
            )
        narrowing = parsed
    agent = enroll(
        _registry_root(args),
        agent_id=str(args.agent_id),
        role=str(args.role),
        adapter=str(args.adapter),
        workspace_root=Path(args.workspace),
        enrolled_by=str(args.enrolled_by),
        description=str(getattr(args, "description", "") or ""),
        profile_narrowing=narrowing,
        replace=bool(getattr(args, "replace", False)),
    )
    return (
        {
            "enrolled": agent.model_dump(mode="json"),
            "note": (
                "ENROLLMENT != AUTHORIZATION. This records that the agent "
                "exists, which runtime it is, where it works and what it may "
                "narrow. It grants nothing"
            ),
        },
        EXIT_OK,
    )


def run_agent_list(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    registry = load_registry(_registry_root(args))
    return (
        {
            "registry": str(_registry_root(args)),
            "agents": [
                agent.model_dump(mode="json")
                for agent in sorted(
                    registry.agents.values(), key=lambda item: item.agent_id
                )
            ],
        },
        EXIT_OK,
    )


def run_agent_assign(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    agent, loaded = assign(
        _registry_root(args),
        agent_id=str(args.agent_id),
        program_path=Path(args.program),
        assigned_by=str(args.assigned_by),
        allow_runtime_substitution=bool(
            getattr(args, "allow_runtime_substitution", False)
        ),
    )
    effective = bind(agent, loaded, allow_runtime_substitution=True)
    return (
        {
            "agent_id": agent.agent_id,
            "role": agent.role,
            "program_id": loaded.program.program_id,
            "program_sha256": loaded.digest,
            "assigned_program": agent.assigned_program,
            "effective_profile_sha256": profile_digest(effective),
            "effective_permission_mode": effective.permission_mode,
            "note": (
                "the binding was resolved before being recorded, so an "
                "assignment that could not run is refused now rather than when "
                "a worker would have started"
            ),
            "merge_authorized": False,
        },
        EXIT_OK,
    )


def run_agent_status(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    registry = load_registry(_registry_root(args))
    agent = registry.agents.get(str(args.agent_id))
    if agent is None:
        raise EnrollmentError(
            f"unknown agent {args.agent_id}", code="UNKNOWN_AGENT"
        )
    assigned = agent.assigned_program
    loaded = load_program(Path(assigned)) if assigned is not None else None
    payload = identity_view(agent, loaded)
    if loaded is not None and assigned is not None:
        supervisor = ProgramSupervisor(loaded, state_root=Path(assigned).parent)
        payload["program_status"] = supervisor.status()
    return payload, EXIT_OK


def run_agent_set_status(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    agent = set_status(
        _registry_root(args), agent_id=str(args.agent_id), status=str(args.status)
    )
    return (
        {
            "agent_id": agent.agent_id,
            "status": agent.status.value,
            "note": (
                "dispatch is withheld; task ownership is deliberately not "
                "touched, because dropping a lease on a surface somebody may "
                "still be writing is worse than pausing dispatch"
            ),
        },
        EXIT_OK,
    )


def run_agent_launch(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Launch the program assigned to an enrolled agent, under supervision."""
    registry = load_registry(_registry_root(args))
    agent = registry.agents.get(str(args.agent_id))
    if agent is None:
        raise EnrollmentError(
            f"unknown agent {args.agent_id}", code="UNKNOWN_AGENT"
        )
    if agent.status is not AgentStatus.ACTIVE:
        raise EnrollmentError(
            f"agent {agent.agent_id} is {agent.status.value}", code="AGENT_NOT_ACTIVE"
        )
    if agent.assigned_program is None:
        raise EnrollmentError(
            f"agent {agent.agent_id} has no assigned program", code="NO_ASSIGNMENT"
        )
    loaded = load_program(Path(agent.assigned_program))
    supervisor = ProgramSupervisor(
        loaded,
        state_root=Path(
            getattr(args, "state_root", None) or Path(agent.assigned_program).parent
        ),
        enrolled_agents=(agent,),
        # So a suspension, retirement or withdrawn grant recorded while the
        # program runs is seen before the NEXT dispatch, not cached from here.
        registry_root=_registry_root(args),
    )
    report = supervisor.start()
    payload = report.to_public_dict()
    payload["launched_as_agent"] = agent.agent_id
    return payload, EXIT_OK


_HANDLERS = {
    "validate": run_validate,
    "start": run_start,
    "status": run_status,
    "cancel": run_cancel,
    "reconcile": run_reconcile,
    "events": run_events,
    "runtimes": run_runtimes,
    "capabilities": run_capabilities,
    "credentials": run_credentials,
    "handoff": run_handoff,
    "service": run_service,
    "control": run_control,
}

_AGENT_HANDLERS = {
    "enroll": run_agent_enroll,
    "list": run_agent_list,
    "assign": run_agent_assign,
    "status": run_agent_status,
    "set-status": run_agent_set_status,
    "launch": run_agent_launch,
}

#: Commands that operate on a program file. ``runtimes`` does not.
_PROGRAM_SCOPED = (
    "validate",
    "start",
    "status",
    "cancel",
    "reconcile",
    "events",
    "handoff",
    "service",
    "control",
    "credentials",
)


def register_program_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Attach the ``program`` command to any argparse subparser collection.

    Kept separate from ``_build_parser`` so wiring this into the main ``atlas``
    CLI later is one call plus one dispatch line, with no change to the
    commands themselves.
    """
    parser = subparsers.add_parser(
        "program",
        help="Run an approved work program to completion (read/execute).",
        description=(
            "Execute an approved work program: select eligible tasks, dispatch "
            "one worker at a time through a runtime adapter, check acceptance "
            "locally, checkpoint, and continue. Never merges and never grants "
            "an owner gate."
        ),
    )
    sub = parser.add_subparsers(dest="program_command", required=True)

    for name, help_text in (
        ("validate", "Validate a program file and report its enforcement picture."),
        ("start", "Run the program until a stop reason."),
        ("status", "Compact read-only status. Dispatches nothing."),
        ("cancel", "Ask a running supervisor to stop before its next launch."),
        ("reconcile", "Inspect interrupted attempts; optionally settle one."),
        ("events", "Print the tail of the durable event log."),
        ("runtimes", "Report supported runtimes and what they cannot do."),
        (
            "capabilities",
            "The three-tier capability matrix: runtime-tested, implemented "
            "fixture-only, and inventoried-unavailable.",
        ),
        (
            "credentials",
            "Which account each profile will actually authenticate as, and why. "
            "Reports names and presence; never a credential value.",
        ),
        (
            "handoff",
            "Enrol an existing stored session so the next dispatch continues "
            "it in a new supervised run.",
        ),
        ("service", "Install, start, stop or inspect a durable supervisor service."),
        (
            "control",
            "The stable read-only control contract, or one governed action "
            "request (pause / resume / cancel / reconcile).",
        ),
    ):
        child = sub.add_parser(name, help=help_text)
        if name in _PROGRAM_SCOPED:
            child.add_argument(
                "--program",
                required=True,
                type=Path,
                help="Path to the approved program JSON file.",
            )
            child.add_argument(
                "--state-root",
                type=Path,
                default=None,
                help=(
                    "Directory holding program state (default: the program "
                    "file's own directory). State never lives inside the "
                    "workspace."
                ),
            )
        if name == "handoff":
            child.add_argument("--task", required=True, help="Task to hand the session to.")
            child.add_argument(
                "--session-id",
                required=True,
                help=(
                    "The runtime's own id for a session it has STORED. No live "
                    "process is adopted."
                ),
            )
            child.add_argument(
                "--enrolled-by",
                required=True,
                help="Who is making this enrolment. Recorded in program state.",
            )
            child.add_argument("--note", default="", help="Why, for the record.")
        if name == "reconcile":
            child.add_argument(
                "--resolve-uncertain",
                default=None,
                metavar="ATTEMPT_ID",
                help=(
                    "Record an operator judgement that this interrupted "
                    "attempt's effect did not land, and let its task be "
                    "scheduled again. This is your assertion, not the "
                    "supervisor's determination."
                ),
            )
        if name == "events":
            child.add_argument("--limit", type=int, default=50)
        if name in {"start", "status", "control"}:
            child.add_argument(
                "--registry",
                type=Path,
                default=None,
                help=(
                    "Bind every ACTIVE enrolled agent whose role this program "
                    "declares. Their identities become the principals on the "
                    "leases and their authority is re-read before each dispatch."
                ),
            )
        if name == "control":
            child.add_argument(
                "--action",
                default=None,
                choices=control.SUPPORTED_ACTIONS,
                help="Omit for the read-only view.",
            )
            child.add_argument(
                "--requested-by",
                default="unknown",
                help="Who is asking. Recorded with the request.",
            )
            child.add_argument(
                "--attempt-id",
                default=None,
                help="With --action reconcile: the attempt to settle.",
            )
            child.add_argument("--events", type=int, default=25)
        if name == "service":
            child.add_argument(
                "service_action",
                choices=("install", "start", "stop", "status", "run"),
                help=(
                    "install writes a launcher and activates nothing; start "
                    "detaches a service; run is the service body itself."
                ),
            )
            child.add_argument(
                "--poll-seconds",
                type=float,
                default=30.0,
                help="Pause between rounds when nothing was eligible.",
            )
            child.add_argument(
                "--max-rounds",
                type=int,
                default=None,
                help="Bound the service to this many rounds (mostly for tests).",
            )
            child.add_argument(
                "--registry",
                type=Path,
                default=None,
                help=(
                    "Directory holding the enrolled-agent registry. Detached "
                    "start/run requires this unless --allow-unregistered is explicit."
                ),
            )
            child.add_argument(
                "--allow-unregistered",
                action="store_true",
                help="Explicitly allow the existing unregistered service mode.",
            )
    return parser


def register_agent_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Attach the ``agent`` command: enroll, assign, launch, inspect."""
    parser = subparsers.add_parser(
        "agent",
        help="Enroll agents, assign approved programs, and launch under supervision.",
        description=(
            "One supported way to register an agent profile, associate its "
            "workspace and role, assign an approved work program, and launch "
            "it. ENROLLMENT != AUTHORIZATION: enrolling grants nothing, and "
            "no existing session or process is ever adopted silently."
        ),
    )
    sub = parser.add_subparsers(dest="agent_command", required=True)

    def _with_registry(child: argparse.ArgumentParser) -> argparse.ArgumentParser:
        child.add_argument(
            "--registry",
            required=True,
            type=Path,
            help="Directory holding the agent registry.",
        )
        return child

    enroll_cmd = _with_registry(
        sub.add_parser("enroll", help="Register an agent, or replace its record.")
    )
    enroll_cmd.add_argument("--agent-id", required=True)
    enroll_cmd.add_argument(
        "--role",
        required=True,
        help="The program-profile name this agent fills.",
    )
    enroll_cmd.add_argument(
        "--adapter",
        required=True,
        choices=[kind.value for kind in AdapterKind],
        help="Which runtime this agent is.",
    )
    enroll_cmd.add_argument("--workspace", required=True, type=Path)
    enroll_cmd.add_argument("--enrolled-by", required=True)
    enroll_cmd.add_argument("--description", default="")
    enroll_cmd.add_argument(
        "--narrow",
        default=None,
        metavar="JSON",
        help=(
            "Narrowing-only profile overrides, as a JSON object. Checked by "
            "the same rules a task override is; widening is refused."
        ),
    )
    enroll_cmd.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing record. Keeps any program assignment.",
    )

    _with_registry(sub.add_parser("list", help="List enrolled agents."))

    assign_cmd = _with_registry(
        sub.add_parser("assign", help="Assign an approved program to an agent.")
    )
    assign_cmd.add_argument("--agent-id", required=True)
    assign_cmd.add_argument("--program", required=True, type=Path)
    assign_cmd.add_argument("--assigned-by", required=True)
    assign_cmd.add_argument(
        "--allow-runtime-substitution",
        action="store_true",
        help=(
            "Permit an agent to run a role written for a different runtime. "
            "A decision, never a default."
        ),
    )

    status_cmd = _with_registry(
        sub.add_parser(
            "status",
            help="The four identities side by side, plus program status.",
        )
    )
    status_cmd.add_argument("--agent-id", required=True)

    set_status_cmd = _with_registry(
        sub.add_parser("set-status", help="Suspend, retire or reactivate an agent.")
    )
    set_status_cmd.add_argument("--agent-id", required=True)
    set_status_cmd.add_argument(
        "--status", required=True, choices=[item.value for item in AgentStatus]
    )

    launch_cmd = _with_registry(
        sub.add_parser("launch", help="Run this agent's assigned program.")
    )
    launch_cmd.add_argument("--agent-id", required=True)
    launch_cmd.add_argument("--state-root", type=Path, default=None)
    return parser


def dispatch_program(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    handler = _HANDLERS.get(getattr(args, "program_command", ""))
    if handler is None:
        return {"error": "unknown program command"}, EXIT_USAGE
    return handler(args)


def dispatch_agent(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    handler = _AGENT_HANDLERS.get(getattr(args, "agent_command", ""))
    if handler is None:
        return {"error": "unknown agent command"}, EXIT_USAGE
    return handler(args)


def emit(payload: dict[str, Any]) -> None:
    """Print one JSON object. The whole output contract, in one place."""
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def error_payload(exc: ProgramError) -> dict[str, Any]:
    """A refusal, in the same shape as every other result."""
    return {
        "error": str(exc),
        "code": getattr(exc, "code", "PROGRAM_ERROR"),
        "merge_authorized": False,
    }


def dispatch_cli(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Dispatch a `program` or `agent` namespace, refusals included.

    Both entry points -- `atlas program ...` and `python -m
    project_atlas.orchestration.program.cli` -- go through this one function,
    so the Atlas command and the module command cannot drift into two
    lifecycle interfaces that behave differently under the same arguments.
    """
    try:
        if getattr(args, "command", "") == "agent":
            return dispatch_agent(args)
        return dispatch_program(args)
    except (ProgramLoadError, ProgramError) as exc:
        return error_payload(exc), EXIT_ERROR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m project_atlas.orchestration.program.cli",
        description=(
            "AS-ORCH-PROGRAM-SUPERVISOR-001 -- continuous execution of an "
            "approved work program."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    register_program_parser(sub)
    register_agent_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    payload, code = dispatch_cli(args)
    emit(payload)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
