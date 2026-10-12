"""Runtime adapters for AS-ORCH-PROGRAM-SUPERVISOR-001.

``claude-code`` is the real runtime. ``local-command`` is a labelled fixture.
A run through the fixture proves supervisor behaviour and nothing about
real-runtime compatibility.
"""

from __future__ import annotations

from project_atlas.orchestration.program.adapters.base import (
    AdapterCapabilities,
    AdapterError,
    AdapterOutcome,
    AdapterRequest,
    AdapterUnavailableError,
    RuntimeAdapter,
    build_child_env,
    version_at_least,
)
from project_atlas.orchestration.program.adapters.claude_code import ClaudeCodeAdapter
from project_atlas.orchestration.program.adapters.codex import CodexAdapter
from project_atlas.orchestration.program.adapters.local_command import (
    FIXTURE_LABEL,
    LocalCommandAdapter,
)

__all__ = [
    "FIXTURE_LABEL",
    "AdapterCapabilities",
    "AdapterError",
    "AdapterOutcome",
    "AdapterRequest",
    "AdapterUnavailableError",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "LocalCommandAdapter",
    "RuntimeAdapter",
    "build_child_env",
    "version_at_least",
]
