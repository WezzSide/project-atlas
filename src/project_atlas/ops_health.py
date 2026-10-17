"""AS-OBS-001 Operational Health Snapshot (collect → normalize → expose).

Read-oriented collectors consume receipts, quarantine indexes, readiness
registries, and optional ops evidence files. The only write surface is
``generated/ops/`` (regenerable, non-canonical). Collectors MUST NOT write
claims, temporal/current/authoritative state, projects/, or knowledge-query
caches. Health ≠ project authority / truth (OBS-001-FR-004/005).

Missing mandatory evidence → ``unknown`` / fail-closed — never fabricated
``ok`` or ``healthy`` (Unknown ≠ healthy).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text

SCHEMA_ID = "atlas.ops.health_snapshot.v1"
COLLECTOR_ID = "atlas.ops.health"
GENERATED_BY = "atlas-obs-001"
SNAPSHOT_RELATIVE = Path("generated") / "ops" / "health-snapshot.json"

HealthState = Literal["healthy", "degraded", "unhealthy", "unknown"]
SignalStatus = Literal["ok", "warn", "fail", "unknown"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
ScopeKind = Literal["estate", "project", "adapter", "skill", "run"]

# Contract §6 required signal set (AS-OBS-001 minimum).
REQUIRED_SIGNAL_IDS: tuple[str, ...] = (
    "OPS-SIG-001",
    "OPS-SIG-002",
    "OPS-SIG-003",
    "OPS-SIG-004",
    "OPS-SIG-005",
    "OPS-SIG-006",
    "OPS-SIG-008",
    "OPS-SIG-010",
    "OPS-SIG-011",
    "OPS-SIG-013",
    "OPS-SIG-014",
)

# Strongly recommended (same schema; evaluated when evidence present).
RECOMMENDED_SIGNAL_IDS: tuple[str, ...] = (
    "OPS-SIG-007",
    "OPS-SIG-009",
    "OPS-SIG-012",
)

ALL_SIGNAL_IDS: tuple[str, ...] = tuple(
    sorted(set(REQUIRED_SIGNAL_IDS) | set(RECOMMENDED_SIGNAL_IDS))
)

_QUARANTINE_WARN_THRESHOLD = 0
_QUARANTINE_FAIL_THRESHOLD = 10


class OpsHealthError(ValueError):
    """Raised when the health snapshot cannot be emitted safely."""


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _read_yaml_mapping(path: Path) -> dict[str, Any] | None:
    """Best-effort YAML mapping load without requiring PyYAML at import time
    for absent files. Returns None when missing or unreadable."""
    if not path.is_file():
        return None
    try:
        import yaml  # local import keeps optional surface explicit
    except ImportError:  # pragma: no cover
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


def _vault_identity(vault: Path) -> dict[str, Any] | None:
    raw = _read_json(vault / ".atlas" / "vault.json", None)
    return raw if isinstance(raw, dict) else None


def _estate_id(vault: Path) -> str:
    identity = _vault_identity(vault)
    if identity is None:
        return "unknown"
    for key in ("vault_uuid", "vault_id"):
        value = identity.get(key)
        if isinstance(value, str) and value.strip():
            token = value.strip()
            # AS-SEC-SCAN-OPS-HEALTH-UUID-JSON-ESC-001: json.loads of
            # vault.json can decode \\u escapes that scan_text misses on raw
            # bytes. Do not persist a decoded secret as estate_id.
            if scan_text(token):
                return "unknown"
            return token
    return "unknown"


def _signal(
    *,
    signal_id: str,
    scope: ScopeKind,
    scope_id: str,
    status: SignalStatus,
    severity: Severity | None,
    observed_value: Any,
    threshold: dict[str, Any] | None,
    evidence_refs: list[str],
    observed_generation: str | None = None,
) -> dict[str, Any]:
    if status == "ok":
        severity = None
    return {
        "signal_id": signal_id,
        "scope": scope,
        "scope_id": scope_id,
        "status": status,
        "severity": severity,
        "observed_value": observed_value,
        "threshold": threshold,
        "evidence_refs": sorted(evidence_refs),
        "observed_generation": observed_generation,
        "collector": COLLECTOR_ID,
    }


def _rel(vault: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _count_pending_spool(vault: Path) -> tuple[int | None, list[str]]:
    """Return pending spool count, or None when no spool surface exists."""
    candidates = [
        vault / ".atlas-spool",
        vault / "routing" / "state" / "spool",
        vault / ".atlas" / "spool",
    ]
    existing = [path for path in candidates if path.is_dir()]
    if not existing:
        return None, []
    count = 0
    refs: list[str] = []
    for root in existing:
        refs.append(_rel(vault, root))
        for path in root.rglob("*"):
            if path.is_file() and path.name not in (".gitkeep", ".keep"):
                count += 1
    return count, refs


def _collect_pending_spool(vault: Path, estate_id: str) -> dict[str, Any]:
    count, refs = _count_pending_spool(vault)
    if count is None:
        return _signal(
            signal_id="OPS-SIG-001",
            scope="estate",
            scope_id=estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"pending_max": 0},
            evidence_refs=[],
        )
    if count == 0:
        status: SignalStatus = "ok"
        severity: Severity | None = None
    else:
        status = "fail"
        severity = "HIGH"
    return _signal(
        signal_id="OPS-SIG-001",
        scope="estate",
        scope_id=estate_id,
        status=status,
        severity=severity,
        observed_value=count,
        threshold={"pending_max": 0},
        evidence_refs=refs,
    )


def _sync_failure_paths(vault: Path) -> list[Path]:
    return [
        vault / "generated" / "ops" / "evidence" / "sync-failures.json",
        vault / "quarantine" / "sync-failures" / "index.json",
        vault / "generated" / "reports" / "sync-failures.json",
    ]


def _collect_failed_sync(
    vault: Path, estate_id: str, project_filter: str | None
) -> dict[str, Any]:
    scope: ScopeKind = "project" if project_filter else "estate"
    scope_id = project_filter or estate_id
    found_path: Path | None = None
    failures: list[Any] = []
    for path in _sync_failure_paths(vault):
        raw = _read_json(path, None)
        if raw is None:
            continue
        found_path = path
        if isinstance(raw, list):
            failures = raw
        elif isinstance(raw, dict):
            items = raw.get("failures", raw.get("items", []))
            failures = items if isinstance(items, list) else []
        break
    if found_path is None:
        return _signal(
            signal_id="OPS-SIG-002",
            scope=scope,
            scope_id=scope_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"unresolved_failures": 0},
            evidence_refs=[],
        )
    if project_filter:
        failures = [
            item
            for item in failures
            if isinstance(item, dict)
            and str(item.get("project_id") or item.get("scope_id") or "") == project_filter
        ]
    unresolved = [
        item
        for item in failures
        if not (isinstance(item, dict) and item.get("resolved") is True)
    ]
    if unresolved:
        return _signal(
            signal_id="OPS-SIG-002",
            scope=scope,
            scope_id=scope_id,
            status="fail",
            severity="HIGH",
            observed_value=len(unresolved),
            threshold={"unresolved_failures": 0},
            evidence_refs=[_rel(vault, found_path)],
        )
    return _signal(
        signal_id="OPS-SIG-002",
        scope=scope,
        scope_id=scope_id,
        status="ok",
        severity=None,
        observed_value=0,
        threshold={"unresolved_failures": 0},
        evidence_refs=[_rel(vault, found_path)],
    )


def _collect_last_sync(
    vault: Path, estate_id: str, project_filter: str | None
) -> dict[str, Any]:
    scope: ScopeKind = "project" if project_filter else "estate"
    scope_id = project_filter or estate_id
    path = vault / "generated" / "ops" / "evidence" / "last-sync.json"
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        return _signal(
            signal_id="OPS-SIG-003",
            scope=scope,
            scope_id=scope_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"freshness": "within_window_or_disabled"},
            evidence_refs=[],
        )
    if raw.get("disabled") is True:
        return _signal(
            signal_id="OPS-SIG-003",
            scope=scope,
            scope_id=scope_id,
            status="ok",
            severity=None,
            observed_value="disabled",
            threshold={"freshness": "within_window_or_disabled"},
            evidence_refs=[_rel(vault, path)],
            observed_generation=str(raw.get("generation")) if raw.get("generation") else None,
        )
    status_value = str(raw.get("status") or "").lower()
    if status_value in {"ok", "success", "fresh"}:
        return _signal(
            signal_id="OPS-SIG-003",
            scope=scope,
            scope_id=scope_id,
            status="ok",
            severity=None,
            observed_value=status_value,
            threshold={"freshness": "within_window_or_disabled"},
            evidence_refs=[_rel(vault, path)],
            observed_generation=str(raw.get("generation")) if raw.get("generation") else None,
        )
    if status_value in {"stale", "fail", "failed"}:
        return _signal(
            signal_id="OPS-SIG-003",
            scope=scope,
            scope_id=scope_id,
            status="fail",
            severity="MEDIUM",
            observed_value=status_value,
            threshold={"freshness": "within_window_or_disabled"},
            evidence_refs=[_rel(vault, path)],
            observed_generation=str(raw.get("generation")) if raw.get("generation") else None,
        )
    return _signal(
        signal_id="OPS-SIG-003",
        scope=scope,
        scope_id=scope_id,
        status="unknown",
        severity=None,
        observed_value=None,
        threshold={"freshness": "within_window_or_disabled"},
        evidence_refs=[_rel(vault, path)],
    )


def _collect_freshness(
    vault: Path, estate_id: str, project_filter: str | None
) -> dict[str, Any]:
    scope: ScopeKind = "project" if project_filter else "estate"
    scope_id = project_filter or estate_id
    path = vault / "generated" / "ops" / "evidence" / "freshness.json"
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        return _signal(
            signal_id="OPS-SIG-004",
            scope=scope,
            scope_id=scope_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"stale_when_sync_needed": True},
            evidence_refs=[],
        )
    stale = bool(raw.get("stale"))
    sync_needed = bool(raw.get("sync_needed", True))
    if stale and sync_needed:
        return _signal(
            signal_id="OPS-SIG-004",
            scope=scope,
            scope_id=scope_id,
            status="fail",
            severity="MEDIUM",
            observed_value="stale",
            threshold={"stale_when_sync_needed": True},
            evidence_refs=[_rel(vault, path)],
        )
    return _signal(
        signal_id="OPS-SIG-004",
        scope=scope,
        scope_id=scope_id,
        status="ok",
        severity=None,
        observed_value="fresh" if not stale else "stale_not_needed",
        threshold={"stale_when_sync_needed": True},
        evidence_refs=[_rel(vault, path)],
    )


def _collect_transaction_failures(vault: Path, estate_id: str) -> dict[str, Any]:
    path = vault / "quarantine" / "promotion-failures" / "index.json"
    raw = _read_json(path, None)
    if raw is None:
        # Absent evidence surface → unknown (OBS-001-FR-002); never fabricate ok.
        return _signal(
            signal_id="OPS-SIG-005",
            scope="estate",
            scope_id=estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"open_failures": 0},
            evidence_refs=[],
        )
    items: list[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        candidate = raw.get("failures", raw.get("items", []))
        items = candidate if isinstance(candidate, list) else []
    else:
        items = []
    open_failures = [
        item
        for item in items
        if not (isinstance(item, dict) and item.get("resolved") is True)
    ]
    if open_failures:
        return _signal(
            signal_id="OPS-SIG-005",
            scope="estate",
            scope_id=estate_id,
            status="fail",
            severity="HIGH",
            observed_value=len(open_failures),
            threshold={"open_failures": 0},
            evidence_refs=[_rel(vault, path)],
        )
    return _signal(
        signal_id="OPS-SIG-005",
        scope="estate",
        scope_id=estate_id,
        status="ok",
        severity=None,
        observed_value=0,
        threshold={"open_failures": 0},
        evidence_refs=[_rel(vault, path)],
    )


def _quarantine_count(vault: Path) -> tuple[int, list[str]]:
    refs: list[str] = []
    count = 0
    report_names = (
        "injection-findings.json",
        "secret-findings.json",
        "graph-quarantine.json",
    )
    for name in report_names:
        path = vault / "generated" / "reports" / name
        raw = _read_json(path, None)
        if raw is None:
            continue
        refs.append(_rel(vault, path))
        if isinstance(raw, list):
            count += len(raw)
        elif isinstance(raw, dict):
            findings = raw.get("findings", [])
            if isinstance(findings, list):
                count += len(findings)
    # Also count promotion-adjacent graph quarantine indexes if present.
    for path in (
        vault / "quarantine" / "graph" / "index.json",
        vault / "quarantine" / "agent-events" / "index.json",
    ):
        raw = _read_json(path, None)
        if raw is None:
            continue
        refs.append(_rel(vault, path))
        if isinstance(raw, list):
            count += len(raw)
        elif isinstance(raw, dict):
            items = raw.get("items", raw.get("findings", []))
            if isinstance(items, list):
                count += len(items)
    return count, refs


def _collect_quarantine(
    vault: Path, estate_id: str, project_filter: str | None
) -> dict[str, Any]:
    scope: ScopeKind = "project" if project_filter else "estate"
    scope_id = project_filter or estate_id
    count, refs = _quarantine_count(vault)
    threshold = {
        "warn_above": _QUARANTINE_WARN_THRESHOLD,
        "fail_above": _QUARANTINE_FAIL_THRESHOLD,
    }
    # No quarantine evidence surface observed → unknown (OBS-001-FR-002).
    if not refs:
        return _signal(
            signal_id="OPS-SIG-006",
            scope=scope,
            scope_id=scope_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold=threshold,
            evidence_refs=[],
        )
    if count > _QUARANTINE_FAIL_THRESHOLD:
        status: SignalStatus = "fail"
        severity: Severity | None = "HIGH"
    elif count > _QUARANTINE_WARN_THRESHOLD:
        status = "warn"
        severity = "MEDIUM"
    else:
        status = "ok"
        severity = None
    return _signal(
        signal_id="OPS-SIG-006",
        scope=scope,
        scope_id=scope_id,
        status=status,
        severity=severity,
        observed_value=count,
        threshold=threshold,
        evidence_refs=refs,
    )


def _collect_normalization(vault: Path, estate_id: str) -> dict[str, Any]:
    path = vault / "generated" / "reports" / "ingestion-report.json"
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        return _signal(
            signal_id="OPS-SIG-007",
            scope="estate",
            scope_id=estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"unknown_classifications_max": None},
            evidence_refs=[],
        )
    unknown = 0
    classifications = raw.get("classifications")
    if isinstance(classifications, dict):
        unknown = int(classifications.get("unknown", 0) or 0)
    elif isinstance(raw.get("unknown_count"), int):
        unknown = int(raw["unknown_count"])
    status: SignalStatus = "ok" if unknown == 0 else "warn"
    severity: Severity | None = None if unknown == 0 else "LOW"
    return _signal(
        signal_id="OPS-SIG-007",
        scope="estate",
        scope_id=estate_id,
        status=status,
        severity=severity,
        observed_value=unknown,
        threshold={"unknown_classifications_max": None},
        evidence_refs=[_rel(vault, path)],
    )


def _collect_graph_acceptance(
    vault: Path, estate_id: str, project_filter: str | None
) -> dict[str, Any]:
    root = vault / "generated" / "graph" / "acceptance"
    if not root.is_dir():
        return _signal(
            signal_id="OPS-SIG-008",
            scope="project" if project_filter else "estate",
            scope_id=project_filter or estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"rejected_max": 0},
            evidence_refs=[],
        )
    rejected = 0
    refs: list[str] = []
    for path in sorted(root.glob("*.json")):
        raw = _read_json(path, None)
        if not isinstance(raw, dict):
            continue
        if project_filter and str(raw.get("project_id") or "") != project_filter:
            continue
        refs.append(_rel(vault, path))
        rejected += int(raw.get("rejected_count") or 0)
    if not refs:
        return _signal(
            signal_id="OPS-SIG-008",
            scope="project" if project_filter else "estate",
            scope_id=project_filter or estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"rejected_max": 0},
            evidence_refs=[],
        )
    if rejected > 0:
        return _signal(
            signal_id="OPS-SIG-008",
            scope="project" if project_filter else "estate",
            scope_id=project_filter or estate_id,
            status="fail",
            severity="HIGH",
            observed_value=rejected,
            threshold={"rejected_max": 0},
            evidence_refs=refs,
        )
    return _signal(
        signal_id="OPS-SIG-008",
        scope="project" if project_filter else "estate",
        scope_id=project_filter or estate_id,
        status="ok",
        severity=None,
        observed_value=0,
        threshold={"rejected_max": 0},
        evidence_refs=refs,
    )


def _collect_query_diagnostics(
    vault: Path, estate_id: str, project_filter: str | None
) -> dict[str, Any]:
    """OPS-SIG-009 — corruption = fail; honest nonanswer = ok metric."""
    scope: ScopeKind = "project" if project_filter else "estate"
    scope_id = project_filter or estate_id
    path = vault / "generated" / "ops" / "evidence" / "query-diagnostics.json"
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        return _signal(
            signal_id="OPS-SIG-009",
            scope=scope,
            scope_id=scope_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"corruption_max": 0},
            evidence_refs=[],
        )
    corruption = int(raw.get("query_corruption_count") or raw.get("corruption_count") or 0)
    nonanswers = int(raw.get("query_nonanswer_count") or raw.get("nonanswer_count") or 0)
    observed = {
        "query_corruption_count": corruption,
        "query_nonanswer_count": nonanswers,
    }
    if corruption > 0:
        return _signal(
            signal_id="OPS-SIG-009",
            scope=scope,
            scope_id=scope_id,
            status="fail",
            severity="CRITICAL",
            observed_value=json.dumps(observed, sort_keys=True),
            threshold={"corruption_max": 0},
            evidence_refs=[_rel(vault, path)],
        )
    # Honest nonanswers are healthy honesty, not an outage.
    return _signal(
        signal_id="OPS-SIG-009",
        scope=scope,
        scope_id=scope_id,
        status="ok",
        severity=None,
        observed_value=json.dumps(observed, sort_keys=True),
        threshold={"corruption_max": 0},
        evidence_refs=[_rel(vault, path)],
    )


def _load_readiness(vault: Path) -> tuple[dict[str, Any] | None, Path | None]:
    for relative in (
        Path(".atlas") / "agent-readiness.yaml",
        Path(".atlas") / "agent-readiness.yml",
        Path(".atlas") / "agent-readiness.json",
    ):
        path = vault / relative
        if relative.suffix in {".yaml", ".yml"}:
            raw = _read_yaml_mapping(path)
        else:
            loaded = _read_json(path, None)
            raw = loaded if isinstance(loaded, dict) else None
        if raw is not None:
            return raw, path
    return None, None


def _collect_adapter_and_skill(
    vault: Path, estate_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    readiness, path = _load_readiness(vault)
    if readiness is None or path is None:
        unknown_adapter = _signal(
            signal_id="OPS-SIG-010",
            scope="adapter",
            scope_id=estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"rehearsal_status": "passed"},
            evidence_refs=[],
        )
        unknown_skill = _signal(
            signal_id="OPS-SIG-011",
            scope="skill",
            scope_id=estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"sha256_match": True},
            evidence_refs=[],
        )
        return unknown_adapter, unknown_skill

    adapters = readiness.get("adapters")
    if not isinstance(adapters, dict) or not adapters:
        return (
            _signal(
                signal_id="OPS-SIG-010",
                scope="adapter",
                scope_id=estate_id,
                status="unknown",
                severity=None,
                observed_value=None,
                threshold={"rehearsal_status": "passed"},
                evidence_refs=[_rel(vault, path)],
            ),
            _signal(
                signal_id="OPS-SIG-011",
                scope="skill",
                scope_id=estate_id,
                status="unknown",
                severity=None,
                observed_value=None,
                threshold={"sha256_match": True},
                evidence_refs=[_rel(vault, path)],
            ),
        )

    stale: list[str] = []
    drift: list[str] = []
    for adapter_id, entry in sorted(adapters.items()):
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("rehearsal_status") or "").lower()
        revoked = bool(entry.get("revoked"))
        ready = entry.get("governed_work_ready")
        if revoked or status in {"pending", "stale", "failed", "fail"} or ready is False:
            stale.append(adapter_id)
        expected = entry.get("skill_sha256")
        observed = entry.get("observed_skill_sha256", expected)
        if (
            isinstance(expected, str)
            and isinstance(observed, str)
            and expected
            and observed
            and expected != observed
        ):
            drift.append(adapter_id)
        # Explicit drift flag from fixture evidence.
        if entry.get("skill_drift") is True:
            drift.append(adapter_id)

    adapter_scope = stale[0] if stale else next(iter(sorted(adapters)))
    skill_scope = drift[0] if drift else adapter_scope
    adapter_signal = _signal(
        signal_id="OPS-SIG-010",
        scope="adapter",
        scope_id=str(adapter_scope),
        status="fail" if stale else "ok",
        severity="HIGH" if stale else None,
        observed_value=len(stale),
        threshold={"rehearsal_status": "passed"},
        evidence_refs=[_rel(vault, path)],
    )
    skill_signal = _signal(
        signal_id="OPS-SIG-011",
        scope="skill",
        scope_id=str(skill_scope),
        status="fail" if drift else "ok",
        severity="CRITICAL" if drift else None,
        observed_value=len(drift),
        threshold={"sha256_match": True},
        evidence_refs=[_rel(vault, path)],
    )
    return adapter_signal, skill_signal


def _collect_ci(vault: Path, estate_id: str) -> dict[str, Any]:
    path = vault / "generated" / "ops" / "evidence" / "ci-status.json"
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        return _signal(
            signal_id="OPS-SIG-012",
            scope="estate",
            scope_id=estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"required_workflows": "green"},
            evidence_refs=[],
        )
    status_value = str(raw.get("status") or "").lower()
    if status_value in {"ok", "success", "green", "passed", "pass"}:
        return _signal(
            signal_id="OPS-SIG-012",
            scope="estate",
            scope_id=estate_id,
            status="ok",
            severity=None,
            observed_value=status_value,
            threshold={"required_workflows": "green"},
            evidence_refs=[_rel(vault, path)],
        )
    if status_value in {"fail", "failed", "red", "failure"}:
        return _signal(
            signal_id="OPS-SIG-012",
            scope="estate",
            scope_id=estate_id,
            status="fail",
            severity="HIGH",
            observed_value=status_value,
            threshold={"required_workflows": "green"},
            evidence_refs=[_rel(vault, path)],
        )
    return _signal(
        signal_id="OPS-SIG-012",
        scope="estate",
        scope_id=estate_id,
        status="unknown",
        severity=None,
        observed_value=status_value or None,
        threshold={"required_workflows": "green"},
        evidence_refs=[_rel(vault, path)],
    )


def _collect_backup(vault: Path, estate_id: str) -> dict[str, Any]:
    path = vault / "generated" / "ops" / "evidence" / "backup-receipt.json"
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        # Missing AS-OPS-001 evidence → unknown, never fabricated green.
        return _signal(
            signal_id="OPS-SIG-013",
            scope="estate",
            scope_id=estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"within_rpo": True},
            evidence_refs=[],
        )
    status_value = str(raw.get("status") or "").lower()
    if status_value in {"ok", "success", "within_rpo"}:
        return _signal(
            signal_id="OPS-SIG-013",
            scope="estate",
            scope_id=estate_id,
            status="ok",
            severity=None,
            observed_value=status_value,
            threshold={"within_rpo": True},
            evidence_refs=[_rel(vault, path)],
        )
    return _signal(
        signal_id="OPS-SIG-013",
        scope="estate",
        scope_id=estate_id,
        status="fail",
        severity="HIGH",
        observed_value=status_value or "failed",
        threshold={"within_rpo": True},
        evidence_refs=[_rel(vault, path)],
    )


def _collect_migration(vault: Path, estate_id: str) -> dict[str, Any]:
    path = vault / "generated" / "ops" / "evidence" / "migration-status.json"
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        return _signal(
            signal_id="OPS-SIG-014",
            scope="estate",
            scope_id=estate_id,
            status="unknown",
            severity=None,
            observed_value=None,
            threshold={"partial_migration": False},
            evidence_refs=[],
        )
    if raw.get("partial") is True or str(raw.get("status") or "").lower() == "partial":
        return _signal(
            signal_id="OPS-SIG-014",
            scope="estate",
            scope_id=estate_id,
            status="fail",
            severity="CRITICAL",
            observed_value="partial",
            threshold={"partial_migration": False},
            evidence_refs=[_rel(vault, path)],
        )
    if str(raw.get("status") or "").lower() in {"ok", "consistent", "complete"}:
        return _signal(
            signal_id="OPS-SIG-014",
            scope="estate",
            scope_id=estate_id,
            status="ok",
            severity=None,
            observed_value=str(raw.get("status")),
            threshold={"partial_migration": False},
            evidence_refs=[_rel(vault, path)],
        )
    return _signal(
        signal_id="OPS-SIG-014",
        scope="estate",
        scope_id=estate_id,
        status="unknown",
        severity=None,
        observed_value=None,
        threshold={"partial_migration": False},
        evidence_refs=[_rel(vault, path)],
    )


def collect_signals(
    vault: Path, *, project_filter: str | None = None
) -> list[dict[str, Any]]:
    """Collect OPS-SIG-* records (consume-only)."""
    vault = vault.expanduser().resolve()
    estate_id = _estate_id(vault)
    adapter_signal, skill_signal = _collect_adapter_and_skill(vault, estate_id)
    signals = [
        _collect_pending_spool(vault, estate_id),
        _collect_failed_sync(vault, estate_id, project_filter),
        _collect_last_sync(vault, estate_id, project_filter),
        _collect_freshness(vault, estate_id, project_filter),
        _collect_transaction_failures(vault, estate_id),
        _collect_quarantine(vault, estate_id, project_filter),
        _collect_normalization(vault, estate_id),
        _collect_graph_acceptance(vault, estate_id, project_filter),
        _collect_query_diagnostics(vault, estate_id, project_filter),
        adapter_signal,
        skill_signal,
        _collect_ci(vault, estate_id),
        _collect_backup(vault, estate_id),
        _collect_migration(vault, estate_id),
    ]
    return sorted(signals, key=lambda item: (item["signal_id"], item["scope_id"]))


def rollup_health(signals: list[dict[str, Any]], *, require_known: bool = True) -> HealthState:
    """Deterministic composite health per contract §5.2.

    Unknown ≠ healthy: when ``require_known`` and any evaluated signal is
    ``unknown``, the rollup is ``unknown`` unless CRITICAL/HIGH already force
    ``unhealthy`` or MEDIUM forces ``degraded``.
    """
    open_severities: list[str] = []
    has_unknown = False
    for signal in signals:
        status = signal.get("status")
        if status == "unknown":
            has_unknown = True
            continue
        if status == "ok":
            continue
        severity = signal.get("severity")
        if isinstance(severity, str):
            open_severities.append(severity)
    if "CRITICAL" in open_severities or "HIGH" in open_severities:
        return "unhealthy"
    if "MEDIUM" in open_severities:
        return "degraded"
    if require_known and has_unknown:
        return "unknown"
    return "healthy"


def _scope_entries(
    signals: list[dict[str, Any]], estate_id: str
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for signal in signals:
        key = (str(signal["scope"]), str(signal["scope_id"]))
        grouped.setdefault(key, []).append(signal)
    if ("estate", estate_id) not in grouped:
        grouped[("estate", estate_id)] = list(signals)
    scopes: list[dict[str, Any]] = []
    for (scope, scope_id), group in sorted(grouped.items()):
        scopes.append(
            {
                "scope": scope,
                "scope_id": scope_id,
                "health": rollup_health(group, require_known=True),
            }
        )
    return scopes


def build_health_snapshot(
    vault: Path, *, project_filter: str | None = None
) -> dict[str, Any]:
    """Normalize collected signals into a schema-bound snapshot (no I/O write)."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise OpsHealthError(f"vault is not a directory: {vault}")
    estate_id = _estate_id(vault)
    signals = collect_signals(vault, project_filter=project_filter)
    snapshot: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "OPERATIONAL HEALTH ≠ PROJECT AUTHORITY",
        "estate_id": estate_id,
        "project_filter": project_filter,
        "rollup": {"estate": rollup_health(signals, require_known=True)},
        "scopes": _scope_entries(signals, estate_id),
        "signals": signals,
        "collector": COLLECTOR_ID,
        "generated": {"by": GENERATED_BY},
    }
    validate_record(snapshot, "ops-health-snapshot")
    return snapshot


def snapshot_to_json(snapshot: dict[str, Any]) -> str:
    """Deterministic JSON serialization (sort_keys, stable trailing newline)."""
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def _inside(vault: Path, path: Path) -> Path:
    resolved_vault = vault.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(resolved_vault):
        raise OpsHealthError(f"path escapes vault root: {path}")
    return resolved


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_name.write_bytes(content)
        os.replace(tmp_name, path)
    finally:
        if tmp_name.exists():
            tmp_name.unlink(missing_ok=True)


def write_health_snapshot(vault: Path, snapshot: dict[str, Any]) -> Path:
    """Persist regenerable snapshot under ``generated/ops/`` only."""
    vault = vault.expanduser().resolve()
    target = _inside(vault, vault / SNAPSHOT_RELATIVE)
    # Defence in depth: refuse any non-ops generated path.
    if "generated/ops" not in target.as_posix().replace("\\", "/"):
        raise OpsHealthError(f"refusing non-ops write path: {target}")
    payload = snapshot_to_json(snapshot).encode("utf-8")
    _write_atomic(target, payload)
    return target


def emit_health_snapshot(
    vault: Path,
    *,
    project_filter: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Collect → normalize → optionally expose (write) the health snapshot."""
    snapshot = build_health_snapshot(vault, project_filter=project_filter)
    if persist:
        write_health_snapshot(vault, snapshot)
    return snapshot
