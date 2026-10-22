"""D-148 — AUTHENTIC_ESTATE_ROOT credential consumption and preflight."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from project_atlas.pilot_auth_prep import MARKER, is_fixture_or_temp_marker
from project_atlas.secrets import scan_text

PACKAGE_ID: Final[str] = "AS-D148-AUTHENTIC-ESTATE-001"
_CREDENTIAL_REL = Path(".atlas/orchestration/sdk-runtime/d148-authentic-estate-credential.json")
_PLACEHOLDER_ROOTS: Final[frozenset[str]] = frozenset(
    {"<ABSOLUTE_REAL_PROJECT_PATH>", "<absolute_real_project_path>"}
)
_NON_AUTHENTIC_FRAGMENTS: Final[tuple[str, ...]] = (
    "/tests/fixtures/",
    "\\tests\\fixtures\\",
    "/fixtures/demo/",
    "\\fixtures\\demo\\",
    "/fixtures/pilots/",
    "\\fixtures\\pilots\\",
    "harbor-api",
    "synthetic",
)
# Owner-held gates that authentic-estate availability must never rewrite.
IMMUTABLE_OWNER_GATES: Final[frozenset[str]] = frozenset(
    {
        "MERGE",
        "SECURITY",
        "HUMAN",
        "OWNER",
        "RELEASE",
        "GOVERNOR",
        "SIGNOFF",
    }
)
_ESTATE_DEPENDENCY: Final[str] = "AUTHENTIC_ESTATE_ROOT"


class EstatePreflight(BaseModel):
    """Read-only preflight for an owner-supplied estate root."""

    model_config = ConfigDict(extra="forbid")

    package_id: str = PACKAGE_ID
    root: str
    root_exists: bool = False
    root_is_directory: bool = False
    root_is_authentic: bool = False
    root_is_not_atlas_fixture: bool = False
    root_is_not_test_fixture: bool = False
    root_is_not_synthetic_demo: bool = False
    root_is_readable: bool = False
    has_project_marker: bool = False
    project_id: str | None = None
    project_uuid: str | None = None
    preflight_pass: bool = False
    estate_fingerprint: str | None = None


def _rt(root: Path) -> Path:
    return root / ".atlas" / "orchestration" / "sdk-runtime"


def resolve_authentic_estate_root(
    repo_root: Path,
    *,
    explicit: str | None = None,
) -> Path | None:
    """Resolve canonical estate path from explicit arg, env, or credential file."""
    candidates: list[str] = []
    if explicit and explicit.strip():
        candidates.append(explicit.strip())
    env = os.environ.get("AUTHENTIC_ESTATE_ROOT", "").strip()
    if env:
        candidates.append(env)
    cred_path = _rt(repo_root) / "d148-authentic-estate-credential.json"
    if cred_path.is_file():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw = str(data.get("AUTHENTIC_ESTATE_ROOT") or data.get("root") or "")
                if raw:
                    candidates.append(raw)
        except (OSError, json.JSONDecodeError):
            pass
    for raw in candidates:
        if raw in _PLACEHOLDER_ROOTS:
            continue
        path = Path(raw)
        if path.is_dir():
            return path.resolve()
    return None


def marker_fingerprint(estate_root: Path) -> str:
    """SHA-256 of the project marker alone (not sufficient for O2 binding)."""
    marker = estate_root / MARKER
    if marker.is_file():
        import hashlib

        return hashlib.sha256(marker.read_bytes()).hexdigest()
    return ""


def estate_content_fingerprint(estate_root: Path) -> str:
    """Deterministic digest of ingest-eligible estate source corpus.

    Uses discovery inventory active sources (include/exclude contract), so:
    - certified source document changes invalidate the fingerprint
    - excluded/transient paths (``.git``, ``node_modules``, ``__pycache__``,
      generated caches) do not affect the digest
    """
    from project_atlas.discovery import discover
    from project_atlas.incremental_connect import inventory_fingerprint

    try:
        manifest = discover(estate_root)
    except (OSError, ValueError):
        return ""
    return str(inventory_fingerprint(manifest)["digest"])


def estate_fingerprint(estate_root: Path) -> str:
    """Canonical estate identity for D-148 evidence binding (content corpus)."""
    return estate_content_fingerprint(estate_root)


def d148_evidence_applies(
    evidence: dict[str, Any],
    main_head: str,
    repo_root: Path,
) -> bool:
    """D-148 certification applies only to current main head and estate identity."""
    if not evidence:
        return False
    pin = str(evidence.get("live_main_head") or "")
    if not pin or len(pin) != 40:
        return False
    from project_atlas.orchestration.autonomy.exact_main_closure import (
        cert_evidence_applies_to_head,
        is_ancestor,
        is_metadata_only_post_cert_delta,
    )

    head_ok = pin == main_head or cert_evidence_applies_to_head(
        {"CERTIFICATION_TARGET_HEAD": pin},
        main_head,
        repo_root,
    )
    if not head_ok and is_ancestor(repo_root, pin, main_head):
        head_ok = is_metadata_only_post_cert_delta(repo_root, pin, main_head)
    if not head_ok:
        return False
    estate = resolve_authentic_estate_root(repo_root)
    if estate is None:
        return False
    recorded_root = str(evidence.get("AUTHENTIC_ESTATE_ROOT") or "").strip()
    if recorded_root and str(estate.resolve()) != recorded_root:
        return False
    recorded_fp = str(evidence.get("estate_fingerprint") or "").strip()
    if not recorded_fp:
        # Fail closed: marker-only / missing content binding is not acceptable.
        return False
    current_fp = estate_fingerprint(estate)
    return bool(current_fp) and recorded_fp == current_fp


def _identity_without_secret(value: str | None) -> str | None:
    """Return ``value`` only when it is non-empty and not secret-shaped.

    AS-SEC-SCAN-ESTATE-YAML-001: ``yaml.safe_load`` decodes quoted ``\\u``/``\\x``
    after any raw-byte scan. Decoded bearer/token identities must not become
    ``project_id`` / ``project_uuid`` or be persisted into the D-148 credential.
    Findings are metadata-only; matched content is never returned.
    """
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token:
        return None
    if scan_text(token):
        return None
    return token


def run_estate_preflight(estate_root: Path) -> EstatePreflight:
    root = estate_root.resolve()
    marker = root / MARKER
    lowered = str(root).lower()
    not_fixture = not any(frag in lowered for frag in _NON_AUTHENTIC_FRAGMENTS)
    not_demo = "demo" not in lowered or "dev-ai" in lowered
    project_id: str | None = None
    project_uuid: str | None = None
    marker_parse_ok = False
    if marker.is_file():
        try:
            import yaml

            data = yaml.safe_load(marker.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                proj = data.get("project")
                raw_id = ""
                if isinstance(proj, dict):
                    raw_id = str(proj.get("id") or "").strip()
                project_id = _identity_without_secret(raw_id)
                raw_uuid = str(data.get("project_uuid") or "").strip()
                project_uuid = _identity_without_secret(raw_uuid)
                # Secret-shaped decoded identities fail closed: do not treat the
                # marker as authentic and do not keep the decoded secret.
                if (raw_id and project_id is None) or (raw_uuid and project_uuid is None):
                    marker_parse_ok = False
                    project_id = None
                    project_uuid = None
                elif project_id:
                    marker_parse_ok = True
        except Exception:
            marker_parse_ok = False
            project_id = None
            project_uuid = None
    authentic_marker = (
        marker.is_file()
        and marker_parse_ok
        and not is_fixture_or_temp_marker(marker)
    )
    readable = os.access(root, os.R_OK)
    preflight_pass = all(
        [
            root.is_dir(),
            authentic_marker,
            not_fixture,
            not_demo,
            readable,
        ]
    )
    return EstatePreflight(
        root=str(root),
        root_exists=root.exists(),
        root_is_directory=root.is_dir(),
        root_is_authentic=authentic_marker,
        root_is_not_atlas_fixture=not_fixture,
        root_is_not_test_fixture=not_fixture,
        root_is_not_synthetic_demo=not_demo,
        root_is_readable=readable,
        has_project_marker=marker.is_file(),
        project_id=project_id,
        project_uuid=project_uuid,
        preflight_pass=preflight_pass,
        estate_fingerprint=estate_fingerprint(root) if marker.is_file() else None,
    )


def write_estate_credential(repo_root: Path, estate_root: Path, preflight: EstatePreflight) -> Path:
    path = _rt(repo_root) / "d148-authentic-estate-credential.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_id = _identity_without_secret(preflight.project_id)
    safe_uuid = _identity_without_secret(preflight.project_uuid)
    secret_blocked = (preflight.project_id is not None and safe_id is None) or (
        preflight.project_uuid is not None and safe_uuid is None
    )
    satisfied = bool(preflight.preflight_pass) and not secret_blocked
    preflight_payload = preflight.model_dump()
    preflight_payload["project_id"] = safe_id
    preflight_payload["project_uuid"] = safe_uuid
    if secret_blocked:
        preflight_payload["preflight_pass"] = False
        preflight_payload["root_is_authentic"] = False
    payload = {
        "directive": "D-148",
        "AUTHENTIC_ESTATE_ROOT": str(estate_root.resolve()),
        # Failed preflight is recorded honestly; it never grants estate availability.
        "AUTHENTIC_ESTATE_ROOT_AVAILABLE": satisfied,
        # Valid estate path satisfies AUTHENTIC_ESTATE_ROOT only — not owner authority.
        "AUTHENTIC_ESTATE_CREDENTIAL_SATISFIED": satisfied,
        "OWNER_CAPABILITY_GRANTED": False,
        "preflight": preflight_payload,
        "preflight_pass": satisfied,
        "estate_fingerprint": preflight.estate_fingerprint,
        "marker_fingerprint": marker_fingerprint(estate_root),
        "project_id": safe_id,
        "project_uuid": safe_uuid,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "merge_authorized": False,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def estate_credential_binding_ok(repo_root: Path) -> bool:
    """Reject stale, cross-project, or failed credential files before DAG mutation."""
    estate = resolve_authentic_estate_root(repo_root)
    if estate is None:
        return False
    cred_path = _rt(repo_root) / "d148-authentic-estate-credential.json"
    if not cred_path.is_file():
        # Env/explicit root may satisfy availability without a persisted credential.
        return run_estate_preflight(estate).preflight_pass
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    granted = data.get("OWNER_CAPABILITY_GRANTED")
    if granted is True or str(granted).strip().lower() in {"true", "1", "yes"}:
        return False
    credential_failed = data.get("preflight_pass") is False or (
        data.get("AUTHENTIC_ESTATE_CREDENTIAL_SATISFIED") is False
    )
    if credential_failed:
        return False
    recorded_root = str(data.get("AUTHENTIC_ESTATE_ROOT") or data.get("root") or "").strip()
    if recorded_root and str(estate.resolve()) != recorded_root:
        return False
    recorded_fp = str(data.get("estate_fingerprint") or "").strip()
    current_fp = estate_fingerprint(estate)
    # Credential files must bind a live content fingerprint. Missing/empty
    # fingerprints are stale evidence and must not authorize mutation.
    if not recorded_fp or not current_fp or recorded_fp != current_fp:
        return False
    recorded_project = str(data.get("project_id") or "").strip()
    live = run_estate_preflight(estate)
    if recorded_project and live.project_id and recorded_project != live.project_id:
        return False
    return live.preflight_pass


def _durable_refresh_integrity_ok(repo_root: Path) -> bool:
    """When operational pins exist, refuse mutation on closure-integrity failure."""
    from project_atlas.orchestration.autonomy.exact_main_closure import (
        closure_integrity_pass,
        inspect_closure_integrity,
        read_operational_pins,
    )

    cert_head, cert_tree = read_operational_pins(repo_root)
    if not cert_head:
        return True
    try:
        integrity = inspect_closure_integrity(
            repo_root,
            certification_target_head=cert_head,
            certification_target_tree=cert_tree,
        )
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False
    return closure_integrity_pass(integrity)


def authentic_estate_available(repo_root: Path) -> bool:
    estate = resolve_authentic_estate_root(repo_root)
    if estate is None:
        return False
    preflight = run_estate_preflight(estate)
    return preflight.preflight_pass


def characterize_estate(estate_root: Path) -> dict[str, Any]:
    root = estate_root.resolve()
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    vcs_root = proc.stdout.strip() if proc.returncode == 0 else None
    manifests: list[str] = []
    for name in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod"):
        if (root / name).is_file():
            manifests.append(name)
    src_dirs = [p.name for p in root.iterdir() if p.is_dir() and p.name in {"src", "lib", "app"}]
    doc_dirs = [p.name for p in root.iterdir() if p.is_dir() and p.name in {"docs", "doc"}]
    file_count = 0
    for _ in root.rglob("*"):
        file_count += 1
        if file_count > 50000:
            break
    return {
        "vcs_root": vcs_root,
        "manifests": manifests,
        "source_directories": src_dirs,
        "documentation_directories": doc_dirs,
        "approx_file_count_cap": file_count,
        "has_node_modules": (root / "node_modules").is_dir(),
        "has_git": (root / ".git").is_dir(),
        "has_atlas_metadata": (root / ".atlas").is_dir(),
    }


AUTHENTIC_O2_PACKAGES: Final[tuple[str, ...]] = (
    "AS-CODER-ALPHA-AUTHENTIC-INGEST-001",
    "AS-CODER-ALPHA-AUTHENTIC-COMPILE-001",
    "AS-CODER-ALPHA-AUTHENTIC-QUERY-001",
)


def _authentic_o2_package_ready(
    package_id: str,
    *,
    ingest_done: bool,
    compile_done: bool,
    query_done: bool,
) -> bool:
    if package_id == "AS-CODER-ALPHA-AUTHENTIC-INGEST-001":
        return not ingest_done
    if package_id == "AS-CODER-ALPHA-AUTHENTIC-COMPILE-001":
        return ingest_done and not compile_done
    if package_id == "AS-CODER-ALPHA-AUTHENTIC-QUERY-001":
        return compile_done and not query_done
    return False


def snapshot_o2_nodes(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Capture dependency/gate/status for rollback after failed mutation."""
    from project_atlas.orchestration.sdk.mission_reconciler import load_nodes

    out: dict[str, dict[str, Any]] = {}
    for node_id, node in load_nodes(repo_root).items():
        if node.PACKAGE_ID not in AUTHENTIC_O2_PACKAGES:
            continue
        out[node_id] = {
            "status": node.status,
            "DEPENDENCIES": list(node.DEPENDENCIES),
            "OWNER_GATE": node.OWNER_GATE,
        }
    return out


def restore_o2_node_snapshot(repo_root: Path, snapshot: dict[str, dict[str, Any]]) -> None:
    """Restore O2 node authority fields from a prior snapshot (fail-closed)."""
    from project_atlas.orchestration.sdk.mission_reconciler import load_nodes, persist_nodes

    if not snapshot:
        return
    nodes = load_nodes(repo_root)
    for node_id, prior in snapshot.items():
        node = nodes.get(node_id)
        if node is None:
            continue
        node.status = prior["status"]
        node.DEPENDENCIES = list(prior["DEPENDENCIES"])
        node.OWNER_GATE = prior["OWNER_GATE"]
    persist_nodes(repo_root, nodes)


def refresh_authentic_o2_node_states(repo_root: Path) -> list[str]:
    """Unblock O2 credential nodes when estate credential is satisfied (sequential).

    Eligible consumption requires ``OWNER_GATE == CREDENTIAL`` (or already
    ``NONE``) **and** an explicit ``AUTHENTIC_ESTATE_ROOT`` dependency.
    Never clears MERGE/SECURITY/HUMAN/OWNER/RELEASE/GOVERNOR/SIGNOFF, never
    clears a CREDENTIAL gate held for a different capability, and never
    blanks unrelated dependencies.

    D149-001 invariant: after consuming ``AUTHENTIC_ESTATE_ROOT``, if any
    residual dependencies remain, ``OWNER_GATE`` must not become ``NONE``.
    Estate availability is not authority over unrelated outstanding deps.
    """
    from project_atlas.orchestration.sdk.mission_reconciler import load_nodes, persist_nodes

    if not authentic_estate_available(repo_root):
        return []
    if not estate_credential_binding_ok(repo_root):
        return []
    if not _durable_refresh_integrity_ok(repo_root):
        return []
    cert_path = _rt(repo_root) / "d148-o2-certification.json"
    d148: dict[str, Any] = {}
    if cert_path.is_file():
        try:
            raw = json.loads(cert_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                d148 = raw
        except (OSError, json.JSONDecodeError):
            d148 = {}
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    main_head = proc.stdout.strip() if proc.returncode == 0 else ""
    if not d148_evidence_applies(d148, main_head, repo_root):
        d148 = {}
    ingest_done = bool(d148.get("AUTHENTIC_INGEST_SATISFIED"))
    compile_done = bool(d148.get("AUTHENTIC_COMPILE_SATISFIED"))
    query_done = bool(d148.get("AUTHENTIC_QUERY_SATISFIED"))
    nodes = load_nodes(repo_root)
    changed: list[str] = []
    for node in nodes.values():
        if node.PACKAGE_ID not in AUTHENTIC_O2_PACKAGES:
            continue
        if node.status in {"COMPLETED", "SUPERSEDED", "DISPATCHED", "RUNNING"}:
            continue
        # Estate credential must never escalate MERGE/SECURITY/etc. to NONE.
        if node.OWNER_GATE in IMMUTABLE_OWNER_GATES:
            continue
        estate_dep_present = _ESTATE_DEPENDENCY in node.DEPENDENCIES
        # CREDENTIAL for some other capability, or no estate dep to consume.
        if not estate_dep_present:
            continue
        if node.OWNER_GATE not in {"NONE", "CREDENTIAL"}:
            continue
        package_ready = _authentic_o2_package_ready(
            node.PACKAGE_ID,
            ingest_done=ingest_done,
            compile_done=compile_done,
            query_done=query_done,
        )
        if not package_ready:
            continue
        node.DEPENDENCIES = [d for d in node.DEPENDENCIES if d != _ESTATE_DEPENDENCY]
        mutated = True
        # D149-001: estate satisfaction must not leave OWNER_GATE as NONE while
        # residual dependencies remain. CREDENTIAL clears to NONE only when the
        # dependency list is empty after AUTHENTIC_ESTATE_ROOT consumption.
        # Pre-existing NONE + residual (inconsistent) is restored to CREDENTIAL
        # so UNRESOLVED_DEPENDENCY ⇒ OWNER_GATE != NONE always holds here.
        if not node.DEPENDENCIES:
            if node.OWNER_GATE == "CREDENTIAL":
                node.OWNER_GATE = "NONE"
        elif node.OWNER_GATE == "NONE":
            node.OWNER_GATE = "CREDENTIAL"
        if (
            node.OWNER_GATE == "NONE"
            and not node.DEPENDENCIES
            and node.status
            not in {"READY", "DISPATCHED", "RUNNING", "COMPLETED"}
        ):
            node.status = "READY"
        if mutated:
            changed.append(node.NODE_ID)
    if changed:
        persist_nodes(repo_root, nodes)
    return changed
