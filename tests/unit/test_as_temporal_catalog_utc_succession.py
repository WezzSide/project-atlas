"""AS-TEMPORAL-F1 — catalog succession uses UTC instants, not ISO string order."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.bitemporal import evaluate_as_of, normalize_validity_window
from project_atlas.bitemporal_catalog import (
    build_project_validity_windows,
    write_project_validity_catalog,
)


def _vault_with_mixed_offsets(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "state" / "claims").mkdir(parents=True)
    (vault / "sources" / "imported-documents").mkdir(parents=True)
    (vault / "state" / "claims" / "harbor-api.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "claim-early",
                        "subject": "db",
                        "field": "version",
                        "value": "15",
                        "provenance": [
                            {
                                "resource": "sources/imported-documents/early.md",
                                "source_id": "s1",
                            }
                        ],
                    },
                    {
                        "claim_id": "claim-late",
                        "subject": "db",
                        "field": "version",
                        "value": "16",
                        "provenance": [
                            {
                                "resource": "sources/imported-documents/late.md",
                                "source_id": "s2",
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    # Chronologically: early = 2024-09-15T00:00Z, late = 2024-09-15T01:00Z.
    # Lexicographic ISO order inverts them (2024-09-14T20:... < 2024-09-15T00:...).
    (vault / "sources" / "imported-documents" / "early.md").write_text(
        "timestamp: 2024-09-15T00:00:00+00:00\n",
        encoding="utf-8",
    )
    (vault / "sources" / "imported-documents" / "late.md").write_text(
        "timestamp: 2024-09-14T20:00:00-05:00\n",
        encoding="utf-8",
    )
    return vault


def test_mixed_offset_succession_is_chronological(tmp_path: Path) -> None:
    vault = _vault_with_mixed_offsets(tmp_path)
    windows = build_project_validity_windows(vault, "harbor-api")
    by_id = {window.claim_id: window for window in windows}
    assert set(by_id) == {"claim-early", "claim-late"}
    for window in windows:
        normalize_validity_window(window)
    assert by_id["claim-early"].valid_to == by_id["claim-late"].valid_from
    assert by_id["claim-late"].valid_to is None
    selected = evaluate_as_of(
        windows,
        as_of_valid_time="2024-09-15T02:00:00+00:00",
        subject="db",
        field="version",
    )
    assert selected["status"] == "selected"
    assert selected["selected_claim_id"] == "claim-late"
    catalog = write_project_validity_catalog(vault, "harbor-api")
    assert catalog is not None
    assert catalog["window_count"] == 2


def test_same_offset_control_still_selects_later_claim(tmp_path: Path) -> None:
    vault = _vault_with_mixed_offsets(tmp_path)
    (vault / "sources" / "imported-documents" / "late.md").write_text(
        "timestamp: 2024-09-15T01:00:00+00:00\n",
        encoding="utf-8",
    )
    windows = build_project_validity_windows(vault, "harbor-api")
    selected = evaluate_as_of(
        windows,
        as_of_valid_time="2024-09-15T02:00:00+00:00",
        subject="db",
        field="version",
    )
    assert selected["status"] == "selected"
    assert selected["selected_claim_id"] == "claim-late"
