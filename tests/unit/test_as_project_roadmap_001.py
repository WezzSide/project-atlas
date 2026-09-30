"""AS-PROJECT-ROADMAP-001 — Living Project Roadmap V1."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.app_service import open_app_service
from project_atlas.cli import EXIT_OK, main
from project_atlas.project_roadmap import (
    build_roadmap_lens,
    derive_roadmap_lenses,
    materialize_roadmap_lenses,
)
from project_atlas.schema import validate_record
from project_atlas.web_api.roadmap import read_project_roadmap


def _write_roadmap(vault: Path, project_id: str, record: dict) -> None:
    note = vault / "projects" / project_id / "roadmap.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntype: Roadmap\n---\n\n# Roadmap\n\n## Roadmap record\n\n```json\n"
        + json.dumps(record)
        + "\n```\n",
        encoding="utf-8",
    )
    (vault / "projects" / project_id / "project.md").write_text(
        f"---\ntype: Project\ntitle: {project_id}\n---\n\n# {project_id}\n",
        encoding="utf-8",
    )


def _realistic_record() -> dict:
    return {
        "milestones": [
            {"id": "m-core", "title": "Core compile", "status": "VERIFIED_COMPLETION"},
            {"id": "m-next", "title": "Next unlock", "status": "IN_PROGRESS"},
        ],
        "items": [
            {
                "id": "pkg-identity",
                "title": "Governed identity",
                "status": "VERIFIED_COMPLETION",
                "milestone": "m-core",
                "depends_on": [],
                "evidence": ["generated/ops/receipts/identity.json"],
            },
            {
                "id": "pkg-connect",
                "title": "Connect compile",
                "status": "IMPLEMENTED",
                "milestone": "m-core",
                "depends_on": ["pkg-identity"],
                "evidence": ["generated/ops/receipts/connect.json"],
            },
            {
                "id": "pkg-roadmap",
                "title": "Living roadmap",
                "status": "IN_PROGRESS",
                "milestone": "m-next",
                "depends_on": ["pkg-connect"],
                "evidence": [],
            },
            {
                "id": "pkg-gated",
                "title": "Gated surface",
                "status": "BLOCKED",
                "milestone": "m-next",
                "depends_on": ["pkg-roadmap"],
                "blockers": [
                    {
                        "reason": "owner merge gate",
                        "waiting_on": "D042",
                        "unlock_condition": "D042 merged to main",
                    }
                ],
            },
        ],
    }


def _seed_realistic(vault: Path, project_id: str = "harbor-slice") -> str:
    _write_roadmap(vault, project_id, _realistic_record())
    receipts = vault / "generated" / "ops" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / "identity.json").write_text("{}\n", encoding="utf-8")
    (receipts / "connect.json").write_text("{}\n", encoding="utf-8")
    (vault / "generated" / "answers").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "answers" / f"ans-state-{project_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "derived",
                "rollup": "IN_PROGRESS",
                "summary": "compile live; gated work blocked",
            }
        ),
        encoding="utf-8",
    )
    return project_id


def test_realistic_fixture_you_are_here_and_critical_path(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    project_id = _seed_realistic(vault)
    lens = build_roadmap_lens(vault, project_id)
    validate_record(lens, "project-roadmap")
    assert lens["honesty"]["roadmap_is_canonical"] is False
    assert lens["honesty"]["percent_complete_is_canonical"] is False
    assert lens["critical_path"] == [
        "pkg-identity",
        "pkg-connect",
        "pkg-roadmap",
        "pkg-gated",
    ]
    assert lens["you_are_here"]["item_id"] == "pkg-roadmap"
    assert lens["you_are_here"]["status"] == "IN_PROGRESS"
    assert lens["next_unlock"]["item_id"] == "pkg-roadmap"
    gated = next(item for item in lens["items"] if item["id"] == "pkg-gated")
    assert gated["status"] == "BLOCKED"
    assert gated["blockers"][0]["waiting_on"] == "D042"
    assert "74%" not in json.dumps(lens)


def test_missing_evidence_is_not_verified(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "missing-ev",
        {
            "items": [
                {
                    "id": "pkg-a",
                    "title": "Claimed done",
                    "status": "VERIFIED_COMPLETION",
                    "evidence": ["generated/ops/receipts/absent.json"],
                }
            ]
        },
    )
    lens = build_roadmap_lens(vault, "missing-ev")
    item = lens["items"][0]
    assert item["status"] != "VERIFIED_COMPLETION"
    assert item["missing_acceptance_evidence"] is True
    assert "MISSING_ACCEPTANCE_EVIDENCE" in item["flags"]


def test_percent_theatre_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "theatre",
        {"items": [{"id": "pkg-a", "title": "Counted", "status": "37 packages done / 50"}]},
    )
    lens = build_roadmap_lens(vault, "theatre")
    assert lens["items"][0]["status"] == "UNKNOWN"
    assert "rejected_package_count_theatre" in lens["items"][0]["flags"]


def test_cyclic_dependencies_unknown_honesty(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "cycle",
        {
            "items": [
                {"id": "a", "title": "A", "status": "IN_PROGRESS", "depends_on": ["b"]},
                {"id": "b", "title": "B", "status": "IN_PROGRESS", "depends_on": ["a"]},
            ]
        },
    )
    lens = build_roadmap_lens(vault, "cycle")
    assert lens["honesty"]["cyclic_dependencies"] is True
    assert lens["critical_path"] == []
    assert lens["you_are_here"]["status"] == "UNKNOWN"
    assert lens["next_unlock"]["reason"] == "cyclic_dependencies"
    assert "cyclic dependencies" in lens["unknowns"]


def test_contradictory_verified_without_evidence_list(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "contradict",
        {"items": [{"id": "a", "title": "Bare complete", "status": "VERIFIED_COMPLETION"}]},
    )
    lens = build_roadmap_lens(vault, "contradict")
    assert lens["items"][0]["status"] == "UNKNOWN"
    assert "no_evidence_neq_complete" in lens["items"][0]["flags"]


def test_unknown_without_roadmap_record(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    project = vault / "projects" / "empty"
    project.mkdir(parents=True)
    (project / "project.md").write_text("# empty\n", encoding="utf-8")
    lens = build_roadmap_lens(vault, "empty")
    assert lens["status"] == "unknown"
    assert lens["you_are_here"]["status"] == "UNKNOWN"
    assert "no structured roadmap record" in lens["unknowns"]
    validate_record(lens, "project-roadmap")


def test_cli_and_api_and_materialize(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    project_id = _seed_realistic(vault)
    report = materialize_roadmap_lenses(vault, project_ids=[project_id])
    written = vault / "generated" / "answers" / f"ans-roadmap-{project_id}.json"
    assert written.is_file()
    assert report["honesty"]["roadmap_is_canonical"] is False

    code = main(["roadmap", "--vault", str(vault), "--project", project_id])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "ROADMAP!=CANONICAL_TRUTH" in out
    assert "pkg-roadmap" in out

    service = open_app_service(vault)
    api = service.roadmap(project_id)
    assert api["you_are_here"]["item_id"] == "pkg-roadmap"
    assert api["honesty"]["ui_is_canonical"] is False
    read = read_project_roadmap(vault, project_id)
    assert read["critical_path"] == api["critical_path"]
    assert api["next_unlock"]["why"]
    assert api["honesty"]["merged_eq_closed"] is False
    assert api["honesty"]["dogfood_local_vault_executed"] is False


def test_merged_is_not_closed_or_verified(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "gates",
        {
            "items": [
                {
                    "id": "pkg-merged",
                    "title": "Merged package",
                    "lifecycle": "MERGED",
                    "status": "IMPLEMENTED",
                    "evidence": ["generated/ops/receipts/merge.json"],
                },
                {
                    "id": "pkg-closed-claim",
                    "title": "Claimed closed",
                    "status": "CLOSED",
                    "depends_on": ["pkg-merged"],
                },
            ]
        },
    )
    (vault / "generated" / "ops" / "receipts").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "ops" / "receipts" / "merge.json").write_text("{}\n", encoding="utf-8")
    lens = build_roadmap_lens(vault, "gates")
    merged = next(item for item in lens["items"] if item["id"] == "pkg-merged")
    claimed = next(item for item in lens["items"] if item["id"] == "pkg-closed-claim")
    assert merged["lifecycle"] == "MERGED"
    assert merged["status"] == "IMPLEMENTED"
    assert merged["lifecycle"] != "CLOSED"
    assert claimed["status"] == "UNKNOWN"
    assert "closed_requires_post_merge_verified" in claimed["flags"]


def test_unknown_prerequisite_blocks_unlock(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "unk-dep",
        {
            "items": [
                {"id": "a", "title": "Unknown dep", "status": "UNKNOWN"},
                {"id": "b", "title": "Waiting", "status": "NOT_STARTED", "depends_on": ["a"]},
            ]
        },
    )
    lens = build_roadmap_lens(vault, "unk-dep")
    assert lens["next_unlock"]["reason"] == "unknown_prerequisite"
    assert lens["next_unlock"]["waiting_on"] == "a"
    assert lens["next_unlock"]["smallest_transition"] is None
    assert "UNKNOWN" in (lens["next_unlock"]["why"] or "")


def test_checked_in_harbor_slice_fixture() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "roadmap" / "v1" / "harbor-slice"
    lens = build_roadmap_lens(fixture, "harbor-slice")
    validate_record(lens, "project-roadmap")
    assert lens["you_are_here"]["item_id"] == "pkg-roadmap"
    assert lens["next_unlock"]["smallest_transition"]["to"] == "IMPLEMENTATION_COMPLETE"
    assert lens["honesty"]["dogfood_local_vault_executed"] is False


def test_parallel_unfinished_work_is_not_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "parallel",
        {
            "items": [
                {
                    "id": "a",
                    "title": "Root",
                    "status": "VERIFIED_COMPLETION",
                    "evidence": ["generated/ops/receipts/a.json"],
                },
                {
                    "id": "b",
                    "title": "Done branch",
                    "status": "VERIFIED_COMPLETION",
                    "depends_on": ["a"],
                    "evidence": ["generated/ops/receipts/b.json"],
                },
                {
                    "id": "c",
                    "title": "Open sibling",
                    "status": "NOT_STARTED",
                    "depends_on": ["a"],
                },
            ]
        },
    )
    receipts = vault / "generated" / "ops" / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / "a.json").write_text("{}\n", encoding="utf-8")
    (receipts / "b.json").write_text("{}\n", encoding="utf-8")
    lens = build_roadmap_lens(vault, "parallel")
    validate_record(lens, "project-roadmap")
    assert "c" in lens["critical_path"]
    assert lens["you_are_here"]["item_id"] == "c"
    assert lens["you_are_here"]["status"] != "VERIFIED_COMPLETION"
    assert lens["next_unlock"]["item_id"] == "c"
    assert lens["next_unlock"]["status"] != "VERIFIED_COMPLETION"
    assert lens["next_unlock"]["lifecycle"] != "CLOSED"


def test_review_conflicts_mixed_entries_stay_scoped(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "alpha", {"items": [{"id": "a", "title": "A", "status": "IN_PROGRESS"}]})
    _write_roadmap(vault, "beta", {"items": [{"id": "b", "title": "B", "status": "IN_PROGRESS"}]})
    (vault / "review" / "conflicts").mkdir(parents=True, exist_ok=True)
    (vault / "review" / "conflicts" / "alpha.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"conflict_id": "c-alpha", "project_id": "alpha"},
                    {"conflict_id": "c-beta", "project_id": "beta"},
                    {"conflict_id": "c-unscoped"},
                ]
            }
        ),
        encoding="utf-8",
    )
    alpha = build_roadmap_lens(vault, "alpha")
    beta = build_roadmap_lens(vault, "beta")
    assert alpha["unresolved_conflicts"] == 2
    assert beta["unresolved_conflicts"] == 0


def test_global_pending_unscoped_does_not_bleed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "alpha", {"items": [{"id": "a", "title": "A", "status": "IN_PROGRESS"}]})
    _write_roadmap(vault, "beta", {"items": [{"id": "b", "title": "B", "status": "IN_PROGRESS"}]})
    (vault / "review").mkdir(parents=True, exist_ok=True)
    (vault / "review" / "pending.json").write_text(
        json.dumps({"entries": [{"status": "pending"}]}),
        encoding="utf-8",
    )
    alpha = build_roadmap_lens(vault, "alpha")
    beta = build_roadmap_lens(vault, "beta")
    assert alpha["pending_reviews"] == 0
    assert beta["pending_reviews"] == 0


def test_stale_unknown_lens_does_not_inflate_live_conflicts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "alpha", {"items": [{"id": "a", "title": "A", "status": "IN_PROGRESS"}]})
    (vault / "review" / "conflicts").mkdir(parents=True, exist_ok=True)
    (vault / "review" / "conflicts" / "alpha.json").write_text(
        json.dumps({"entries": []}),
        encoding="utf-8",
    )
    (vault / "generated" / "answers").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "answers" / "ans-unknown-alpha.json").write_text(
        json.dumps({"unresolved_conflicts": 9}),
        encoding="utf-8",
    )
    lens = build_roadmap_lens(vault, "alpha")
    assert lens["unresolved_conflicts"] == 0


def test_cross_project_conflicts_do_not_bleed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(vault, "alpha", {"items": [{"id": "a", "title": "A", "status": "IN_PROGRESS"}]})
    _write_roadmap(vault, "beta", {"items": [{"id": "b", "title": "B", "status": "IN_PROGRESS"}]})
    conflicts = vault / "generated" / "ops" / "conflicts"
    conflicts.mkdir(parents=True, exist_ok=True)
    (conflicts / "alpha-only.json").write_text(
        json.dumps({"project_id": "alpha", "entries": [{}]}),
        encoding="utf-8",
    )
    (conflicts / "beta-only.json").write_text(
        json.dumps({"project_id": "beta", "entries": [{}]}),
        encoding="utf-8",
    )
    (vault / "review" / "conflicts").mkdir(parents=True, exist_ok=True)
    (vault / "review" / "conflicts" / "alpha.json").write_text(
        json.dumps({"entries": [{"conflict_id": "c-alpha"}]}),
        encoding="utf-8",
    )
    alpha = build_roadmap_lens(vault, "alpha")
    beta = build_roadmap_lens(vault, "beta")
    assert alpha["unresolved_conflicts"] == 1
    assert beta["unresolved_conflicts"] == 1
    assert "unresolved conflicts" in alpha["unknowns"]
    assert "unresolved conflicts" in beta["unknowns"]


def test_missing_dependency_id_is_unknown_not_ready(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "ghost-dep",
        {
            "items": [
                {
                    "id": "pkg-b",
                    "title": "Waiting on ghost",
                    "status": "NOT_STARTED",
                    "depends_on": ["missing-a"],
                }
            ]
        },
    )
    lens = build_roadmap_lens(vault, "ghost-dep")
    item = lens["items"][0]
    assert "MISSING_DEPENDENCY" in item["flags"]
    assert item["missing_dependencies"] == ["missing-a"]
    assert lens["next_unlock"]["reason"] == "unknown_prerequisite"
    assert lens["next_unlock"]["waiting_on"] == "missing-a"
    assert lens["next_unlock"]["status"] != "VERIFIED_COMPLETION"
    assert "missing dependency ids" in lens["unknowns"]


def test_state_lens_percent_theatre_is_normalized(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "theatre-state",
        {
            "items": [
                {
                    "id": "pkg-a",
                    "title": "Done",
                    "status": "IMPLEMENTED",
                    "evidence": ["generated/ops/receipts/a.json"],
                }
            ]
        },
    )
    (vault / "generated" / "ops" / "receipts").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "ops" / "receipts" / "a.json").write_text("{}\n", encoding="utf-8")
    (vault / "generated" / "answers").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "answers" / "ans-state-theatre-state.json").write_text(
        json.dumps({"schema_version": 1, "status": "derived", "rollup": "100% complete"}),
        encoding="utf-8",
    )
    lens = build_roadmap_lens(vault, "theatre-state")
    assert lens["you_are_here"]["status"] != "100% complete"
    assert lens["you_are_here"]["status"] in {"UNKNOWN", "IMPLEMENTED"}
    assert "74%" not in json.dumps(lens)
    assert "100%" not in lens["you_are_here"]["status"]


def test_tied_critical_paths_are_disclosed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "ties",
        {
            "items": [
                {
                    "id": "root",
                    "title": "Root",
                    "status": "IMPLEMENTED",
                    "evidence": ["generated/ops/receipts/r.json"],
                },
                {"id": "left", "title": "Left", "status": "IN_PROGRESS", "depends_on": ["root"]},
                {"id": "right", "title": "Right", "status": "IN_PROGRESS", "depends_on": ["root"]},
            ]
        },
    )
    (vault / "generated" / "ops" / "receipts").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "ops" / "receipts" / "r.json").write_text("{}\n", encoding="utf-8")
    lens = build_roadmap_lens(vault, "ties")
    assert lens["honesty"]["multiple_critical_paths"] is True
    assert "multiple equal-length critical paths" in lens["unknowns"]
    assert lens["next_unlock"]["item_id"] in {"left", "right"}


def test_read_only_cli_does_not_write(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    project_id = _seed_realistic(vault)
    answers = vault / "generated" / "answers"
    before = {path.name for path in answers.glob("ans-roadmap-*.json")}
    code = main(["roadmap", "--vault", str(vault), "--project", project_id, "--read-only"])
    assert code == EXIT_OK
    after = {path.name for path in answers.glob("ans-roadmap-*.json")}
    assert after == before
    out = capsys.readouterr().out
    assert "ROADMAP!=CANONICAL_TRUTH" in out
    report = derive_roadmap_lenses(vault, project_ids=[project_id])
    assert report["answers_written"] == []
    assert report["honesty"]["read_only"] is True


def test_implemented_all_done_is_not_verified_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_roadmap(
        vault,
        "impl-only",
        {
            "items": [
                {
                    "id": "pkg-a",
                    "title": "Implemented only",
                    "status": "IMPLEMENTED",
                    "evidence": ["generated/ops/receipts/a.json"],
                }
            ]
        },
    )
    (vault / "generated" / "ops" / "receipts").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "ops" / "receipts" / "a.json").write_text("{}\n", encoding="utf-8")
    lens = build_roadmap_lens(vault, "impl-only")
    assert lens["next_unlock"]["status"] == "IMPLEMENTED"
    assert lens["next_unlock"]["lifecycle"] != "CLOSED"
    assert lens["next_unlock"]["reason"] == "remaining_verification"
    assert lens["you_are_here"]["status"] != "VERIFIED_COMPLETION"
