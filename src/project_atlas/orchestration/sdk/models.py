"""Typed contracts for the Cursor SDK durable runtime. Evidence only, never authority."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_atlas.orchestration.autonomy.models import CANONICAL_REPOSITORY_IDENTITY
from project_atlas.orchestration.autonomy.trust import (
    repository_identities_match,
    require_full_pin,
)

PACKAGE_ID: Final[Literal["AS-ORCH-CONTINUATION-BROKER-001"]] = (
    "AS-ORCH-CONTINUATION-BROKER-001"
)
DIRECTIVE_ID: Final[
    Literal["D-AUTONOMOUS-CURSOR-SDK-SUPERVISOR-ACTIVATION-AND-LIVE-DAG-TAKEOVER-083"]
] = "D-AUTONOMOUS-CURSOR-SDK-SUPERVISOR-ACTIVATION-AND-LIVE-DAG-TAKEOVER-083"
PRIMARY_BACKEND: Final[Literal["CURSOR_SDK_DURABLE_AGENT_RUNTIME"]] = (
    "CURSOR_SDK_DURABLE_AGENT_RUNTIME"
)
STOP_HOOK_BACKEND: Final[Literal["CURSOR_STOP_HOOK_FOLLOWUP"]] = "CURSOR_STOP_HOOK_FOLLOWUP"
CANONICAL_REPO_URL: Final[str] = "https://github.com/WezzSide/project-atlas"
DEFAULT_MODEL: Final[str] = "composer-2.5"
STATE_DIR_RELATIVE: Final[str] = ".atlas/orchestration/sdk-runtime"
AGENTS_NAME: Final[str] = "agents.json"
RUNS_NAME: Final[str] = "runs.json"
AUTH_PREREQ_NAME: Final[str] = "auth-prerequisite.json"
PLACEHOLDER_DIGEST: Final[str] = "0" * 64

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
# SDK cloud/local agents (bc-/agent-*) and CLI sessions (cli-<uuid>).
_AGENT_ID_RE = re.compile(
    r"^(?:bc-|agent-|cli-)[A-Za-z0-9_-]{1,128}$"
)


class SdkRuntimeError(ValueError):
    """Fail-closed SDK runtime error. Not an authority grant."""

    code = "SDK_RUNTIME_FAILED_CLOSED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class AgentRole(StrEnum):
    IMPLEMENTER = "IMPLEMENTER"
    REMEDIATOR = "REMEDIATOR"
    INDEPENDENT_VERIFIER = "INDEPENDENT_VERIFIER"
    SECURITY_REVIEWER = "SECURITY_REVIEWER"
    LOCAL_AUTHENTIC_WORKER = "LOCAL_AUTHENTIC_WORKER"
    READ_ONLY_ANALYST = "READ_ONLY_ANALYST"
    LOOKAHEAD = "LOOKAHEAD"
    CLOUD_RUNTIME_AUDITOR = "CLOUD_RUNTIME_AUDITOR"


class AgentRuntime(StrEnum):
    CLOUD = "CLOUD"
    LOCAL = "LOCAL"


class AgentState(StrEnum):
    ACTIVE = "ACTIVE"
    BUSY = "BUSY"
    IDLE = "IDLE"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"


class RunStatus(StrEnum):
    CREATING = "CREATING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


TERMINAL_RUN_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.FINISHED, RunStatus.ERROR, RunStatus.CANCELLED}
)

# Roles that must never share worker lineage with IMPLEMENTER/REMEDIATOR.
INDEPENDENT_ROLES: Final[frozenset[AgentRole]] = frozenset(
    {
        AgentRole.INDEPENDENT_VERIFIER,
        AgentRole.SECURITY_REVIEWER,
        AgentRole.READ_ONLY_ANALYST,
        AgentRole.CLOUD_RUNTIME_AUDITOR,
    }
)

MUTATING_ROLES: Final[frozenset[AgentRole]] = frozenset(
    {AgentRole.IMPLEMENTER, AgentRole.REMEDIATOR}
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AgentRecord(BaseModel):
    """Persisted agent routing identity. Grants no authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    agent_id: str = Field(min_length=4, max_length=160)
    runtime: AgentRuntime
    role: AgentRole
    package_id: str = Field(min_length=1, max_length=128)
    base_main: str = Field(min_length=40, max_length=40)
    branch: str | None = None
    created_at: str
    last_run_id: str | None = None
    state: AgentState = AgentState.IDLE
    archived: bool = False
    # Persisted worker lineage (D-092). Resume must use stored values.
    worker_backend: str | None = None
    workspace: str | None = None
    repository: str | None = None
    creation_generation: int | None = Field(default=None, ge=0, le=1_000_000)
    creation_sequence: int | None = Field(default=None, ge=1, le=1_000_000)
    lineage_id: str | None = None
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False
    authority_granted: Literal[False] = False

    @field_validator("agent_id")
    @classmethod
    def _agent_id(cls, value: str) -> str:
        if not _AGENT_ID_RE.fullmatch(value):
            raise ValueError("forged or malformed agent_id rejected")
        return value

    @field_validator("base_main")
    @classmethod
    def _pin(cls, value: str) -> str:
        return require_full_pin(value, "agent base_main")

    @field_validator("package_id", "lineage_id", "worker_backend")
    @classmethod
    def _token_opt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _ID_RE.fullmatch(value):
            raise ValueError("unsafe identity token")
        return value

    @field_validator("branch")
    @classmethod
    def _branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _BRANCH_RE.fullmatch(value):
            raise ValueError("unsafe branch token")
        return value

    @model_validator(mode="after")
    def _closed(self) -> AgentRecord:
        if self.merge_authorized or self.execution_authorized or self.authority_granted:
            raise ValueError("agent registry cannot carry authority")
        if self.runtime == AgentRuntime.CLOUD and not self.agent_id.startswith("bc-"):
            raise ValueError("cloud agents must use bc- agent_id")
        if self.runtime == AgentRuntime.LOCAL and self.agent_id.startswith("bc-"):
            raise ValueError("local agents must not use bc- agent_id")
        if self.agent_id.startswith("cli-") and self.runtime != AgentRuntime.LOCAL:
            raise ValueError("cli- identities are local-only")
        if self.archived and self.state != AgentState.ARCHIVED:
            raise ValueError("archived agent must be ARCHIVED")
        return self


class RunRecord(BaseModel):
    """Persisted run routing identity. Prompt body is never stored."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=160)
    agent_id: str = Field(min_length=4, max_length=160)
    cycle_id: str | None = None
    package_id: str = Field(min_length=1, max_length=128)
    lease_id: str | None = None
    role: AgentRole
    prompt_digest: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=256)
    status: RunStatus = RunStatus.CREATING
    started_at: str
    completed_at: str | None = None
    result_digest: str | None = None
    candidate_head: str | None = None
    candidate_tree: str | None = None
    node_id: str | None = None
    dag_generation: int = Field(default=0, ge=0, le=1_000_000)
    attempt: int = Field(default=1, ge=1, le=10_000)
    repository_identity: str = CANONICAL_REPOSITORY_IDENTITY
    token_usage_total: int | None = None
    cost_charged_cents: float | None = None
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False
    authority_granted: Literal[False] = False

    @field_validator("agent_id")
    @classmethod
    def _agent_id(cls, value: str) -> str:
        if not _AGENT_ID_RE.fullmatch(value):
            raise ValueError("forged or malformed agent_id rejected")
        return value

    @field_validator("run_id", "idempotency_key", "package_id", "lease_id", "cycle_id", "node_id")
    @classmethod
    def _token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _ID_RE.fullmatch(value):
            raise ValueError("unsafe identity token")
        return value

    @field_validator("candidate_head", "candidate_tree")
    @classmethod
    def _optional_pin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_full_pin(value, "candidate pin")

    @model_validator(mode="after")
    def _closed(self) -> RunRecord:
        if self.merge_authorized or self.execution_authorized or self.authority_granted:
            raise ValueError("run registry cannot carry authority")
        if not repository_identities_match(
            self.repository_identity, CANONICAL_REPOSITORY_IDENTITY
        ):
            raise ValueError("cross-project run reuse is forbidden")
        if self.status in TERMINAL_RUN_STATUSES and self.completed_at is None:
            raise ValueError("terminal run requires completed_at")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES


class ScheduleRequest(BaseModel):
    """Governor → SDK worker assignment. Not authority."""

    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    package_id: str
    node_id: str
    cycle_id: str
    dag_generation: int = Field(ge=0, le=1_000_000)
    attempt: int = Field(default=1, ge=1, le=10_000)
    lease_id: str | None = None
    base_main: str
    branch: str | None = None
    candidate_head: str | None = None
    candidate_tree: str | None = None
    prompt: str = Field(min_length=1, max_length=100_000)
    prefer_followup: bool = False
    existing_agent_id: str | None = None
    runtime: AgentRuntime | None = None
    model: str = DEFAULT_MODEL

    @field_validator("base_main")
    @classmethod
    def _pin(cls, value: str) -> str:
        return require_full_pin(value, "schedule base_main")


class IngestedRunResult(BaseModel):
    """Normalized terminal run for governor ingestion. Not authority."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_id: str
    status: RunStatus
    result_digest: str
    result_text_digest: str | None = None
    candidate_head: str | None = None
    candidate_tree: str | None = None
    token_usage_total: int | None = None
    cost_charged_cents: float | None = None
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False
    authority_granted: Literal[False] = False
