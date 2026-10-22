"""AS-CODER-ALPHA-CONNECT-001 — ``atlas connect`` one-command project bind+compile.

Binds an explicit project root to a Vault and runs the Core compile chain:

    ensure vault → discover → ingest → rediscover (SEC-002) → ingest
    → build-indexes → validate

AS-CODER-ALPHA-INCREMENTAL-CONNECT-001: a no-change reconnect inspects the
current discover inventory against the committed connect-manifest + receipt
and skips redundant ingest / rematerialization. Skip is operational only —
not Truth Core authority. A partial or missing prior receipt is never a
clean skip. Validation is not weakened to obtain a skip.

Does not invent authentic pilot estates, does not wake Atlas-OPT, and does not
claim AUTHENTIC_PILOT / RELEASE. Deterministic receipts omit wall-clock times
(NFR-001 / ADR-001).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from atlas_contracts.identity import safe_relative_component
from project_atlas.discovery import discover, write_manifest
from project_atlas.domain.claims import ID_PATTERN
from project_atlas.incremental_connect import (
    IncrementalDecision,
    acquire_project_identity_locks,
    attach_incremental,
    evaluate_incremental_reconnect,
    overlay_prior_lens_fields,
    release_project_identity_locks,
    write_incremental_receipt,
)
from project_atlas.indexes import build_indexes
from project_atlas.ingestion import ingest
from project_atlas.scaffold import ScaffoldError, create_scaffold
from project_atlas.secrets import redact_text, scan_text
from project_atlas.source_identity import (
    IdentityLockError,
    assert_project_uuid_one_owner,
    validate_project_uuid,
)
from project_atlas.validation import validate
from project_atlas.vault_identity import VaultIdentityError, read_vault_identity

PACKAGE_ID = "AS-CODER-ALPHA-CONNECT-001"
GENERATOR_ID = "atlas-coder-alpha-connect-001"
DEFAULT_VAULT_DIRNAME = ".atlas-vault"
BIND_RELATIVE = Path(".atlas") / "connect.json"
MANIFEST_RELATIVE = Path("generated") / "ops" / "connect-manifest.json"
STAGING_MANIFEST_RELATIVE = Path("generated") / "ops" / ".connect-manifest.staging.json"
RECEIPT_RELATIVE = Path("generated") / "ops" / "connect-receipt.json"

# Defense-in-depth globs (DEFAULT_EXCLUDES already drops `.atlas-vault` / `.atlas`
# by path part). Keep patterns for nested / alternate layouts.
_CONNECT_EXCLUDES = [
    ".git/**",
    ".venv/**",
    "node_modules/**",
    "__pycache__/**",
    ".atlas/**",
    ".atlas-vault/**",
    ".atlas-inbox/**",
    # Keep fixture estates out of real-repo dogfood connects.
    "fixtures/**",
    "**/fixtures/**",
    "tests/fixtures/**",
    # Nested package / vendor trees pollute root-project overview purpose.
    "deps/**",
    "advance-005/**",
    "coverage.xml",
]

_SLUG_DASHES = re.compile(r"-+")


def _yaml_scalar(value: str) -> str:
    """Quote a YAML scalar when unsafe as a plain string."""
    if not value or any(ch in value for ch in ":#{}[],&*?|>!%@`'\"\\") or value != value.strip():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def root_identity_fingerprint(project_root: Path) -> str:
    """Stable 8-hex disambiguator for a canonical project root (D-050 R2).

    Derived from the resolved, casefolded POSIX path so the same physical root
    reconnects to the same auto-generated project.id across process restarts
    without depending on discovery order. Distinct roots stay distinct even
    when display-name normalization is lossy (``Foo Bar`` vs ``Foo_Bar``).
    """
    canon = project_root.expanduser().resolve().as_posix().casefold()
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:8]


def project_slug_from_dirname(name: str, *, project_root: Path | None = None) -> str:
    """Derive a safe ``project.id`` slug from a directory name (AT-013 / D-044/D-050).

    Must satisfy domain ``ID_PATTERN`` (ASCII ``[A-Za-z0-9._-]``). Non-ASCII
    directory names keep collision resistance via a deterministic content hash
    while the original dirname remains display ``project.name`` in the marker.
    Unicode letters are casefolded before ASCII extraction (Windows FS honesty).

    When ``project_root`` is provided (connect auto-marker path), a stable root
    fingerprint is appended so lossy slug normalization cannot silently collide
    distinct project roots (D-050 R2). Existing explicit markers are never
    rewritten by this helper.
    """
    raw = unicodedata.normalize("NFKC", (name or "").strip())
    folded = raw.casefold()
    ascii_chars: list[str] = []
    has_non_ascii_alnum = False
    for ch in folded:
        if ch.isascii() and ch.isalnum():
            ascii_chars.append(ch)
        elif ch.isascii():
            ascii_chars.append("-")
        elif ch.isalnum():
            has_non_ascii_alnum = True
            ascii_chars.append("-")
        else:
            ascii_chars.append("-")
    ascii_slug = _SLUG_DASHES.sub("-", "".join(ascii_chars)).strip("-")
    # Hash the casefolded form so Windows case-insensitive FS gets stable IDs.
    digest = hashlib.sha256(folded.encode("utf-8")).hexdigest()[:12]
    if has_non_ascii_alnum:
        slug = f"{ascii_slug}-{digest}" if ascii_slug else f"project-{digest}"
    elif ascii_slug:
        slug = ascii_slug
    else:
        slug = f"project-{digest}"
    if project_root is not None:
        slug = f"{slug}-{root_identity_fingerprint(project_root)}"
    if slug[0].isdigit():
        slug = f"p-{slug}"
    slug = safe_relative_component(slug, label="project.id")
    if not re.fullmatch(ID_PATTERN, slug):
        # Defence in depth — never emit a slug ingestion cannot accept.
        fallback = digest
        if project_root is not None:
            fallback = f"{digest}{root_identity_fingerprint(project_root)}"
        slug = f"project-{fallback}"
    return slug


class ConnectError(ValueError):
    """Fail-closed connect error."""


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _refuse_dangerous_root(path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise ConnectError(f"{label} does not exist: {resolved}")
    if not resolved.is_dir():
        raise ConnectError(f"{label} is not a directory: {resolved}")
    if resolved.parent == resolved:
        raise ConnectError(f"refusing filesystem root as {label}: {resolved}")
    home = Path.home().resolve()
    if resolved == home:
        raise ConnectError(f"refusing home directory as {label}: {resolved}")
    return resolved


def _read_bind(project_root: Path) -> dict[str, Any] | None:
    bind_path = project_root / BIND_RELATIVE
    if not bind_path.is_file():
        return None
    try:
        raw = json.loads(bind_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectError(f"unreadable connect bind file: {bind_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConnectError(f"connect bind file must be a JSON object: {bind_path}")
    return raw


def _bind_owns_root(bind: dict[str, Any], project_root: Path) -> bool:
    """True when bind ``project_root`` matches the caller's resolved cwd/root."""
    recorded = bind.get("project_root")
    if not isinstance(recorded, str) or not recorded.strip():
        return False
    try:
        return Path(recorded).expanduser().resolve() == project_root.resolve()
    except OSError:
        return False


def _require_bind_owns_root(bind: dict[str, Any], project_root: Path) -> None:
    """Fail closed when a bind file was copied/stolen from another tree (D-047 IV)."""
    if not _bind_owns_root(bind, project_root):
        raise ConnectError(
            "connect bind project_root does not match current directory; "
            "re-run `atlas connect .` or pass --vault/--project explicitly"
        )


def resolve_vault_path(project_root: Path, vault: Path | None) -> Path:
    """Resolve vault path: ``--vault`` > bind file > ``<project>/.atlas-vault``."""
    if vault is not None:
        return vault.expanduser().resolve()
    root = project_root.expanduser().resolve()
    bind = _read_bind(root)
    if bind is not None:
        _require_bind_owns_root(bind, root)
        bound = bind.get("vault")
        if isinstance(bound, str) and bound.strip():
            bound_text = bound.strip().replace("\\", "/")
            candidate = Path(bound_text)
            if not candidate.is_absolute():
                candidate = (root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            # Relative bind vaults without ``..`` must stay inside project root
            # after resolve() (blocks `.atlas-vault` → symlink escape; D-047 IV).
            # Explicit ``../other`` or absolute out-of-tree binds remain allowed.
            if (
                not Path(bound_text).is_absolute()
                and ".." not in Path(bound_text).parts
                and not candidate.is_relative_to(root)
            ):
                raise ConnectError(
                    "bind vault resolves outside project root; refuse symlink escape — "
                    "re-run `atlas connect .` or pass --vault explicitly"
                )
            return candidate
    default_vault = (root / DEFAULT_VAULT_DIRNAME).resolve()
    if not default_vault.is_relative_to(root):
        raise ConnectError(
            "default vault path resolves outside project root; refuse symlink escape — "
            "pass --vault explicitly after connect"
        )
    return default_vault


def _ensure_project_marker(project_root: Path) -> bool:
    """Create a minimal marker when absent so ingest can allocate ``project_uuid``.

    Writes both ``project.id`` (slug used as vault project key) and
    ``project.name`` (display). Does not invent UUIDs here — genesis remains
    ingestion's job (SEC-002). Returns True when a marker was written.
    """
    for name in (".atlas-project.yaml", ".atlas-project.yml"):
        if (project_root / name).is_file():
            return False
    display = project_root.name.strip() or "project"
    project_id = project_slug_from_dirname(display, project_root=project_root)
    content = (
        "schema_version: 1\n"
        "project:\n"
        f"  id: {_yaml_scalar(project_id)}\n"
        f"  name: {_yaml_scalar(display)}\n"
    )
    _write_atomic(project_root / ".atlas-project.yaml", content.encode("utf-8"))
    return True


def _active_source_count(manifest: dict[str, Any]) -> int:
    """Count discover records that are eligible for ingest (not excluded)."""
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        return 0
    count = 0
    for row in sources:
        if not isinstance(row, dict):
            continue
        if row.get("exclusion_reason"):
            continue
        count += 1
    return count


def _vault_rel_or_abs(project_root: Path, vault: Path) -> str:
    try:
        return vault.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return vault.resolve().as_posix()


def _read_project_marker(project_root: Path) -> tuple[Path, dict[str, Any]]:
    """Load the root project marker or raise a controlled ConnectError (D-057)."""
    for name in (".atlas-project.yaml", ".atlas-project.yml"):
        marker = project_root / name
        if not marker.is_file():
            continue
        try:
            raw = yaml.safe_load(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConnectError(
                f"INVALID_PROJECT_MARKER: invalid project marker YAML: {marker.name}"
            ) from exc
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ConnectError(
                f"INVALID_PROJECT_MARKER: project marker must be an object: {marker.name}"
            )
        return marker, raw
    raise ConnectError("INVALID_PROJECT_MARKER: project marker not found")


def _identity_without_secret(value: str | None) -> str | None:
    """Return ``value`` only when it is ID-grammar-safe and not secret-shaped.

    AS-SEC-SCAN-CONNECT-YAML-001: ``yaml.safe_load`` decodes quoted ``\\u``/``\\x``
    after any raw-byte scan. An ID-pattern cloud-access-key (e.g. decoded
    ``AKIA…``) must not become bind ``project_id``. Findings are metadata-only.
    """
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or not re.fullmatch(ID_PATTERN, token):
        return None
    if scan_text(token):
        return None
    return token


def _safe_bound_project_id(value: object) -> str | None:
    """Sanitize a candidate bind/receipt project id (AS-SEC-SCAN-CONNECT-YAML-001).

    Ingest may still allocate ``projects/<decoded-secret>/`` (F5-B freeze;
    do not remedi ``ingestion.py`` here). Connect must not copy that
    directory name, or a prior receipt's ``bound_project_id``, into a
    new bind or receipt when the value is secret-shaped.
    """
    if not isinstance(value, str):
        return None
    return _identity_without_secret(value)


def _safe_project_ids(values: object) -> list[str]:
    """Return ID-safe, non-secret project ids from a receipt/vault list."""
    if not isinstance(values, list):
        return []
    return [
        token
        for item in values
        for token in [_safe_bound_project_id(item)]
        if token is not None
    ]


def _marker_project_id(project_root: Path) -> str | None:
    """Return marker ``project.id`` when present, ID-safe, and not secret-shaped."""
    try:
        _marker, raw = _read_project_marker(project_root)
    except ConnectError:
        return None
    project = raw.get("project")
    candidate = None
    if isinstance(project, dict):
        candidate = project.get("id")
    if not isinstance(candidate, str) or not candidate.strip():
        candidate = raw.get("project_id")
    return _identity_without_secret(candidate if isinstance(candidate, str) else None)


def _assert_marker_uuid_ownership(project_root: Path, vault: Path) -> None:
    """Fail closed before discover/ingest when marker UUID contradicts vault ownership."""
    try:
        _marker, raw = _read_project_marker(project_root)
    except ConnectError as exc:
        if "not found" in str(exc):
            return
        raise
    project = raw.get("project")
    project_id = project.get("id") if isinstance(project, dict) else None
    if not isinstance(project_id, str) or not project_id.strip():
        project_id = raw.get("project_id")
    raw_uuid = raw.get("project_uuid")
    if not isinstance(project_id, str) or not project_id.strip():
        return
    if raw_uuid is None:
        return
    safe_id = _identity_without_secret(project_id)
    if safe_id is None:
        return
    project_uuid = validate_project_uuid(str(raw_uuid))
    assert_project_uuid_one_owner(vault, {safe_id: project_uuid})


def _write_bind(
    project_root: Path,
    vault: Path,
    vault_id: str,
    *,
    project_ids: list[str] | None = None,
    primary_project_id: str | None = None,
) -> Path:
    projects = sorted(
        {
            token
            for item in (project_ids or [])
            for token in [_identity_without_secret(str(item))]
            if token is not None
        }
    )
    primary: str | None = None
    safe_primary = _identity_without_secret(primary_project_id)
    if safe_primary is not None and safe_primary in projects:
        primary = safe_primary
    elif len(projects) == 1:
        primary = projects[0]
    payload = {
        "schema_version": 1,
        "schema": "atlas.connect.bind.v1",
        "package": PACKAGE_ID,
        "project_root": project_root.resolve().as_posix(),
        "vault": _vault_rel_or_abs(project_root, vault),
        "vault_id": vault_id,
        "project_ids": projects,
        "project_id": primary,
        "generated": {"by": GENERATOR_ID},
    }
    path = project_root / BIND_RELATIVE
    _write_atomic(
        path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return path


def resolve_bound_vault(cwd: Path | None = None) -> Path:
    """Resolve vault from ``.atlas/connect.json`` under ``cwd`` (fail closed)."""
    root = (cwd or Path.cwd()).expanduser().resolve()
    bind = _read_bind(root)
    if bind is None:
        # Fall back to default vault dir when present after connect.
        # Refuse symlink escape outside the project root (D-047 IV).
        candidate = root / DEFAULT_VAULT_DIRNAME
        if candidate.is_dir():
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                raise ConnectError(
                    "default vault path resolves outside project root; "
                    "refuse symlink escape — pass --vault explicitly after connect"
                )
            return resolved
        raise ConnectError(
            "no connect bind found; run `atlas connect .` or pass --vault explicitly"
        )
    _require_bind_owns_root(bind, root)
    return resolve_vault_path(root, None)


def resolve_bound_project_id(
    cwd: Path | None = None,
    *,
    vault: Path | None = None,
    requested: str | None = None,
) -> str:
    """Resolve a single project id for stranger CLI commands (fail closed).

    Ambiguous multi-project vaults without an explicit ``--project`` raise.
    When ``vault`` is an explicit override that differs from the bind vault,
    bind ``project_id`` is ignored and resolution uses that vault's projects
    (D-047 IV — no cross-vault project scoping).
    """
    if requested is not None and str(requested).strip():
        return safe_relative_component(str(requested).strip(), label="project id")
    root = (cwd or Path.cwd()).expanduser().resolve()
    explicit_vault = vault.expanduser().resolve() if vault is not None else None
    bind = _read_bind(root)
    use_bind_project = False
    if bind is not None:
        if not _bind_owns_root(bind, root):
            # Stolen/copied bind: only allow recovery via explicit --vault.
            if explicit_vault is None:
                _require_bind_owns_root(bind, root)
        else:
            bind_vault = resolve_vault_path(root, None)
            if explicit_vault is None or explicit_vault == bind_vault:
                use_bind_project = True
    if use_bind_project and bind is not None:
        bound = bind.get("project_id")
        if isinstance(bound, str) and bound.strip():
            return safe_relative_component(bound.strip(), label="project id")
        bound_ids = bind.get("project_ids")
        if isinstance(bound_ids, list):
            ids = [
                safe_relative_component(str(item).strip(), label="project id")
                for item in bound_ids
                if isinstance(item, str) and item.strip()
            ]
            if len(ids) == 1:
                return ids[0]
            if len(ids) > 1:
                raise ConnectError(
                    "ambiguous project_ids in connect bind; pass --project explicitly "
                    f"(candidates: {', '.join(ids)})"
                )
    vault_path = explicit_vault or resolve_bound_vault(root)
    projects = _safe_project_ids(_list_vault_projects(vault_path))
    if len(projects) == 1:
        return projects[0]
    if not projects:
        raise ConnectError(
            "no projects found in vault; run `atlas connect .` or pass --project"
        )
    raise ConnectError(
        "ambiguous vault projects; pass --project explicitly "
        f"(candidates: {', '.join(projects)})"
    )


def _write_receipt(vault: Path, report: dict[str, Any]) -> Path:
    """Persist the connect receipt without credential-shaped identities.

    AS-SEC-SCAN-CONNECT-YAML-001 / P1-RECEIPT-BOUND: ingest may still mint
    ``projects/<decoded-secret>/`` (F5-B). The receipt must not copy that
    name into ``bound_project_id`` or echo it in ``projects``. Defense in
    depth redacts any remaining secret-shaped spans before the write.
    """
    report["bound_project_id"] = _safe_bound_project_id(report.get("bound_project_id"))
    report["projects"] = _safe_project_ids(report.get("projects"))
    path = vault / RECEIPT_RELATIVE
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if scan_text(serialized):
        serialized = redact_text(serialized)
        if not serialized.endswith("\n"):
            serialized += "\n"
    _write_atomic(path, serialized.encode("utf-8"))
    return path


def _read_connect_receipt(vault: Path) -> tuple[str, dict[str, Any] | None]:
    path = vault / RECEIPT_RELATIVE
    if not path.is_file():
        return "absent", None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "unreadable", None
    if isinstance(raw, dict):
        return "ok", raw
    return "unreadable", None


def _list_vault_projects(vault: Path) -> list[str]:
    projects_root = vault / "projects"
    if not projects_root.is_dir():
        return []
    return sorted(
        path.name
        for path in projects_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def _finish_no_change_reconnect(
    *,
    project_root: Path,
    vault_path: Path,
    report: dict[str, Any],
    decision: IncrementalDecision,
    prior_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Operational skip: reuse prior lenses, rewrite bind/receipt, no ingest."""
    overlay_prior_lens_fields(report, prior_receipt)
    report["documents_ingested"] = 0
    report["projects"] = _list_vault_projects(vault_path) or list(
        prior_receipt.get("projects") or []
    )
    attach_incremental(report, decision)
    primary = _marker_project_id(project_root)
    vault_projects = list(report["projects"] or [])
    if primary is None and len(vault_projects) == 1:
        primary = _safe_bound_project_id(vault_projects[0])
    if primary is None:
        primary = _safe_bound_project_id(prior_receipt.get("bound_project_id"))
    locks = acquire_project_identity_locks(vault_path, [str(item) for item in vault_projects])
    try:
        bind_path = _write_bind(
            project_root,
            vault_path,
            str(report["vault_id"] or ""),
            project_ids=vault_projects,
            primary_project_id=primary,
        )
        report["bind_path"] = bind_path.as_posix()
        report["bound_project_id"] = primary
        incremental_path = write_incremental_receipt(vault_path, decision)
        report["incremental_receipt_path"] = incremental_path.as_posix()
        receipt_path = _write_receipt(vault_path, report)
        report["receipt_path"] = receipt_path.as_posix()
    except IdentityLockError as exc:
        raise ConnectError(str(exc)) from exc
    except (OSError, ValueError, TypeError) as exc:
        raise ConnectError(str(exc)) from exc
    finally:
        release_project_identity_locks(locks)
    report["status"] = "connected"
    return report


def connect_project(
    source: Path,
    *,
    vault: Path | None = None,
    dry_run: bool = False,
    include_portfolio: bool = False,
    skip_validate: bool = False,
    excludes: list[str] | None = None,
    max_file_size: int = 10 * 1024 * 1024,
) -> dict[str, Any]:
    """Bind ``source`` to a Vault and run the Core compile chain."""
    project_root = _refuse_dangerous_root(source, label="project root")
    vault_path = resolve_vault_path(project_root, vault)
    # Vault may live under the project (default ``.atlas-vault``); refuse only
    # when the caller points vault at home/filesystem root.
    if vault_path.parent == vault_path:
        raise ConnectError(f"refusing filesystem root as vault: {vault_path}")
    if vault_path == Path.home().resolve():
        raise ConnectError(f"refusing home directory as vault: {vault_path}")

    merged_excludes = list(dict.fromkeys([*(excludes or []), *_CONNECT_EXCLUDES]))
    steps = [
        "ensure_vault",
        "ensure_project_marker",
        "discover",
        "ingest",
        "rediscover",
        "ingest_baseline",
        "build_indexes",
    ]
    if include_portfolio:
        steps.append("build_portfolio")
    if not skip_validate:
        steps.append("validate")
    steps.extend(
        [
            # Architecture before overview so D-044 A3 coverage reconciliation sees
            # ans-architecture-* on the first connect write (D-047 IV).
            "materialize_architecture",
            "materialize_overview",
            "materialize_state",
            "materialize_changed",
            "materialize_decisions",
            "materialize_unknown",
            "materialize_roadmap",
            "materialize_next",
            "materialize_brief",
            "materialize_obsidian",
            "write_bind",
            "write_receipt",
        ]
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "schema": "atlas.connect.receipt.v1",
        "package": PACKAGE_ID,
        "status": "planned" if dry_run else "connected",
        "project_root": project_root.as_posix(),
        "vault": vault_path.as_posix(),
        "steps": steps,
        "documents_discovered": 0,
        "documents_ingested": 0,
        "projects": [],
        "overview_answers": [],
        "architecture_answers": [],
        "state_answers": [],
        "changed_answers": [],
        "decisions_answers": [],
        "unknown_answers": [],
        "roadmap_answers": [],
        "next_answers": [],
        "brief_paths": [],
        "obsidian_notes": [],
        "marker_created": False,
        "vault_created": False,
        "vault_id": None,
        "bind_path": (project_root / BIND_RELATIVE).as_posix(),
        "manifest_path": (vault_path / MANIFEST_RELATIVE).as_posix(),
        "receipt_path": (vault_path / RECEIPT_RELATIVE).as_posix(),
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "release_certified": False,
            "atlas_opt_wake_gate": "CLOSED",
        },
    }

    if dry_run:
        report["status"] = "dry_run"
        return report

    vault_existed = (vault_path / "index.md").is_file() or (
        vault_path / ".atlas" / "vault.json"
    ).is_file()
    try:
        create_scaffold(vault_path)
    except (ScaffoldError, OSError) as exc:
        raise ConnectError(f"unable to ensure vault: {exc}") from exc
    report["vault_created"] = not vault_existed

    try:
        identity = read_vault_identity(vault_path)
        report["vault_id"] = identity.vault_id
    except VaultIdentityError as exc:
        raise ConnectError(f"vault identity unavailable after scaffold: {exc}") from exc

    report["marker_created"] = _ensure_project_marker(project_root)
    # D-057: reject copied UUID / contradictory identity before discover/ingest.
    try:
        _assert_marker_uuid_ownership(project_root, vault_path)
    except ValueError as exc:
        raise ConnectError(str(exc)) from exc

    manifest_path = vault_path / MANIFEST_RELATIVE
    staging_manifest = vault_path / STAGING_MANIFEST_RELATIVE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    # D-050 R4: mutate candidate inventory on a staging path; promote to the
    # committed connect-manifest only after ingest+validate succeed.
    def _discard_staging() -> None:
        with contextlib.suppress(OSError):
            staging_manifest.unlink(missing_ok=True)

    decision: IncrementalDecision | None = None
    try:
        manifest = discover(
            project_root,
            excludes=merged_excludes,
            max_file_size=max_file_size,
        )
        report["documents_discovered"] = _active_source_count(manifest)
        decision = evaluate_incremental_reconnect(
            vault=vault_path,
            project_root=project_root,
            current_manifest=manifest,
            vault_id=str(report["vault_id"] or ""),
            include_portfolio=include_portfolio,
            skip_validate=skip_validate,
            excludes=merged_excludes,
            max_file_size=max_file_size,
            manifest_relative=MANIFEST_RELATIVE,
            staging_relative=STAGING_MANIFEST_RELATIVE,
            receipt_relative=RECEIPT_RELATIVE,
        )
        if decision.can_skip:
            prior_status, prior_receipt = _read_connect_receipt(vault_path)
            if prior_status != "ok" or prior_receipt is None:
                raise ConnectError(
                    "incremental skip refused: prior connect receipt unreadable"
                )
            _discard_staging()
            return _finish_no_change_reconnect(
                project_root=project_root,
                vault_path=vault_path,
                report=report,
                decision=decision,
                prior_receipt=prior_receipt,
            )

        write_manifest(manifest, staging_manifest)

        ingest(
            staging_manifest,
            vault_path,
            authorized_source_root=project_root,
        )
        # SEC-002: marker genesis may rewrite ``.atlas-project.yaml``.
        manifest = discover(
            project_root,
            excludes=merged_excludes,
            max_file_size=max_file_size,
        )
        write_manifest(manifest, staging_manifest)
        report["documents_discovered"] = _active_source_count(manifest)

        second = ingest(
            staging_manifest,
            vault_path,
            authorized_source_root=project_root,
        )
        index_result = build_indexes(vault_path)
        if include_portfolio:
            from project_atlas.portfolio import build_portfolio as run_build_portfolio

            run_build_portfolio(vault_path)
        if not skip_validate:
            validate(vault_path)

        # Commit connect-manifest only after ingest+validate succeeded (D-050 R4).
        # Do not roll this back later: ingest already promoted vault ownership, and
        # restoring a prior manifest would desync quarantine/lineage indexes.
        staging_bytes = staging_manifest.read_bytes()
        _write_atomic(manifest_path, staging_bytes)
        _discard_staging()

        # Coder Alpha derived lenses for Knowledge/Ask-live + project brief.
        from project_atlas.overview import materialize_overview_lenses
        from project_atlas.project_architecture import materialize_architecture_lenses
        from project_atlas.project_brief import materialize_project_briefs
        from project_atlas.project_changed import materialize_changed_lenses
        from project_atlas.project_decisions import materialize_decisions_lenses
        from project_atlas.project_next import materialize_next_lenses
        from project_atlas.project_roadmap import materialize_roadmap_lenses
        from project_atlas.project_state import materialize_state_lenses
        from project_atlas.project_unknown import materialize_unknown_lenses

        architecture = materialize_architecture_lenses(vault_path)
        overview = materialize_overview_lenses(vault_path)
        state = materialize_state_lenses(vault_path)
        changed = materialize_changed_lenses(vault_path, manifest=manifest)
        decisions = materialize_decisions_lenses(vault_path)
        unknown = materialize_unknown_lenses(vault_path)
        roadmap = materialize_roadmap_lenses(vault_path)
        nxt = materialize_next_lenses(vault_path)
        # refresh=False: lenses just written above.
        brief = materialize_project_briefs(vault_path, refresh=False)
        from project_atlas.obsidian_projection import materialize_obsidian_projection

        obsidian = materialize_obsidian_projection(vault_path, refresh_brief=False)
    except ConnectError:
        _discard_staging()
        raise
    except IdentityLockError as exc:
        _discard_staging()
        raise ConnectError(str(exc)) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _discard_staging()
        raise ConnectError(str(exc)) from exc

    if decision is None:
        raise ConnectError("incremental decision missing after compile")
    report["documents_ingested"] = int(second.get("documents_ingested", 0))
    report["projects"] = _list_vault_projects(vault_path)
    report["indexes"] = {
        "projects": index_result.get("projects"),
        "sources": index_result.get("sources"),
    }
    report["overview_answers"] = overview.get("answers_written", [])
    report["architecture_answers"] = architecture.get("answers_written", [])
    report["state_answers"] = state.get("answers_written", [])
    report["changed_answers"] = changed.get("answers_written", [])
    report["changed_delta"] = changed.get("delta", {})
    report["decisions_answers"] = decisions.get("answers_written", [])
    report["unknown_answers"] = unknown.get("answers_written", [])
    report["roadmap_answers"] = roadmap.get("answers_written", [])
    report["next_answers"] = nxt.get("answers_written", [])
    report["brief_paths"] = brief.get("briefs_written", [])
    report["obsidian_notes"] = obsidian.get("notes_written", [])
    projection_paths = [
        *(report["overview_answers"] or []),
        *(report["architecture_answers"] or []),
        *(report["state_answers"] or []),
        *(report["changed_answers"] or []),
        *(report["decisions_answers"] or []),
        *(report["unknown_answers"] or []),
        *(report["roadmap_answers"] or []),
        *(report["next_answers"] or []),
        *(report["brief_paths"] or []),
        *(report["obsidian_notes"] or []),
    ]
    decision.projections_regenerated = len(projection_paths)
    decision.physical_writes = int(report["documents_ingested"]) + decision.projections_regenerated
    attach_incremental(report, decision)
    report["incremental_receipt_path"] = write_incremental_receipt(vault_path, decision).as_posix()

    # Prefer this source root's marker project.id as bind primary so shared-vault
    # connects do not immediately become stranger-CLI ambiguous (Codex P2).
    primary = _marker_project_id(project_root)
    vault_projects = list(report["projects"] or [])
    if primary is None and len(vault_projects) == 1:
        primary = _safe_bound_project_id(vault_projects[0])
    try:
        bind_path = _write_bind(
            project_root,
            vault_path,
            str(report["vault_id"] or ""),
            project_ids=vault_projects,
            primary_project_id=primary,
        )
        report["bind_path"] = bind_path.as_posix()
        report["bound_project_id"] = primary
        receipt_path = _write_receipt(vault_path, report)
        report["receipt_path"] = receipt_path.as_posix()
    except (OSError, ValueError, TypeError) as exc:
        # Manifest already matches promoted ingest; do not roll it back.
        raise ConnectError(str(exc)) from exc
    report["status"] = "connected"
    return report
