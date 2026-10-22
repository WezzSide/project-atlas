"""AT3-005 2.x → 3.x compatibility prover."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3.compat import INVARIANTS, prove_compatibility
from project_atlas.atlas3.contracts import ITEM_TYPES as AT3_ITEM_TYPES
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.conversation_capture import ITEM_TYPES as CORE_ITEM_TYPES


def _isolated_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    (vault / ".atlas").mkdir()
    return vault


def test_item_types_are_not_forked() -> None:
    assert CORE_ITEM_TYPES == AT3_ITEM_TYPES


def test_compatibility_receipt_passes_on_isolated_vault(tmp_path: Path) -> None:
    vault = _isolated_vault(tmp_path)
    (vault / ".atlas" / "vault.json").write_text('{"vault_id":"v-test"}\n', encoding="utf-8")
    receipt = prove_compatibility(vault)
    assert receipt["passed"] is True
    assert receipt["failed"] == []
    assert receipt["atlas3_writes_layer_b"] is False
    assert set(receipt["invariants"]) == set(INVARIANTS)
    assert (vault / "generated" / "ops" / "atlas3" / "compat" / "receipt.json").is_file()
    assert not (vault / "state" / "claims").exists()


def test_missing_identity_cannot_prove_no_rotation(tmp_path: Path) -> None:
    """AT3-005-F1: absent vault.json is not a silent NO_PROJECT_ID_ROTATION pass."""
    vault = _isolated_vault(tmp_path)
    receipt = prove_compatibility(vault)
    assert receipt["passed"] is False
    assert "NO_PROJECT_ID_ROTATION" in receipt["failed"]
    assert receipt["checks"]["NO_PROJECT_ID_ROTATION"] is False


def test_identity_directory_fail_closed(tmp_path: Path) -> None:
    """AT3-005-F1: a directory at the identity path must not yield a healthy receipt."""
    vault = _isolated_vault(tmp_path)
    (vault / ".atlas" / "vault.json").mkdir()
    with pytest.raises(Atlas3Error) as exc:
        prove_compatibility(vault)
    assert exc.value.code == "IDENTITY_PATH_NOT_FILE"
    assert not (vault / "generated" / "ops" / "atlas3" / "compat" / "receipt.json").exists()


def test_identity_symlink_fail_closed(tmp_path: Path) -> None:
    """AT3-005-F1: identity symlink is not proof of stable vault identity."""
    vault = _isolated_vault(tmp_path)
    target = tmp_path / "foreign-identity.json"
    target.write_text('{"vault_id":"foreign"}\n', encoding="utf-8")
    (vault / ".atlas" / "vault.json").symlink_to(target)
    with pytest.raises(Atlas3Error) as exc:
        prove_compatibility(vault)
    assert exc.value.code == "IDENTITY_PATH_NOT_FILE"


def test_identity_corrupt_json_fail_closed(tmp_path: Path) -> None:
    """AT3-005-F1: malformed identity must not be treated as no-rotation."""
    vault = _isolated_vault(tmp_path)
    (vault / ".atlas" / "vault.json").write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        prove_compatibility(vault)
    assert exc.value.code == "IDENTITY_CORRUPT"


def test_identity_non_object_fail_closed(tmp_path: Path) -> None:
    vault = _isolated_vault(tmp_path)
    (vault / ".atlas" / "vault.json").write_text('["vault"]\n', encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        prove_compatibility(vault)
    assert exc.value.code == "IDENTITY_CORRUPT"
