"""Read-only loaders for delivery evidence and optional vault ops surfaces."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from project_atlas.improvement_plane.errors import ImprovementPlaneError
from project_atlas.ops_receipts import inventory_ops_receipts
from project_atlas.secrets import scan_text

EVIDENCE_REL = Path("docs") / "evidence"

# Hard bounds for safe consumption of repository evidence as *data*.
MAX_EVIDENCE_FILES = 500
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 20_000_000

# Lane-generated artifacts must never become delivery evidence for themselves.
_SELF_INGEST_NAME_RE = re.compile(
    r"^AS-IMPR-PLANE-.*-(DEMO-REPORT|REPORT|AGENT-RESULT|DOC-RECEIPT|REVIEW-CI)"
    r"|.*improvement-plane.*report.*",
    re.IGNORECASE,
)
_SELF_INGEST_SCHEMAS = {
    "atlas.improvement-plane.report.v1",
    "atlas.improvement-plane.compare.v1",
    "atlas.improvement-plane.evaluation.v1",
    "atlas.improvement-plane.outcome.v1",
    "atlas.improvement-plane.dq-audit.v1",
    "atlas.improvement-plane.review-ci.v1",
}


def is_self_ingest_path(relative_posix: str) -> bool:
    name = Path(relative_posix).name
    if _SELF_INGEST_NAME_RE.search(name):
        return True
    return bool(
        "/improvement-plane/" in relative_posix
        and name.endswith((".json", ".jsonl"))
        and ("outcomes" in name or "report" in name or "compare" in name)
    )


def is_self_ingest_payload(payload: dict[str, Any]) -> bool:
    schema = payload.get("schema")
    if isinstance(schema, str) and schema in _SELF_INGEST_SCHEMAS:
        return True
    if payload.get("package_id") == "AS-IMPR-PLANE-001" and schema in _SELF_INGEST_SCHEMAS:
        return True
    return bool(
        payload.get("generator") == "atlas-impr-plane-001"
        and isinstance(schema, str)
        and schema.startswith("atlas.improvement-plane.")
    )


def load_evidence_records(repo_root: Path) -> list[dict[str, Any]]:
    """Load JSON evidence packets under ``docs/evidence`` (read-only).

    Unreadable or non-object JSON is skipped with an honest skip record rather
    than fabricated content. Lane-generated reports are excluded (self-ingest).
    Source bytes are treated as data only — never executed. Secret-bearing
    files are rejected with metadata-only findings (matched content never
    returned). File/count/byte bounds fail closed with skip records.
    """
    root = repo_root.expanduser().resolve()
    evidence_dir = root / EVIDENCE_REL
    records: list[dict[str, Any]] = []
    if not evidence_dir.is_dir():
        return records

    total_bytes = 0
    files_seen = 0
    for path in sorted(evidence_dir.rglob("*.json")):
        if path.name.endswith(".tmp"):
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        # Bound: only under docs/evidence of this repo.
        if not resolved.is_relative_to(evidence_dir.resolve()):
            continue

        files_seen += 1
        rel = path.relative_to(root).as_posix()
        if files_seen > MAX_EVIDENCE_FILES:
            records.append(
                {
                    "path": rel,
                    "parse_status": "skipped_limit",
                    "error": f"max-evidence-files-{MAX_EVIDENCE_FILES}",
                    "payload": None,
                    "format": "skipped_limit",
                }
            )
            continue

        if is_self_ingest_path(rel):
            records.append(
                {
                    "path": rel,
                    "parse_status": "excluded_self_ingest",
                    "error": "lane-generated-artifact",
                    "payload": None,
                    "format": "self_ingest_excluded",
                }
            )
            continue

        try:
            size = path.stat().st_size
        except OSError as exc:
            records.append(
                {
                    "path": rel,
                    "parse_status": "unreadable",
                    "error": type(exc).__name__,
                    "payload": None,
                    "format": "unreadable",
                }
            )
            continue

        if size > MAX_FILE_BYTES:
            records.append(
                {
                    "path": rel,
                    "parse_status": "skipped_limit",
                    "error": f"max-file-bytes-{MAX_FILE_BYTES}",
                    "payload": None,
                    "format": "skipped_limit",
                }
            )
            continue
        if total_bytes + size > MAX_TOTAL_BYTES:
            records.append(
                {
                    "path": rel,
                    "parse_status": "skipped_limit",
                    "error": f"max-total-bytes-{MAX_TOTAL_BYTES}",
                    "payload": None,
                    "format": "skipped_limit",
                }
            )
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            records.append(
                {
                    "path": rel,
                    "parse_status": "unreadable",
                    "error": type(exc).__name__,
                    "payload": None,
                    "format": "unreadable",
                }
            )
            continue

        # Treat content as data: scan for secrets; never echo matched values.
        secret_hits = scan_text(text)
        if secret_hits:
            records.append(
                {
                    "path": rel,
                    "parse_status": "excluded_secrets",
                    "error": "secret-patterns-detected",
                    "payload": None,
                    "format": "secrets_excluded",
                    "secret_patterns": sorted({hit.pattern for hit in secret_hits}),
                }
            )
            continue

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            records.append(
                {
                    "path": rel,
                    "parse_status": "unreadable",
                    "error": type(exc).__name__,
                    "payload": None,
                    "format": "unreadable",
                }
            )
            continue
        if not isinstance(raw, dict):
            records.append(
                {
                    "path": rel,
                    "parse_status": "non_object",
                    "error": "expected-json-object",
                    "payload": None,
                    "format": "non_object",
                }
            )
            continue
        if is_self_ingest_payload(raw):
            records.append(
                {
                    "path": rel,
                    "parse_status": "excluded_self_ingest",
                    "error": "lane-generated-payload",
                    "payload": None,
                    "format": "self_ingest_excluded",
                }
            )
            continue

        total_bytes += size
        records.append(
            {
                "path": rel,
                "parse_status": "ok",
                "error": None,
                "payload": raw,
                "format": classify_record_format(raw),
                "content_treated_as": "data",
            }
        )
    return records


def classify_record_format(payload: dict[str, Any]) -> str:
    """Identify supported evidence shapes without inventing fields."""
    if "owner_gated_nodes" in payload:
        return "owner_gated_frontier"
    if "successor_dag" in payload:
        return "successor_dag"
    if isinstance(payload.get("findings"), list):
        return "findings_packet"
    if isinstance(payload.get("hard_counters"), dict):
        return "hard_counters_packet"
    if payload.get("pin_kind") == "source_snapshot":
        return "source_pin"
    if "directive" in payload:
        return "directive_packet"
    if "package_id" in payload:
        return "package_packet"
    return "generic_object"


def build_coverage_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evidence coverage and provenance summary (not a quality score)."""
    by_status: dict[str, int] = {}
    by_format: dict[str, int] = {}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    missing_fields: list[dict[str, Any]] = []

    for record in records:
        status = str(record.get("parse_status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        fmt = str(record.get("format") or "unknown")
        by_format[fmt] = by_format.get(fmt, 0) + 1
        path = str(record.get("path"))
        if status == "ok":
            payload = record.get("payload")
            accepted.append({"path": path, "format": fmt})
            if isinstance(payload, dict):
                gaps: list[str] = []
                if "as_of_utc" not in payload and "as_of" not in payload:
                    live = payload.get("live_refresh")
                    if not (isinstance(live, dict) and live.get("timestamp")):
                        gaps.append("timestamp")
                if fmt == "findings_packet":
                    findings = payload.get("findings") or []
                    if isinstance(findings, list):
                        for finding in findings:
                            if not isinstance(finding, dict):
                                continue
                            if not (
                                finding.get("finding_id")
                                or finding.get("code")
                                or finding.get("id")
                                or finding.get("attack")
                            ):
                                gaps.append("finding_identifier")
                                break
                if gaps:
                    missing_fields.append({"path": path, "missing": sorted(set(gaps))})
        else:
            rejected.append(
                {
                    "path": path,
                    "parse_status": status,
                    "error": record.get("error"),
                    "format": fmt,
                }
            )

    return {
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "by_parse_status": dict(sorted(by_status.items())),
        "by_format": dict(sorted(by_format.items())),
        "accepted": accepted,
        "rejected": rejected,
        "missing_fields": missing_fields,
        "bounds": {
            "max_evidence_files": MAX_EVIDENCE_FILES,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
            "supported_root": str(EVIDENCE_REL.as_posix()),
        },
        "note": (
            "Coverage describes parse/format acceptance only. "
            "File count alone is not evidence quality. "
            "Source content is consumed as data; embedded instructions are not executed."
        ),
    }


def load_optional_ops_coverage(vault_path: Path | None) -> dict[str, Any]:
    """Inventory optional vault ops receipts without inventing health."""
    if vault_path is None:
        return {
            "vault_provided": False,
            "kinds": {},
            "receipt_rows": 0,
            "health_snapshot": "not_requested",
            "workflow_metrics": "not_requested",
            "ops_report": "not_requested",
            "events_stream": "not_requested",
            "note": "No vault path supplied; ops coverage not scanned.",
        }

    vault = vault_path.expanduser().resolve()
    if not vault.exists():
        raise ImprovementPlaneError(
            "vault-not-found",
            f"Vault path does not exist: {vault}",
        )
    inventory = inventory_ops_receipts(vault, limit=100)
    ops = vault / "generated" / "ops"

    def _presence(rel: Path) -> str:
        path = ops / rel
        if not path.exists():
            return "absent"
        if not path.is_file():
            return "unknown"
        return "present"

    return {
        "vault_provided": True,
        "kinds": dict(inventory.get("kinds") or {}),
        "receipt_rows": len(inventory.get("receipts") or []),
        "ops_root_status": inventory.get("ops_root"),
        "health_snapshot": _presence(Path("health-snapshot.json")),
        "workflow_metrics": _presence(Path("workflow-metrics.json")),
        "ops_report": _presence(Path("ops-report.json")),
        "events_stream": _presence(Path("events") / "stream.jsonl"),
        "note": (
            "Absence stays unknown; receipt presence is not health, completion, "
            "or Truth Core. Optional consume only."
        ),
        "truth_boundary": inventory.get("truth_boundary"),
    }


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """Read a JSON object fail-closed for operator workflows."""
    target = path.expanduser().resolve()
    if not target.is_file():
        raise ImprovementPlaneError("input-not-found", f"{label} not found: {target}")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ImprovementPlaneError(
            "input-unreadable",
            f"{label} is not readable: {target} ({type(exc).__name__})",
        ) from None
    if size > MAX_FILE_BYTES:
        raise ImprovementPlaneError(
            "input-too-large",
            f"{label} exceeds {MAX_FILE_BYTES} byte bound: {target}",
        )
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ImprovementPlaneError(
            "input-unreadable",
            f"{label} is not readable JSON: {target} ({type(exc).__name__})",
        ) from None
    secret_hits = scan_text(text)
    if secret_hits:
        raise ImprovementPlaneError(
            "input-secrets-detected",
            f"{label} rejected: secret patterns detected (content not echoed)",
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImprovementPlaneError(
            "input-unreadable",
            f"{label} is not readable JSON: {target} ({type(exc).__name__})",
        ) from None
    if not isinstance(raw, dict):
        raise ImprovementPlaneError(
            "input-not-object",
            f"{label} must be a JSON object: {target}",
        )
    return raw
