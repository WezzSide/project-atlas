"""Additive Atlas 3 aggregate diagnostics for ``atlas validate``.

Observational only: reuses the canonical ``validate()`` result and
``validation_exit_code()`` mapping verbatim -- it does not reimplement
validation semantics, does not re-run the check pass a second time, and does
not persist anything (no canonical vault write, no Atlas 3 ops-store write
either). The complete existing ``atlas validate`` command is untouched.

Precise wording, not overclaimed: this command is *additively registered
through Atlas 3* (the CLI surface) and *reuses the canonical validation
result* -- it is not "entirely Atlas 3" in the sense of an independent
Atlas 3 subsystem, and "complete payload" means the complete current
``validate()`` result contract (``ok`` / ``errors`` / ``findings`` /
``markdown_files``), not a new, independently versioned public schema.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

from project_atlas.atlas3.contracts import MERGE_AUTHORIZATION, honesty_block
from project_atlas.validation import validate, validation_exit_code

PACKAGE_ID: Final[str] = "AT3-DOGFOOD-004-SUCCESSOR"
SCHEMA: Final[str] = "atlas3.validate-report.v1"

_log = logging.getLogger("project_atlas.atlas3.validate_report")


def compile_validate_report(vault: Path | str) -> dict[str, Any]:
    """Aggregate projection of the canonical ``validate()`` result.

    Calls ``validate()`` exactly once and derives the exit code from that
    same, unmodified result via ``validation_exit_code()`` -- the sole
    canonical exit-mapping authority; this function never assigns an exit
    code itself. Read-only: never writes canonical vault state or any
    Atlas 3 ops-store file.
    """
    resolved = Path(vault)
    try:
        result = validate(resolved)
    except (OSError, ValueError) as exc:
        # Mirrors the existing `atlas validate` command's own failure
        # boundary (cli.py's `except (OSError, ValueError)` around the same
        # call) -- a missing/malformed vault is a well-defined validation
        # failure, not an uncaught traceback, and it still flows through
        # validation_exit_code() rather than a hardcoded exit code.
        _log.error("validate-report: validate() failed: %s", exc)
        result = {
            "ok": False,
            "errors": [f"validate failed: {exc}"],
            "findings": [],
            "markdown_files": 0,
        }
    exit_code = validation_exit_code(result)
    if exit_code != 0:
        for error in result.get("errors") or []:
            _log.error("validate-report: %s", error)
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "package": PACKAGE_ID,
        "ok": result["ok"],
        "errors": list(result["errors"]),
        "findings": list(result["findings"]),
        "markdown_files": result["markdown_files"],
        "exit_code": exit_code,
        "observational": True,
        "merge_authorization": MERGE_AUTHORIZATION,
        "honesty": honesty_block(),
    }
