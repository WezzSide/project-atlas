"""AT3-015 Atlas Pulse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.ledger import append_event
from project_atlas.atlas3.pulse import compile_pulse


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def test_pulse_unknown_when_lenses_missing(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = compile_pulse(vault, "harbor-api")
    assert report["package"] == "AT3-015"
    assert "what_changed" in report["questions"]
    assert report["questions"]["what_changed"]["status"] == "UNKNOWN"
    assert report["honesty"]["lens_is_authority"] is False


def test_pulse_composes_changed_and_failures(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    answers = vault / "generated" / "answers"
    answers.mkdir(parents=True)
    (answers / "ans-changed-harbor-api.json").write_text(
        json.dumps({"status": "derived", "summary": "compose.yml changed"}, sort_keys=True),
        encoding="utf-8",
    )
    append_event(
        vault,
        "harbor-api",
        kind="failure",
        source_plane="engineering",
        summary="deploy failed",
    )
    report = compile_pulse(vault, "harbor-api")
    assert report["questions"]["what_changed"]["status"] == "derived"
    assert report["questions"]["what_failed"]["status"] == "derived"
    assert report["questions"]["what_failed"]["items"]
    assert report["questions"]["what_requires_attention"]["status"] == "derived"
    assert report["questions"]["what_requires_attention"]["items"]


def test_foreign_answer_project_id_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    answers = vault / "generated" / "answers"
    answers.mkdir(parents=True)
    (answers / "ans-changed-harbor-api.json").write_text(
        json.dumps(
            {
                "status": "derived",
                "summary": "FOREIGN-ANSWER",
                "project_id": "other-api",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_pulse(vault, "harbor-api")
    assert exc.value.code == "PROJECT_MISMATCH"
    assert not (vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json").exists()


def test_unlabeled_answer_still_composes(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    answers = vault / "generated" / "answers"
    answers.mkdir(parents=True)
    (answers / "ans-changed-harbor-api.json").write_text(
        json.dumps({"status": "derived", "summary": "compose.yml changed"}, sort_keys=True),
        encoding="utf-8",
    )
    report = compile_pulse(vault, "harbor-api")
    assert report["questions"]["what_changed"]["status"] == "derived"
    assert report["project_id"] == "harbor-api"


def test_pulse_stale_is_not_changed_and_attention_unknown(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = compile_pulse(vault, "harbor-api")
    assert report["questions"]["what_became_stale"]["status"] == "UNKNOWN"
    assert report["questions"]["what_changed"]["status"] == "UNKNOWN"
    assert report["questions"]["what_requires_attention"]["status"] == "UNKNOWN"
