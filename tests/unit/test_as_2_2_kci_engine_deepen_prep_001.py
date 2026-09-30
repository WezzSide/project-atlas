"""AS-2.2-KCI-ENGINE-DEEPEN-PREP-001 - docs/contracts/fixtures only."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "kci-engine"
CONTRACTS = PREP / "contracts"
FIXTURES = PREP / "fixtures"
BASE = ROOT / "docs" / "atlas-2.2" / "AS-2.2-KCI-ENGINE-PREP-001.md"


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_kci_engine_deepen_docs_present() -> None:
    for name in (
        "AS-2.2-KCI-ENGINE-DEEPEN-PREP-001.md",
        "INVARIANTS.md",
        "DEEPEN-FIXTURE-PLAN.md",
        "CONTRACT.md",
        "adr/ADR-2.2-KCI-ENGINE-002-deepen-prep.md",
    ):
        path = PREP / name
        assert path.is_file(), name
        assert "PREP" in path.read_text(encoding="utf-8").upper()
    package = (PREP / "AS-2.2-KCI-ENGINE-DEEPEN-PREP-001.md").read_text(encoding="utf-8")
    invariants = (PREP / "INVARIANTS.md").read_text(encoding="utf-8")
    for text in (package, invariants):
        assert "ATLAS_2_1_RELEASE_CERTIFIED" in text or "RELEASE" in text.upper()


def test_kci_engine_deepen_extends_base() -> None:
    assert BASE.is_file()
    package = (PREP / "AS-2.2-KCI-ENGINE-DEEPEN-PREP-001.md").read_text(encoding="utf-8")
    assert "AS-2.2-KCI-ENGINE-PREP-001" in package
    assert "dual-own" in package.lower() or "dual-owning" in package.lower()


def test_kci_forbidden_negatives() -> None:
    schema = _load(CONTRACTS / "kci-forbidden-action.schema.json")
    assert "PREP STUB" in json.dumps(schema)
    enum = schema["properties"]["action"]["enum"]  # type: ignore[index]
    for action in enum:
        payload = _load(FIXTURES / f"negative-{action}.expect.json")
        jsonschema.validate(payload, schema)  # type: ignore[arg-type]
        assert payload["expect"] == "reject"
        assert payload["pilot_pass"] is False
        assert payload["release_certified"] is False
        assert payload["canonical_writes"] is False
