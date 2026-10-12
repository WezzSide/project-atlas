"""Load and validate an approved program file.

The program file is the artifact the owner approves once. It is read as data
and validated fully before anything runs, so a malformed dependency, a profile
that does not exist, an override that widens authority, or a task whose
acceptance conditions do not satisfy its profile's evidence requirements is a
validation failure -- not something discovered halfway through execution with
a worker already running.

File format (JSON):

    {
      "schema_version": 1,
      "program": {"program_id": ..., "objective": ..., "tasks": [...], ...},
      "profile_defaults": { ...fields shared by every profile... },
      "profiles": {"implementer": {...}, "verifier": {...}}
    }

``program.workspace_root`` is resolved relative to the program file's own
directory when it is not absolute, so a program is relocatable with its repo.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from project_atlas.orchestration.program.models import (
    ProgramError,
    ProgramTask,
    WorkProgram,
)
from project_atlas.orchestration.program.profiles import (
    AgentProfile,
    ProfileSet,
    build_profile_set,
    resolve_effective_profile,
)

MAX_PROGRAM_BYTES = 2 * 1024 * 1024


class ProgramLoadError(ProgramError):
    code = "PROGRAM_LOAD_ERROR"


@dataclass(frozen=True)
class LoadedProgram:
    """A fully validated program plus everything derived from it."""

    program: WorkProgram
    profiles: ProfileSet
    workspace: Path
    source_path: Path
    digest: str
    #: Effective profile per task, already resolved and narrowing-checked.
    effective: dict[str, AgentProfile]
    #: Effective verifier profile per task that declares one.
    verifiers: dict[str, AgentProfile]

    def effective_profile(self, task_id: str) -> AgentProfile:
        profile = self.effective.get(task_id)
        if profile is None:
            raise ProgramLoadError(
                f"no effective profile resolved for task {task_id}",
                code="UNKNOWN_TASK",
            )
        return profile


def _summarise(exc: ValidationError) -> str:
    """One line per validation error: where it was and what was wrong.

    Deliberately does not echo the rejected value. A program file can carry an
    instruction, a path, or anything else an author put in it, and an error
    message is not the place to reprint content that was rejected.
    """
    parts: list[str] = []
    for error in exc.errors()[:8]:
        location = ".".join(str(item) for item in error.get("loc", ())) or "(root)"
        parts.append(f"{location}: {error.get('msg', 'invalid')}")
    remaining = len(exc.errors()) - len(parts)
    if remaining > 0:
        parts.append(f"and {remaining} more")
    return "; ".join(parts)


def program_digest(payload: dict[str, Any]) -> str:
    """Canonical digest of the approved program file's content.

    Recorded in program state so a program edited underneath a running
    supervisor is visible as a different program rather than silently
    continuing under an approval that no longer describes it.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def profile_digest(profile: AgentProfile) -> str:
    canonical = json.dumps(
        profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_program(path: Path) -> LoadedProgram:
    """Read, validate and fully resolve a program file. Fails closed."""
    source = path.expanduser().resolve()
    if not source.is_file():
        raise ProgramLoadError(f"program file not found: {source}", code="FILE_MISSING")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ProgramLoadError(
            f"cannot stat program file {source}: {exc}", code="FILE_UNREADABLE"
        ) from exc
    if size > MAX_PROGRAM_BYTES:
        raise ProgramLoadError(
            f"program file is {size} bytes, over the {MAX_PROGRAM_BYTES} limit",
            code="FILE_TOO_LARGE",
        )
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProgramLoadError(
            f"program file {source} is not readable JSON: {exc}",
            code="FILE_UNREADABLE",
        ) from exc
    if not isinstance(raw, dict):
        raise ProgramLoadError(
            "program file must contain a JSON object", code="FILE_MALFORMED"
        )

    unknown = sorted(
        set(raw) - {"schema_version", "program", "profile_defaults", "profiles"}
    )
    if unknown:
        raise ProgramLoadError(
            f"program file has unknown top-level key(s): {', '.join(unknown)}",
            code="FILE_MALFORMED",
        )

    raw_profiles = raw.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ProgramLoadError(
            "program file must declare at least one profile", code="NO_PROFILES"
        )
    defaults = raw.get("profile_defaults")
    if defaults is not None and not isinstance(defaults, dict):
        raise ProgramLoadError(
            "profile_defaults must be an object", code="FILE_MALFORMED"
        )
    try:
        profiles = build_profile_set(defaults=defaults, raw_profiles=raw_profiles)
    except ValidationError as exc:
        raise ProgramLoadError(
            "a profile is not valid: " + _summarise(exc), code="PROFILE_INVALID"
        ) from exc

    raw_program = raw.get("program")
    if not isinstance(raw_program, dict):
        raise ProgramLoadError("program file must contain 'program'", code="FILE_MALFORMED")
    try:
        program = WorkProgram.model_validate(raw_program)
    except ValidationError as exc:
        # The CLI's contract is one JSON object per command. A pydantic
        # traceback escaping to the terminal breaks that for exactly the
        # readers most likely to hit it -- someone writing their first program
        # file. The location and message are kept; the stack is not.
        raise ProgramLoadError(
            "the program is not valid: " + _summarise(exc),
            code="PROGRAM_INVALID",
        ) from exc

    workspace = Path(program.workspace_root).expanduser()
    if not workspace.is_absolute():
        workspace = (source.parent / workspace).resolve()
    else:
        workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ProgramLoadError(
            f"workspace_root {workspace} is not a directory",
            code="WORKSPACE_MISSING",
        )

    effective: dict[str, AgentProfile] = {}
    verifiers: dict[str, AgentProfile] = {}
    for task in program.tasks:
        resolved = resolve_effective_profile(
            profiles, profile_ref=task.profile_ref, override=task.profile_override
        )
        _check_task_against_profile(task, resolved)
        effective[task.task_id] = resolved
        if task.verifier_profile_ref is not None:
            verifier = profiles.resolve(task.verifier_profile_ref)
            _check_verifier(task, implementer=resolved, verifier=verifier)
            verifiers[task.task_id] = verifier

    return LoadedProgram(
        program=program,
        profiles=profiles,
        workspace=workspace,
        source_path=source,
        digest=program_digest(raw),
        effective=effective,
        verifiers=verifiers,
    )


def _check_task_against_profile(task: ProgramTask, profile: AgentProfile) -> None:
    """Every way a task can exceed its profile, checked before anything runs."""
    missing_caps = sorted(
        cap.value for cap in task.capabilities_required if cap not in profile.capabilities
    )
    if missing_caps:
        raise ProgramLoadError(
            f"task {task.task_id} requires capability/-ies {', '.join(missing_caps)} "
            f"that profile {profile.profile_id} does not have",
            code="CAPABILITY_MISMATCH",
        )

    outside = sorted(
        path for path in task.mutation_paths if not profile.permits_mutation(path)
    )
    if outside:
        raise ProgramLoadError(
            f"task {task.task_id} declares mutation path(s) outside profile "
            f"{profile.profile_id}: {', '.join(outside)}",
            code="MUTATION_SURFACE_EXPANSION",
        )

    present = {check.kind for check in task.acceptance}
    missing_evidence = sorted(
        kind.value for kind in profile.required_evidence if kind not in present
    )
    if missing_evidence:
        raise ProgramLoadError(
            f"task {task.task_id} omits evidence profile {profile.profile_id} "
            f"requires: {', '.join(missing_evidence)}",
            code="EVIDENCE_REQUIREMENT_UNMET",
        )


def _check_verifier(
    task: ProgramTask, *, implementer: AgentProfile, verifier: AgentProfile
) -> None:
    """The implementer can never satisfy its own independent verification gate.

    Checked on ``agent_id``, not on ``profile_id``: two profiles are a real
    separation of principals only when they name different agents. Distinct
    profile names over one agent id would look independent and be nothing of
    the kind.
    """
    from project_atlas.orchestration.autonomy.models import AgentCapability

    if verifier.agent_id == implementer.agent_id:
        raise ProgramLoadError(
            f"task {task.task_id} names verifier profile "
            f"{verifier.profile_id}, but it resolves to the same agent_id "
            f"({verifier.agent_id}) as the implementer; an agent cannot "
            "independently verify its own work",
            code="IMPLEMENTER_CANNOT_VERIFY",
        )
    if AgentCapability.VERIFY not in verifier.capabilities:
        raise ProgramLoadError(
            f"verifier profile {verifier.profile_id} for task {task.task_id} "
            "does not hold the VERIFY capability",
            code="CAPABILITY_MISMATCH",
        )
