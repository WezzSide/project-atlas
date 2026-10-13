"""AS-INT-001 public event-package ingestion coverage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from project_atlas.cli import EXIT_OK, main

pytestmark = pytest.mark.integration
def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_event(
    root: Path,
    *,
    event_id: str,
    event_type: str,
    project_id: str = "integrated-atlas-project",
    pipeline: dict[str, bool] | None = None,
    directory_event_id: str | None = None,
    summary: str | None = None,
) -> Path:
    event_dir = root / ".atlas-inbox" / "agent-events" / project_id / (
        directory_event_id or event_id
    )
    event_dir.mkdir(parents=True, exist_ok=True)
    event_md = ((summary or event_type.title()) + "\n").encode("utf-8")
    event = {
        "event_id": event_id,
        "event_type": event_type,
        "project_id": project_id,
        "session_id": "AS-integrated-session",
        "agent_id": "agent-one",
        "adapter_id": "generic-cli-v1",
        "timestamp": "2026-08-01T18:00:00Z",
        "work_package_id": "AS-INT-001",
        "summary": summary or f"Recorded {event_type}",
    }
    skill = {"id": "atlas-governed-work", "version": "1.0.0", "sha256": "0" * 64}
    vault = {"vault_id": "atlas-main", "vault_uuid": "fixture-vault-uuid"}
    actual_pipeline = pipeline or {
        "captured": True,
        "normalized": True,
        "verified": True,
        "routed": True,
    }
    receipt = {"receipt_id": f"AR-{event_id}", "status": "valid", "event_id": event_id}
    envelope = {
        "schema_version": 1,
        "event": event,
        "skill": skill,
        "vault": vault,
        "provenance": {
            "content_sha256": _hash(event_md),
            "normalized_sha256": "pending",
            "source_receipt_id": receipt["receipt_id"],
        },
        "pipeline": actual_pipeline,
        "receipt": receipt,
    }
    envelope["provenance"]["normalized_sha256"] = _hash(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    (event_dir / "event.md").write_bytes(event_md)
    (event_dir / "event.json").write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (event_dir / "provenance.json").write_text(
        json.dumps(envelope["provenance"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (event_dir / "receipt.yaml").write_text(
        yaml.safe_dump(receipt, sort_keys=True), encoding="utf-8"
    )
    return event_dir


def _run_fixture(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "integrated-atlas-project"
    fixture = Path(__file__).parents[1] / "fixtures" / "integrated-atlas-project"
    for path in fixture.rglob("*"):
        if path.is_file():
            target = source / path.relative_to(fixture)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    for event_type, event_id in (
        ("session-start", "AE-start"),
        ("implementation", "AE-implementation"),
        ("decision", "AE-decision"),
        ("validation", "AE-validation"),
        ("completion", "AE-completion"),
    ):
        _write_event(source, event_id=event_id, event_type=event_type)
    return source, tmp_path / "vault"


def _write_vault_identity(vault: Path, *, vault_id: str = "atlas-main") -> None:
    (vault / ".atlas").mkdir(parents=True, exist_ok=True)
    (vault / ".atlas" / "vault.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "vault_id": vault_id,
                "vault_uuid": "fixture-vault-uuid",
                "name": "Fixture Vault",
            }
        ),
        encoding="utf-8",
    )
    (vault / ".atlas" / "agent-event-policy.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "skill": {
                    "id": "atlas-governed-work",
                    "version": "1.0.0",
                    "sha256": "0" * 64,
                },
            }
        ),
        encoding="utf-8",
    )


def test_public_event_package_workflow_and_projections(tmp_path: Path) -> None:
    source, vault = _run_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(payload["agent_events"]) == 5
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault)
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    assert main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
    assert main(["validate", "--vault", str(vault)]) == EXIT_OK
    activity = vault / "projects" / "integrated-atlas-project" / "activity.md"
    assert "AE-implementation" in activity.read_text(encoding="utf-8")
    assert "agent-events/integrated-atlas-project" in activity.read_text(encoding="utf-8")
    assert (vault / "projects" / "integrated-atlas-project" / "validations.md").is_file()
    assert (vault / "state" / "agent-events" / "integrated-atlas-project.json").is_file()
    assert (
        vault
        / "receipts"
        / "agent-events"
        / "integrated-atlas-project"
        / "AE-completion.yaml"
    ).is_file()


def test_pending_malformed_and_traversal_packages_are_explicitly_quarantined(
    tmp_path: Path,
) -> None:
    source, vault = _run_fixture(tmp_path)
    _write_event(
        source,
        event_id="AE-pending",
        event_type="validation",
        pipeline={"captured": True, "normalized": False, "verified": False, "routed": False},
    )
    malformed = (
        source
        / ".atlas-inbox"
        / "agent-events"
        / "integrated-atlas-project"
        / "AE-malformed"
    )
    malformed.mkdir(parents=True)
    (malformed / "event.json").write_text("{}", encoding="utf-8")
    _write_event(
        source,
        event_id="../../outside",
        event_type="completion",
        directory_event_id="AE-traversal",
    )
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault)
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    quarantine = vault / "quarantine" / "agent-events" / "index.json"
    assert quarantine.is_file()
    text = quarantine.read_text(encoding="utf-8")
    assert "AE-pending" in text
    assert "AE-malformed" in text
    assert "AE-traversal" in text
    assert not (tmp_path / "outside").exists()


def test_conflicting_duplicate_event_id_is_not_canonical(tmp_path: Path) -> None:
    source, vault = _run_fixture(tmp_path)
    _write_event(
        source,
        event_id="AE-duplicate",
        event_type="implementation",
        project_id="project-one",
        summary="first",
    )
    _write_event(
        source,
        event_id="AE-duplicate",
        event_type="completion",
        project_id="project-two",
        summary="different",
    )
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert sum(item["status"] == "conflicting" for item in payload["agent_events"]) == 2
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault)
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    assert "conflicting duplicate" in (
        vault / "quarantine" / "agent-events" / "index.json"
    ).read_text(encoding="utf-8")


def test_repeated_identical_package_replay_is_idempotent(tmp_path: Path) -> None:
    source, vault = _run_fixture(tmp_path)
    _write_event(source, event_id="AE-identical", event_type="implementation")
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault)
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    assert main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
    assert main(["validate", "--vault", str(vault)]) == EXIT_OK
    # SEC-002: rediscover after marker genesis before replay ingest.
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    assert main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
    before = {
        path.relative_to(vault).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in vault.rglob("*")
        if path.is_file()
    }
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    assert main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
    assert main(["validate", "--vault", str(vault)]) == EXIT_OK
    after = {
        path.relative_to(vault).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in vault.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert (vault / "state" / "agent-events" / "integrated-atlas-project.json").is_file()


def test_hash_mismatch_is_quarantined_at_ingestion_boundary(tmp_path: Path) -> None:
    source, vault = _run_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    tampered_event = (
        source
        / ".atlas-inbox"
        / "agent-events"
        / "integrated-atlas-project"
        / "AE-implementation"
        / "event.md"
    )
    tampered_event.write_text(
        "tampered\n", encoding="utf-8"
    )
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault)
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    quarantine = vault / "quarantine" / "agent-events" / "index.json"
    assert "event.md hash mismatch" in quarantine.read_text(encoding="utf-8")
    assert "AE-implementation" not in (
        vault / "projects" / "integrated-atlas-project" / "activity.md"
    ).read_text(encoding="utf-8")


def test_wrong_vault_identity_is_quarantined_without_canonical_event_write(
    tmp_path: Path,
) -> None:
    source, vault = _run_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault, vault_id="wrong-vault")
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    quarantine = vault / "quarantine" / "agent-events" / "index.json"
    assert "Vault identity does not match" in quarantine.read_text(encoding="utf-8")
    activity = vault / "projects" / "integrated-atlas-project" / "activity.md"
    assert "AE-implementation" not in activity.read_text(encoding="utf-8")


def test_handcrafted_event_inventory_project_traversal_cannot_escape_vault(
    tmp_path: Path,
) -> None:
    source, vault = _run_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["agent_events"][0]["project_id"] = "../../../../outside-event-state"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault)
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    assert not (tmp_path / "outside-event-state").exists()
    assert (vault / "quarantine" / "agent-events" / "index.json").is_file()


def test_missing_vault_identity_keeps_events_out_of_canonical_projection(tmp_path: Path) -> None:
    source, vault = _run_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    # AS-DEMO-2.2-RECOVERY-ID-001: init now mints identity; strip it to exercise
    # the still-required ingest fail-closed path for an absent Vault identity.
    identity = vault / ".atlas" / "vault.json"
    assert identity.is_file()
    identity.unlink()
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    quarantine = vault / "quarantine" / "agent-events" / "index.json"
    assert "target Vault identity is unavailable" in quarantine.read_text(encoding="utf-8")
    activity = vault / "projects" / "integrated-atlas-project" / "activity.md"
    assert "AE-implementation" not in activity.read_text(encoding="utf-8")


def test_skill_hash_mismatch_is_quarantined_against_trusted_policy(tmp_path: Path) -> None:
    source, vault = _run_fixture(tmp_path)
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    event_json = (
        source
        / ".atlas-inbox"
        / "agent-events"
        / "integrated-atlas-project"
        / "AE-implementation"
        / "event.json"
    )
    payload = json.loads(event_json.read_text(encoding="utf-8"))
    payload["skill"]["sha256"] = "f" * 64
    event_json.write_text(json.dumps(payload), encoding="utf-8")
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault)
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    quarantine = vault / "quarantine" / "agent-events" / "index.json"
    assert "skill binding does not match trusted skill policy" in quarantine.read_text(
        encoding="utf-8"
    )


def test_symlinked_event_package_isolated_during_discovery(tmp_path: Path) -> None:
    source, _ = _run_fixture(tmp_path)
    outside = tmp_path / "outside-package"
    outside.mkdir()
    symlink = source / ".atlas-inbox" / "agent-events" / "integrated-atlas-project" / "AE-symlink"
    symlink.symlink_to(outside, target_is_directory=True)
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    symlink_record = next(
        item for item in payload["agent_events"] if item["event_id"] == "AE-symlink"
    )
    assert symlink_record["status"] == "invalid"


def test_component_symlinked_outside_the_root_never_reaches_the_vault(
    tmp_path: Path,
) -> None:
    """End to end: an escaping component's bytes must not land in the Vault.

    Asserted on **bytes**, never on an error string or a manifest wording, so
    the test is independent of the outstanding evidence-policy choice --
    between the two options that actually close the defect. Refusing any
    symlinked component and resolve-and-confine both refuse a component whose
    target escapes the root, and differ only on the intra-root case, which
    this does not touch. Verified by implementing resolve-and-confine as a
    mutation: this test passes unchanged under it.

    Repair-only is the exception, and an earlier version of this docstring
    wrongly swept it in by saying "all three". Under repair-only the package
    is still inventoried as valid and this test fails -- which is the option
    table's own verdict that the defect stays open, so the failure is correct.

    The declared `content_sha256` is set to the *outside* file's digest, so
    hash verification cannot be what refuses the package. Containment is the
    only thing left that can.
    """
    source, vault = _run_fixture(tmp_path)
    outside = tmp_path / "outside" / "secret.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    body = b"private key material outside the source root"
    outside.write_bytes(body + b"\n")
    # The needle deliberately excludes the trailing newline: with it, the
    # assertion would pin only byte-identical copies, and a path that
    # re-encoded or re-wrapped the content would slip through.
    secret = body

    package = (
        source
        / ".atlas-inbox"
        / "agent-events"
        / "integrated-atlas-project"
        / "AE-implementation"
    )
    component = package / "event.md"
    component.unlink()
    component.symlink_to(outside)

    digest = _hash(outside.read_bytes())
    provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
    provenance["content_sha256"] = digest
    (package / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    envelope = json.loads((package / "event.json").read_text(encoding="utf-8"))
    envelope["provenance"]["content_sha256"] = digest
    (package / "event.json").write_text(
        json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    valid = [row for row in payload["agent_events"] if row["status"] == "valid"]
    assert len(valid) == 4, (
        "the escaping package is still inventoried as valid evidence: "
        f"{[row.get('event_id') for row in valid]}"
    )
    # The count alone would also pass if the row *vanished*, which is the
    # silent-disappearance mode this package's own history includes. The row
    # must be present and say so.
    escaping = [
        row for row in payload["agent_events"] if row.get("event_id") == "AE-implementation"
    ]
    assert len(escaping) == 1, "the escaping package left no row at all"
    assert escaping[0]["status"] == "invalid", escaping[0]

    assert main(["init", "--output", str(vault)]) == EXIT_OK
    _write_vault_identity(vault)
    assert main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK

    leaked = sorted(
        str(path.relative_to(vault))
        for path in vault.rglob("*")
        if path.is_file() and secret in path.read_bytes()
    )
    assert leaked == [], f"outside bytes reached the Vault at: {leaked}"
