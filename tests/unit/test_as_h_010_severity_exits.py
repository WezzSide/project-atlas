"""AS-H-010 — severity→exit mapping for ``atlas validate`` (H010-FR-001..007)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.domain import Severity
from project_atlas.validation import (
    VALIDATION_EXIT_ERROR,
    VALIDATION_EXIT_OK,
    validate,
    validation_exit_code,
)

REFERENCE_NOW = datetime(2026, 8, 9, tzinfo=UTC)


def _seed_vault(vault: Path) -> None:
    for relative in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")


def _write_manifest(vault: Path, sources: list[dict[str, object]]) -> None:
    path = vault / "sources" / "manifests" / "source-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "sources": sources}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_h010_fr001_error_finding_maps_to_nonzero() -> None:
    """H010-FR-001: Severity.ERROR finding → exit 1 even without legacy errors."""
    result = {
        "ok": True,
        "errors": [],
        "findings": [
            {
                "finding_id": "err.1",
                "rule_id": "synthetic-error",
                "severity": Severity.ERROR.value,
                "gate": "structural",
                "message": "synthetic error finding",
                "path": None,
                "concept_id": None,
            }
        ],
        "markdown_files": 0,
    }
    assert validation_exit_code(result) == VALIDATION_EXIT_ERROR


def test_h010_fr002_warning_only_maps_to_zero() -> None:
    """H010-FR-002: WARNING alone does not fail (explicit policy)."""
    result = {
        "ok": True,
        "errors": [],
        "findings": [
            {
                "finding_id": "warn.1",
                "rule_id": "synthetic-warning",
                "severity": Severity.WARNING.value,
                "gate": "freshness",
                "message": "synthetic warning finding",
                "path": "projects/demo/orphan.md",
                "concept_id": None,
            }
        ],
        "markdown_files": 4,
    }
    assert validation_exit_code(result) == VALIDATION_EXIT_OK


def test_h010_fr003_info_only_maps_to_zero() -> None:
    """H010-FR-003: INFO alone → exit 0."""
    result = {
        "ok": True,
        "errors": [],
        "findings": [
            {
                "finding_id": "info.1",
                "rule_id": "synthetic-info",
                "severity": Severity.INFO.value,
                "gate": "content",
                "message": "synthetic info finding",
                "path": None,
                "concept_id": None,
            }
        ],
        "markdown_files": 1,
    }
    assert validation_exit_code(result) == VALIDATION_EXIT_OK


def test_h010_fr005_deterministic_repeat() -> None:
    """H010-FR-005: identical payload → identical exit code."""
    payload = {
        "ok": True,
        "errors": [],
        "findings": [
            {
                "finding_id": "warn.a",
                "rule_id": "H-007-orphan",
                "severity": "warning",
                "gate": "structural",
                "message": "orphan note",
                "path": "projects/x/note.md",
                "concept_id": None,
            },
            {
                "finding_id": "err.a",
                "rule_id": "H-006-corrupt",
                "severity": "error",
                "gate": "freshness",
                "message": "corrupt modified_at",
                "path": "docs/a.md",
                "concept_id": None,
            },
        ],
        "markdown_files": 2,
    }
    first = validation_exit_code(payload)
    second = validation_exit_code(dict(payload))
    assert first == second == VALIDATION_EXIT_ERROR


def test_h010_fr006_legacy_errors_fail_closed() -> None:
    """H010-FR-006: non-empty errors / ok=False → exit 1 without findings."""
    assert (
        validation_exit_code({"ok": False, "errors": ["broken link"], "findings": []})
        == VALIDATION_EXIT_ERROR
    )
    assert (
        validation_exit_code({"ok": True, "errors": ["missing file"], "findings": []})
        == VALIDATION_EXIT_ERROR
    )


def test_h010_clean_result_exit_zero(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    result = validate(vault, reference_now=REFERENCE_NOW)
    assert result["ok"] is True
    assert result["findings"] == []
    assert validation_exit_code(result) == VALIDATION_EXIT_OK
    assert main(["validate", "--vault", str(vault)]) == EXIT_OK


def test_h010_warning_stale_does_not_fail_cli(tmp_path: Path) -> None:
    """Honest stale WARNING (H-006) must exit 0 under H010-FR-002."""
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-stale",
                "path": "docs/old.md",
                "likely_project": "demo",
                "modified_at": "2025-01-01T00:00:00+00:00",
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    assert any(f["severity"] == "warning" for f in result["findings"])
    assert not any(f["severity"] == "error" for f in result["findings"])
    assert validation_exit_code(result) == VALIDATION_EXIT_OK
    assert main(["validate", "--vault", str(vault)]) == EXIT_OK


def test_h010_error_corrupt_freshness_fails_cli(tmp_path: Path) -> None:
    """Corrupt freshness ERROR finding → CLI exit 1 (H010-FR-001)."""
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-bad",
                "path": "docs/bad.md",
                "likely_project": "demo",
                "modified_at": "not-a-timestamp",
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    assert any(f["severity"] == "error" for f in result["findings"])
    assert validation_exit_code(result) == VALIDATION_EXIT_ERROR
    assert main(["validate", "--vault", str(vault)]) == EXIT_ERROR


def test_h010_fr004_argparse_usage_exit_two() -> None:
    """H010-FR-004: argparse usage remains exit 2 (not remapped by H-010)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["validate"])
    assert excinfo.value.code == 2


def test_h010_adv_no_h006_h007_reimpl() -> None:
    """ADV: H-010 must not re-open H-006/H-007 validator bodies as owned rewrite."""
    source = Path(__file__).resolve().parents[2] / "src" / "project_atlas" / "validation.py"
    text = source.read_text(encoding="utf-8")
    assert "def validation_exit_code(" in text
    assert "def _validate_freshness(" in text
    assert "def _validate_orphans(" in text
    # Contract freeze: exit helper is additive; freshness/orphan still AS-VAL-001.
    assert "AS-H-010" in text
    assert "AS-VAL-001" in text


def test_h010_adv_forbidden_fail_on_warning_flag() -> None:
    """ADV: tip-safe band forbids inventing --fail-on-warning."""
    cli = Path(__file__).resolve().parents[2] / "src" / "project_atlas" / "cli.py"
    text = cli.read_text(encoding="utf-8")
    assert "fail-on-warning" not in text
    assert "fail_on_warning" not in text
