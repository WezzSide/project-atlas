"""AS-2.2-KDIFF-001 — Knowledge Diff / Time Machine P0 (read-only) tests.

Covers: as-of read (known/unknown/overlap), each diff change kind
(added/removed/value/authority/freshness/conflict), the empty (no-change) diff,
cross-project isolation, missing/partial temporal data → honest unknown,
determinism (byte-identical), project-scope-required fail-closed, schema
registration/validity, and the no-canonical-write invariant.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.knowledge_diff import (
    DEFAULT_SUBJECT_CAP,
    KnowledgeDiffError,
    diff_knowledge,
    diff_to_json,
    read_as_of,
    snapshot_to_json,
)
from project_atlas.schema import available_schemas, validate_record

T1 = "2024-01-01"
T2 = "2024-06-01"
BOUNDARY = "2024-03-01"


# ---------------------------------------------------------------------------
# Fixture helpers (write persisted Core state only; never a kdiff output)
# ---------------------------------------------------------------------------


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _claim(cid: str, subject: str, field: str, value: str, source_id: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "claim_id": cid,
        "subject": subject,
        "field": field,
        "value": value,
        "provenance": [
            {
                "schema_version": 1,
                "source_id": source_id,
                "resource": f"sources/{source_id}.md",
            }
        ],
    }


def _window(
    cid: str, valid_from: str, valid_to: str | None = None, *, cid_kc: str = "kc-1"
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "claim_id": cid,
        "valid_from": valid_from,
        "evidence_kind": "document-declared",
        "knowledge_compilation_id": cid_kc,
    }
    if valid_to is not None:
        entry["valid_to"] = valid_to
    return entry


def _auth(
    subject: str,
    field: str,
    disposition: str,
    *,
    authoritative_claim_id: str | None = None,
    competing: tuple[str, ...] = (),
    subordinate: tuple[str, ...] = (),
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "subject": subject,
        "field": field,
        "disposition": disposition,
        "competing_claim_ids": list(competing),
        "subordinate_claim_ids": list(subordinate),
    }
    if authoritative_claim_id is not None:
        record["authoritative_claim_id"] = authoritative_claim_id
    return record


def _build_matrix_vault(root: Path) -> Path:
    """Build a project exercising every diff change kind between T1 and T2."""
    vault = root / "vault"
    claims = [
        # added: only covers at T2
        _claim("cadd", "s-add", "status", "added-value", "src1"),
        # removed: only covers at T1
        _claim("crem", "s-rem", "status", "removed-value", "src1"),
        # value_changed (no authority record → role stable/unknown)
        _claim("cval1", "s-val", "status", "alpha", "src1"),
        _claim("cval2", "s-val", "status", "beta", "src1"),
        # authority_changed: authoritative at T1, competing at T2
        _claim("cauth1", "s-auth", "title", "Same Title", "src1"),
        _claim("cauth2", "s-auth", "title", "Same Title", "src1"),
        # freshness_changed: fresh source at T1, stale source at T2
        _claim("cfresh1", "s-fresh", "status", "f-val", "src-fresh"),
        _claim("cfresh2", "s-fresh", "status", "f-val2", "src-stale"),
        # conflict_changed: no conflict at T1, unresolved conflict at T2
        _claim("cconf1", "s-conf", "status", "c-val", "src1"),
        _claim("cconf2", "s-conf", "status", "c-val2", "src1"),
        # stable: identical at both references
        _claim("cstable", "s-stable", "title", "Stable", "src1"),
    ]
    _write_json(
        vault / "state" / "claims" / "proj-a.json",
        {"compilation_id": "kc-1", "claims": claims},
    )
    windows = [
        _window("cadd", BOUNDARY),
        _window("crem", "2023-01-01", BOUNDARY),
        _window("cval1", "2023-01-01", BOUNDARY),
        _window("cval2", BOUNDARY),
        _window("cauth1", "2023-01-01", BOUNDARY),
        _window("cauth2", BOUNDARY),
        _window("cfresh1", "2023-01-01", BOUNDARY),
        _window("cfresh2", BOUNDARY),
        _window("cconf1", "2023-01-01", BOUNDARY),
        _window("cconf2", BOUNDARY),
        _window("cstable", "2023-01-01"),
    ]
    _write_json(
        vault / "generated" / "ops" / "bitemporal" / "kc-1-validity-catalog.json",
        {"windows": windows},
    )
    _write_json(
        vault / "state" / "authoritative-state" / "proj-a.json",
        {
            "authoritative_states": [
                _auth(
                    "s-auth",
                    "title",
                    "authoritative",
                    authoritative_claim_id="cauth1",
                    competing=("cauth2",),
                ),
            ]
        },
    )
    _write_json(
        vault / "generated" / "portfolio" / "stale-knowledge.json",
        {
            "sources": [
                {"source_id": "src-fresh", "freshness": "fresh"},
                {"source_id": "src-stale", "freshness": "stale"},
            ]
        },
    )
    _write_json(
        vault / "review" / "conflicts" / "proj-a.json",
        {
            "entries": [
                {
                    "state": "unresolved",
                    "conflict_id": "conf-1",
                    "claim_ids": ["cconf2", "cother"],
                    "subject": "s-conf",
                    "field": "status",
                }
            ]
        },
    )
    return vault


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _bucket(report: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [row for row in report[name] if row["subject"] != "s-stable"]


def _keys(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(row["subject"], row["field"]) for row in rows}


# ---------------------------------------------------------------------------
# Schema registration / validity
# ---------------------------------------------------------------------------


def test_schemas_registered() -> None:
    assert "kdiff-record" in available_schemas()
    assert "kdiff-as-of-snapshot" in available_schemas()


def test_as_of_and_diff_records_validate(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    snapshot = read_as_of(vault, project_id="proj-a", as_of_valid_time=T1)
    validate_record(snapshot, "kdiff-as-of-snapshot")
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    validate_record(report, "kdiff-record")


# ---------------------------------------------------------------------------
# As-of read (known / unknown / overlap)
# ---------------------------------------------------------------------------


def test_as_of_known_selection(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    snapshot = read_as_of(vault, project_id="proj-a", as_of_valid_time=T1)
    cells = {(c["subject"], c["field"]): c for c in snapshot["cells"]}
    val = cells[("s-val", "status")]
    assert val["disposition"] == "selected"
    assert val["selected_claim_id"] == "cval1"
    assert val["value_sketch"] == "alpha"
    # s-auth title is authoritative at T1
    assert cells[("s-auth", "title")]["authority_role"] == "authoritative"
    assert snapshot["authority"]["level"] == "derived"
    assert snapshot["authority"]["llm_authority"] is False
    assert "WALL-CLOCK NOW" in snapshot["truth_boundary"]


def test_as_of_missing_temporal_data_is_honest_unknown(tmp_path: Path) -> None:
    """Authority projection with no validity window → unknown, never invented."""
    vault = tmp_path / "vault"
    _write_json(
        vault / "state" / "claims" / "proj-a.json",
        {"claims": [_claim("c1", "s-x", "status", "v", "src1")]},
    )
    # No validity catalog at all, but an authoritative projection exists.
    _write_json(
        vault / "state" / "authoritative-state" / "proj-a.json",
        {
            "authoritative_states": [
                _auth("s-x", "status", "authoritative", authoritative_claim_id="c1")
            ]
        },
    )
    snapshot = read_as_of(vault, project_id="proj-a", as_of_valid_time=T1)
    cell = snapshot["cells"][0]
    assert cell["disposition"] == "unknown"
    assert cell["reason"] == "temporal-data-missing"
    assert cell["selected_claim_id"] is None
    assert cell["authority_role"] == "unknown"
    assert {"subject": "s-x", "field": "status", "reason": "temporal-data-missing"} in snapshot[
        "unresolved"
    ]
    assert snapshot["status"] == "partial"
    validate_record(snapshot, "kdiff-as-of-snapshot")


def test_as_of_overlap_is_unresolved_not_silent_winner(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_json(
        vault / "state" / "claims" / "proj-a.json",
        {
            "claims": [
                _claim("co1", "s-ov", "status", "one", "src1"),
                _claim("co2", "s-ov", "status", "two", "src1"),
            ]
        },
    )
    _write_json(
        vault / "generated" / "ops" / "bitemporal" / "kc-1-validity-catalog.json",
        {"windows": [_window("co1", "2023-01-01"), _window("co2", "2023-01-01")]},
    )
    snapshot = read_as_of(vault, project_id="proj-a", as_of_valid_time=T1)
    cell = snapshot["cells"][0]
    assert cell["disposition"] == "unresolved"
    assert cell["reason"] == "unresolved_overlap"
    assert cell["selected_claim_id"] is None
    assert set(cell["candidate_claim_ids"]) == {"co1", "co2"}


def test_as_of_wall_clock_rejected(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    snapshot = read_as_of(vault, project_id="proj-a", as_of_valid_time="now")
    assert snapshot["status"] == "rejected_malformed"
    assert snapshot["cells"] == []
    validate_record(snapshot, "kdiff-as-of-snapshot")


# ---------------------------------------------------------------------------
# T1 -> T2 diff — each change kind
# ---------------------------------------------------------------------------


def test_diff_added(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    added = _keys(report["added"])
    assert ("s-add", "status") in added
    row = next(r for r in report["added"] if r["subject"] == "s-add")
    assert row["to_claim_id"] == "cadd"
    assert row["value_sketch"] == "added-value"


def test_diff_removed(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    assert ("s-rem", "status") in _keys(report["removed"])
    row = next(r for r in report["removed"] if r["subject"] == "s-rem")
    assert row["from_claim_id"] == "crem"


def test_diff_value_changed(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    row = next(r for r in report["value_changed"] if r["subject"] == "s-val")
    assert row["from_claim_id"] == "cval1"
    assert row["to_claim_id"] == "cval2"
    assert row["from_value_sketch"] == "alpha"
    assert row["to_value_sketch"] == "beta"


def test_diff_authority_changed(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    row = next(r for r in report["authority_changed"] if r["subject"] == "s-auth")
    assert row["from_role"] == "authoritative"
    assert row["to_role"] == "competing"
    assert row["from_disposition"] == "authoritative"
    assert row["to_disposition"] == "authoritative"


def test_diff_freshness_changed(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    row = next(r for r in report["freshness_changed"] if r["subject"] == "s-fresh")
    assert row["from_freshness"] == "fresh"
    assert row["to_freshness"] == "stale"


def test_diff_conflict_changed(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    row = next(r for r in report["conflict_changed"] if r["subject"] == "s-conf")
    assert row["from_state"] == "none"
    assert row["to_state"] == "unresolved"
    assert "conf-1" in row["conflict_ids"]


def test_diff_stable_subject_never_reported(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    for name in (
        "added",
        "removed",
        "value_changed",
        "authority_changed",
        "freshness_changed",
        "conflict_changed",
        "unresolved_delta",
    ):
        assert all(row["subject"] != "s-stable" for row in report[name])


# ---------------------------------------------------------------------------
# Empty / no-change diff
# ---------------------------------------------------------------------------


def test_diff_no_change_is_empty(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_json(
        vault / "state" / "claims" / "proj-a.json",
        {"claims": [_claim("cs", "s-stable", "title", "Stable", "src1")]},
    )
    _write_json(
        vault / "generated" / "ops" / "bitemporal" / "kc-1-validity-catalog.json",
        {"windows": [_window("cs", "2023-01-01")]},
    )
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    assert report["status"] == "ok"
    assert report["change_count"] == 0
    for name in (
        "added",
        "removed",
        "value_changed",
        "authority_changed",
        "freshness_changed",
        "conflict_changed",
        "unresolved_delta",
    ):
        assert report[name] == []


def test_diff_unknown_side_becomes_unresolved_delta(tmp_path: Path) -> None:
    """If either reference is temporally unknown, never invent add/remove/change."""
    vault = tmp_path / "vault"
    _write_json(
        vault / "state" / "claims" / "proj-a.json",
        {"claims": [_claim("c1", "s-x", "status", "v", "src1")]},
    )
    _write_json(
        vault / "state" / "authoritative-state" / "proj-a.json",
        {
            "authoritative_states": [
                _auth("s-x", "status", "authoritative", authoritative_claim_id="c1")
            ]
        },
    )
    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    assert report["status"] == "partial"
    assert report["change_count"] == 0
    assert {"subject": "s-x", "field": "status", "reason": "temporal-data-missing"} in report[
        "unresolved_delta"
    ]


# ---------------------------------------------------------------------------
# Cross-project isolation
# ---------------------------------------------------------------------------


def test_cross_project_isolation(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    # Second project with its own claims + windows in a SHARED catalog file.
    _write_json(
        vault / "state" / "claims" / "proj-b.json",
        {"claims": [_claim("cb1", "s-leak", "status", "secret-b", "srcb")]},
    )
    catalog = vault / "generated" / "ops" / "bitemporal" / "kc-1-validity-catalog.json"
    data = json.loads(catalog.read_text(encoding="utf-8"))
    data["windows"].append(_window("cb1", "2023-01-01"))
    _write_json(catalog, data)

    snapshot = read_as_of(vault, project_id="proj-a", as_of_valid_time=T1)
    subjects = {c["subject"] for c in snapshot["cells"]}
    assert "s-leak" not in subjects

    report = diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    for name in ("added", "removed", "value_changed"):
        assert all(row["subject"] != "s-leak" for row in report[name])

    # And the b-scoped read only sees its own subject.
    snap_b = read_as_of(vault, project_id="proj-b", as_of_valid_time=T1)
    assert {c["subject"] for c in snap_b["cells"]} == {"s-leak"}


# ---------------------------------------------------------------------------
# Determinism / read-only / scope
# ---------------------------------------------------------------------------


def test_determinism_byte_identical(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    a = snapshot_to_json(read_as_of(vault, project_id="proj-a", as_of_valid_time=T1))
    b = snapshot_to_json(read_as_of(vault, project_id="proj-a", as_of_valid_time=T1))
    assert a == b
    c = diff_to_json(diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2))
    d = diff_to_json(diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2))
    assert c == d
    # No wall-clock timestamps leak into output.
    assert "generated" in json.loads(a)
    assert json.loads(a)["generated"] == {"by": "project-atlas"}


def test_no_canonical_write(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    before = _hash_tree(vault)
    read_as_of(vault, project_id="proj-a", as_of_valid_time=T1)
    diff_knowledge(vault, project_id="proj-a", t1=T1, t2=T2)
    read_as_of(vault, project_id="proj-a", as_of_valid_time="now")
    after = _hash_tree(vault)
    assert before == after


def test_project_scope_required(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    with pytest.raises(KnowledgeDiffError, match="project-scope-required"):
        read_as_of(vault, project_id="  ", as_of_valid_time=T1)
    with pytest.raises(KnowledgeDiffError, match="project-scope-required"):
        diff_knowledge(vault, project_id="", t1=T1, t2=T2)


def test_missing_claims_state_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(KnowledgeDiffError, match="kdiff-claims-missing"):
        read_as_of(vault, project_id="ghost", as_of_valid_time=T1)


def test_subject_cap_bounds_and_truncates(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    snapshot = read_as_of(vault, project_id="proj-a", as_of_valid_time=T1, subject_cap=2)
    assert snapshot["cell_count"] == 2
    assert snapshot["truncated"] is True
    assert snapshot["caps"]["subject_cap"] == 2
    with pytest.raises(KnowledgeDiffError, match="subject-cap-out-of-range"):
        read_as_of(vault, project_id="proj-a", as_of_valid_time=T1, subject_cap=0)


def test_default_subject_cap_reported(tmp_path: Path) -> None:
    vault = _build_matrix_vault(tmp_path)
    snapshot = read_as_of(vault, project_id="proj-a", as_of_valid_time=T1)
    assert snapshot["caps"]["subject_cap"] == DEFAULT_SUBJECT_CAP
    assert snapshot["truncated"] is False


# ---------------------------------------------------------------------------
# CLI exit codes (0/1/2)
# ---------------------------------------------------------------------------


def test_cli_as_of_exit_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from project_atlas.cli import main as cli_main

    vault = _build_matrix_vault(tmp_path)
    code = cli_main(
        ["kdiff", "--vault", str(vault), "--project", "proj-a", "--as-of", T1, "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["artifact_kind"] == "kdiff-as-of-snapshot"
    validate_record(payload, "kdiff-as-of-snapshot")


def test_cli_diff_exit_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from project_atlas.cli import main as cli_main

    vault = _build_matrix_vault(tmp_path)
    code = cli_main(
        [
            "kdiff",
            "--vault",
            str(vault),
            "--project",
            "proj-a",
            "--from",
            T1,
            "--to",
            T2,
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["artifact_kind"] == "kdiff-record"


def test_cli_missing_project_is_usage_error(tmp_path: Path) -> None:
    from project_atlas.cli import main as cli_main

    vault = _build_matrix_vault(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli_main(["kdiff", "--vault", str(vault), "--as-of", T1])
    assert exc.value.code == 2


def test_cli_requires_a_mode(tmp_path: Path) -> None:
    from project_atlas.cli import main as cli_main

    vault = _build_matrix_vault(tmp_path)
    code = cli_main(["kdiff", "--vault", str(vault), "--project", "proj-a"])
    assert code == 1


def test_cli_as_of_and_range_mutually_exclusive(tmp_path: Path) -> None:
    from project_atlas.cli import main as cli_main

    vault = _build_matrix_vault(tmp_path)
    code = cli_main(
        [
            "kdiff",
            "--vault",
            str(vault),
            "--project",
            "proj-a",
            "--as-of",
            T1,
            "--from",
            T1,
        ]
    )
    assert code == 1


def test_cli_operational_error_exit_one(tmp_path: Path) -> None:
    from project_atlas.cli import main as cli_main

    vault = tmp_path / "empty-vault"
    vault.mkdir()
    code = cli_main(["kdiff", "--vault", str(vault), "--project", "ghost", "--as-of", T1])
    assert code == 1
