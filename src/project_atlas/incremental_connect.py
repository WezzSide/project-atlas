"""AS-CODER-ALPHA-INCREMENTAL-CONNECT-001 — operational no-change reconnect.

Inspects the current discover inventory against the last committed
``connect-manifest`` + ``connect-receipt``. When those artifacts prove an
unchanged active-source set, ``atlas connect`` skips redundant ingest and
derived rematerialization.

Skip is **operational only**. It is not Truth Core authority, not a trust
score, and not a substitute for validation. A missing, partial, or
unreadable prior receipt is never treated as a clean skip.

Windows path honesty: fingerprints use ``canonicalize_project_path``
(backslash → POSIX, no case-fold identity). Distinct case forms stay distinct.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from project_atlas.secrets import scan_text
from project_atlas.source_identity import (
    IdentityLockError,
    ProjectIdentityLock,
    canonicalize_project_path,
)

PACKAGE_ID = "AS-CODER-ALPHA-INCREMENTAL-CONNECT-001"
GENERATOR_ID = "atlas-coder-alpha-incremental-connect-001"
SCHEMA_ID = "atlas.coder-alpha.incremental-connect.v1"
INCREMENTAL_RECEIPT_RELATIVE = Path("generated") / "ops" / "incremental-connect-receipt.json"

Disposition = Literal[
    "full_compile",
    "no_change_skip",
    "dirty_prior_full_recompile",
    "unknown_full_compile",
]

_REQUIRED_RECEIPT_KEYS = (
    "schema",
    "status",
    "vault_id",
    "projects",
    "steps",
    "project_root",
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _read_json_object(path: Path) -> tuple[str, dict[str, Any] | None]:
    """Return ``(ok|absent|unreadable, payload)``."""
    if not path.is_file():
        return "absent", None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "unreadable", None
    if isinstance(raw, dict):
        return "ok", raw
    return "unreadable", None


def _posix_source_path(raw: str) -> str | None:
    """Canonical POSIX project-relative path, or None when unprovable."""
    try:
        return canonicalize_project_path(raw.replace("\\", "/"))
    except ValueError:
        return None


def active_source_rows(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Active (ingest-eligible) sources keyed by POSIX path, not case-folded."""
    sources = manifest.get("sources")
    rows: list[dict[str, str]] = []
    if not isinstance(sources, list):
        return rows
    for entry in sources:
        if not isinstance(entry, dict) or entry.get("exclusion_reason"):
            continue
        path_raw = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(path_raw, str) or not isinstance(digest, str) or not digest:
            continue
        path = _posix_source_path(path_raw)
        if path is None:
            continue
        # AS-SEC-SCAN-INCREMENTAL-PATH-JSON-ESC-001: json.loads of a prior
        # connect-manifest can decode \\u path escapes that scan_text misses.
        if scan_text(path) or scan_text(path_raw):
            continue
        project = entry.get("likely_project") or "unknown-project"
        source_id = entry.get("source_id") or ""
        rows.append(
            {
                "path": path,
                "sha256": digest,
                "likely_project": str(project),
                "source_id": str(source_id),
            }
        )
    rows.sort(key=lambda item: (item["path"], item["source_id"]))
    return rows


def agent_event_fingerprint(manifest: dict[str, Any]) -> list[dict[str, str]]:
    events = manifest.get("agent_events")
    rows: list[dict[str, str]] = []
    if not isinstance(events, list):
        return rows
    for entry in events:
        if not isinstance(entry, dict):
            continue
        event_id = entry.get("event_id")
        digest = entry.get("component_sha256") or entry.get("sha256") or ""
        if isinstance(event_id, str) and event_id.strip():
            rows.append({"event_id": event_id.strip(), "sha256": str(digest)})
    rows.sort(key=lambda item: item["event_id"])
    return rows


def inventory_fingerprint(manifest: dict[str, Any]) -> dict[str, Any]:
    """Deterministic active-source + agent-event fingerprint (mtime-free)."""
    sources = active_source_rows(manifest)
    events = agent_event_fingerprint(manifest)
    payload = {"sources": sources, "agent_events": events}
    return {
        "schema_version": 1,
        "sources": sources,
        "agent_events": events,
        "by_path": {item["path"]: item["sha256"] for item in sources},
        "digest": _digest(payload),
    }


@dataclass(frozen=True)
class ActiveDelta:
    """Path-level delta over active sources. Rename is proven only by unique hash."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    modified: tuple[str, ...]
    renamed: tuple[tuple[str, str], ...]
    unknown_moves: tuple[str, ...]
    content_changed: int
    semantic_records_changed: int
    lineage_proven: bool

    @property
    def unchanged(self) -> bool:
        return (
            not self.added
            and not self.removed
            and not self.modified
            and not self.renamed
            and not self.unknown_moves
        )


def classify_active_delta(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> ActiveDelta:
    """Compare fingerprints. Duplicate hashes make rename unprovable (UNKNOWN)."""
    prev_fp = inventory_fingerprint(previous) if isinstance(previous, dict) else {
        "by_path": {},
        "sources": [],
    }
    curr_fp = inventory_fingerprint(current)
    prev_map = {
        str(key): str(value)
        for key, value in (prev_fp.get("by_path") or {}).items()
        if isinstance(key, str) and isinstance(value, str)
    }
    curr_map = {
        str(key): str(value)
        for key, value in (curr_fp.get("by_path") or {}).items()
        if isinstance(key, str) and isinstance(value, str)
    }
    prev_paths = set(prev_map)
    curr_paths = set(curr_map)
    added = sorted(curr_paths - prev_paths)
    removed = sorted(prev_paths - curr_paths)
    modified = sorted(
        path for path in sorted(prev_paths & curr_paths) if prev_map[path] != curr_map[path]
    )

    renamed: list[tuple[str, str]] = []
    unknown_moves: list[str] = []
    leftover_added = list(added)
    leftover_removed = list(removed)
    prev_hash_all: dict[str, list[str]] = {}
    for path, digest in prev_map.items():
        prev_hash_all.setdefault(digest, []).append(path)
    curr_hash_all: dict[str, list[str]] = {}
    for path, digest in curr_map.items():
        curr_hash_all.setdefault(digest, []).append(path)
    if leftover_added and leftover_removed:
        proven_from: set[str] = set()
        proven_to: set[str] = set()
        for path in leftover_removed:
            digest = prev_map[path]
            prior_hits = prev_hash_all.get(digest, [])
            current_hits = curr_hash_all.get(digest, [])
            added_hits = [item for item in current_hits if item in leftover_added]
            if len(prior_hits) == 1 and len(current_hits) == 1 and len(added_hits) == 1:
                renamed.append((path, added_hits[0]))
                proven_from.add(path)
                proven_to.add(added_hits[0])
            elif added_hits:
                unknown_moves.extend(sorted({path, *added_hits}))
        leftover_added = [path for path in leftover_added if path not in proven_to]
        leftover_removed = [path for path in leftover_removed if path not in proven_from]
        leftover_added = [path for path in leftover_added if path not in unknown_moves]
        leftover_removed = [path for path in leftover_removed if path not in unknown_moves]

    content_changed = len(modified) + len(leftover_added) + len(leftover_removed)
    semantic = content_changed + len(renamed) + len(unknown_moves)
    return ActiveDelta(
        added=tuple(leftover_added),
        removed=tuple(leftover_removed),
        modified=tuple(modified),
        renamed=tuple(renamed),
        unknown_moves=tuple(sorted(set(unknown_moves))),
        content_changed=content_changed,
        semantic_records_changed=semantic,
        lineage_proven=not unknown_moves,
    )


def files_inspected_count(manifest: dict[str, Any]) -> int:
    sources = manifest.get("sources")
    events = manifest.get("agent_events")
    source_n = len(sources) if isinstance(sources, list) else 0
    event_n = len(events) if isinstance(events, list) else 0
    return source_n + event_n


def compile_options_payload(
    *,
    include_portfolio: bool,
    skip_validate: bool,
    excludes: list[str],
    max_file_size: int,
) -> dict[str, Any]:
    return {
        "include_portfolio": bool(include_portfolio),
        "skip_validate": bool(skip_validate),
        "excludes": sorted(str(item) for item in excludes),
        "max_file_size": int(max_file_size),
    }


def prior_receipt_is_complete(receipt: dict[str, Any] | None) -> tuple[bool, str]:
    """A clean skip requires a finished prior connect receipt — not a partial one."""
    if receipt is None:
        return False, "prior_receipt_absent"
    for key in _REQUIRED_RECEIPT_KEYS:
        if key not in receipt:
            return False, f"prior_receipt_missing_{key}"
    if receipt.get("status") != "connected":
        return False, "prior_receipt_not_connected"
    if receipt.get("schema") != "atlas.connect.receipt.v1":
        return False, "prior_receipt_schema_mismatch"
    vault_id = receipt.get("vault_id")
    if not isinstance(vault_id, str) or not vault_id.strip():
        return False, "prior_receipt_vault_id_missing"
    projects = receipt.get("projects")
    if not isinstance(projects, list) or not projects:
        return False, "prior_receipt_projects_missing"
    steps = receipt.get("steps")
    if not isinstance(steps, list) or "ingest" not in steps:
        return False, "prior_receipt_steps_incomplete"
    return True, "prior_receipt_complete"


def options_compatible(
    prior: dict[str, Any] | None,
    current: dict[str, Any],
) -> tuple[bool, str]:
    """Refuse skip when the caller asked for work the prior receipt did not do."""
    recorded = (prior or {}).get("compile_options")
    if not isinstance(recorded, dict):
        recorded = {
            "include_portfolio": False,
            "skip_validate": False,
        }
    if current["include_portfolio"] and not recorded.get("include_portfolio"):
        return False, "portfolio_requested_but_prior_omitted"
    if not current["skip_validate"] and recorded.get("skip_validate"):
        return False, "validate_required_but_prior_skipped"
    if current["skip_validate"] is False:
        prior_steps = (prior or {}).get("steps")
        if isinstance(prior_steps, list) and "validate" not in prior_steps:
            return False, "validate_required_but_prior_steps_omit_validate"
    return True, "options_compatible"


def identity_lock_path(vault: Path, project_id: str) -> Path:
    lock_key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:20]
    return vault / ".atlas" / "identity-locks" / f"{lock_key}.lock"


def acquire_project_identity_locks(
    vault: Path,
    project_ids: list[str],
    *,
    wait_seconds: float = 0.2,
) -> list[ProjectIdentityLock]:
    """Acquire existing ingest identity locks. Fail closed if any are held."""
    held: list[ProjectIdentityLock] = []
    try:
        for project_id in sorted({item for item in project_ids if item.strip()}):
            lock = ProjectIdentityLock(
                identity_lock_path(vault, project_id),
                wait_seconds=wait_seconds,
                stale_seconds=300.0,
                poll_seconds=0.05,
            )
            lock.acquire()
            held.append(lock)
    except IdentityLockError:
        for lock in held:
            lock.release()
        raise
    return held


def release_project_identity_locks(locks: list[ProjectIdentityLock]) -> None:
    for lock in locks:
        lock.release()


@dataclass
class IncrementalDecision:
    """Operational reconnect decision. Never a Truth Core verdict."""

    disposition: Disposition
    reason: str
    files_inspected: int
    content_changed: int
    semantic_records_changed: int
    physical_writes: int
    projections_regenerated: int
    ingest_invocations: int
    discover_invocations: int
    fingerprint_digest: str
    prior_receipt_complete: bool
    delta: ActiveDelta
    compile_options: dict[str, Any] = field(default_factory=dict)

    @property
    def can_skip(self) -> bool:
        return self.disposition == "no_change_skip"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "schema": SCHEMA_ID,
            "package": PACKAGE_ID,
            "disposition": self.disposition,
            "reason": self.reason,
            "authority": "operational_not_truth_core",
            "files_inspected": self.files_inspected,
            "content_changed": self.content_changed,
            "semantic_records_changed": self.semantic_records_changed,
            "physical_writes": self.physical_writes,
            "projections_regenerated": self.projections_regenerated,
            "ingest_invocations": self.ingest_invocations,
            "discover_invocations": self.discover_invocations,
            "fingerprint_digest": self.fingerprint_digest,
            "prior_receipt_complete": self.prior_receipt_complete,
            "delta": {
                "added": list(self.delta.added),
                "removed": list(self.delta.removed),
                "modified": list(self.delta.modified),
                "renamed": [{"from": src, "to": dst} for src, dst in self.delta.renamed],
                "unknown_moves": list(self.delta.unknown_moves),
                "lineage_proven": self.delta.lineage_proven,
            },
            "compile_options": self.compile_options,
            "generated": {"by": GENERATOR_ID},
            "honesty": {
                "incremental_skip_is_authority": False,
                "incremental_skip_is_operational": True,
                "truth_core_authority": False,
                "unknown_is_valid": True,
                "atlas_opt_wake_gate": "CLOSED",
            },
        }


def evaluate_incremental_reconnect(
    *,
    vault: Path,
    project_root: Path,
    current_manifest: dict[str, Any],
    vault_id: str,
    include_portfolio: bool,
    skip_validate: bool,
    excludes: list[str],
    max_file_size: int,
    manifest_relative: Path,
    staging_relative: Path,
    receipt_relative: Path,
) -> IncrementalDecision:
    """Decide skip vs full compile from existing connect artifacts only."""
    options = compile_options_payload(
        include_portfolio=include_portfolio,
        skip_validate=skip_validate,
        excludes=excludes,
        max_file_size=max_file_size,
    )
    inspected = files_inspected_count(current_manifest)
    current_fp = inventory_fingerprint(current_manifest)
    empty_delta = classify_active_delta(None, current_manifest)

    def _decision(
        disposition: Disposition,
        reason: str,
        *,
        delta: ActiveDelta | None = None,
        prior_ok: bool = False,
        ingest_invocations: int = 2,
        discover_invocations: int = 2,
        physical_writes: int = 0,
        projections_regenerated: int = 0,
    ) -> IncrementalDecision:
        used = delta if delta is not None else empty_delta
        return IncrementalDecision(
            disposition=disposition,
            reason=reason,
            files_inspected=inspected,
            content_changed=used.content_changed,
            semantic_records_changed=used.semantic_records_changed,
            physical_writes=physical_writes,
            projections_regenerated=projections_regenerated,
            ingest_invocations=ingest_invocations,
            discover_invocations=discover_invocations,
            fingerprint_digest=str(current_fp["digest"]),
            prior_receipt_complete=prior_ok,
            delta=used,
            compile_options=options,
        )

    staging = vault / staging_relative
    if staging.is_file():
        return _decision(
            "dirty_prior_full_recompile",
            "staging_manifest_present",
        )

    receipt_status, receipt = _read_json_object(vault / receipt_relative)
    if receipt_status == "unreadable":
        return _decision("dirty_prior_full_recompile", "prior_receipt_unreadable")
    complete, complete_reason = prior_receipt_is_complete(receipt)
    if not complete:
        if complete_reason == "prior_receipt_absent" and not (vault / manifest_relative).is_file():
            return _decision("full_compile", "first_connect")
        return _decision("dirty_prior_full_recompile", complete_reason)

    assert receipt is not None
    if str(receipt.get("vault_id") or "") != vault_id:
        return _decision("dirty_prior_full_recompile", "vault_id_mismatch", prior_ok=True)

    recorded_root = receipt.get("project_root")
    try:
        same_root = (
            isinstance(recorded_root, str)
            and Path(recorded_root).expanduser().resolve() == project_root.resolve()
        )
    except OSError:
        same_root = False
    if not same_root:
        return _decision("dirty_prior_full_recompile", "project_root_mismatch", prior_ok=True)

    opt_ok, opt_reason = options_compatible(receipt, options)
    if not opt_ok:
        return _decision("dirty_prior_full_recompile", opt_reason, prior_ok=True)

    manifest_status, prior_manifest = _read_json_object(vault / manifest_relative)
    if manifest_status == "absent":
        return _decision("dirty_prior_full_recompile", "prior_manifest_absent", prior_ok=True)
    if manifest_status == "unreadable" or prior_manifest is None:
        return _decision("dirty_prior_full_recompile", "prior_manifest_unreadable", prior_ok=True)

    prior_root = prior_manifest.get("source_root")
    try:
        same_manifest_root = (
            isinstance(prior_root, str)
            and Path(prior_root).expanduser().resolve() == project_root.resolve()
        )
    except OSError:
        same_manifest_root = False
    if not same_manifest_root:
        return _decision(
            "full_compile",
            "connect_manifest_source_root_mismatch",
            prior_ok=True,
        )

    delta = classify_active_delta(prior_manifest, current_manifest)
    if delta.unknown_moves:
        return _decision(
            "unknown_full_compile",
            "rename_lineage_unproven",
            delta=delta,
            prior_ok=True,
        )
    if not delta.unchanged:
        return _decision(
            "full_compile",
            "active_sources_changed",
            delta=delta,
            prior_ok=True,
        )

    if not (vault / "generated" / "indexes").is_dir():
        return _decision(
            "dirty_prior_full_recompile",
            "indexes_absent",
            delta=delta,
            prior_ok=True,
        )

    return _decision(
        "no_change_skip",
        "active_sources_unchanged",
        delta=delta,
        prior_ok=True,
        ingest_invocations=0,
        discover_invocations=1,
        physical_writes=0,
        projections_regenerated=0,
    )


def write_incremental_receipt(vault: Path, decision: IncrementalDecision) -> Path:
    """Write a derived, non-authoritative ops receipt with observable counters."""
    path = vault / INCREMENTAL_RECEIPT_RELATIVE
    payload = decision.as_dict()
    _write_atomic(
        path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return path


def overlay_prior_lens_fields(report: dict[str, Any], prior: dict[str, Any]) -> None:
    """Reuse prior derived lens paths on skip — do not invent a new baseline."""
    for key in (
        "overview_answers",
        "architecture_answers",
        "state_answers",
        "changed_answers",
        "decisions_answers",
        "unknown_answers",
        "roadmap_answers",
        "next_answers",
        "brief_paths",
        "obsidian_notes",
        "indexes",
        "changed_delta",
        "bound_project_id",
        "projects",
    ):
        if key in prior:
            report[key] = prior[key]


def attach_incremental(report: dict[str, Any], decision: IncrementalDecision) -> None:
    report["incremental"] = decision.as_dict()
    report["compile_options"] = decision.compile_options
