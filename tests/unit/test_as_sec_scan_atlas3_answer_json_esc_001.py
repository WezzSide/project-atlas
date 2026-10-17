"""AS-SEC-SCAN-ATLAS3-ANSWER-JSON-ESC-001 — decoded answer text must not persist."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.atlas3.contracts import OPS_RELATIVE
from project_atlas.atlas3.pulse import compile_pulse
from project_atlas.atlas3.start import compile_start
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_pulse_decision_summary_is_not_persisted(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    pid = "harbor-api"
    (vault / "projects" / pid).mkdir(parents=True)
    raw = (
        "{"
        '"schema_version":1,'
        '"answer_id":"ans-decisions-harbor-api",'
        '"project_id":"harbor-api",'
        f'"summary":"decision {ESC} done",'
        '"status":"derived"'
        "}"
    )
    answer = vault / "generated" / "answers" / f"ans-decisions-{pid}.json"
    answer.parent.mkdir(parents=True)
    answer.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    assert json.loads(raw)["summary"] == f"decision {TOKEN} done"
    compile_pulse(vault, pid)
    written = (vault / OPS_RELATIVE / "pulse" / f"{pid}.json").read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_start_state_summary_is_not_persisted(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    pid = "harbor-api"
    (vault / "projects" / pid).mkdir(parents=True)
    raw = (
        "{"
        '"schema_version":1,'
        '"answer_id":"ans-state-harbor-api",'
        '"project_id":"harbor-api",'
        f'"summary":"lifecycle={ESC}",'
        '"title":"state",'
        '"status":"derived"'
        "}"
    )
    answer = vault / "generated" / "answers" / f"ans-state-{pid}.json"
    answer.parent.mkdir(parents=True)
    answer.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    assert json.loads(raw)["summary"] == f"lifecycle={TOKEN}"
    compile_start(vault, pid, token_budget=500)
    written = (vault / OPS_RELATIVE / "start" / f"{pid}.json").read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
