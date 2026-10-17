"""AS-INT-012 — Schema compatibility and migration tooling (operational).

Scans known vault ops JSON artifacts against shipped package schemas, reports
compatibility drift, and emits a dry-run migration plan. Never invents
authority; never rewrites Layer B; never mutates vault content on dry-run.

Hard rules (INT12-FR-001..007):
- Own this helper; thin CLI only.
- Fail closed on malformed JSON / schema validation errors.
- Deterministic reports under ``generated/ops/`` (``sort_keys=True``, no wall-clock).
- Do not dual-own receipt_revocation / event_tombstones / event_retention cores.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from project_atlas.schema import SCHEMA_FILES, validate_record
from project_atlas.secrets import scan_text

GENERATOR_ID = "atlas-int-012"
REPORT_SCHEMA = "schema-compat-report"
REPORT_RELATIVE = Path("generated") / "ops" / "schema-compat-report.json"

Mode = Literal["compat", "migrate-dry-run"]
Status = Literal["ok", "drift", "error", "dry-run"]
FindingResult = Literal[
    "compatible",
    "unknown-schema",
    "malformed",
    "missing",
    "migrate-candidate",
]


class SchemaCompatError(ValueError):
    """Raised when schema compatibility tooling cannot proceed safely."""


@dataclass(frozen=True)
class ScanTarget:
    """One vault-relative ops artifact mapped to a known schema kind."""

    relative_path: str
    schema_kind: str


# Tip-safe inventory of operational JSON projections INT packages own or scan.
DEFAULT_TARGETS: tuple[ScanTarget, ...] = (
    ScanTarget("generated/ops/event-tombstones.json", "event-tombstone-index"),
    ScanTarget("generated/ops/receipt-revocations.json", "receipt-revocation-index"),
    ScanTarget("generated/ops/retention-report.json", "event-retention-report"),
    ScanTarget(".atlas/retention-policy.json", "event-retention-policy"),
)


def _inside(vault: Path, path: Path) -> Path:
    resolved_vault = vault.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(resolved_vault):
        raise SchemaCompatError(f"path escapes vault root: {path}")
    return resolved


def _posix_rel(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault.resolve()).as_posix()


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _safe_rel(path: str) -> str:
    if not path or "\\" in path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise SchemaCompatError(f"unsafe relative path: {path!r}")
    return path


def detect_schema_identity(record: Mapping[str, Any]) -> tuple[str | None, int | None]:
    """Extract ``schema`` const and ``schema_version`` when present."""
    schema = record.get("schema")
    version = record.get("schema_version")
    schema_s = str(schema) if isinstance(schema, str) and schema else None
    version_i = int(version) if isinstance(version, int) else None
    # AS-SEC-SCAN-SCHEMA-COMPAT-JSON-ESC-001: json.loads can decode ``\\u``
    # schema ids that scan_text misses on raw bytes. Do not persist them.
    if schema_s and scan_text(schema_s):
        schema_s = None
    return schema_s, version_i


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchemaCompatError(f"malformed JSON at {path}: {exc}") from exc


def _finding(
    *,
    path: str,
    kind: str,
    result: FindingResult,
    detail: str | None = None,
    from_schema: str | None = None,
    to_schema: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"path": path, "kind": kind, "result": result}
    if detail:
        row["detail"] = detail
    if from_schema:
        row["from_schema"] = from_schema
    if to_schema:
        row["to_schema"] = to_schema
    return row


def _scan_one(vault: Path, target: ScanTarget) -> dict[str, Any]:
    rel = _safe_rel(target.relative_path)
    if target.schema_kind not in SCHEMA_FILES:
        return _finding(
            path=rel,
            kind=target.schema_kind,
            result="unknown-schema",
            detail=f"schema kind not registered: {target.schema_kind}",
        )
    path = vault / PurePosixPath(rel)
    if not path.exists():
        return _finding(path=rel, kind=target.schema_kind, result="missing")
    if not path.is_file() or path.is_symlink():
        return _finding(
            path=rel,
            kind=target.schema_kind,
            result="malformed",
            detail="target is not a regular file",
        )
    try:
        _inside(vault, path)
        loaded = _load_json(path)
    except SchemaCompatError as exc:
        return _finding(
            path=rel,
            kind=target.schema_kind,
            result="malformed",
            detail=str(exc),
        )
    if not isinstance(loaded, Mapping):
        return _finding(
            path=rel,
            kind=target.schema_kind,
            result="malformed",
            detail="JSON root must be an object",
        )
    schema_id, schema_version = detect_schema_identity(loaded)
    try:
        validate_record(dict(loaded), target.schema_kind)
    except Exception as exc:
        # Validation failure with a declared older/foreign schema → migrate candidate.
        if schema_id is not None:
            return _finding(
                path=rel,
                kind=target.schema_kind,
                result="migrate-candidate",
                detail=str(exc),
                from_schema=schema_id,
                to_schema=target.schema_kind,
            )
        return _finding(
            path=rel,
            kind=target.schema_kind,
            result="malformed",
            detail=str(exc),
        )
    detail = None
    if schema_version is not None:
        detail = f"schema_version={schema_version}"
    return _finding(
        path=rel,
        kind=target.schema_kind,
        result="compatible",
        detail=detail,
        from_schema=schema_id,
        to_schema=target.schema_kind,
    )


def _counts(findings: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    tallies = {
        "scanned": len(findings),
        "compatible": 0,
        "unknown_schema": 0,
        "malformed": 0,
        "missing": 0,
        "migrate_candidate": 0,
    }
    key_map = {
        "compatible": "compatible",
        "unknown-schema": "unknown_schema",
        "malformed": "malformed",
        "missing": "missing",
        "migrate-candidate": "migrate_candidate",
    }
    for row in findings:
        mapped = key_map.get(str(row.get("result")))
        if mapped:
            tallies[mapped] += 1
    return tallies


def _status_for(mode: Mode, counts: Mapping[str, int]) -> Status:
    if mode == "migrate-dry-run":
        return "dry-run"
    if counts["malformed"] or counts["unknown_schema"]:
        return "error"
    if counts["migrate_candidate"]:
        return "drift"
    return "ok"


def build_report(
    vault: Path,
    *,
    mode: Mode = "compat",
    targets: Sequence[ScanTarget] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Scan targets and optionally persist ``schema-compat-report.json``."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise SchemaCompatError(f"vault is not a directory: {vault}")
    if mode not in {"compat", "migrate-dry-run"}:
        raise SchemaCompatError(f"unsupported mode: {mode!r}")

    scan_targets = tuple(targets) if targets is not None else DEFAULT_TARGETS
    findings = [_scan_one(vault, target) for target in scan_targets]
    # Deterministic order by path then kind.
    findings = sorted(findings, key=lambda r: (r["path"], r["kind"]))
    counts = _counts(findings)
    status = _status_for(mode, counts)

    # Dry-run migration lists migrate-candidates only; never applies mutations.
    if mode == "migrate-dry-run":
        findings = [
            row
            if row["result"] != "compatible"
            else {
                **row,
                "detail": (row.get("detail") or "") + "; dry-run no-op",
            }
            for row in findings
        ]
        # Keep migrate-candidates; annotate plan detail.
        planned: list[dict[str, Any]] = []
        for row in findings:
            if row["result"] == "migrate-candidate":
                planned.append(
                    {
                        **row,
                        "detail": (
                            (row.get("detail") or "validation drift")
                            + "; migrate plan: re-validate against shipped schema "
                            f"{row['kind']} (no auto-apply)"
                        ),
                    }
                )
            else:
                planned.append(row)
        findings = planned

    report: dict[str, Any] = {
        "schema_version": 1,
        "schema": "atlas.schema_compat.report.v1",
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "SCHEMA COMPAT / MIGRATION REPORT ≠ PROJECT AUTHORITY",
        "status": status,
        "mode": mode,
        "findings": findings,
        "counts": counts,
        "generated": {"by": GENERATOR_ID},
    }
    validate_record(report, REPORT_SCHEMA)
    if write:
        write_report(vault, report)
    return report


def write_report(vault: Path, report: dict[str, Any]) -> Path:
    """Persist a schema-valid compat report under ``generated/ops/`` only."""
    vault = vault.expanduser().resolve()
    validate_record(report, REPORT_SCHEMA)
    target = _inside(vault, vault / REPORT_RELATIVE)
    rel = _posix_rel(vault, target)
    if not rel.startswith("generated/ops/"):
        raise SchemaCompatError(f"refusing non-ops report path: {rel}")
    forbidden_prefixes = (
        "projects/",
        "01-portfolio/",
        "00-system/",
        "concepts/",
        "relationships/",
        "apps/",
        "sources/",
        "receipts/",
    )
    for prefix in forbidden_prefixes:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            raise SchemaCompatError(f"refusing Layer B / forbidden write: {rel}")
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    _write_atomic(target, payload.encode("utf-8"))
    return target


def scan_compat(vault: Path, *, write: bool = True) -> dict[str, Any]:
    """Convenience: compatibility scan mode."""
    return build_report(vault, mode="compat", write=write)


def migrate_dry_run(vault: Path, *, write: bool = True) -> dict[str, Any]:
    """Convenience: dry-run migration plan (never mutates scanned artifacts)."""
    return build_report(vault, mode="migrate-dry-run", write=write)
