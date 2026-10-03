"""AS-ORCH-PROGRAM-SUPERVISOR-001 -- continuous execution of an approved program.

The owner approves a work program once. The supervisor executes its eligible
tasks, preserves progress across worker sessions and its own restarts, waits
for external events without blocking unrelated work, and asks for input only
when a decision is genuinely the owner's.

  PROGRAM_APPROVAL != MERGE_AUTHORIZATION
  WORKER_REPORTED_COMPLETION != ACCEPTANCE
  ACCEPTANCE != INDEPENDENT_VERIFICATION
  TASK_COMPLETE != PROGRAM_COMPLETE
  ADAPTER_EXIT_ZERO != ACCEPTANCE_PASSED
  ESTIMATED_COST != BILLED_SPEND
  FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY

Scheduling, ownership, transitions and owner gates are the existing
``orchestration.autonomy`` control plane. This package adds the program, the
profiles, the runtime adapters, and the loop.
"""

from __future__ import annotations

from project_atlas.orchestration.program.loader import (
    LoadedProgram,
    ProgramLoadError,
    load_program,
)
from project_atlas.orchestration.program.models import (
    DIRECTIVE_ID,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    AcceptanceCheck,
    AcceptanceKind,
    AttemptPhase,
    ExecutionConfidence,
    ExternalPrecondition,
    FailureClass,
    ProgramError,
    ProgramLimits,
    ProgramStopReason,
    ProgramTask,
    WorkProgram,
)
from project_atlas.orchestration.program.profiles import (
    AdapterKind,
    AgentProfile,
    ProfileSet,
    resolve_effective_profile,
)
from project_atlas.orchestration.program.supervisor import (
    ProgramSupervisor,
    SupervisorReport,
)

__all__ = [
    "DIRECTIVE_ID",
    "PACKAGE_ID",
    "TRUTH_BOUNDARY",
    "AcceptanceCheck",
    "AcceptanceKind",
    "AdapterKind",
    "AgentProfile",
    "AttemptPhase",
    "ExecutionConfidence",
    "ExternalPrecondition",
    "FailureClass",
    "LoadedProgram",
    "ProfileSet",
    "ProgramError",
    "ProgramLimits",
    "ProgramLoadError",
    "ProgramStopReason",
    "ProgramSupervisor",
    "ProgramTask",
    "SupervisorReport",
    "WorkProgram",
    "load_program",
    "resolve_effective_profile",
]
