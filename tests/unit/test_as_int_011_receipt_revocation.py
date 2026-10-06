"""AS-INT-011 receipt revocation / invalidation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.receipt_revocation import (
    RevocationError,
    assert_receipt_active,
    empty_index,
    inventory_with_revocations,
    is_receipt_revoked,
    list_revocations,
    receipt_trust_disposition,
    revoke_receipt,
)
from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text


def _write_receipt(vault: Path, project: str, event: str) -> Path:
    path = vault / "receipts" / "agent-events" / project / f"{event}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"receipt_id: {event}\nstatus: valid\nevent_id: {event}\n",
        encoding="utf-8",
    )
    return path


def test_as_int_011_operator_revoke_preserves_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    receipt = _write_receipt(vault, "proj-a", "AE-001")
    concept = vault / "projects" / "proj-a" / "concepts" / "note.md"
    concept.parent.mkdir(parents=True)
    concept.write_text("# keep\n", encoding="utf-8")

    index = revoke_receipt(
        vault, project_id="proj-a", event_id="AE-001", reason="operator"
    )
    validate_record(index, "receipt-revocation-index")
    assert index["revocations"][0]["status"] == "revoked"
    assert index["revocations"][0]["reason"] == "operator"
    assert receipt.is_file()
    assert concept.is_file()
    assert "at" not in index["generated"]
    path = vault / "generated" / "ops" / "receipt-revocations.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == index


def test_as_int_011_integrity_defaults_to_invalidated(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    index = revoke_receipt(
        vault,
        project_id="proj-b",
        event_id="AE-x",
        reason="integrity",
        detail="hash-mismatch-followup",
    )
    entry = index["revocations"][0]
    assert entry["status"] == "invalidated"
    assert entry["detail"] == "hash-mismatch-followup"
    assert receipt_trust_disposition(vault, project_id="proj-b", event_id="AE-x") == (
        "invalidated"
    )


def test_as_int_011_skill_policy_revoke(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    revoke_receipt(
        vault, project_id="proj-a", event_id="AE-002", reason="skill_policy"
    )
    assert is_receipt_revoked(vault, project_id="proj-a", event_id="AE-002")
    assert receipt_trust_disposition(vault, project_id="proj-a", event_id="AE-002") == (
        "revoked"
    )


def test_as_int_011_assert_active_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    revoke_receipt(vault, project_id="proj-a", event_id="AE-001")
    with pytest.raises(RevocationError, match="revoked"):
        assert_receipt_active(vault, project_id="proj-a", event_id="AE-001")
    assert_receipt_active(vault, project_id="proj-a", event_id="AE-alive")


def test_as_int_011_refuses_non_receipt_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(RevocationError, match="outside agent-event receipts"):
        revoke_receipt(
            vault,
            project_id="proj-a",
            event_id="AE-001",
            receipt_path="projects/proj-a/note.md",
        )


def test_as_int_011_inventory_keeps_revoked_visible(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_receipt(vault, "proj-a", "AE-001")
    _write_receipt(vault, "proj-a", "AE-002")
    revoke_receipt(vault, project_id="proj-a", event_id="AE-001", reason="operator")
    inventory = inventory_with_revocations(vault)
    by_key = {row["unit_key"]: row for row in inventory}
    assert by_key["proj-a/AE-001"]["disposition"] == "revoked"
    assert by_key["proj-a/AE-002"]["disposition"] == "active"


def test_as_int_011_deterministic_merge(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    revoke_receipt(vault, project_id="proj-a", event_id="AE-002")
    revoke_receipt(vault, project_id="proj-a", event_id="AE-001")
    first = (vault / "generated" / "ops" / "receipt-revocations.json").read_bytes()
    revoke_receipt(vault, project_id="proj-a", event_id="AE-001")
    second = (vault / "generated" / "ops" / "receipt-revocations.json").read_bytes()
    assert first == second
    keys = [r["unit_key"] for r in list_revocations(vault)]
    assert keys == sorted(keys)


def test_as_int_011_does_not_touch_tombstone_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    revoke_receipt(vault, project_id="proj-a", event_id="AE-001")
    assert not (vault / "generated" / "ops" / "event-tombstones.json").exists()
    assert (vault / "generated" / "ops" / "receipt-revocations.json").is_file()


def test_json_unicode_escape_event_id_is_not_rewritten(tmp_path: Path) -> None:
    """AS-SEC-SCAN-REVOCATION-JSON-ESC-001: decoded event_id must not persist."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    vault = tmp_path / "vault"
    vault.mkdir()
    index = empty_index()
    index["revocations"] = [
        {
            "unit_key": f"harbor-api/{token}",
            "project_id": "harbor-api",
            "event_id": token,
            "receipt_path": f"receipts/agent-events/harbor-api/{token}.yaml",
            "reason": "operator",
            "status": "revoked",
        }
    ]
    raw = json.dumps(index).replace(token, "\\u0041KIAAAAAAAAAAAAAAAAA")
    written = vault / "generated" / "ops" / "receipt-revocations.json"
    written.parent.mkdir(parents=True)
    written.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    revoke_receipt(vault, project_id="harbor-api", event_id="evt-other")
    text = written.read_text(encoding="utf-8")
    assert token not in text
    assert scan_text(text) == []
