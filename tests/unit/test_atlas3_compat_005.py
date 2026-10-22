"""AT3-005 2.x → 3.x compatibility prover."""

from __future__ import annotations

from pathlib import Path

from project_atlas.atlas3.compat import INVARIANTS, prove_compatibility
from project_atlas.atlas3.contracts import ITEM_TYPES as AT3_ITEM_TYPES
from project_atlas.conversation_capture import ITEM_TYPES as CORE_ITEM_TYPES


def test_item_types_are_not_forked() -> None:
    assert CORE_ITEM_TYPES == AT3_ITEM_TYPES


def test_compatibility_receipt_passes_on_isolated_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    (vault / ".atlas").mkdir()
    (vault / ".atlas" / "vault.json").write_text('{"vault_id":"v-test"}\n', encoding="utf-8")
    receipt = prove_compatibility(vault)
    assert receipt["passed"] is True
    assert receipt["failed"] == []
    assert receipt["atlas3_writes_layer_b"] is False
    assert set(receipt["invariants"]) == set(INVARIANTS)
    assert (vault / "generated" / "ops" / "atlas3" / "compat" / "receipt.json").is_file()
    assert not (vault / "state" / "claims").exists()


def test_compatibility_fails_when_vault_identity_missing(tmp_path: Path) -> None:
    """AT3-005-F2: missing .atlas/vault.json is not NO_PROJECT_ID_ROTATION."""
    vault = tmp_path / "vault"
    vault.mkdir()
    receipt = prove_compatibility(vault)
    assert receipt["passed"] is False
    assert "NO_PROJECT_ID_ROTATION" in receipt["failed"]
    assert receipt["checks"]["NO_PROJECT_ID_ROTATION"] is False
