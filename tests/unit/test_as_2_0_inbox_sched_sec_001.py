"""AS-2.0-INBOX/SCHED/SEC-001 tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.knowledge_inbox import (
    KnowledgeInboxError,
    build_knowledge_inbox_receipt,
)
from project_atlas.scheduler_dry_run import (
    SchedulerDryRunError,
    build_scheduler_dry_run,
)
from project_atlas.schema import available_schemas, validate_record
from project_atlas.security_continuous import (
    SecurityContinuousError,
    build_security_continuous_receipt,
)


def test_inbox(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_knowledge_inbox_receipt(vault, record_id="in-1", item_count=2)
    assert report["promoted_to_authority"] is False
    validate_record(report, "knowledge-inbox-receipt")


def test_inbox_reject_promote(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(KnowledgeInboxError, match="authority-promote-forbidden"):
        build_knowledge_inbox_receipt(vault, record_id="in-1", promote_authority=True)


def test_sched(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_scheduler_dry_run(vault, record_id="plan-1")
    assert report["live_dispatch"] is False
    validate_record(report, "scheduler-dry-run")


def test_sched_reject_live(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(SchedulerDryRunError, match="live-dispatch-forbidden"):
        build_scheduler_dry_run(vault, record_id="plan-1", enable_live_dispatch=True)


def test_sec(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_security_continuous_receipt(vault, record_id="sec-1", findings_count=0)
    assert report["matched_content_logged"] is False
    validate_record(report, "security-continuous-receipt")


def test_sec_reject_log(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(SecurityContinuousError, match="matched-content-log-forbidden"):
        build_security_continuous_receipt(vault, record_id="sec-1", log_matched_content=True)


def test_docs() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("AS-2.0-INBOX-001.md", "AS-2.0-SCHED-001.md", "AS-2.0-SEC-001.md"):
        assert (root / "docs" / name).is_file()
    for kind in (
        "knowledge-inbox-receipt",
        "scheduler-dry-run",
        "security-continuous-receipt",
    ):
        assert kind in available_schemas()
