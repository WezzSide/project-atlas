"""AS-CTRL-001 bootstrap, drift and receipt-gate tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from agent_control import agent_identity, bootstrap, capability, event_client, preflight, receipt_gate, session, skill_ack, skill_compiler, skill_loader

ROOT = Path(__file__).resolve().parent.parent


def _readiness_registry(path: Path, *, skill_version: str, skill_sha256: str, adapters: tuple[str, ...] = ("generic-cli-v1", "ide-agent-v1", "background-agent-v1", "remote-agent-v1")) -> Path:
    lines = ["schema_version: 1", "adapters:"]
    for adapter_id in adapters:
        lines.extend(
            [
                f"  {adapter_id}:",
                f"    skill_version: {skill_version}",
                f"    skill_sha256: {skill_sha256}",
                "    rehearsal_status: passed",
                "    revoked: false",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _project_and_vault(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    vault = tmp_path / "vault"
    (project / ".atlas").mkdir(parents=True)
    skill = skill_loader.load(ROOT / "skill")
    registry = _readiness_registry(tmp_path / "readiness.yaml", skill_version=skill.version, skill_sha256=skill.sha256)
    (project / ".atlas" / "project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        "  id: control-fixture\n"
        "  name: Control Fixture\n"
        "documentation:\n"
        "  require_receipt: true\n"
        "  strict: true\n"
        f"  readiness_registry: {registry}\n"
        "vault:\n"
        "  required_vault_id: atlas-main\n",
        encoding="utf-8",
    )
    (vault / ".atlas").mkdir(parents=True)
    (vault / ".atlas" / "vault.json").write_text(json.dumps({"schema_version": 1, "vault_id": "atlas-main", "vault_uuid": "fixture-uuid"}), encoding="utf-8")
    return project, vault


def test_skill_hash_and_generated_adapter_drift_are_enforced(tmp_path: Path) -> None:
    skill = skill_loader.load(ROOT / "skill")
    output = tmp_path / "adapters"
    skill_compiler.generate(skill, output)
    assert skill_compiler.verify(skill, output) == []
    target = output / "generic" / "ATLAS-INSTRUCTIONS.md"
    target.write_text(target.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
    assert skill_compiler.verify(skill, output)


def test_preflight_rejects_wrong_vault_and_accepts_verified_identity(tmp_path: Path) -> None:
    project, vault = _project_and_vault(tmp_path)
    report = preflight.run(project_root=project, vault_root=vault, agent_type="ide", agent_value="ide-fixture", skill_root=ROOT / "skill")
    assert report["ok"]
    wrong = tmp_path / "wrong"
    (wrong / ".atlas").mkdir(parents=True)
    (wrong / ".atlas" / "vault.json").write_text(json.dumps({"vault_id": "other", "vault_uuid": "wrong"}), encoding="utf-8")
    with pytest.raises(ValueError, match="wrong Atlas Vault ID"):
        preflight.run(project_root=project, vault_root=wrong, agent_type="generic", agent_value=None, skill_root=ROOT / "skill")


def test_managed_session_requires_validation_and_completion_then_issues_receipt(tmp_path: Path) -> None:
    project, vault = _project_and_vault(tmp_path)
    state, _environment = bootstrap.start(project_root=project, vault_root=vault, agent_type="generic", agent_value="agent-fixture", task_id="AS-CTRL-001", skill_root=ROOT / "skill")
    sid = str(state["session"]["session_id"])
    event_client.document(vault_root=vault, session_id=sid, event_type="session-start", summary="Session started")
    assert not receipt_gate.validate(__import__("agent_control.session", fromlist=["load"]).load(vault, sid)) == []
    event_client.document(vault_root=vault, session_id=sid, event_type="implementation", summary="Implemented control fixture")
    event_client.document(vault_root=vault, session_id=sid, event_type="validation", summary="Validated control fixture")
    event_client.document(vault_root=vault, session_id=sid, event_type="completion", summary="Completed control fixture")
    final_state = __import__("agent_control.session", fromlist=["load"]).load(vault, sid)
    final_state["pipeline"].update(normalized=5, verified=5, routed=5)
    receipt = receipt_gate.issue(vault, final_state)
    assert receipt["skill"]["verified"] is True
    assert receipt["is_authority"] is False
    assert receipt["receipt_is_authority"] is False
    assert receipt["authority_role"] == "evidence-only"
    assert (vault / ".atlas" / "receipts" / f"{receipt['receipt_id']}.json").is_file()


def test_spool_pending_events_fail_strict_completion(tmp_path: Path) -> None:
    project, vault = _project_and_vault(tmp_path)
    state, _environment = bootstrap.start(project_root=project, vault_root=vault, agent_type="generic", agent_value="offline-agent", task_id="AS-CTRL-001", skill_root=ROOT / "skill")
    sid = str(state["session"]["session_id"])
    event_client.document(vault_root=vault, session_id=sid, event_type="session-start", summary="Session started", spool=True)
    event_client.document(vault_root=vault, session_id=sid, event_type="validation", summary="Validation", spool=True)
    event_client.document(vault_root=vault, session_id=sid, event_type="completion", summary="Completion", spool=True)
    current = __import__("agent_control.session", fromlist=["load"]).load(vault, sid)
    assert any("pending spool" in error for error in receipt_gate.validate(current))


def test_offline_preflight_uses_approved_spool_when_vault_is_unavailable(tmp_path: Path) -> None:
    project, _vault = _project_and_vault(tmp_path)
    report = preflight.run(project_root=project, vault_root=None, agent_type="remote", agent_value="remote-fixture", skill_root=ROOT / "skill")
    assert report["ok"]
    assert report["spool"]["mode"] is True
    assert Path(str(report["spool"]["root"])).is_dir()


def test_two_managed_sessions_receive_distinct_ids(tmp_path: Path) -> None:
    project, vault = _project_and_vault(tmp_path)
    def start(index: int) -> str:
        state, _ = bootstrap.start(project_root=project, vault_root=vault, agent_type="background", agent_value=f"worker-{index}", task_id="AS-CTRL-001", skill_root=ROOT / "skill")
        return str(state["session"]["session_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(start, (1, 2)))
    assert len(set(ids)) == 2


def test_agent_and_session_ids_are_unique_and_safe() -> None:
    first = agent_identity.session_id("generic", "control-fixture")
    second = agent_identity.session_id("generic", "control-fixture")
    assert first != second
    with pytest.raises(ValueError):
        agent_identity.agent_id("bad agent", "generic")


def test_governed_skill_requires_ack_capability_and_records_real_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    vault = tmp_path / "vault"
    (project / ".atlas").mkdir(parents=True)
    registry = tmp_path / "readiness.yaml"
    registry.write_text("schema_version: 1\nadapters:\n  generic-cli-v1:\n    skill_version: 1.0.0\n    skill_sha256: 2d8eb525631e27800ffac120b5a79ac712fad58489879d96a3ad535cf8da4123\n    rehearsal_status: passed\n    revoked: false\n", encoding="utf-8")
    (project / ".atlas" / "project.yaml").write_text(f"schema_version: 1\nproject:\n  id: control-fixture\n  name: Control Fixture\ndocumentation:\n  skill_id: atlas-governed-work\n  readiness_registry: {registry}\n  require_receipt: true\n  strict: true\nvault:\n  required_vault_id: atlas-main\n", encoding="utf-8")
    (vault / ".atlas").mkdir(parents=True)
    (vault / ".atlas" / "vault.json").write_text(json.dumps({"schema_version": 1, "vault_id": "atlas-main", "vault_uuid": "fixture-uuid"}), encoding="utf-8")
    monkeypatch.setenv("ATLAS_MDA_COMMAND", str(ROOT / "tests" / "fixtures" / "bin" / "mda"))
    state, _ = bootstrap.start(project_root=project, vault_root=vault, agent_type="generic", agent_value="governed-agent", task_id="AS-SKILL-001", skill_root=ROOT / "skills" / "atlas-governed-work")
    sid = str(state["session"]["session_id"])
    assert receipt_gate.validate(session.load(vault, sid))
    skill = session.load(vault, sid)["skill"]
    skill_ack.acknowledge(vault, sid, skill["id"], skill["version"], skill["sha256"])
    assert capability.check(vault, sid)["ready"]
    event_client.document(vault_root=vault, session_id=sid, event_type="implementation", summary="Implemented governed lifecycle")
    event_client.document(vault_root=vault, session_id=sid, event_type="validation", summary="Lifecycle validation passed")
    event_client.document(vault_root=vault, session_id=sid, event_type="completion", summary="Governed lifecycle completed")
    final = session.load(vault, sid)
    assert final["pipeline"]["normalized"] == final["pipeline"]["captured"]
    assert final["pipeline"]["verified"] == final["pipeline"]["captured"]
    assert final["pipeline"]["routed"] == final["pipeline"]["captured"]
    receipt = receipt_gate.issue(vault, final)
    assert receipt["skill"]["id"] == "atlas-governed-work"


def test_real_cli_rehearsal_and_negative_ack_gate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    vault = tmp_path / "vault"
    registry = tmp_path / "readiness.yaml"
    (project / ".atlas").mkdir(parents=True)
    (vault / ".atlas").mkdir(parents=True)
    (vault / ".atlas" / "vault.json").write_text(json.dumps({"vault_id": "atlas-rehearsal", "vault_uuid": "rehearsal-uuid"}), encoding="utf-8")
    skill = skill_loader.load(ROOT / "skills" / "atlas-governed-work")
    registry.write_text(f"schema_version: 1\nadapters:\n  generic-cli-v1:\n    skill_version: {skill.version}\n    skill_sha256: {skill.sha256}\n    rehearsal_status: passed\n    revoked: false\n", encoding="utf-8")
    (project / ".atlas" / "project.yaml").write_text(f"schema_version: 1\nproject:\n  id: governed-work-rehearsal\n  name: Governed Work Rehearsal\nvault:\n  required_vault_id: atlas-rehearsal\ndocumentation:\n  skill_id: atlas-governed-work\n  readiness_registry: {registry}\n  strict: true\n", encoding="utf-8")
    cli = str(ROOT / "scripts" / "atlas_agent.py")
    env = dict(os.environ, ATLAS_MDA_COMMAND=str(ROOT / "tests" / "fixtures" / "bin" / "mda"))

    def run(*args: str) -> dict[str, Any]:
        result = subprocess.run([sys.executable, cli, *args, "--json"], env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr or result.stdout
        return cast(dict[str, Any], json.loads(result.stdout))

    boot = run("bootstrap", "--project-root", str(project), "--vault-root", str(vault), "--task-id", "AS-SKILL-001")
    sid = str(boot["session"]["id"])
    missing_ack = subprocess.run([sys.executable, cli, "capability-check", "--vault-root", str(vault), "--session-id", sid, "--json"], env=env, capture_output=True, text=True, check=False)
    assert missing_ack.returncode != 0
    run("acknowledge-skill", "--vault-root", str(vault), "--session-id", sid)
    run("capability-check", "--vault-root", str(vault), "--session-id", sid)
    for event_type in ("implementation", "validation", "completion"):
        run("document", "--vault-root", str(vault), "--session-id", sid, "--type", event_type, "--summary", f"Rehearsal {event_type}")
    checked = run("validate", "--vault-root", str(vault), "--session-id", sid)
    assert checked["ok"] is True
    receipt = run("receipt", "--vault-root", str(vault), "--session-id", sid)
    assert receipt["receipt_type"] == "atlas-governed-work-rehearsal"
    postflight = run("postflight", "--vault-root", str(vault), "--session-id", sid)
    assert postflight["status"] == "complete"


def test_real_cli_offline_spool_syncs_once(tmp_path: Path) -> None:
    project = tmp_path / "project"
    vault = tmp_path / "vault"
    registry = tmp_path / "readiness.yaml"
    (project / ".atlas").mkdir(parents=True)
    (vault / ".atlas").mkdir(parents=True)
    (vault / ".atlas" / "vault.json").write_text(json.dumps({"vault_id": "atlas-rehearsal", "vault_uuid": "rehearsal-uuid"}), encoding="utf-8")
    skill = skill_loader.load(ROOT / "skills" / "atlas-governed-work")
    registry.write_text(f"schema_version: 1\nadapters:\n  generic-cli-v1:\n    skill_version: {skill.version}\n    skill_sha256: {skill.sha256}\n    rehearsal_status: passed\n    revoked: false\n", encoding="utf-8")
    (project / ".atlas" / "project.yaml").write_text(f"schema_version: 1\nproject:\n  id: governed-work-offline\n  name: Governed Work Offline\nvault:\n  required_vault_id: atlas-rehearsal\n  required_vault_uuid: rehearsal-uuid\ndocumentation:\n  skill_id: atlas-governed-work\n  readiness_registry: {registry}\n  strict: true\n", encoding="utf-8")
    cli = str(ROOT / "scripts" / "atlas_agent.py")
    env = dict(os.environ, ATLAS_MDA_COMMAND=str(ROOT / "tests" / "fixtures" / "bin" / "mda"))
    boot = subprocess.run([sys.executable, cli, "bootstrap", "--project-root", str(project), "--task-id", "AS-SKILL-001", "--json"], env=env, capture_output=True, text=True, check=False)
    assert boot.returncode == 0, boot.stderr
    sid = str(json.loads(boot.stdout)["session"]["id"])
    spool = project / ".atlas-spool"
    for command in (("acknowledge-skill",), ("capability-check",)):
        result = subprocess.run([sys.executable, cli, *command, "--vault-root", str(spool), "--session-id", sid, "--json"], env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    for event_type in ("implementation", "validation", "completion"):
        result = subprocess.run([sys.executable, cli, "document", "--vault-root", str(spool), "--session-id", sid, "--type", event_type, "--summary", f"Offline {event_type}", "--json"], env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    sync = subprocess.run([sys.executable, cli, "sync-spool", "--spool-root", str(project), "--vault-root", str(vault), "--mda-command", env["ATLAS_MDA_COMMAND"], "--json"], env=env, capture_output=True, text=True, check=False)
    assert sync.returncode == 0, sync.stderr or sync.stdout
    payload = json.loads(sync.stdout)
    assert payload["spool"]["synchronized"] == 4
    assert payload["pipeline"]["pending_spool"] == 0
    assert payload["receipt_id"]
    assert list((vault / ".atlas" / "receipts").glob("ASR-*.json"))
    assert not list((project / ".atlas-spool").glob("AE-*.md"))
    replay = subprocess.run([sys.executable, cli, "sync-spool", "--spool-root", str(project), "--vault-root", str(vault), "--mda-command", env["ATLAS_MDA_COMMAND"], "--json"], env=env, capture_output=True, text=True, check=False)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["spool"]["synchronized"] == 0


def test_managed_launcher_automates_ack_capability_and_postflight(tmp_path: Path) -> None:
    project = tmp_path / "project"
    vault = tmp_path / "vault"
    (project / ".atlas").mkdir(parents=True)
    (vault / ".atlas").mkdir(parents=True)
    (vault / ".atlas" / "vault.json").write_text(json.dumps({"vault_id": "atlas-main", "vault_uuid": "managed-uuid"}), encoding="utf-8")
    registry = ROOT / "config" / "agent-readiness.yaml"
    certification = ROOT / "AS-SKILL-001-CERTIFICATION-RECEIPT.yaml"
    (project / ".atlas" / "project.yaml").write_text(f"schema_version: 1\nproject:\n  id: project-atlas\n  name: Managed Fixture\nvault:\n  required_vault_id: atlas-main\n  required_vault_uuid: managed-uuid\ndocumentation:\n  skill_id: atlas-governed-work\n  readiness_registry: {registry}\n  skill_certification: {certification}\n  strict: true\n", encoding="utf-8")
    child = tmp_path / "child.py"
    child.write_text("import os, subprocess, sys\ncli = os.environ['ATLAS_CLI']\nfor kind in ('implementation', 'validation', 'completion'):\n    result = subprocess.run([sys.executable, cli, 'document', '--vault-root', os.environ['ATLAS_VAULT_ROOT'], '--session-id', os.environ['ATLAS_SESSION_ID'], '--type', kind, '--summary', 'managed ' + kind, '--json'], check=False)\n    if result.returncode:\n        raise SystemExit(result.returncode)\n", encoding="utf-8")
    env = dict(os.environ, ATLAS_MDA_COMMAND=str(ROOT / "tests" / "fixtures" / "bin" / "mda"), ATLAS_CLI=str(ROOT / "scripts" / "atlas_agent.py"))
    def run_agent(index: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "atlas_agent.py"), "run", "--project-root", str(project), "--vault-root", str(vault), "--agent", "generic", "--agent-id", f"managed-agent-{index}", "--task-id", f"AS-CTRL-001-CERT-{index}", "--", sys.executable, str(child)], env=env, capture_output=True, text=True, check=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        # Windows FS verification races when two agents normalize into one vault
        # directory concurrently; serialize there, keep parallel coverage on POSIX.
        if sys.platform == "win32":
            results = [run_agent(1), run_agent(2)]
        else:
            results = list(pool.map(run_agent, (1, 2)))
    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    receipts = list((vault / ".atlas" / "receipts").glob("ASR-*.json"))
    assert len(receipts) == 2
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in receipts]
    assert len({payload["session"]["session_id"] for payload in payloads}) == 2
    assert all(payload["events"]["session-start"] for payload in payloads)
    assert all(payload["skill"]["sha256"] == skill_loader.load(ROOT / "skills" / "atlas-governed-work").sha256 for payload in payloads)


def test_document_ignores_vault_sibling_capture_script(tmp_path: Path) -> None:
    """AS-CTRL-CAPTURE-SCRIPT-HIJACK-001: certified capture_event.py only."""
    project, vault = _project_and_vault(tmp_path)
    planted = vault.parent / "atlas-vault-documentation" / "scripts" / "capture_event.py"
    planted.parent.mkdir(parents=True)
    marker = tmp_path / "PLANTED_RAN"
    planted.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
        "print(json.dumps({'ok': True, 'event_id': 'AE-HIJACK', 'path': '/tmp/fake.md'}))\n",
        encoding="utf-8",
    )
    state, _environment = bootstrap.start(
        project_root=project,
        vault_root=vault,
        agent_type="generic",
        agent_value="agent-fixture",
        task_id="AS-CTRL-001",
        skill_root=ROOT / "skill",
    )
    sid = str(state["session"]["session_id"])
    payload = event_client.document(
        vault_root=vault,
        session_id=sid,
        event_type="implementation",
        summary="Implemented control fixture",
    )
    assert payload.get("event_id") != "AE-HIJACK"
    assert not marker.exists()
    loaded = session.load(vault, sid)
    assert "AE-HIJACK" not in loaded.get("events", {}).get("implementation", [])
    assert "AE-HIJACK" not in loaded.get("events", {}).get("session-start", [])


def test_repository_gate_rejects_missing_receipt_and_protected_write(tmp_path: Path) -> None:
    cli = str(ROOT / "scripts" / "atlas_agent.py")
    missing = subprocess.run([sys.executable, cli, "repository-gate", "--project-id", "managed-fixture", "--changed-file", "src/example.py", "--json"], capture_output=True, text=True, check=False)
    assert missing.returncode != 0
    protected = subprocess.run([sys.executable, cli, "repository-gate", "--project-id", "managed-fixture", "--changed-file", "projects/managed-fixture/events/AE-1.md", "--json"], capture_output=True, text=True, check=False)
    assert protected.returncode != 0
