"""AS-VAL-001 (H-006 / H-007) freshness and orphan validator tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from project_atlas.validation import validate

REFERENCE_NOW = datetime(2026, 8, 9, tzinfo=UTC)


def _seed_vault(vault: Path) -> None:
    for relative in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")


def _write_manifest(vault: Path, sources: list[dict[str, object]]) -> None:
    path = vault / "sources" / "manifests" / "source-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "sources": sources}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_portfolio_stale(
    vault: Path, *, sources_by_project: dict[str, list[dict[str, object]]]
) -> None:
    path = vault / "generated" / "portfolio" / "stale-knowledge.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    projects = {
        project_id: {
            "stale_count": sum(1 for item in items if item.get("freshness") == "stale"),
            "sources": items,
        }
        for project_id, items in sorted(sources_by_project.items())
    }
    payload = {
        "schema_version": 1,
        "reference_date": REFERENCE_NOW.isoformat(),
        "stale_after_days": 180,
        "projects": projects,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_h006_stale_honestly_reported_is_warning_not_error(tmp_path: Path) -> None:
    """Stale classification emits a finding; without portfolio laundering, ok stays True."""
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-stale",
                "path": "docs/old.md",
                "likely_project": "demo",
                "modified_at": "2025-01-01T00:00:00+00:00",
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    stale = [f for f in result["findings"] if f["rule_id"] == "H-006-stale"]
    assert len(stale) == 1
    assert stale[0]["severity"] == "warning"
    assert stale[0]["gate"] == "freshness"
    assert not any(
        "laundering" in err or "missing from portfolio" in err for err in result["errors"]
    )
    assert result["ok"] is True


def test_h006_portfolio_acknowledged_stale_not_laundering(tmp_path: Path) -> None:
    """When portfolio labels match objective stale, do not emit H-006-launder."""
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-stale",
                "path": "docs/old.md",
                "likely_project": "demo",
                "modified_at": "2025-01-01T00:00:00+00:00",
            }
        ],
    )
    # Minimal on-disk labels for the laundering cross-check only. Full portfolio
    # drift is out of scope for this unit (covered by AS-MVP-001).
    labels_path = vault / "generated" / "portfolio" / "stale-knowledge.json"
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reference_date": REFERENCE_NOW.isoformat(),
                "stale_after_days": 180,
                "projects": {
                    "demo": {
                        "stale_count": 1,
                        "sources": [
                            {
                                "source_id": "src-stale",
                                "path": "docs/old.md",
                                "freshness": "stale",
                                "modified_at": "2025-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    assert not any(f["rule_id"] == "H-006-launder" for f in result["findings"])
    assert any(f["rule_id"] == "H-006-stale" for f in result["findings"])
    # Portfolio drift vs recomputed payloads is expected with a stub file; the
    # freshness gate itself must not invent a laundering error.
    assert not any("laundering" in err for err in result["errors"])


def test_h006_quarantined_stale_source_is_not_silent_error(tmp_path: Path) -> None:
    """AS-SEC-001: quarantined sources must not be demanded in stale-knowledge."""
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-secret",
                "path": "docs/canary.env",
                "likely_project": "demo",
                "modified_at": "2020-01-01T00:00:00+00:00",
            }
        ],
    )
    reports = vault / "generated" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "secret-findings.json").write_text(
        json.dumps([{"source_id": "src-secret", "rule": "aws-access-key"}], indent=2) + "\n",
        encoding="utf-8",
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    assert not any(f["rule_id"] == "H-006-silent" for f in result["findings"])
    assert not any("missing from portfolio" in err for err in result["errors"])
    assert result["ok"] is True


def test_h006_epoch_mtime_is_untrusted_warning_not_stale(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-epoch",
                "path": "docs/copied.md",
                "likely_project": "demo",
                "modified_at": "1970-01-01T00:00:00+00:00",
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    assert any(f["rule_id"] == "H-006-untrusted" for f in result["findings"])
    assert not any(f["rule_id"] == "H-006-stale" for f in result["findings"])
    assert not any(f["rule_id"] == "H-006-silent" for f in result["findings"])
    assert result["ok"] is True


def test_h006_unknown_missing_timestamp_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-unknown",
                "path": "docs/mystery.md",
                "likely_project": "demo",
                "modified_at": None,
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW)
    assert result["ok"] is False
    assert any(f["rule_id"] == "H-006-unknown" for f in result["findings"])
    assert any("freshness unknown" in err for err in result["errors"])


def test_h006_corrupt_timestamp_refuses_silent_normalization(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-corrupt",
                "path": "docs/bad.md",
                "likely_project": "demo",
                "modified_at": "not-a-real-timestamp",
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW)
    assert result["ok"] is False
    corrupt = [f for f in result["findings"] if f["rule_id"] == "H-006-corrupt"]
    assert len(corrupt) == 1
    assert "refuse silent normalization" in corrupt[0]["message"]
    assert any("refuse silent normalization" in err for err in result["errors"])


def test_h006_laundering_marked_fresh_but_stale_fails(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-launder",
                "path": "docs/old.md",
                "likely_project": "demo",
                "modified_at": "2025-01-01T00:00:00+00:00",
            }
        ],
    )
    _write_portfolio_stale(
        vault,
        sources_by_project={
            "demo": [
                {
                    "source_id": "src-launder",
                    "path": "docs/old.md",
                    "freshness": "fresh",
                    "modified_at": "2025-01-01T00:00:00+00:00",
                }
            ]
        },
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    assert result["ok"] is False
    assert any(f["rule_id"] == "H-006-launder" for f in result["findings"])
    assert any("laundering" in err for err in result["errors"])


def test_h006_findings_are_deterministically_ordered(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-b",
                "path": "b.md",
                "likely_project": "demo",
                "modified_at": None,
            },
            {
                "source_id": "src-a",
                "path": "a.md",
                "likely_project": "demo",
                "modified_at": "bogus",
            },
        ],
    )
    first = validate(vault, reference_now=REFERENCE_NOW)
    second = validate(vault, reference_now=REFERENCE_NOW)
    assert first["findings"] == second["findings"]
    ids = [item["finding_id"] for item in first["findings"]]
    assert ids == sorted(ids)


def test_h006_injected_reference_now_not_wall_clock_in_payload(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-stale",
                "path": "docs/old.md",
                "likely_project": "demo",
                "modified_at": "2020-01-01T00:00:00+00:00",
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=30)
    payload = json.dumps(result["findings"], sort_keys=True)
    assert "2026-08-09" not in payload
    assert "reference_now" not in payload
    assert any(f["rule_id"] == "H-006-stale" for f in result["findings"])


def test_h007_orphan_report_only_does_not_fail_validate(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    orphan = vault / "projects" / "lone" / "stray.md"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("# stray orphan\n", encoding="utf-8")
    result = validate(vault, reference_now=REFERENCE_NOW)
    orphans = [f for f in result["findings"] if f["rule_id"] == "H-007-orphan"]
    assert len(orphans) == 1
    assert orphans[0]["path"] == "projects/lone/stray.md"
    assert orphans[0]["severity"] == "warning"
    assert result["ok"] is True
    assert result["errors"] == []


def test_h007_reachable_project_bundle_not_false_orphan(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    project = vault / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.md").write_text("# demo\n", encoding="utf-8")
    (project / "concepts.md").write_text(
        "---\nconcept_id: c1\n---\n# concepts\n<!-- BEGIN HUMAN: notes -->\nkeep\n"
        "<!-- END HUMAN: notes -->\n",
        encoding="utf-8",
    )
    nav = vault / "generated" / "navigation" / "projects.md"
    nav.parent.mkdir(parents=True, exist_ok=True)
    nav.write_text("- [demo](../../projects/demo/project.md)\n", encoding="utf-8")
    result = validate(vault, reference_now=REFERENCE_NOW)
    orphan_paths = {f["path"] for f in result["findings"] if f["rule_id"] == "H-007-orphan"}
    assert "projects/demo/project.md" not in orphan_paths
    assert "projects/demo/concepts.md" not in orphan_paths


def test_h007_no_trust_score_fields_in_findings(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _seed_vault(vault)
    orphan = vault / "01-portfolio" / "drift-note.md"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("# drift\n", encoding="utf-8")
    result = validate(vault, reference_now=REFERENCE_NOW)
    for finding in result["findings"]:
        assert "trust" not in finding
        assert "score" not in finding
        assert set(finding) <= {
            "finding_id",
            "rule_id",
            "severity",
            "gate",
            "message",
            "path",
            "concept_id",
        }


def test_h006_future_mtime_is_untrusted_warning_not_stale(tmp_path: Path) -> None:
    """A source dated after the evaluation instant is untrusted metadata: it
    reports through the existing H-006-untrusted rule (WARNING), never as
    H-006-stale, and never as a silent omission error."""
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-future",
                "path": "docs/future.md",
                "likely_project": "demo",
                "modified_at": (REFERENCE_NOW + timedelta(days=400)).isoformat(),
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    untrusted = [f for f in result["findings"] if f["rule_id"] == "H-006-untrusted"]
    assert len(untrusted) == 1
    assert "after the evaluation instant" in untrusted[0]["message"]
    assert not any(f["rule_id"] == "H-006-stale" for f in result["findings"])
    assert not any(f["rule_id"] == "H-006-silent" for f in result["findings"])
    assert not any(f["rule_id"] == "H-006-unknown" for f in result["findings"])
    assert result["ok"] is True


def test_h006_laundering_marked_fresh_but_untrusted_future_fails(tmp_path: Path) -> None:
    """A "fresh" label surviving in the on-disk portfolio for a source now
    classified untrusted-future (e.g. the portfolio was built under a since-
    corrected future clock) is the same laundering defect as the objectively-
    stale case: the report keeps asserting freshness evidence that no longer
    holds. This must fail validation as H-006-launder (ERROR), not silently
    pass with only the H-006-untrusted WARNING."""
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-future-launder",
                "path": "docs/future.md",
                "likely_project": "demo",
                "modified_at": (REFERENCE_NOW + timedelta(days=400)).isoformat(),
            }
        ],
    )
    _write_portfolio_stale(
        vault,
        sources_by_project={
            "demo": [
                {
                    "source_id": "src-future-launder",
                    "path": "docs/future.md",
                    "freshness": "fresh",
                    "modified_at": (REFERENCE_NOW + timedelta(days=400)).isoformat(),
                }
            ]
        },
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    assert result["ok"] is False
    assert any(f["rule_id"] == "H-006-untrusted" for f in result["findings"])
    assert any(f["rule_id"] == "H-006-launder" for f in result["findings"])
    assert any("laundering" in err for err in result["errors"])


def test_h006_sub_day_future_skew_is_evaluated_normally(tmp_path: Path) -> None:
    """Clock skew smaller than the trust tolerance is not a finding."""
    vault = tmp_path / "vault"
    _seed_vault(vault)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-skewed",
                "path": "docs/skewed.md",
                "likely_project": "demo",
                "modified_at": (REFERENCE_NOW + timedelta(hours=6)).isoformat(),
            }
        ],
    )
    result = validate(vault, reference_now=REFERENCE_NOW, stale_after_days=180)
    assert not any(f["rule_id"].startswith("H-006") for f in result["findings"])
    assert result["ok"] is True
