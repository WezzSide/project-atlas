"""AT3-082 — isolated next-action honesty."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.next_honesty import PACKAGE_ID, compile_next_action_honesty


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_missing_evidence_stays_unknown(tmp_path: Path) -> None:
    report = compile_next_action_honesty(_vault(tmp_path), "harbor-api")
    assert report["package_id"] == PACKAGE_ID
    assert report["status"] == "UNKNOWN"
    assert report["reason"] == "NO_NEXT_EVIDENCE"
    assert report["next"] is None
    assert report["next_is_command"] is False
    assert report["auto_execute"] is False
    assert report["new_cli_command"] is False
    assert report["graph_is_authority"] is False
    assert report["stale_as_current"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"
    assert report["write_applied"] is False
    assert report["certified_for_merge"] is False


def test_unknown_project_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "UNKNOWN_PROJECT"


def test_composes_pulse_next_without_command(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json",
        {
            "questions": {
                "what_should_i_look_at_next": {
                    "status": "derived",
                    "items": [{"value": "review the datastore conflict", "status": "derived"}],
                }
            }
        },
    )
    report = compile_next_action_honesty(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["next"] == "review the datastore conflict"
    assert report["next_is_command"] is False
    assert report["sources"]["pulse_artifact_present"] is True


def test_composes_next_lens_without_writing(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "answers" / "ans-next-harbor-api.json",
        {"status": "derived", "value": "open the unknown queue"},
    )
    report = compile_next_action_honesty(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["next"] == "open the unknown queue"
    assert report["sources"]["next_lens_present"] is True
    assert report["write_applied"] is False


def test_pulse_unknown_next_stays_unknown(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json",
        {
            "questions": {
                "what_should_i_look_at_next": {
                    "status": "UNKNOWN",
                    "reason": "next lens not materialized",
                    "items": [],
                }
            }
        },
    )
    report = compile_next_action_honesty(vault, "harbor-api")
    assert report["status"] == "UNKNOWN"
    assert report["next"] is None


def test_command_claim_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json",
        {
            "questions": {
                "what_should_i_look_at_next": {
                    "status": "derived",
                    "command": "atlas merge --yes",
                    "value": "merge now",
                }
            }
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "NEXT_IS_COMMAND"


def test_auto_execute_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "answers" / "ans-next-harbor-api.json",
        {"status": "derived", "value": "run tests", "auto_execute": True},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "NEXT_IS_COMMAND"


def test_stale_as_current_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "answers" / "ans-next-harbor-api.json",
        {"status": "CURRENT", "freshness": "STALE", "value": "old next"},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "STALE_AS_CURRENT"


def test_unverified_as_current_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "answers" / "ans-next-harbor-api.json",
        {"status": "verified", "unverified": True, "value": "guess"},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "UNVERIFIED_AS_CURRENT"


def test_graph_winner_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json",
        {"graph_winner": "chatgpt", "questions": {}},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "GRAPH_WINNER_FORBIDDEN"


def test_merge_grant_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "answers" / "ans-next-harbor-api.json",
        {"value": "ship it", "merge_authorization": "GRANTED"},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "MERGE_CLAIM_FORBIDDEN"


def test_corrupt_pulse_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "PULSE_CORRUPT"


def test_foreign_pulse_project_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json",
        {
            "project_id": "other-api",
            "questions": {
                "what_should_i_look_at_next": {
                    "status": "derived",
                    "value": "review the foreign datastore",
                }
            },
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "PROJECT_MISMATCH"


def test_foreign_next_lens_project_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "answers" / "ans-next-harbor-api.json",
        {
            "status": "derived",
            "value": "open the foreign unknown queue",
            "project_id": "other-api",
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_next_action_honesty(vault, "harbor-api")
    assert exc.value.code == "PROJECT_MISMATCH"


def test_same_project_pulse_label_still_composes(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_json(
        vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json",
        {
            "project_id": "harbor-api",
            "questions": {
                "what_should_i_look_at_next": {
                    "status": "derived",
                    "value": "review the datastore conflict",
                }
            },
        },
    )
    report = compile_next_action_honesty(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["next"] == "review the datastore conflict"
    assert report["write_applied"] is False


def test_does_not_invoke_pulse_writer(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    compile_next_action_honesty(vault, "harbor-api")
    pulse_path = vault / "generated" / "ops" / "atlas3" / "pulse" / "harbor-api.json"
    assert not pulse_path.exists()


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/next_honesty.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "from project_atlas.atlas3.pulse",
        "compile_pulse(",
        "chatgpt_bridge",
        "from project_atlas.ingestion",
        "add_parser",
        "atlas.query.read",
    ):
        assert name not in source
