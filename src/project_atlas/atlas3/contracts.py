"""Shared Atlas 3 contracts, honesty stamps, and atomic helpers.

D-191 / D-192 / D-193. Isolated namespace. No wall-clock in generated content.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

from atlas_contracts.identity import safe_relative_component
from project_atlas.secrets import scan_text

ATLAS3_NAMESPACE: Final[str] = "project_atlas.atlas3"
GENERATOR_ID: Final[str] = "atlas-3-isolated-runtime-001"
OPS_RELATIVE: Final[Path] = Path("generated") / "ops" / "atlas3"
FULL_LIVE_DEMO_READY: Final[bool] = False
MERGE_AUTHORIZATION: Final[str] = "NOT_GRANTED"
TRUTH_BOUNDARY: Final[str] = (
    "MODEL OUTPUT != AUTHORITY / CONVERSATION != TRUTH CORE / "
    "GRAPH != AUTHORITY / UI != CANONICAL TRUTH / "
    "CAPTURE != CANONICAL FACT / PROOF != MODEL CLAIM / "
    "FULL_LIVE_DEMO_READY = NO / MERGE_AUTHORIZATION = NOT_GRANTED"
)
HONESTY: Final[dict[str, object]] = {
    "full_live_demo_ready": False,
    "authentic_pilot": False,
    "demo_is_release": False,
    "ui_is_canonical_truth": False,
    "model_output_is_authority": False,
    "conversation_is_authority": False,
    "graph_is_authority": False,
    "capture_is_canonical_fact": False,
    "promoted_to_truth_core": False,
    "merge_authorization": MERGE_AUTHORIZATION,
    "lens_is_authority": False,
}

ITEM_TYPES: Final[frozenset[str]] = frozenset(
    {
        "session_note",
        "idea",
        "observation",
        "research_finding",
        "action_item",
        "open_question",
        "proposed_decision",
        "confirmed_owner_decision",
        "claim_candidate",
        "constraint",
        "lesson_learned",
        "failed_approach",
        "next_step",
    }
)


class Atlas3Error(ValueError):
    """Fail-closed Atlas 3 error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def honesty_block() -> dict[str, object]:
    return dict(HONESTY)


def safe_project_id(project_id: str) -> str:
    try:
        return safe_relative_component(project_id, label="project id")
    except ValueError as exc:
        raise Atlas3Error("UNSAFE_PROJECT_ID", str(exc)) from exc


def require_vault(vault: Path | str) -> Path:
    resolved = Path(vault).expanduser().resolve()
    if not resolved.is_dir():
        raise Atlas3Error("VAULT_NOT_FOUND", f"vault is not a directory: {resolved}")
    return resolved


def require_project(vault: Path, project_id: str) -> str:
    pid = safe_project_id(project_id)
    path = vault / "projects" / pid
    if not path.is_dir():
        raise Atlas3Error("UNKNOWN_PROJECT", f"project {pid!r} is not in the vault")
    return pid


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _redact_secret_strings(value: Any) -> Any:
    """Replace decoded secret-shaped strings after json.loads.

    AS-SEC-SCAN-ATLAS3-ANSWER-JSON-ESC-001: scan_text misses JSON ``\\u``
    escapes on raw bytes. Pulse/Start composers must not persist those
    decoded tokens into generated/ops/atlas3/**.
    """
    if isinstance(value, str):
        return "UNKNOWN" if scan_text(value) else value
    if isinstance(value, list):
        return [_redact_secret_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_secret_strings(item) for key, item in value.items()}
    return value


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    redacted = _redact_secret_strings(raw)
    return redacted if isinstance(redacted, dict) else None


def load_answer(vault: Path, answer_id: str) -> dict[str, Any] | None:
    return read_json(vault / "generated" / "answers" / f"{answer_id}.json")
