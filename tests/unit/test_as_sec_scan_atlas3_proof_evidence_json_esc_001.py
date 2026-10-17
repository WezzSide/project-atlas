"""AS-SEC-SCAN-ATLAS3-PROOF-EVIDENCE-JSON-ESC-001 — decoded proof refs must not persist."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.atlas3.contracts import OPS_RELATIVE
from project_atlas.atlas3.proof import evaluate_proof
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_proof_evidence_ref_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    raw = f'{{"TASK": {{"evidence_ref": "{ESC}"}}}}'
    evidence = json.loads(raw)
    assert scan_text(raw) == []
    assert evidence["TASK"]["evidence_ref"] == TOKEN
    evaluate_proof(vault, "hunt-proof", project_id="harbor-api", evidence=evidence)
    written = (vault / OPS_RELATIVE / "proof" / "hunt-proof.json").read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
    payload = json.loads(written)
    assert payload["stages"]["TASK"]["status"] == "UNKNOWN"
