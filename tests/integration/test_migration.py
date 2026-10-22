import json
import subprocess
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_OK, main
from project_atlas.migrations import claim_v2_migration
from project_atlas.migrations.claim_v2_migration import migrate_v2

pytestmark = pytest.mark.integration
def test_migrate_v2_cli_smoke(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sources").mkdir()
    (vault / ".atlas").mkdir()

    assert main(["migrate-v2", "--vault", str(vault), "--project", "cli-project"]) == EXIT_OK

    alias_map_path = (
        vault / "state" / "claim-alias-maps" / "cli-project" / "claim-alias-map.json"
    )
    assert alias_map_path.exists()
    alias_map = json.loads(alias_map_path.read_text(encoding="utf-8"))
    assert alias_map["project_id"] == "cli-project"


def test_migrate_v2_creates_alias_map_and_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sources").mkdir()
    (vault / ".atlas").mkdir()

    result = migrate_v2(vault, "test-project")

    assert result["status"] == "success"
    assert "receipt" in result

    alias_map_path = Path(result["alias_map_path"])
    assert alias_map_path.exists()

    alias_map = json.loads(alias_map_path.read_text(encoding="utf-8"))
    assert alias_map["schema_version"] == 1
    assert alias_map["project_id"] == "test-project"
    assert isinstance(alias_map["aliases"], list)
    assert isinstance(alias_map["ambiguous"], list)
    assert alias_map["audit"]["source_commits_scanned"] == 0
    assert alias_map["audit"]["input_claims"] == 0
    assert alias_map["audit"]["output_aliases"] == 0


def test_migrate_v2_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sources").mkdir()
    (vault / ".atlas").mkdir()

    result1 = migrate_v2(vault, "test-project")
    assert result1["status"] == "success"

    result2 = migrate_v2(vault, "test-project")
    assert result2["status"] == "idempotent"
    assert result2["migrated_claims"] == 0


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def test_migrate_v2_records_ambiguous_locator_mappings(tmp_path: Path) -> None:
    """A v1 claim that resolves to two different v2 locators is recorded as ambiguous."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "sources").mkdir()
    (vault / ".atlas").mkdir()

    _run_git(["init"], vault)
    _run_git(["config", "user.name", "Test"], vault)
    _run_git(["config", "user.email", "test@test.com"], vault)

    doc = vault / "sources" / "doc.md"
    doc.write_text("# Heading A {#a}\n\n- decision: keep it\n", encoding="utf-8")
    _run_git(["add", "sources/doc.md"], vault)
    _run_git(["commit", "-m", "first"], vault)

    doc.write_text("# Heading B {#b}\n\n- decision: keep it\n", encoding="utf-8")
    _run_git(["add", "sources/doc.md"], vault)
    _run_git(["commit", "-m", "second"], vault)

    (vault / "state").mkdir(parents=True, exist_ok=True)
    (vault / "state" / "sources.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "source_id": "doc",
                        "source_lineage_id": "lineage-doc",
                        "current_path": "sources/doc.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = migrate_v2(vault, "ambig-project")
    assert result["status"] == "success"
    assert result["ambiguous_count"] == 1

    alias_map = json.loads(Path(result["alias_map_path"]).read_text(encoding="utf-8"))
    assert len(alias_map["ambiguous"]) == 1
    expected_reason = "single v1 identity maps to multiple v2 identities"
    assert alias_map["ambiguous"][0]["reason"] == expected_reason

    # F-002 invariant: ambiguous records must never be treated as resolved aliases.
    ambiguous_v1 = {item["v1_claim_id"] for item in alias_map["ambiguous"]}
    resolved_v1 = {item["v1_claim_id"] for item in alias_map["aliases"]}
    assert ambiguous_v1.isdisjoint(resolved_v1)


def test_migrate_v2_rejects_unsafe_project_component(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ValueError, match="unsafe project id"):
        migrate_v2(vault, "..\\escape")
    assert main(
        ["migrate-v2", "--vault", str(vault), "--project", "../escape"]
    ) != EXIT_OK
    assert not (tmp_path / "escape").exists()


def test_alias_and_receipt_are_one_atomic_recoverable_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    original_write = claim_v2_migration._write_json

    def fail_receipt(path: Path, payload: dict[str, object]) -> None:
        if path.name == "migration-receipt.json":
            raise OSError("injected receipt failure")
        original_write(path, payload)

    with monkeypatch.context() as scoped:
        scoped.setattr(claim_v2_migration, "_write_json", fail_receipt)
        with pytest.raises(OSError, match="injected receipt failure"):
            migrate_v2(vault, "atomic-project")

    bundle = vault / "state" / "claim-alias-maps" / "atomic-project"
    assert not bundle.exists()
    assert not list((vault / "state" / "claim-alias-maps").glob("*.tmp"))

    result = migrate_v2(vault, "atomic-project")
    alias_path = Path(result["alias_map_path"])
    receipt_path = Path(result["receipt"])
    assert alias_path.is_file() and receipt_path.is_file()
    receipt_path.unlink()
    with pytest.raises(ValueError, match="incomplete claim identity migration bundle"):
        migrate_v2(vault, "atomic-project")
    assert alias_path.is_file()


def test_existing_alias_map_rejects_resolved_ambiguous_overlap(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    result = migrate_v2(vault, "overlap-project")
    alias_path = Path(result["alias_map_path"])
    payload = json.loads(alias_path.read_text(encoding="utf-8"))
    record = {
        "v1_claim_id": "claim-v1",
        "v2_claim_id": "claim-v2",
        "project_identity": "overlap-project",
        "source_lineage_id": "source",
        "claim_type": "decision",
        "field": "decision",
        "stable_semantic_locator": "id:decision",
        "source_commit": "commit",
        "source_path": "sources/doc.md",
    }
    payload["aliases"] = [record]
    payload["ambiguous"] = [
        {
            "v1_claim_id": "claim-v1",
            "reason": "ambiguous",
            "records": [record],
        }
    ]
    payload["audit"]["output_aliases"] = 1
    alias_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="resolved/ambiguous overlap"):
        migrate_v2(vault, "overlap-project")


def test_projects_have_isolated_alias_bundles_and_history(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True)
    (vault / "state").mkdir()
    _run_git(["init"], vault)
    _run_git(["config", "user.name", "Test"], vault)
    _run_git(["config", "user.email", "test@test.com"], vault)
    for source_id, label in (("source-one", "one"), ("source-two", "two")):
        (imported / f"{source_id}.md").write_text(
            f"# Decisions\n\ndecision: {label} {{#{label}}}\n", encoding="utf-8"
        )
    _run_git(["add", "sources/imported-documents"], vault)
    _run_git(["commit", "-m", "two projects"], vault)
    uuid_one = "00000000-0000-4000-8000-000000000201"
    uuid_two = "00000000-0000-4000-8000-000000000202"
    (vault / "state" / "sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "source-one",
                        "source_lineage_id": "sline-one",
                        "canonical_project_id": uuid_one,
                        "current_path": "one/DECISIONS.md",
                    },
                    {
                        "source_id": "source-two",
                        "source_lineage_id": "sline-two",
                        "canonical_project_id": uuid_two,
                        "current_path": "two/DECISIONS.md",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    manifests = vault / "sources" / "manifests"
    manifests.mkdir()
    (manifests / "source-manifest.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "source-one",
                        "likely_project": "project-one",
                    },
                    {
                        "source_id": "source-two",
                        "likely_project": "project-two",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    one = migrate_v2(vault, "project-one")
    two = migrate_v2(vault, "project-two")
    assert Path(one["alias_map_path"]) != Path(two["alias_map_path"])
    one_payload = json.loads(Path(one["alias_map_path"]).read_text(encoding="utf-8"))
    two_payload = json.loads(Path(two["alias_map_path"]).read_text(encoding="utf-8"))
    assert {item["source_lineage_id"] for item in one_payload["aliases"]} == {
        "sline-one"
    }
    assert {item["source_lineage_id"] for item in two_payload["aliases"]} == {
        "sline-two"
    }


def test_migrate_v2_does_not_adopt_sole_foreign_uuid(tmp_path: Path) -> None:
    """AS-CLAIM-V2-UUID-FALLBACK-001: --project must not inherit another UUID."""
    vault = tmp_path / "vault"
    (vault / "state").mkdir(parents=True)
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True)
    (vault / "sources" / "manifests").mkdir(parents=True)
    (vault / "generated" / "reports").mkdir(parents=True)
    (imported / "s1.md").write_text(
        "# Architecture\n\nThe datastore is PostgreSQL 15.\n",
        encoding="utf-8",
    )
    _run_git(["init"], vault)
    _run_git(["config", "user.name", "Test"], vault)
    _run_git(["config", "user.email", "test@test.com"], vault)
    _run_git(["add", "sources"], vault)
    _run_git(["commit", "-m", "src"], vault)
    foreign_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    (vault / "state" / "sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "s1",
                        "canonical_project_id": foreign_uuid,
                        "source_lineage_id": "lin-1",
                        "current_path": "sources/imported-documents/s1.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (vault / "sources" / "manifests" / "source-manifest.json").write_text(
        json.dumps(
            {"sources": [{"source_id": "s1", "likely_project": "other-project"}]}
        ),
        encoding="utf-8",
    )
    (vault / "generated" / "reports" / "ingestion-report.json").write_text(
        json.dumps({"classifications": {"s1": {"type": "architecture"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cannot resolve project identity"):
        migrate_v2(vault, "requested-project")
    assert not (vault / "state" / "claim-alias-maps" / "requested-project").exists()


def test_historical_recognized_claim_without_locator_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "sources").mkdir(parents=True)
    (vault / "state").mkdir()
    _run_git(["init"], vault)
    _run_git(["config", "user.name", "Test"], vault)
    _run_git(["config", "user.email", "test@test.com"], vault)
    (vault / "sources" / "source.md").write_text(
        "decision: missing durable locator\n", encoding="utf-8"
    )
    _run_git(["add", "sources/source.md"], vault)
    _run_git(["commit", "-m", "unresolved claim"], vault)
    (vault / "state" / "sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "source",
                        "source_lineage_id": "sline-source",
                        "current_path": "source.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no stable locator found"):
        migrate_v2(vault, "unresolved-project")
    assert not (
        vault / "state" / "claim-alias-maps" / "unresolved-project"
    ).exists()
