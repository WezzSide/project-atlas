"""AS-2.0-TEMPORAL-001 bitemporal validity window tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.bitemporal import (
    BitemporalError,
    ClaimValidityWindow,
    evaluate_as_of,
    normalize_validity_window,
    write_validity_catalog,
)
from project_atlas.schema import available_schemas, validate_record

ROOT = Path(__file__).resolve().parents[2]


def _window(
    claim_id: str,
    valid_from: str,
    *,
    valid_to: str | None = None,
    evidence_kind: str = "document-declared",
) -> ClaimValidityWindow:
    return ClaimValidityWindow(
        claim_id=claim_id,
        valid_from=valid_from,
        valid_to=valid_to,
        knowledge_compilation_id="compile-1",
        evidence_kind=evidence_kind,  # type: ignore[arg-type]
        core005_temporal_status="current",
    )


def test_normalize_and_catalog(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    window = _window("claim.a", "2024-01-01", valid_to="2024-06-01")
    payload = normalize_validity_window(window)
    validate_record(payload, "claim-validity-window")
    catalog = write_validity_catalog(vault, [window], catalog_id="pilot")
    assert catalog["window_count"] == 1
    assert catalog["compat_snapshot_id"] == "atlas-1.0.0-compat"
    validate_record(catalog, "claim-validity-catalog")
    assert (vault / "generated" / "ops" / "bitemporal" / "pilot-validity-catalog.json").is_file()


def test_as_of_selects_single_window() -> None:
    result = evaluate_as_of(
        [_window("claim.a", "2024-01-01", valid_to="2024-12-31")],
        as_of_valid_time="2024-06-15",
        subject="proj.status",
        field="package_status",
    )
    assert result["status"] == "selected"
    assert result["selected_claim_id"] == "claim.a"
    validate_record(result, "bitemporal-as-of-result")


def test_as_of_overlap_fail_closed() -> None:
    result = evaluate_as_of(
        [
            _window("claim.a", "2024-01-01", valid_to="2024-12-31"),
            _window("claim.b", "2024-03-01", valid_to="2024-09-01"),
        ],
        as_of_valid_time="2024-06-01",
        subject="proj.status",
        field="package_status",
    )
    assert result["status"] == "unresolved_overlap"
    assert result["selected_claim_id"] is None
    assert result["candidate_claim_ids"] == ["claim.a", "claim.b"]


def test_as_of_unknown_evidence_fail_closed() -> None:
    result = evaluate_as_of(
        [_window("claim.a", "2024-01-01", evidence_kind="unknown")],
        as_of_valid_time="2024-06-01",
        subject="proj.status",
        field="package_status",
    )
    assert result["status"] == "unresolved_incomplete"


def test_rejects_wall_clock_and_inverted() -> None:
    with pytest.raises(BitemporalError, match="wall-clock"):
        normalize_validity_window(_window("claim.a", "now"))
    with pytest.raises(BitemporalError, match="inverted"):
        normalize_validity_window(_window("claim.a", "2024-06-01", valid_to="2024-01-01"))


def test_temporal_docs_and_schemas() -> None:
    for kind in (
        "claim-validity-window",
        "claim-validity-catalog",
        "bitemporal-as-of-result",
    ):
        assert kind in available_schemas()
    assert (ROOT / "docs" / "AS-2.0-TEMPORAL-001.md").is_file()
