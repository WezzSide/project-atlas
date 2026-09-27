"""Governed recovery tests for already-captured Vault events."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import atlas_agent
import capture_event
import normalize_event
import pytest
from agent_control import event_client, event_reprocess, session, skill_loader

ROOT = Path(__file__).resolve().parents[1]
MOCK_MDA = ROOT / "tests" / "fixtures" / "bin" / "mda"
SESSION_ID = "AS-20260927T090000Z-cli-project-atlas-aabbccdd"
SKILL_HASH = skill_loader.load(ROOT / "skills" / "atlas-governed-work").sha256


def _raw(
    vault: Path,
    event_id: str,
    *,
    session_id: str = SESSION_ID,
    skill_hash: str = SKILL_HASH,
    work_package: str = "AS-CTRL-EVENT-REPROCESS-001",
) -> Path:
    args = [
        "--vault",
        str(vault),
        "--project-id",
        "PRJ-PROJECT-ATLAS",
        "--project-slug",
        "project-atlas",
        "--event-kind",
        "implementation",
        "--summary",
        "reprocess fixture",
        "--agent",
        "cli",
        "--adapter-id",
        "generic-cli-v1",
        "--skill-id",
        "atlas-governed-work",
        "--skill-version",
        "1.0.0",
        "--skill-sha256",
        skill_hash,
        "--session-id",
        session_id,
        "--work-package",
        work_package,
        "--occurred-at",
        "2026-09-27T09:00:00Z",
        "--event-id",
        event_id,
    ]
    assert capture_event.main(args) == 0
    matches = list((vault / "sources" / "agent-events").glob(f"*/*/*/{event_id}.md"))
    assert len(matches) == 1
    return matches[0]


def _state(vault: Path, event_ids: list[str], *, captured: int | None = None) -> dict[str, Any]:
    (vault / ".atlas").mkdir(parents=True, exist_ok=True)
    (vault / ".atlas" / "vault.json").write_text(
        json.dumps({"schema_version": 1, "vault_id": "atlas-main", "vault_uuid": "fixture-uuid"}),
        encoding="utf-8",
    )
    state: dict[str, Any] = {
        "schema_version": 1,
        "session": {
            "session_id": SESSION_ID,
            "project_id": "project-atlas",
            "task_id": "AS-CTRL-EVENT-REPROCESS-001",
        },
        "agent": {"agent_id": "cli", "adapter_id": "generic-cli-v1"},
        "skill": {"id": "atlas-governed-work", "version": "1.0.0", "sha256": SKILL_HASH},
        "skill_acknowledgement": {
            "skill_id": "atlas-governed-work",
            "version": "1.0.0",
            "sha256": SKILL_HASH,
            "acknowledged_at": "2026-09-27T09:00:00Z",
            "session_id": SESSION_ID,
            "agent_id": "cli",
            "adapter_id": "generic-cli-v1",
        },
        "capability": {"ready": True},
        "vault": {"root": str(vault), "vault_id": "atlas-main", "vault_uuid": "fixture-uuid"},
        "preflight": {"ok": True, "readiness": {"authorized": True}, "spool": {"mode": False}},
        "events": {"implementation": list(event_ids)},
        "pipeline": {
            "captured": len(event_ids) if captured is None else captured,
            "normalized": 0,
            "verified": 0,
            "routed": 0,
            "pending_spool": 0,
        },
        "status": "active",
    }
    session.save(vault, state)
    return state


def _event_id(index: int) -> str:
    return f"AE-20260927T0900{index:02d}Z-project-atlas-rp{index:02d}"


def _run(
    vault: Path, ids: list[str], *, captured: int | None = None, mda_command: str | None = None
) -> dict[str, Any]:
    for event_id in ids:
        _raw(vault, event_id)
    _state(vault, ids, captured=captured)
    return event_reprocess.reprocess(
        vault_root=vault,
        session_id=SESSION_ID,
        mda_command=mda_command or str(MOCK_MDA),
    )


def test_captured_only_reconciles_through_normalizer_and_router(vault: Path) -> None:
    result = _run(vault, [_event_id(1)])
    assert result["ok"] is True
    assert result["pipeline"] == {
        "captured": 1,
        "normalized": 1,
        "verified": 1,
        "routed": 1,
        "pending_spool": 0,
    }
    assert result["events"][0]["normalization"] == "new"
    assert result["events"][0]["raw_sha256"]


def test_multiple_events_process_once_and_replay_is_idempotent(vault: Path) -> None:
    ids = [_event_id(i) for i in range(1, 6)]
    first = _run(vault, ids)
    raw_files = [
        path
        for path in (vault / "sources" / "agent-events").rglob("*.md")
        if ".restructured." not in path.name
    ]
    normalized_files = list((vault / "sources" / "agent-events").rglob("*.restructured.md"))
    routed_receipts = sorted(
        path.relative_to(vault)
        for path in (vault / "routing" / "receipts").rglob("*")
        if path.is_file()
    )
    second = event_reprocess.reprocess(
        vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
    )
    routed_after_replay = sorted(
        path.relative_to(vault)
        for path in (vault / "routing" / "receipts").rglob("*")
        if path.is_file()
    )
    assert len(raw_files) == 5
    assert len(normalized_files) == 5
    assert first["pipeline"] == second["pipeline"]
    assert routed_after_replay == routed_receipts
    assert [item["normalization"] for item in second["events"]] == ["reused"] * 5
    assert all(item["route_status"] for item in second["events"])


def test_existing_normalized_unrouted_event_is_reused(vault: Path) -> None:
    raw = _raw(vault, _event_id(1))
    _state(vault, [_event_id(1)])
    assert (
        normalize_event.main(
            [
                "--event",
                str(raw),
                "--root",
                str(vault),
                "--mda-command",
                str(MOCK_MDA),
                "--skill-dir",
                str(ROOT),
                "--skill",
                "atlas-governed-work",
                "--json",
            ]
        )
        == 0
    )
    result = event_reprocess.reprocess(
        vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
    )
    assert result["events"][0]["normalization"] == "reused"
    assert result["pipeline"]["routed"] == 1


def test_known_verification_failure_is_preserved_and_reprocessed(vault: Path) -> None:
    event_id = _event_id(1)
    raw = _raw(vault, event_id)
    _state(vault, [event_id])
    output = event_reprocess._expected_output(vault, raw, event_id, str(MOCK_MDA))
    rejected_content = "The earlier provider output was missing required Atlas provenance.\n"
    output.write_text(rejected_content, encoding="utf-8")
    raw.with_name(f"{raw.stem}.normalization-failed.json").write_text(
        json.dumps(
            {
                "type": "normalization-failure",
                "event_id": event_id,
                "category": "verification-failed",
                "message": "normalized output failed verification",
            }
        ),
        encoding="utf-8",
    )

    result = event_reprocess.reprocess(
        vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
    )

    preserved = list(output.parent.glob(f"{output.name}.invalid-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == rejected_content
    assert output.is_file()
    assert result["events"][0]["normalization"] == "new"
    assert result["pipeline"]["captured"] == 1
    assert result["pipeline"]["normalized"] == 1
    assert result["pipeline"]["verified"] == 1
    assert result["pipeline"]["routed"] == 1


def test_failure_after_normalization_leaves_counters_and_is_resumable(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [_event_id(1), _event_id(2)]
    for event_id in ids:
        _raw(vault, event_id)
    _state(vault, ids)
    real_run = subprocess.run
    failed = False

    def fail_first_route(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal failed
        if not failed and str(args[1]).endswith("route_event.py"):
            failed = True
            return subprocess.CompletedProcess(args, 5, "", "injected route failure")
        return real_run(args, **kwargs)

    monkeypatch.setattr(event_reprocess, "_run_command", fail_first_route)
    with pytest.raises(event_reprocess.EventReprocessError, match="ROUTE_FAILED"):
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert session.load(vault, SESSION_ID)["pipeline"] == {
        "captured": 2,
        "normalized": 0,
        "verified": 0,
        "routed": 0,
        "pending_spool": 0,
    }
    normalized = list((vault / "sources" / "agent-events").rglob("*.restructured.md"))
    assert len(normalized) == 1

    monkeypatch.setattr(event_reprocess, "_run_command", real_run)
    recovered = event_reprocess.reprocess(
        vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
    )
    assert recovered["pipeline"]["routed"] == 2
    assert recovered["events"][0]["normalization"] == "reused"


def test_mda_failure_mid_batch_keeps_accounting_closed_only_after_replay(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [_event_id(1), _event_id(2), _event_id(3)]
    for event_id in ids:
        _raw(vault, event_id)
    _state(vault, ids)
    real_run = subprocess.run
    failed = False

    def fail_second_normalization(
        args: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        nonlocal failed
        if (
            not failed
            and str(args[1]).endswith("normalize_event.py")
            and "--dry-run" not in args
            and any(_event_id(2) in str(arg) for arg in args)
        ):
            failed = True
            return subprocess.CompletedProcess(args, 4, "", "injected MDA failure")
        return real_run(args, **kwargs)

    monkeypatch.setattr(event_reprocess, "_run_command", fail_second_normalization)
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == "NORMALIZATION_FAILED"
    assert session.load(vault, SESSION_ID)["pipeline"]["normalized"] == 0
    assert (
        len([path for path in (vault / "sources" / "agent-events").rglob("*.restructured.md")]) == 1
    )

    monkeypatch.setattr(event_reprocess, "_run_command", real_run)
    recovered = event_reprocess.reprocess(
        vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
    )
    assert recovered["pipeline"]["normalized"] == 3
    assert recovered["events"][0]["normalization"] == "reused"


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("wrong-session", "SESSION_EVENT_BINDING_MISMATCH"),
        ("wrong-skill", "SESSION_EVENT_BINDING_MISMATCH"),
        ("invalid-raw", "RAW_EVENT_INVALID"),
    ],
)
def test_invalid_or_mismatched_raw_event_fails_closed(
    vault: Path, mutation: str, code: str
) -> None:
    event_id = _event_id(1)
    raw = _raw(
        vault,
        event_id,
        session_id="AS-20260927T091000Z-other-project-atlas-deadbeef"
        if mutation == "wrong-session"
        else SESSION_ID,
        skill_hash="b" * 64 if mutation == "wrong-skill" else SKILL_HASH,
    )
    _state(vault, [event_id])
    if mutation == "invalid-raw":
        raw.write_text("not frontmatter\n", encoding="utf-8")
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == code


def test_unregistered_event_is_untouched_and_missing_raw_fails_closed(vault: Path) -> None:
    registered = _event_id(1)
    extra = _event_id(2)
    _raw(vault, registered)
    extra_path = _raw(vault, extra, session_id="AS-20260927T091000Z-other-project-atlas-deadbeef")
    _state(vault, [registered])
    result = event_reprocess.reprocess(
        vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
    )
    assert [item["event_id"] for item in result["events"]] == [registered]
    assert not extra_path.with_name(extra_path.stem + ".restructured.md").exists()

    extra_path.unlink()
    raw = next((vault / "sources" / "agent-events").rglob(f"{registered}.md"))
    raw.unlink()
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == "RAW_EVENT_MISSING"


def test_pending_spool_and_accounting_mismatch_do_not_run_commands(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id = _event_id(1)
    _raw(vault, event_id)
    state = _state(vault, [event_id])
    state["pipeline"]["pending_spool"] = 1
    session.save(vault, state)
    monkeypatch.setattr(
        event_reprocess,
        "_run_command",
        lambda *args, **kwargs: pytest.fail("subprocess must not run"),
    )
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == "PENDING_SPOOL"

    state["pipeline"]["pending_spool"] = 0
    state["pipeline"]["captured"] = 2
    session.save(vault, state)
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == "SESSION_EVENT_ACCOUNTING_MISMATCH"


@pytest.mark.parametrize(
    "field,code",
    [
        ("capability", "CAPABILITY_NOT_READY"),
        ("preflight", "PREFLIGHT_NOT_READY"),
        ("skill_acknowledgement", "SKILL_ACKNOWLEDGEMENT_INVALID"),
        ("session", "SESSION_CONTEXT_INVALID"),
    ],
)
def test_current_governed_session_gate_is_required(vault: Path, field: str, code: str) -> None:
    event_id = _event_id(1)
    _raw(vault, event_id)
    state = _state(vault, [event_id])
    if field == "capability":
        state[field]["ready"] = False
    elif field == "preflight":
        state[field]["readiness"]["authorized"] = False
    elif field == "session":
        state[field] = None
    else:
        state[field]["acknowledged_at"] = "not-a-timestamp"
    if field == "session":
        session.path(vault, SESSION_ID).write_text(json.dumps(state), encoding="utf-8")
    else:
        session.save(vault, state)
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == code


def test_ambiguous_raw_event_fails_closed(vault: Path) -> None:
    event_id = _event_id(1)
    raw = _raw(vault, event_id)
    _state(vault, [event_id])
    duplicate = vault / "sources" / "agent-events" / "2026" / "09" / "28" / raw.name
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(raw.read_bytes())
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == "RAW_EVENT_AMBIGUOUS"


@pytest.mark.parametrize(
    "change,code",
    [
        ("wrong-vault", "SESSION_VAULT_BINDING_MISMATCH"),
        ("superseded", "SESSION_SUPERSEDED"),
        ("nested-superseded", "SESSION_SUPERSEDED"),
    ],
)
def test_wrong_or_superseded_session_context_fails_closed(
    vault: Path, change: str, code: str
) -> None:
    event_id = _event_id(1)
    _raw(vault, event_id)
    state = _state(vault, [event_id])
    if change == "wrong-vault":
        state["vault"]["vault_uuid"] = "wrong-uuid"
    elif change == "superseded":
        state["session"]["superseded_by"] = "AS-20260927T091000Z-cli-project-atlas-deadbeef"
    else:
        state["session"]["status"] = "superseded"
    session.save(vault, state)
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == code


def test_unsafe_session_path_is_rejected_before_load(vault: Path) -> None:
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id="../../outside", mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == "SESSION_ID_INVALID"


def test_invalid_vault_root_is_rejected_without_creating_lock_or_directory(
    tmp_path: Path,
) -> None:
    uninitialized = tmp_path / "not-a-vault"
    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=uninitialized,
            session_id=SESSION_ID,
            mda_command=str(MOCK_MDA),
        )
    assert exc.value.code == "VAULT_IDENTITY_INVALID"
    assert not uninitialized.exists()


def test_raw_event_must_remain_under_its_canonical_occurred_date(vault: Path) -> None:
    event_id = _event_id(1)
    raw = _raw(vault, event_id)
    _state(vault, [event_id])
    moved = vault / "sources" / "agent-events" / "2026" / "10" / "01" / raw.name
    moved.parent.mkdir(parents=True)
    raw.replace(moved)

    with pytest.raises(event_reprocess.EventReprocessError) as exc:
        event_reprocess.reprocess(
            vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
        )
    assert exc.value.code == "RAW_EVENT_PATH_MISMATCH"


def test_public_command_has_no_arbitrary_event_path_and_never_issues_receipt(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event_id = _event_id(1)
    _raw(vault, event_id)
    _state(vault, [event_id])
    capsys.readouterr()
    monkeypatch.setattr(
        event_reprocess,
        "reprocess",
        lambda **kwargs: {"ok": True, "receipt_issued": False, "session_completed": False},
    )
    assert (
        atlas_agent.main(
            [
                "reprocess-events",
                "--vault-root",
                str(vault),
                "--session-id",
                SESSION_ID,
                "--mda-command",
                str(MOCK_MDA),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["receipt_issued"] is False
    assert result["session_completed"] is False
    assert "receipt_id" not in session.load(vault, SESSION_ID)

    with pytest.raises(SystemExit):
        atlas_agent.main(
            [
                "reprocess-events",
                "--vault-root",
                str(vault),
                "--session-id",
                SESSION_ID,
                "--mda-command",
                str(MOCK_MDA),
                "--event-path",
                str(raw := next((vault / "sources" / "agent-events").rglob(f"{event_id}.md"))),
            ]
        )
    assert raw.is_file()


def test_same_vault_document_path_still_captures_normally(vault: Path) -> None:
    _state(vault, [])
    result = event_client.document(
        vault_root=vault,
        session_id=SESSION_ID,
        event_type="validation",
        summary="normal path regression",
    )
    assert result["event_id"] in session.load(vault, SESSION_ID)["events"]["validation"]
    assert session.load(vault, SESSION_ID)["pipeline"]["captured"] == 1


def test_document_persists_capture_before_provider_failure(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _state(vault, [])
    monkeypatch.setenv("ATLAS_MDA_COMMAND", "mda")
    real_run = subprocess.run

    def fail_normalization(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if str(args[1]).endswith("normalize_event.py"):
            return subprocess.CompletedProcess(args, 4, "", "provider unavailable")
        return real_run(args, **kwargs)

    monkeypatch.setattr(event_client.subprocess, "run", fail_normalization)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        event_client.document(
            vault_root=vault,
            session_id=SESSION_ID,
            event_type="implementation",
            summary="capture survives MDA failure",
        )
    saved = session.load(vault, SESSION_ID)
    assert saved["pipeline"]["captured"] == 1
    assert saved["pipeline"]["normalized"] == 0
    assert len(saved["events"]["implementation"]) == 1
    event_id = saved["events"]["implementation"][0]
    assert len(list((vault / "sources" / "agent-events").glob(f"*/*/*/{event_id}.md"))) == 1


def test_windows_lock_fallback_is_exclusive_and_released(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins
    import types

    calls: list[tuple[int, int]] = []
    fake_msvcrt = types.SimpleNamespace(
        LK_LOCK=1, LK_UNLCK=2, locking=lambda fd, mode, count: calls.append((mode, count))
    )
    real_import = builtins.__import__

    def import_without_fcntl(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fcntl":
            raise ImportError("simulate Windows")
        if name == "msvcrt":
            return fake_msvcrt
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_fcntl)
    with event_client._normalization_lock(vault):
        assert (vault / ".atlas-normalization.lock").read_bytes() == b"\0"
    assert calls == [(fake_msvcrt.LK_LOCK, 1), (fake_msvcrt.LK_UNLCK, 1)]


def test_other_session_event_is_not_selected(vault: Path) -> None:
    target = _event_id(1)
    other = _event_id(2)
    _raw(vault, target)
    other_raw = _raw(vault, other, session_id="AS-20260927T091000Z-other-project-atlas-deadbeef")
    _state(vault, [target])
    result = event_reprocess.reprocess(
        vault_root=vault, session_id=SESSION_ID, mda_command=str(MOCK_MDA)
    )
    assert result["pipeline"]["captured"] == 1
    assert not other_raw.with_name(other_raw.stem + ".restructured.md").exists()
