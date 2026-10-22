"""AS-CODER-ALPHA-KNOWLEDGE-ESTATE-DISCOVERY-001 — bounded knowledge estate discovery.

D-049 / D-063: find probable projects and knowledge under an explicit authorized
root. Discovery sits ABOVE the accepted Coder Alpha identity layer and must
never weaken:

    one project_uuid → one durable project identity
    DISCOVER != INGEST != TRUST != AUTHORITY
    discovery match != proof of ownership
    heuristic similarity != identity

CONNECTED requires durable bind / source-root ownership evidence — never
merely ``project.id`` presence in ``vault/projects/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from project_atlas.connect import (
    BIND_RELATIVE,
    MANIFEST_RELATIVE,
    root_identity_fingerprint,
)
from project_atlas.estate_path_index import (
    DiscoveryPerf,
    ancestor_items_from_index,
    candidate_path_key_from_record,
    canonical_path_key_resolved,
    current_discovery_perf,
    estate_region_resolved,
    has_selected_project_ancestor,
    is_canonical_under,
    parent_canonical_key,
    phase_timer,
    reset_discovery_perf,
)
from project_atlas.secrets import scan_text
from project_atlas.source_identity import (
    load_allocation_project_uuids,
    load_allocation_uuid_owners,
    validate_project_uuid,
)

PACKAGE_ID = "AS-CODER-ALPHA-KNOWLEDGE-ESTATE-DISCOVERY-001"
DIRECTIVE_FAMILY = "D-PROJECT-ATLAS-KNOWLEDGE-ESTATE-DISCOVERY-049"
REPORT_SCHEMA = "estate-discovery-report"
REPORT_RELATIVE = Path("generated") / "ops" / "estate-discovery-report.json"
INCREMENTAL_CACHE_RELATIVE = Path("generated") / "ops" / "estate-discovery-cache.json"
DURABLE_SOURCE_MANIFEST = Path("sources") / "manifests" / "source-manifest.json"
CONNECT_RECEIPT_RELATIVE = Path("generated") / "ops" / "connect-receipt.json"

# Windows FILE_ATTRIBUTE_REPARSE_POINT
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

MatchState = Literal[
    "EXACT",
    "STRONG_EVIDENCE",
    "LIKELY",
    "AMBIGUOUS",
    "UNMATCHED",
    "CONFLICTING",
]

LifecycleState = Literal[
    "FOUND",
    "CANDIDATE",
    "CLASSIFIED",
    "PROJECT_MATCHED",
    "POLICY_REVIEW",
    "CONNECTED",
    "INGESTED",
    "VERIFIED",
]

Category = Literal[
    "DISCOVERED_PROJECTS",
    "NEW_KNOWLEDGE",
    "AMBIGUOUS_MATCHES",
    "UNMATCHED_KNOWLEDGE",
    "IGNORED",
    "CONNECTED",
]

KnowledgeRelation = Literal[
    "KNOWLEDGE_DISCOVERED",
    "KNOWLEDGE_PROJECT_MATCHED",
    "KNOWLEDGE_AMBIGUOUS",
    "KNOWLEDGE_UNMATCHED",
]

IGNORE_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".tmp",
        ".atlas-vault",
        ".atlas",
        "vendor",
        "generated",
        "coverage",
        ".tox",
        ".eggs",
        ".cache",
        "cache",
        "target",
        "out",
    }
)

PROJECT_MARKER_FILES = (".atlas-project.yaml", ".atlas-project.yml")
PROJECT_MARKER_NESTED = (
    Path(".atlas") / "project.yaml",
    Path(".atlas") / "project.yml",
)
PROJECT_MANIFEST_FILES = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "cargo.toml",
        "go.mod",
        "pom.xml",
        "composer.json",
        "gemfile",
        "mix.exs",
    }
)
PROJECT_DOC_NAMES = frozenset(
    {
        "readme",
        "readme.md",
        "readme.txt",
        "agents.md",
        "claude.md",
        "architecture.md",
        "architecture",
    }
)
PROJECT_DIR_SIGNALS = frozenset({".github", ".cursor", "src", "docs", "adr"})
KNOWLEDGE_DIR_SIGNALS = frozenset(
    {
        "notes",
        "research",
        "architecture",
        "decisions",
        "meetings",
        "specs",
        "roadmaps",
        "docs",
        "adr",
    }
)

DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_PROJECT_CANDIDATES = 500
DEFAULT_MAX_KNOWLEDGE_CANDIDATES = 500
CANDIDATE_SELECTION_POLICY = "deterministic_hierarchical_fair_v2"
MAX_FAMILY_REPRESENTATIVES = 2
# Cheap preselection budget: enrich at most emit_limit * multiplier
# sightings, not every project sighting (D-084-C). Not a per-folder quota.
PRESELECT_MULTIPLIER = 3


class EstateDiscoveryError(ValueError):
    """Fail-closed estate discovery error."""


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    """One explainable evidence row (no fake confidence percentage)."""

    kind: str
    detail: str
    weight: str  # exact | strong | likely | weak | conflict | invalid

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "detail": self.detail, "weight": self.weight}


@dataclass
class DiscoveryCandidate:
    """A discovered project or knowledge candidate (pre-ingest)."""

    candidate_id: str
    kind: Literal["project", "knowledge", "obsidian_vault"]
    path: str
    display_name: str
    lifecycle: LifecycleState
    match_state: MatchState
    category: Category
    why_matched: list[str] = field(default_factory=list)
    why_connected: list[str] = field(default_factory=list)
    match_evidence: list[dict[str, str]] = field(default_factory=list)
    conflicting_evidence: list[dict[str, str]] = field(default_factory=list)
    required_review: bool = False
    required_action: str | None = None
    signals: list[str] = field(default_factory=list)
    fingerprint: dict[str, Any] = field(default_factory=dict)
    matched_project_id: str | None = None
    matched_project_uuid: str | None = None
    knowledge_relation: KnowledgeRelation | None = None
    ignored_reason: str | None = None
    candidate_family: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "path": self.path,
            "display_name": self.display_name,
            "lifecycle": self.lifecycle,
            "match_state": self.match_state,
            "category": self.category,
            "why_matched": list(self.why_matched),
            "why_connected": list(self.why_connected),
            "match_evidence": list(self.match_evidence),
            "conflicting_evidence": list(self.conflicting_evidence),
            "required_review": self.required_review,
            "required_action": self.required_action,
            "signals": sorted(self.signals),
            "fingerprint": dict(self.fingerprint),
            "matched_project_id": self.matched_project_id,
            "matched_project_uuid": self.matched_project_uuid,
            "knowledge_relation": self.knowledge_relation,
            "ignored_reason": self.ignored_reason,
            "candidate_family": self.candidate_family,
        }


@dataclass(frozen=True, slots=True)
class VaultProjectIdentity:
    """Governed vault project identity for discovery matching (read-only)."""

    project_id: str
    project_uuid: str | None
    bind_root: str | None = None
    package_name: str | None = None
    git_remote: str | None = None
    bind_proven: bool = False
    identity_sources: tuple[str, ...] = ()


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


ROOT_MODE_BOUNDED_DIRECTORY = "bounded-directory"
ROOT_MODE_OWNER_AUTHORIZED_VOLUME = "owner-authorized-volume"
ROOT_MODE_BOUNDED_TOKEN = "BOUNDED_DIRECTORY"
ROOT_MODE_VOLUME_TOKEN = "OWNER_AUTHORIZED_VOLUME_ROOT"
VOLUME_KIND_NONE = "NONE"
VOLUME_KIND_NON_SYSTEM_WINDOWS = "NON_SYSTEM_WINDOWS_VOLUME"

_WIN_DRIVE_LETTER = re.compile(r"^([A-Za-z]):")


@dataclass(frozen=True)
class AuthorizedRootDecision:
    """Resolved discovery-root policy (D-078). Never a connect/ingest grant."""

    resolved: Path
    authorized_root_mode: str
    volume_root_authorized: bool
    volume_root_kind: str


def normalize_root_mode(value: str) -> str:
    """Map CLI/API tokens to the two explicit root modes. No --force aliases."""
    key = value.strip().lower().replace("_", "-")
    if key in {ROOT_MODE_BOUNDED_DIRECTORY, "bounded"}:
        return ROOT_MODE_BOUNDED_DIRECTORY
    if key in {ROOT_MODE_OWNER_AUTHORIZED_VOLUME, "owner-authorized-volume-root"}:
        return ROOT_MODE_OWNER_AUTHORIZED_VOLUME
    raise EstateDiscoveryError(f"UNKNOWN_ROOT_MODE: unsupported root mode {value!r}")


def is_filesystem_root(path: Path) -> bool:
    resolved = path.expanduser().resolve(strict=False)
    return resolved.parent == resolved


def is_unc_root(path: Path) -> bool:
    """True for UNC/network roots. Not a local Windows drive volume."""
    raw = os.fspath(path).replace("/", "\\")
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    if raw.startswith("\\\\") and not _WIN_DRIVE_LETTER.match(raw.lstrip("\\")):
        # \\server\share … — not \\?\C:\ (already stripped)
        rest = raw[2:]
        return not rest.startswith("?\\")
    try:
        anchor = path.expanduser().resolve(strict=False).anchor.replace("/", "\\")
    except (OSError, RuntimeError):
        return True
    return bool(
        anchor.startswith("\\\\")
        and not _WIN_DRIVE_LETTER.match(anchor.lstrip("\\"))
    )


def _windows_volume_letter(path: Path) -> str | None:
    for candidate in (os.fspath(path), getattr(path, "anchor", ""), str(path)):
        text = str(candidate).replace("/", "\\")
        if text.startswith("\\\\?\\"):
            text = text[4:]
        match = _WIN_DRIVE_LETTER.match(text)
        if match:
            return match.group(1).upper()
    return None


def is_windows_drive_volume_root(
    path: Path, *, host_os: str | None = None
) -> bool:
    """True for a local Windows drive-volume root (D:\\), never UNC or /."""
    host = host_os if host_os is not None else os.name
    if host != "nt":
        return False
    if is_unc_root(path):
        return False
    resolved = path.expanduser().resolve(strict=False)
    if not is_filesystem_root(resolved):
        return False
    return _windows_volume_letter(resolved) is not None


def windows_system_drive_letter(
    *, environ: dict[str, str] | None = None
) -> str | None:
    env = environ if environ is not None else dict(os.environ)
    for key in ("SystemDrive", "SYSTEMDRIVE"):
        raw = env.get(key)
        if isinstance(raw, str) and raw.strip():
            letter = raw.strip().rstrip(":\\/")
            if len(letter) == 1 and letter.isalpha():
                return letter.upper()
    for key in ("SystemRoot", "SYSTEMROOT", "WINDIR", "windir"):
        raw = env.get(key)
        if isinstance(raw, str) and raw.strip():
            match = _WIN_DRIVE_LETTER.match(raw.replace("/", "\\"))
            if match:
                return match.group(1).upper()
    return None


def is_windows_system_volume_root(
    path: Path,
    *,
    host_os: str | None = None,
    environ: dict[str, str] | None = None,
) -> bool:
    """True when path is the Windows system volume root.

    If the host is Windows and the system drive cannot be determined, fail
    closed (treat as system) so C:\\ cannot be silently classified as a
    dedicated dev volume.
    """
    if not is_windows_drive_volume_root(path, host_os=host_os):
        return False
    letter = _windows_volume_letter(path)
    system = windows_system_drive_letter(environ=environ)
    if system is None:
        host = host_os if host_os is not None else os.name
        return host == "nt"
    return letter == system


def authorize_discovery_root(
    path: Path,
    *,
    root_mode: str = ROOT_MODE_BOUNDED_DIRECTORY,
    host_os: str | None = None,
    environ: dict[str, str] | None = None,
) -> AuthorizedRootDecision:
    """SAFE DEFAULT + explicit Windows non-system volume capability (D-078).

    Volume authorization permits traversal/discovery only. It does not
    connect, ingest, mint identity, or write owner files.
    """
    mode = normalize_root_mode(root_mode)
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.exists():
        raise EstateDiscoveryError(
            f"AUTHORIZED_ROOT_DOES_NOT_EXIST: authorized root does not exist: {resolved}"
        )
    if not resolved.is_dir():
        raise EstateDiscoveryError(
            f"AUTHORIZED_ROOT_NOT_A_DIRECTORY: authorized root is not a directory: {resolved}"
        )
    home = Path.home().resolve()
    if _paths_equal(resolved, home):
        raise EstateDiscoveryError(
            "HOME_DIRECTORY_NOT_ALLOWED: refusing home directory as "
            f"authorized discovery root: {resolved}"
        )

    unc = is_unc_root(path) or is_unc_root(resolved)
    win_vol = is_windows_drive_volume_root(resolved, host_os=host_os)
    sys_vol = is_windows_system_volume_root(
        resolved, host_os=host_os, environ=environ
    )
    fs_root = is_filesystem_root(resolved)

    if mode == ROOT_MODE_OWNER_AUTHORIZED_VOLUME:
        if unc:
            raise EstateDiscoveryError(
                "UNC_VOLUME_ROOT_NOT_ALLOWED: owner-authorized-volume does not "
                f"apply to UNC/network roots: {resolved}"
            )
        if not win_vol:
            if fs_root:
                raise EstateDiscoveryError(
                    "FILESYSTEM_ROOT_NOT_ALLOWED: refusing filesystem root as "
                    f"authorized discovery root: {resolved}"
                )
            raise EstateDiscoveryError(
                "VOLUME_MODE_REQUIRES_WINDOWS_VOLUME_ROOT: "
                "--root-mode owner-authorized-volume requires a Windows "
                f"drive-volume root (for example D:\\); refusing {resolved}"
            )
        if sys_vol:
            raise EstateDiscoveryError(
                "SYSTEM_VOLUME_ROOT_NOT_ALLOWED: refusing Windows system "
                f"volume root: {resolved}"
            )
        return AuthorizedRootDecision(
            resolved=resolved,
            authorized_root_mode=ROOT_MODE_VOLUME_TOKEN,
            volume_root_authorized=True,
            volume_root_kind=VOLUME_KIND_NON_SYSTEM_WINDOWS,
        )

    if fs_root or win_vol:
        raise EstateDiscoveryError(
            "FILESYSTEM_ROOT_NOT_ALLOWED: refusing filesystem root as "
            f"authorized discovery root: {resolved}"
        )
    return AuthorizedRootDecision(
        resolved=resolved,
        authorized_root_mode=ROOT_MODE_BOUNDED_TOKEN,
        volume_root_authorized=False,
        volume_root_kind=VOLUME_KIND_NONE,
    )


def refuse_dangerous_authorized_root(path: Path) -> Path:
    """Default bounded-directory policy (filesystem root / home refused)."""
    return authorize_discovery_root(
        path, root_mode=ROOT_MODE_BOUNDED_DIRECTORY
    ).resolved


def _casefold_paths() -> bool:
    """True when the host filesystem treats paths as case-insensitive."""
    return os.name == "nt" or sys.platform == "darwin"


def canonical_path_key(path: Path) -> str:
    """Platform-correct path identity key for candidate IDs (P7).

    Linux (case-sensitive): preserve case so Foo/ and foo/ stay distinct.
    Windows / macOS: casefold so case aliases collapse to one candidate.
    Unicode is NFC-normalized for deterministic keys.

    Untrusted / external paths must use this helper (resolves). Already
    resolved paths should use ``canonical_path_key_resolved``.
    """
    perf = current_discovery_perf()
    perf.path_resolve_calls += 1
    resolved = path.expanduser().resolve(strict=False)
    return canonical_path_key_resolved(resolved)


def _paths_equal(a: Path, b: Path) -> bool:
    return canonical_path_key(a) == canonical_path_key(b)


def _under_authorized(path: Path, authorized: Path) -> bool:
    """Security boundary for untrusted paths. Resolves both sides.

    Do not call this in inner knowledge↔project selection loops. After a
    path is already resolved and accepted, use ``is_canonical_under``.
    """
    perf = current_discovery_perf()
    perf.under_authorized_calls += 1
    try:
        perf.path_resolve_calls += 2
        path.resolve(strict=False).relative_to(authorized.resolve(strict=False))
        return True
    except ValueError:
        return False
    except (OSError, RuntimeError):
        # Symlink loops / unresolvable paths are not "inside" the root.
        return False


def _is_reparse_or_symlink(entry: Path) -> bool:
    """True for symlinks and Windows reparse/junction points (P6)."""
    perf = current_discovery_perf()
    try:
        perf.filesystem_stat_calls += 1
        if entry.is_symlink():
            return True
    except OSError:
        return True
    if os.name == "nt":
        try:
            perf.filesystem_stat_calls += 1
            st = os.lstat(entry)
        except OSError:
            return True
        attrs = int(getattr(st, "st_file_attributes", 0) or 0)
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    return False


def _reparse_escape(entry: Path, authorized: Path) -> bool:
    """True when a reparse/symlink target resolves outside authorized root.

    Symlink loops and unresolvable reparse targets are treated as escapes
    (ignored, not crashed) — D-064 overnight IV.
    """
    if not _is_reparse_or_symlink(entry):
        return False
    try:
        target = entry.resolve(strict=False)
    except (OSError, RuntimeError):
        return True
    return not _under_authorized(target, authorized)


def _candidate_id(kind: str, path: Path, *, path_key: str | None = None) -> str:
    key = path_key if path_key is not None else canonical_path_key(path)
    digest = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:16]
    return f"{kind}-{digest}"


def _safe_read_text(path: Path, *, limit: int = 64_000) -> str | None:
    """Best-effort UTF-8 read. Returns None when unreadable / binary."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit)
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_json_object(path: Path) -> dict[str, Any] | None:
    text = _safe_read_text(path, limit=2_000_000)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _unwrap_git_config_quotes(url: str) -> str:
    """Strip one matching git-config quote pair; do not invent a full parser."""
    raw = url.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        return raw[1:-1].strip()
    return raw


def sanitize_git_remote_url(url: str) -> str:
    """Strip credential userinfo from git remotes (D-064 / D-067 secret hygiene).

    Discovery may use remotes as fingerprint evidence, but must never echo
    passwords / tokens embedded in URLs into reports, CLI, API, or Web.
    Quoted git-config values (``url = "https://user:pass@host/repo.git"``)
    are unwrapped before ``urlsplit`` so userinfo is not left in-place.
    """
    from urllib.parse import urlsplit, urlunsplit

    raw = _unwrap_git_config_quotes(url)
    if not raw:
        return raw
    if "://" in raw:
        parts = urlsplit(raw)
        if parts.username is None and parts.password is None:
            return raw
        host = parts.hostname or ""
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        return urlunsplit(
            (parts.scheme, host, parts.path, parts.query, parts.fragment)
        )
    # scp-like forms rarely embed passwords; leave unchanged.
    return raw


def _git_remote_url(directory: Path) -> str | None:
    """Read git remote without executing git (config parse only)."""
    config = directory / ".git" / "config"
    if not config.is_file() or _is_reparse_or_symlink(config):
        return None
    text = _safe_read_text(config)
    if text is None:
        return None
    origin_url: str | None = None
    any_url: str | None = None
    section: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip().lower() != "url":
            continue
        url = value.strip()
        if not url:
            continue
        if section == 'remote "origin"':
            origin_url = url
        elif section and section.startswith("remote ") and any_url is None:
            any_url = url
    chosen = origin_url or any_url
    if chosen is None:
        return None
    return sanitize_git_remote_url(chosen)


def _identity_is_secret_shaped(value: str) -> bool:
    """True when a decoded identity token matches secret scanners.

    Findings stay metadata-only (NFR-004). Used after YAML/JSON decode so
    quoted ``\\u`` / ``\\x`` escapes cannot persist as fingerprint identity.
    AS-SEC-SCAN-ESTATE-DISCOVERY-YAML-001.
    """
    return bool(scan_text(value))


def _package_name(directory: Path) -> str | None:
    pkg = directory / "package.json"
    if pkg.is_file() and not _is_reparse_or_symlink(pkg):
        text = _safe_read_text(pkg)
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                name = data.get("name")
                if isinstance(name, str) and name.strip():
                    cleaned = name.strip()
                    if _identity_is_secret_shaped(cleaned):
                        return None
                    return cleaned
    pyproject = directory / "pyproject.toml"
    if pyproject.is_file() and not _is_reparse_or_symlink(pyproject):
        text = _safe_read_text(pyproject)
        if text:
            match = re.search(r"(?m)^\s*name\s*=\s*[\"']([^\"']+)[\"']", text)
            if match:
                cleaned = match.group(1).strip()
                if cleaned and _identity_is_secret_shaped(cleaned):
                    return None
                return cleaned
    return None


def _normalize_remote(url: str) -> str:
    value = url.strip().casefold()
    value = re.sub(r"^git\+", "", value)
    value = re.sub(r"\.git$", "", value)
    value = value.removeprefix("ssh://")
    return value


def _read_atlas_marker(directory: Path) -> dict[str, Any]:
    """Return marker payload plus status metadata (never silent invalid)."""
    for name in PROJECT_MARKER_FILES:
        marker = directory / name
        if marker.is_file() and not _is_reparse_or_symlink(marker):
            return _parse_marker_file(marker, name)
    for rel in PROJECT_MARKER_NESTED:
        marker = directory / rel
        if marker.is_file() and not _is_reparse_or_symlink(marker):
            return _parse_marker_file(marker, rel.as_posix())
    return {
        "marker_status": "absent",
        "uuid_status": "absent",
        "atlas_project_id": None,
        "atlas_project_uuid": None,
    }


def _parse_marker_file(marker: Path, label: str) -> dict[str, Any]:
    text = _safe_read_text(marker)
    if text is None:
        return {
            "marker_status": "unreadable",
            "uuid_status": "absent",
            "atlas_project_id": None,
            "atlas_project_uuid": None,
            "_marker": label,
        }
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {
            "marker_status": "invalid",
            "uuid_status": "absent",
            "atlas_project_id": None,
            "atlas_project_uuid": None,
            "_marker": label,
        }
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return {
            "marker_status": "invalid",
            "uuid_status": "absent",
            "atlas_project_id": None,
            "atlas_project_uuid": None,
            "_marker": label,
        }
    project = data.get("project")
    project_id: str | None = None
    marker_status = "ok"
    if isinstance(project, dict):
        raw_id = project.get("id")
        if isinstance(raw_id, str) and raw_id.strip():
            project_id = raw_id.strip()
            # YAML has already decoded quoted \u / \x escapes. Scan the
            # decoded token before it becomes fingerprint.atlas_project_id.
            # AS-SEC-SCAN-ESTATE-DISCOVERY-YAML-001.
            if _identity_is_secret_shaped(project_id):
                project_id = None
                marker_status = "invalid"
    raw_uuid = data.get("project_uuid")
    uuid_status = "absent"
    project_uuid: str | None = None
    if raw_uuid is not None and not (isinstance(raw_uuid, str) and not raw_uuid.strip()):
        if not isinstance(raw_uuid, str):
            uuid_status = "invalid"
        else:
            try:
                project_uuid = validate_project_uuid(raw_uuid.strip())
                uuid_status = "ok"
            except ValueError:
                uuid_status = "invalid"
    return {
        "marker_status": marker_status,
        "uuid_status": uuid_status,
        "atlas_project_id": project_id,
        "atlas_project_uuid": project_uuid,
        "_marker": label,
    }


def _dir_entries(directory: Path) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    lower_files: set[str] = set()
    try:
        with os.scandir(directory) as iterator:
            for entry in iterator:
                names.add(entry.name)
                if entry.is_file(follow_symlinks=False):
                    lower_files.add(entry.name.casefold())
    except OSError:
        return set(), set()
    return names, lower_files


def _score_project_signals(
    directory: Path, names: set[str], lower_files: set[str]
) -> list[str]:
    signals: list[str] = []
    if ".git" in names:
        signals.append("git_dir")
    if any((directory / m).is_file() for m in PROJECT_MARKER_FILES) or any(
        (directory / m).is_file() for m in PROJECT_MARKER_NESTED
    ):
        signals.append("atlas_project_marker")
    for manifest in PROJECT_MANIFEST_FILES:
        if manifest in lower_files:
            signals.append(f"manifest:{manifest}")
    for doc in PROJECT_DOC_NAMES:
        if doc in lower_files:
            signals.append(f"doc:{doc}")
    folded = {n.casefold(): n for n in names}
    for dirname in PROJECT_DIR_SIGNALS:
        real = folded.get(dirname)
        if real is None:
            continue
        child = directory / real
        if child.is_dir() and not _is_reparse_or_symlink(child):
            signals.append(f"dir:{dirname}")
    if any(n.casefold().startswith("docker-compose") for n in names):
        signals.append("docker_compose")
    return signals


def _git_boundary_kind(directory: Path, names: set[str]) -> str:
    """Own repository or worktree boundary from directory entries only."""
    current_discovery_perf().git_boundary_checks += 1
    if ".git" not in names:
        return "none"
    git = directory / ".git"
    if _is_reparse_or_symlink(git):
        return "none"
    try:
        if git.is_dir():
            return "repo"
        if git.is_file():
            return "worktree"
    except OSError:
        return "none"
    return "none"


def _is_project_candidate(signals: Sequence[str]) -> bool:
    if not signals:
        return False
    if "git_dir" in signals or "atlas_project_marker" in signals:
        return True
    manifests = [s for s in signals if s.startswith("manifest:")]
    docs = [s for s in signals if s.startswith("doc:")]
    dirs = [s for s in signals if s.startswith("dir:")]
    if manifests and (docs or dirs or "docker_compose" in signals):
        return True
    return bool(manifests and "dir:src" in signals)


def _is_obsidian_vault(directory: Path, names: set[str]) -> bool:
    if ".obsidian" not in names:
        return False
    obsidian = directory / ".obsidian"
    return obsidian.is_dir() and not _is_reparse_or_symlink(obsidian)


def _knowledge_signals(
    directory: Path, names: set[str], lower_files: set[str]
) -> list[str]:
    signals: list[str] = []
    if _is_obsidian_vault(directory, names):
        signals.append("obsidian_vault")
    folded = {n.casefold(): n for n in names}
    for dirname in KNOWLEDGE_DIR_SIGNALS:
        real = folded.get(dirname)
        if real is None:
            continue
        child = directory / real
        if child.is_dir() and not _is_reparse_or_symlink(child):
            signals.append(f"knowledge_dir:{dirname}")
    md_count = sum(1 for f in lower_files if f.endswith(".md"))
    if md_count >= 3:
        signals.append(f"markdown_cluster:{md_count}")
    return signals


def _build_fingerprint(
    directory: Path,
    signals: Sequence[str],
    *,
    path_key: str | None = None,
) -> dict[str, Any]:
    current_discovery_perf().project_fingerprint_builds += 1
    marker = _read_atlas_marker(directory)
    remote = _git_remote_url(directory) if "git_dir" in signals else None
    package = _package_name(directory)
    if path_key is not None:
        resolved_key = path_key
        canonical_path = directory.as_posix()
    else:
        current_discovery_perf().path_resolve_calls += 1
        resolved = directory.expanduser().resolve(strict=False)
        resolved_key = canonical_path_key_resolved(resolved)
        canonical_path = resolved.as_posix()
    return {
        "canonical_path": canonical_path,
        "path_key": resolved_key,
        "path_fingerprint": root_identity_fingerprint(directory),
        "atlas_project_id": marker.get("atlas_project_id"),
        "atlas_project_uuid": marker.get("atlas_project_uuid"),
        "marker_status": marker.get("marker_status", "absent"),
        "uuid_status": marker.get("uuid_status", "absent"),
        "git_remote": remote,
        "package_name": package,
        "directory_name": directory.name,
    }


def _live_root_metadata(root: Path | None) -> tuple[str | None, str | None]:
    if root is None or not root.is_dir():
        return None, None
    return _git_remote_url(root), _package_name(root)


def _collect_bind_roots_from_estate(
    authorized_root: Path | None,
) -> dict[str, dict[str, Any]]:
    """Find ``.atlas/connect.json`` binds under the authorized estate (read-only)."""
    found: dict[str, dict[str, Any]] = {}
    if authorized_root is None or not authorized_root.is_dir():
        return found
    # Shallow-ish walk for bind files only (bounded).
    stack: list[tuple[Path, int]] = [(authorized_root, 0)]
    seen: set[str] = set()
    while stack:
        current, depth = stack.pop()
        key = canonical_path_key(current)
        if key in seen:
            continue
        seen.add(key)
        bind_path = current / BIND_RELATIVE
        if bind_path.is_file() and not _is_reparse_or_symlink(bind_path):
            payload = _read_json_object(bind_path)
            if payload is not None:
                found[key] = payload
        if depth >= DEFAULT_MAX_DEPTH:
            continue
        names, _ = _dir_entries(current)
        for name in names:
            if name.casefold() in {n.casefold() for n in IGNORE_DIR_NAMES}:
                continue
            child = current / name
            if _is_reparse_or_symlink(child):
                continue
            if child.is_dir():
                stack.append((child, depth + 1))
    return found


def load_vault_project_identities(
    vault: Path | None,
    *,
    authorized_root: Path | None = None,
) -> list[VaultProjectIdentity]:
    """Load governed Atlas identity for matching (P3).

    Source of truth:
    - ``receipts/source-lineage/project-*-allocation.json`` (UUID ownership)
    - ``vault/projects/*`` presence
    - ``generated/ops/connect-manifest.json`` / connect-receipt (last bind root)
    - ``sources/manifests/source-manifest.json`` (multi-project ownership)
    - live ``.atlas/connect.json`` under authorized estate (bind proof)

    Does not invent a discovery-only truth store.
    """
    if vault is None:
        return []
    vault = vault.expanduser().resolve(strict=False)
    projects_root = vault / "projects"
    if not projects_root.is_dir():
        return []

    by_id: dict[str, VaultProjectIdentity] = {}
    for entry in sorted(projects_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        by_id[entry.name] = VaultProjectIdentity(
            project_id=entry.name,
            project_uuid=None,
            identity_sources=("vault/projects",),
        )

    # Canonical UUID ownership from allocation receipts.
    try:
        uuid_owners = load_allocation_uuid_owners(vault)
        id_to_uuid = load_allocation_project_uuids(vault)
    except ValueError:
        # Unreadable/conflicting receipts: leave UUIDs unset; matching will
        # fail closed on conflicts when fingerprint provides UUID evidence.
        uuid_owners = {}
        id_to_uuid = {}

    for project_id, project_uuid in id_to_uuid.items():
        existing = by_id.get(project_id)
        alloc_sources: tuple[str, ...] = ("allocation_receipt",)
        if existing is not None:
            merged_sources = tuple(
                dict.fromkeys([*existing.identity_sources, *alloc_sources])
            )
            by_id[project_id] = VaultProjectIdentity(
                project_id=project_id,
                project_uuid=project_uuid,
                bind_root=existing.bind_root,
                package_name=existing.package_name,
                git_remote=existing.git_remote,
                bind_proven=existing.bind_proven,
                identity_sources=merged_sources,
            )
        else:
            by_id[project_id] = VaultProjectIdentity(
                project_id=project_id,
                project_uuid=project_uuid,
                identity_sources=alloc_sources,
            )
    _ = uuid_owners  # cardinality already enforced by load helpers

    # Last-writer connect-manifest + receipt for bind root.
    bind_by_project: dict[str, str] = {}
    connect_manifest = _read_json_object(vault / MANIFEST_RELATIVE)
    if connect_manifest is not None:
        source_root = connect_manifest.get("source_root")
        if isinstance(source_root, str) and source_root.strip():
            # Infer primary project from sources' likely_project majority.
            counts: dict[str, int] = {}
            for src in connect_manifest.get("sources") or []:
                if not isinstance(src, dict):
                    continue
                pid = src.get("likely_project")
                if isinstance(pid, str) and pid.strip():
                    counts[pid.strip()] = counts.get(pid.strip(), 0) + 1
            if counts:
                primary = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                bind_by_project[primary] = source_root.strip()

    receipt = _read_json_object(vault / CONNECT_RECEIPT_RELATIVE)
    if receipt is not None:
        root = receipt.get("project_root")
        pid = receipt.get("project_id")
        if isinstance(root, str) and isinstance(pid, str) and pid.strip():
            bind_by_project[pid.strip()] = root.strip()
        projects = receipt.get("projects")
        if isinstance(root, str) and isinstance(projects, list) and len(projects) == 1:
            only = projects[0]
            if isinstance(only, str) and only.strip():
                bind_by_project.setdefault(only.strip(), root.strip())

    # Durable multi-project source-manifest ownership roots.
    durable = _read_json_object(vault / DURABLE_SOURCE_MANIFEST)
    if durable is not None:
        durable_root = durable.get("source_root")
        if isinstance(durable_root, str) and durable_root.strip():
            for src in durable.get("sources") or []:
                if not isinstance(src, dict):
                    continue
                pid = src.get("likely_project")
                if isinstance(pid, str) and pid.strip():
                    bind_by_project.setdefault(pid.strip(), durable_root.strip())

    # Live binds under authorized estate.
    estate_binds = _collect_bind_roots_from_estate(authorized_root)
    for bind in estate_binds.values():
        recorded_root = bind.get("project_root")
        pid = bind.get("project_id")
        if not isinstance(recorded_root, str) or not isinstance(pid, str):
            continue
        if not recorded_root.strip() or not pid.strip():
            continue
        try:
            recorded_path = Path(recorded_root).expanduser().resolve(strict=False)
        except OSError:
            continue
        # Bind vault must resolve to this vault when possible.
        bound_vault = bind.get("vault")
        if isinstance(bound_vault, str) and bound_vault.strip():
            try:
                bv = Path(bound_vault)
                if not bv.is_absolute():
                    bv = recorded_path / bv
                if bv.resolve(strict=False) != vault:
                    continue
            except OSError:
                continue
        # Bind must still own the recorded root (copied-bind protection).
        if not _paths_equal(recorded_path, Path(recorded_root).expanduser()):
            # Still accept after normalize when expanduser/resolve differs only
            # by symlink resolution within the same key.
            pass
        bind_by_project[pid.strip()] = recorded_path.as_posix()

    # Apply bind roots + live package/git metadata from those roots.
    for project_id, root_text in bind_by_project.items():
        existing = by_id.get(project_id)
        if existing is None:
            continue
        try:
            root_path = Path(root_text).expanduser().resolve(strict=False)
        except OSError:
            continue
        remote, package = _live_root_metadata(root_path)
        bind_sources: tuple[str, ...] = tuple(
            dict.fromkeys(
                [*existing.identity_sources, "bind_or_source_ownership"]
            )
        )
        by_id[project_id] = VaultProjectIdentity(
            project_id=project_id,
            project_uuid=existing.project_uuid or id_to_uuid.get(project_id),
            bind_root=root_path.as_posix(),
            package_name=package,
            git_remote=remote,
            bind_proven=True,
            identity_sources=bind_sources,
        )

    return [by_id[k] for k in sorted(by_id)]


def match_fingerprint(
    fingerprint: dict[str, Any],
    vault_projects: Sequence[VaultProjectIdentity],
) -> tuple[
    MatchState,
    list[MatchEvidence],
    list[MatchEvidence],
    str | None,
    str | None,
]:
    """Explainable project matching with Coder Alpha identity matrix (P1/P4)."""
    evidence: list[MatchEvidence] = []
    conflicts: list[MatchEvidence] = []

    marker_status = str(fingerprint.get("marker_status") or "absent")
    uuid_status = str(fingerprint.get("uuid_status") or "absent")
    atlas_id = fingerprint.get("atlas_project_id")
    atlas_uuid = fingerprint.get("atlas_project_uuid")
    remote = fingerprint.get("git_remote")
    package = fingerprint.get("package_name")
    dirname = fingerprint.get("directory_name")

    if marker_status in {"invalid", "unreadable"}:
        conflicts.append(
            MatchEvidence(
                f"marker_{marker_status}",
                f"project marker is {marker_status}; refusing healthy EXACT",
                "invalid",
            )
        )
        return "CONFLICTING", evidence, conflicts, None, None

    if uuid_status == "invalid":
        conflicts.append(
            MatchEvidence(
                "invalid_project_uuid",
                "explicit project_uuid is invalid UUIDv4; not treated as absent",
                "invalid",
            )
        )
        # Still surface ID conflicts if present.
        if isinstance(atlas_id, str) and atlas_id:
            for vp in vault_projects:
                if vp.project_id == atlas_id and vp.project_uuid:
                    conflicts.append(
                        MatchEvidence(
                            "same_id_invalid_uuid",
                            f"marker id {atlas_id} has invalid uuid while "
                            f"governed uuid is present for that id",
                            "conflict",
                        )
                    )
        return "CONFLICTING", evidence, conflicts, None, None

    if not vault_projects:
        return "UNMATCHED", evidence, conflicts, None, None

    id_hits = [
        vp
        for vp in vault_projects
        if isinstance(atlas_id, str) and atlas_id and vp.project_id == atlas_id
    ]
    uuid_hits = [
        vp
        for vp in vault_projects
        if isinstance(atlas_uuid, str)
        and atlas_uuid
        and vp.project_uuid
        and vp.project_uuid == atlas_uuid
    ]

    # P1-B: same id, different uuid (both present and governed).
    if isinstance(atlas_id, str) and atlas_id and isinstance(atlas_uuid, str) and atlas_uuid:
        for vp in id_hits:
            if vp.project_uuid and vp.project_uuid != atlas_uuid:
                conflicts.append(
                    MatchEvidence(
                        "same_id_different_uuid",
                        f"marker id {atlas_id} has uuid {atlas_uuid} but "
                        f"governed allocation uuid is {vp.project_uuid}",
                        "conflict",
                    )
                )
        # P1-C: different id, same uuid.
        for vp in uuid_hits:
            if vp.project_id != atlas_id:
                conflicts.append(
                    MatchEvidence(
                        "different_id_same_uuid",
                        f"uuid owned by governed project {vp.project_id} but "
                        f"marker id is {atlas_id}",
                        "conflict",
                    )
                )

    if len({h.project_id for h in uuid_hits}) > 1:
        conflicts.append(
            MatchEvidence(
                "uuid_multi_owner",
                "same uuid matched multiple vault project ids",
                "conflict",
            )
        )

    if conflicts:
        return "CONFLICTING", evidence, conflicts, None, (
            atlas_uuid if isinstance(atlas_uuid, str) else None
        )

    def _remote_conflicts_for(matched_id: str) -> list[MatchEvidence]:
        rows: list[MatchEvidence] = []
        if not isinstance(remote, str) or not remote:
            return rows
        norm = _normalize_remote(remote)
        for vp in vault_projects:
            if not vp.git_remote:
                continue
            if _normalize_remote(vp.git_remote) != norm:
                continue
            if vp.project_id != matched_id:
                rows.append(
                    MatchEvidence(
                        "git_remote_vs_marker_id",
                        f"git remote matches {vp.project_id} but marker/"
                        f"matched id is {matched_id}",
                        "conflict",
                    )
                )
        return rows

    if len(uuid_hits) == 1:
        hit = uuid_hits[0]
        remote_conflicts = _remote_conflicts_for(hit.project_id)
        if remote_conflicts:
            return "CONFLICTING", evidence, remote_conflicts, None, hit.project_uuid
        evidence.append(
            MatchEvidence(
                "atlas_project_uuid",
                f"marker uuid matches governed project {hit.project_id}",
                "exact",
            )
        )
        if isinstance(atlas_id, str) and atlas_id and atlas_id == hit.project_id:
            evidence.append(
                MatchEvidence(
                    "atlas_project_id",
                    f"marker project.id equals governed project {hit.project_id}",
                    "exact",
                )
            )
        elif not atlas_id:
            evidence.append(
                MatchEvidence(
                    "atlas_project_id_absent",
                    "marker project.id absent; EXACT via governed uuid ownership",
                    "exact",
                )
            )
        return "EXACT", evidence, [], hit.project_id, hit.project_uuid

    if len(id_hits) == 1 and uuid_status == "absent":
        hit = id_hits[0]
        remote_conflicts = _remote_conflicts_for(hit.project_id)
        if remote_conflicts:
            return "CONFLICTING", evidence, remote_conflicts, None, hit.project_uuid
        evidence.append(
            MatchEvidence(
                "atlas_project_id",
                f"marker project.id equals governed project {hit.project_id}",
                "exact",
            )
        )
        evidence.append(
            MatchEvidence(
                "uuid_absent",
                "marker project_uuid absent; matched by governed project.id only",
                "weak",
            )
        )
        return "EXACT", evidence, [], hit.project_id, hit.project_uuid

    if len(id_hits) > 1:
        return "AMBIGUOUS", evidence, conflicts, None, None

    # Heuristic layers — never EXACT.
    strong_hits: list[VaultProjectIdentity] = []
    likely_hits: list[VaultProjectIdentity] = []

    if isinstance(remote, str) and remote:
        norm = _normalize_remote(remote)
        for vp in vault_projects:
            if vp.git_remote and _normalize_remote(vp.git_remote) == norm:
                if isinstance(atlas_id, str) and atlas_id and atlas_id != vp.project_id:
                    conflicts.append(
                        MatchEvidence(
                            "git_remote_vs_marker_id",
                            f"git remote matches {vp.project_id} but marker id "
                            f"is {atlas_id}",
                            "conflict",
                        )
                    )
                else:
                    strong_hits.append(vp)
                    evidence.append(
                        MatchEvidence(
                            "git_remote",
                            f"git remote matches bind/source root of {vp.project_id}",
                            "strong",
                        )
                    )

    if conflicts:
        return "CONFLICTING", evidence, conflicts, None, None

    if isinstance(package, str) and package:
        for vp in vault_projects:
            if vp.package_name and vp.package_name.casefold() == package.casefold():
                strong_hits.append(vp)
                evidence.append(
                    MatchEvidence(
                        "package_name",
                        f"package name matches bind/source root of {vp.project_id}",
                        "strong",
                    )
                )
            elif vp.project_id.casefold() == package.casefold().replace("_", "-"):
                likely_hits.append(vp)
                evidence.append(
                    MatchEvidence(
                        "package_name_as_id",
                        f"package name aligns with vault project id {vp.project_id}",
                        "likely",
                    )
                )

    if isinstance(dirname, str) and dirname:
        for vp in vault_projects:
            if vp.project_id.casefold() == dirname.casefold():
                likely_hits.append(vp)
                evidence.append(
                    MatchEvidence(
                        "directory_name",
                        f"directory name equals vault project id {vp.project_id}",
                        "likely",
                    )
                )

    # Canonical/root evidence: candidate path equals governed bind_root.
    cand_path = fingerprint.get("canonical_path")
    if isinstance(cand_path, str) and cand_path:
        for vp in vault_projects:
            if vp.bind_root and _paths_equal(Path(cand_path), Path(vp.bind_root)):
                strong_hits.append(vp)
                evidence.append(
                    MatchEvidence(
                        "canonical_bind_root",
                        f"candidate path equals governed bind/source root "
                        f"for {vp.project_id}",
                        "strong",
                    )
                )

    unique_strong = {h.project_id: h for h in strong_hits}
    unique_likely = {h.project_id: h for h in likely_hits}

    if len(unique_strong) == 1:
        hit = next(iter(unique_strong.values()))
        return "STRONG_EVIDENCE", evidence, [], hit.project_id, hit.project_uuid
    if len(unique_strong) > 1:
        return "AMBIGUOUS", evidence, [], None, None
    if len(unique_likely) == 1:
        hit = next(iter(unique_likely.values()))
        return "LIKELY", evidence, [], hit.project_id, hit.project_uuid
    if len(unique_likely) > 1:
        return "AMBIGUOUS", evidence, [], None, None
    return "UNMATCHED", evidence, [], None, None


def prove_connected(
    candidate_path: Path,
    matched_project_id: str | None,
    match_state: MatchState,
    vault_projects: Sequence[VaultProjectIdentity],
    *,
    vault: Path | None = None,
) -> tuple[bool, list[str]]:
    """CONNECTED only with durable bind / source-root ownership (P2)."""
    if match_state != "EXACT" and match_state != "STRONG_EVIDENCE":
        return False, []
    if not matched_project_id:
        return False, []
    vp = next((p for p in vault_projects if p.project_id == matched_project_id), None)
    if vp is None:
        return False, []

    why: list[str] = []
    # Governed bind_root equality.
    if vp.bind_proven and vp.bind_root:
        try:
            if _paths_equal(candidate_path, Path(vp.bind_root)):
                why.append(
                    f"candidate root equals governed bind/source root for "
                    f"{matched_project_id}"
                )
                return True, why
        except OSError:
            pass

    # Live connect bind on the candidate itself.
    bind_path = candidate_path / BIND_RELATIVE
    if bind_path.is_file() and not _is_reparse_or_symlink(bind_path):
        bind = _read_json_object(bind_path)
        if bind is not None:
            recorded = bind.get("project_root")
            pid = bind.get("project_id")
            if (
                isinstance(recorded, str)
                and isinstance(pid, str)
                and pid == matched_project_id
            ):
                try:
                    if _paths_equal(Path(recorded), candidate_path):
                        if vault is not None:
                            bound_vault = bind.get("vault")
                            if isinstance(bound_vault, str) and bound_vault.strip():
                                bv = Path(bound_vault)
                                if not bv.is_absolute():
                                    bv = candidate_path / bv
                                if bv.resolve(strict=False) != vault.resolve(
                                    strict=False
                                ):
                                    return False, [
                                        "connect bind vault does not match "
                                        "discovery vault"
                                    ]
                        why.append(
                            "live .atlas/connect.json bind proves current "
                            f"root ownership for {matched_project_id}"
                        )
                        return True, why
                except OSError:
                    pass

    # Same id in vault/projects alone is NOT connected.
    return False, [
        f"matched {matched_project_id} but no durable bind/source-root "
        "ownership proves this candidate root is currently connected"
    ]


def _why_from_evidence(
    evidence: Sequence[MatchEvidence], match_state: MatchState
) -> list[str]:
    if not evidence:
        if match_state == "UNMATCHED":
            return ["no vault project identity matched this candidate"]
        return [f"classified as {match_state}"]
    return [f"{row.kind}: {row.detail}" for row in evidence]


def _required_action(match_state: MatchState, *, connected: bool) -> str | None:
    if connected:
        return None
    if match_state == "CONFLICTING":
        return (
            "Resolve identity conflict (UUID/id ownership) before connect; "
            "do not unify projects"
        )
    if match_state == "AMBIGUOUS":
        return "Choose the correct project identity explicitly, then connect"
    if match_state in {"EXACT", "STRONG_EVIDENCE", "LIKELY"}:
        return "Review match evidence; connect only if accepted"
    if match_state == "UNMATCHED":
        return "New project candidate — connect to create governed identity"
    return None


def _category_for(
    *,
    kind: str,
    match_state: MatchState,
    connected: bool,
    knowledge_relation: KnowledgeRelation | None = None,
) -> Category:
    if connected:
        return "CONNECTED"
    if match_state in {"AMBIGUOUS", "CONFLICTING"}:
        return "AMBIGUOUS_MATCHES"
    if kind != "project":
        if knowledge_relation == "KNOWLEDGE_PROJECT_MATCHED":
            return "NEW_KNOWLEDGE"
        if knowledge_relation == "KNOWLEDGE_AMBIGUOUS":
            return "AMBIGUOUS_MATCHES"
        if knowledge_relation == "KNOWLEDGE_UNMATCHED" or match_state == "UNMATCHED":
            return "UNMATCHED_KNOWLEDGE"
        return "NEW_KNOWLEDGE"
    return "DISCOVERED_PROJECTS"


def _lifecycle_for(
    match_state: MatchState, *, connected: bool, marker_bad: bool
) -> LifecycleState:
    if connected:
        return "CONNECTED"
    if marker_bad or match_state in {"CONFLICTING", "AMBIGUOUS"}:
        return "POLICY_REVIEW"
    if match_state in {"EXACT", "STRONG_EVIDENCE", "LIKELY"}:
        return "PROJECT_MATCHED"
    if match_state == "UNMATCHED":
        return "CLASSIFIED"
    return "CANDIDATE"


def _valid_id_token(value: object) -> str | None:
    """Non-empty identity token, or None. Never treat blank/whitespace as an id."""
    if not isinstance(value, str):
        return None
    token = value.strip()
    return token or None


def _project_relation_token(parent: DiscoveryCandidate) -> str | None:
    """Pointer to an emitted project candidate or vault id. Never display_name."""
    return _valid_id_token(parent.matched_project_id) or _valid_id_token(
        parent.candidate_id
    )


def _estate_region(path: Path, root: Path) -> str:
    """Untrusted-path region. Resolves both sides. Prefer resolved helper."""
    perf = current_discovery_perf()
    try:
        perf.path_resolve_calls += 2
        rel = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return "_outside"
    parts = rel.parts
    if not parts or parts == (".",):
        return "_root"
    return str(parts[0])


def _region_counts(sightings: Sequence[ProjectSighting]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in sightings:
        counts[item.region] = counts.get(item.region, 0) + 1
    return dict(sorted(counts.items()))


def _parent_path_key(path: Path) -> str:
    """Untrusted-path parent key. Prefer ``parent_canonical_key`` on stored keys."""
    return canonical_path_key(path.parent)


def project_evidence_weight(
    signals: Sequence[str],
    *,
    has_remote: bool,
    marker_status: str,
    uuid_status: str,
) -> int:
    """Integer evidence weight for ranking. Not a confidence percentage."""
    weight = 0
    if "atlas_project_marker" in signals:
        weight += 8
    if marker_status == "ok":
        weight += 2
    if uuid_status == "ok":
        weight += 4
    if "git_dir" in signals:
        weight += 3
    if has_remote:
        weight += 2
    weight += 2 * sum(1 for item in signals if item.startswith("manifest:"))
    weight += sum(1 for item in signals if item.startswith("doc:"))
    if "dir:src" in signals:
        weight += 2
    weight += sum(
        1 for item in signals if item.startswith("dir:") and item != "dir:src"
    )
    if "docker_compose" in signals:
        weight += 1
    return weight


def _primary_family_key(fingerprint: dict[str, Any], path_key: str) -> str:
    uuid = _valid_id_token(fingerprint.get("atlas_project_uuid"))
    if uuid is not None:
        return f"uuid:{uuid.casefold()}"
    atlas_id = _valid_id_token(fingerprint.get("atlas_project_id"))
    if atlas_id is not None:
        return f"id:{atlas_id.casefold()}"
    remote = fingerprint.get("git_remote")
    if isinstance(remote, str) and remote.strip():
        return f"remote:{_normalize_remote(remote)}"
    package = fingerprint.get("package_name")
    if isinstance(package, str) and package.strip():
        return f"pkg:{package.strip().casefold()}"
    return f"path:{path_key}"


@dataclass
class ProjectSighting:
    """Compact project evidence gathered during traversal (D-080 / D-084)."""

    path: Path
    path_key: str
    depth: int
    region: str
    parent_key: str
    signals: list[str]
    fingerprint: dict[str, Any]
    family: str
    evidence_weight: int
    has_independent_boundary: bool = False
    is_nested_component: bool = False
    enriched: bool = False


@dataclass
class KnowledgeSighting:
    """Compact knowledge evidence gathered during traversal (D-080)."""

    path: Path
    path_key: str
    depth: int
    region: str
    signals: list[str]
    is_obsidian: bool
    names: set[str]


def _is_path_key_ancestor(ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return False
    prefix = ancestor.rstrip("/") + "/"
    return descendant.startswith(prefix)


def _has_boundary_ancestor(path_key: str, boundary_keys: set[str]) -> bool:
    parts = path_key.split("/")
    for end in range(len(parts) - 1, 0, -1):
        prefix = "/".join(parts[:end])
        if prefix in boundary_keys:
            return True
    return False


def mark_nested_components(sightings: Sequence[ProjectSighting]) -> None:
    """Non-independent descendants of a bounded ancestor are nested components.

    ANCESTOR_AWARE_SELECTION != SILENT_IDENTITY_MERGE. Independent nested
    repositories remain separately eligible.
    """
    boundary_keys = {
        item.path_key for item in sightings if item.has_independent_boundary
    }
    for item in sightings:
        if item.has_independent_boundary:
            item.is_nested_component = False
            continue
        item.is_nested_component = _has_boundary_ancestor(item.path_key, boundary_keys)


def assign_project_families(sightings: Sequence[ProjectSighting]) -> None:
    """Family grouping for output diversity. Never an identity merge.

    Parent-cluster collapse runs only after enrichment. Cheap preselection
    must not treat sibling projects as one family just because remotes and
    package names have not been read yet.
    """
    parent_counts: dict[str, int] = {}
    for item in sightings:
        parent_counts[item.parent_key] = parent_counts.get(item.parent_key, 0) + 1
    for item in sightings:
        item.family = _primary_family_key(item.fingerprint, item.path_key)
        if (
            item.enriched
            and item.family.startswith("path:")
            and parent_counts.get(item.parent_key, 0) >= 3
        ):
            item.family = f"parent:{item.parent_key}"


def _project_rank_key(item: ProjectSighting) -> tuple[int, int, int, str]:
    nested = 1 if item.is_nested_component else 0
    return (nested, -item.evidence_weight, item.depth, item.path_key)


def hierarchical_preselect_budget(seen: int, emit_limit: int) -> int:
    """Bounded shortlist size. Scales with emit cap, not all sightings."""
    if emit_limit <= 0 or seen <= 0:
        return 0
    if seen <= emit_limit:
        return seen
    return min(seen, emit_limit * PRESELECT_MULTIPLIER)


def _cheap_project_sighting(
    directory: Path,
    *,
    path_key: str,
    depth: int,
    root: Path,
    signals: Sequence[str],
    names: set[str],
) -> ProjectSighting:
    git_kind = _git_boundary_kind(directory, names)
    has_boundary = git_kind != "none" or "atlas_project_marker" in signals
    fingerprint: dict[str, Any] = {
        "canonical_path": directory.as_posix(),
        "path_key": path_key,
        "directory_name": directory.name,
        "git_boundary": git_kind,
        "atlas_marker_present": "atlas_project_marker" in signals,
    }
    return ProjectSighting(
        path=directory,
        path_key=path_key,
        depth=depth,
        region=estate_region_resolved(directory, root),
        parent_key=parent_canonical_key(path_key),
        signals=list(signals),
        fingerprint=fingerprint,
        family=_primary_family_key(fingerprint, path_key),
        evidence_weight=project_evidence_weight(
            signals,
            has_remote=False,
            marker_status="absent",
            uuid_status="absent",
        ),
        has_independent_boundary=has_boundary,
    )


def enrich_project_sighting(item: ProjectSighting) -> None:
    """Expensive fingerprint / identity reads. Call only on a bounded shortlist."""
    fingerprint = _build_fingerprint(item.path, item.signals, path_key=item.path_key)
    git_kind = str(item.fingerprint.get("git_boundary") or "none")
    fingerprint["git_boundary"] = git_kind
    fingerprint["atlas_marker_present"] = "atlas_project_marker" in item.signals
    item.fingerprint = fingerprint
    item.evidence_weight = project_evidence_weight(
        item.signals,
        has_remote=bool(fingerprint.get("git_remote")),
        marker_status=str(fingerprint.get("marker_status") or "absent"),
        uuid_status=str(fingerprint.get("uuid_status") or "absent"),
    )
    if (
        git_kind != "none"
        or _valid_id_token(fingerprint.get("atlas_project_uuid"))
        or _valid_id_token(fingerprint.get("atlas_project_id"))
    ):
        item.has_independent_boundary = True
    item.enriched = True


def select_bounded_project_sightings(
    sightings: Sequence[ProjectSighting],
    limit: int,
) -> tuple[list[ProjectSighting], list[ProjectSighting]]:
    """Deterministic hierarchical-fair selection (D-084).

    REGION → family/boundary → evidence rank → path tie-break.
    Traversal order is not authority. One region cannot consume consecutive
    output slots while other regions still have eligible candidates.
    """
    assign_project_families(sightings)
    mark_nested_components(sightings)
    by_path = sorted(sightings, key=lambda item: (item.path_key, item.path.as_posix()))
    if limit <= 0:
        return [], list(by_path)
    if len(sightings) <= limit:
        return by_path, []

    selected: list[ProjectSighting] = []
    selected_keys: set[str] = set()
    family_counts: dict[str, int] = {}

    def _eligible(
        item: ProjectSighting,
        *,
        allow_nested: bool,
        unused_family_only: bool,
        relax_family: bool,
    ) -> bool:
        if item.path_key in selected_keys:
            return False
        if item.is_nested_component and not allow_nested:
            return False
        count = family_counts.get(item.family, 0)
        if unused_family_only and count != 0:
            return False
        return relax_family or count < MAX_FAMILY_REPRESENTATIVES

    def _round_robin(
        *,
        allow_nested: bool,
        unused_family_only: bool,
        relax_family: bool,
    ) -> None:
        queues: dict[str, list[ProjectSighting]] = {}
        for item in sightings:
            if _eligible(
                item,
                allow_nested=allow_nested,
                unused_family_only=unused_family_only,
                relax_family=relax_family,
            ):
                queues.setdefault(item.region, []).append(item)
        if not queues:
            return
        for region in queues:
            queues[region].sort(key=_project_rank_key)
        cursors = dict.fromkeys(queues, 0)
        progressed = True
        while len(selected) < limit and progressed:
            progressed = False
            for region in sorted(queues):
                if len(selected) >= limit:
                    break
                queue = queues[region]
                cursor = cursors[region]
                while cursor < len(queue):
                    item = queue[cursor]
                    cursor += 1
                    if not _eligible(
                        item,
                        allow_nested=allow_nested,
                        unused_family_only=unused_family_only,
                        relax_family=relax_family,
                    ):
                        continue
                    selected.append(item)
                    selected_keys.add(item.path_key)
                    family_counts[item.family] = family_counts.get(item.family, 0) + 1
                    progressed = True
                    break
                cursors[region] = cursor

    # Independent-boundary / non-nested first, then nested components,
    # then unused families, then leftover capacity (flows to remaining regions).
    _round_robin(allow_nested=False, unused_family_only=False, relax_family=False)
    _round_robin(allow_nested=True, unused_family_only=False, relax_family=False)
    _round_robin(allow_nested=True, unused_family_only=True, relax_family=False)
    # Unused capacity may flow to remaining regions, but family diversity
    # still holds. Filling leftover slots from the same family is monopoly.
    _round_robin(allow_nested=True, unused_family_only=False, relax_family=False)

    selected.sort(key=lambda item: (item.path.as_posix().casefold(), item.path_key))
    suppressed = [item for item in by_path if item.path_key not in selected_keys]
    return selected, suppressed


def _project_candidate_path_key(item: DiscoveryCandidate) -> str:
    """Stored project path key, or one setup resolve if a caller omitted it."""
    stored = candidate_path_key_from_record(item)
    if stored is not None:
        return stored
    return canonical_path_key(Path(item.path))


def select_bounded_knowledge_sightings(
    sightings: Sequence[KnowledgeSighting],
    limit: int,
    selected_projects: Sequence[DiscoveryCandidate],
) -> tuple[list[KnowledgeSighting], list[KnowledgeSighting]]:
    """Prefer knowledge under selected projects; cap is not first-seen order.

    Ancestry uses the in-memory path index (O(K * depth)). It does not
    resolve filesystem paths per knowledge/project pair.
    """
    project_keys = {_project_candidate_path_key(item) for item in selected_projects}

    def nested_under_selected(item: KnowledgeSighting) -> bool:
        return has_selected_project_ancestor(item.path_key, project_keys)

    def rank(item: KnowledgeSighting) -> tuple[int, int, int, str]:
        nested = 1 if nested_under_selected(item) else 0
        obsidian = 1 if item.is_obsidian else 0
        return (-nested, -obsidian, item.depth, item.path_key)

    ordered = sorted(sightings, key=lambda item: (item.path_key, item.path.as_posix()))
    if limit <= 0:
        return [], list(ordered)
    if len(sightings) <= limit:
        return ordered, []
    chosen = sorted(sightings, key=rank)[:limit]
    chosen.sort(key=lambda item: (item.path.as_posix().casefold(), item.path_key))
    chosen_keys = {item.path_key for item in chosen}
    suppressed = [item for item in ordered if item.path_key not in chosen_keys]
    return chosen, suppressed


def _sanitize_knowledge_relations(
    knowledge: list[DiscoveryCandidate],
    projects: Sequence[DiscoveryCandidate],
    vault_projects: Sequence[VaultProjectIdentity],
) -> None:
    """Fail closed: no blank, dangling, or scope-container project attachments."""
    valid: set[str] = set()
    for item in projects:
        token = _valid_id_token(item.candidate_id)
        if token is not None:
            valid.add(token)
        token = _valid_id_token(item.matched_project_id)
        if token is not None:
            valid.add(token)
        token = _valid_id_token(item.matched_project_uuid)
        if token is not None:
            valid.add(token)
    for vault in vault_projects:
        token = _valid_id_token(vault.project_id)
        if token is not None:
            valid.add(token)
        token = _valid_id_token(vault.project_uuid)
        if token is not None:
            valid.add(token)
    for item in knowledge:
        mid = _valid_id_token(item.matched_project_id)
        uuid = _valid_id_token(item.matched_project_uuid)
        if mid is None:
            item.matched_project_id = None
            if item.knowledge_relation == "KNOWLEDGE_PROJECT_MATCHED":
                item.knowledge_relation = "KNOWLEDGE_UNMATCHED"
                item.match_state = "UNMATCHED"
                item.required_review = True
                item.category = _category_for(
                    kind=item.kind,
                    match_state="UNMATCHED",
                    connected=False,
                    knowledge_relation="KNOWLEDGE_UNMATCHED",
                )
            continue
        if mid not in valid:
            item.matched_project_id = None
            item.matched_project_uuid = None
            item.knowledge_relation = "KNOWLEDGE_UNMATCHED"
            item.match_state = "UNMATCHED"
            item.required_review = True
            item.category = _category_for(
                kind=item.kind,
                match_state="UNMATCHED",
                connected=False,
                knowledge_relation="KNOWLEDGE_UNMATCHED",
            )
            continue
        if uuid is not None and uuid not in valid:
            item.matched_project_uuid = None


def _associate_knowledge(
    knowledge_path: Path,
    project_candidates: Sequence[DiscoveryCandidate],
    *,
    is_obsidian: bool,
    vault_projects: Sequence[VaultProjectIdentity],
    knowledge_path_key: str | None = None,
) -> tuple[KnowledgeRelation, MatchState, list[MatchEvidence], str | None]:
    """Classify knowledge→project relationship without ingest (P5).

    Multiple enclosing selected projects remain AMBIGUOUS (D-084). The
    deepest-parent sort is preserved for the single-parent path only.
    """
    evidence: list[MatchEvidence] = []
    k_key = (
        knowledge_path_key
        if knowledge_path_key is not None
        else canonical_path_key(knowledge_path)
    )
    by_key = {_project_candidate_path_key(item): item for item in project_candidates}
    parents = ancestor_items_from_index(k_key, by_key)
    # Prefer deepest project parent (same as D-084; multi-parent stays ambiguous).
    parents.sort(key=lambda p: len(Path(p.path).parts), reverse=True)
    if len(parents) == 1:
        parent = parents[0]
        evidence.append(
            MatchEvidence(
                "nested_under_project",
                f"knowledge path is nested under project candidate "
                f"{parent.display_name}",
                "strong",
            )
        )
        token = _project_relation_token(parent)
        if token is None:
            evidence.append(
                MatchEvidence(
                    "parent_project_unidentifiable",
                    "nested under a project candidate with no valid id; "
                    "refusing silent assignment",
                    "conflict",
                )
            )
            return "KNOWLEDGE_UNMATCHED", "UNMATCHED", evidence, None
        return (
            "KNOWLEDGE_PROJECT_MATCHED",
            parent.match_state if parent.match_state != "UNMATCHED" else "LIKELY",
            evidence,
            token,
        )
    if len(parents) > 1:
        evidence.append(
            MatchEvidence(
                "nested_under_multiple_projects",
                "knowledge path nests under multiple project candidates",
                "conflict",
            )
        )
        return "KNOWLEDGE_AMBIGUOUS", "AMBIGUOUS", evidence, None

    # Obsidian: never silently assign whole personal vault.
    if is_obsidian:
        name_hits = [
            vp
            for vp in vault_projects
            if vp.project_id.casefold() in knowledge_path.name.casefold()
        ]
        if len(name_hits) == 1:
            evidence.append(
                MatchEvidence(
                    "obsidian_name_hint",
                    f"vault directory name hints project {name_hits[0].project_id}; "
                    "review required — not auto-trusted",
                    "likely",
                )
            )
            return (
                "KNOWLEDGE_AMBIGUOUS",
                "LIKELY",
                evidence,
                name_hits[0].project_id,
            )
        evidence.append(
            MatchEvidence(
                "obsidian_unassigned",
                "Obsidian vault discovered; not silently assigned to a project",
                "weak",
            )
        )
        return "KNOWLEDGE_UNMATCHED", "UNMATCHED", evidence, None

    return "KNOWLEDGE_DISCOVERED", "UNMATCHED", evidence, None


def _materialize_project_candidate(
    sighting: ProjectSighting,
    *,
    vault_projects: Sequence[VaultProjectIdentity],
    vault_resolved: Path | None,
) -> DiscoveryCandidate:
    directory = sighting.path
    fingerprint = dict(sighting.fingerprint)
    match_state, evidence, conflicts, matched_id, matched_uuid = match_fingerprint(
        fingerprint, vault_projects
    )
    connected, why_connected = prove_connected(
        directory,
        matched_id,
        match_state,
        vault_projects,
        vault=vault_resolved,
    )
    marker_bad = fingerprint.get("marker_status") in {
        "invalid",
        "unreadable",
    } or fingerprint.get("uuid_status") == "invalid"
    required_review = (
        match_state in {"AMBIGUOUS", "CONFLICTING"}
        or marker_bad
        or (match_state in {"EXACT", "STRONG_EVIDENCE", "LIKELY"} and not connected)
    )
    return DiscoveryCandidate(
        candidate_id=_candidate_id("project", directory, path_key=sighting.path_key),
        kind="project",
        path=directory.as_posix(),
        display_name=directory.name,
        lifecycle=_lifecycle_for(
            match_state, connected=connected, marker_bad=marker_bad
        ),
        match_state=match_state,
        category=_category_for(
            kind="project",
            match_state=match_state,
            connected=connected,
        ),
        why_matched=_why_from_evidence(evidence, match_state),
        why_connected=why_connected if connected else [],
        match_evidence=[row.as_dict() for row in evidence],
        conflicting_evidence=[row.as_dict() for row in conflicts],
        required_review=required_review,
        required_action=_required_action(match_state, connected=connected),
        signals=list(sighting.signals),
        fingerprint=fingerprint,
        matched_project_id=_valid_id_token(matched_id),
        matched_project_uuid=_valid_id_token(matched_uuid),
        candidate_family=sighting.family,
    )


def _materialize_knowledge_candidate(
    sighting: KnowledgeSighting,
    projects: Sequence[DiscoveryCandidate],
    vault_projects: Sequence[VaultProjectIdentity],
) -> DiscoveryCandidate:
    kind: Literal["knowledge", "obsidian_vault"] = (
        "obsidian_vault" if sighting.is_obsidian else "knowledge"
    )
    relation, k_state, k_evidence, k_match = _associate_knowledge(
        sighting.path,
        projects,
        is_obsidian=sighting.is_obsidian,
        vault_projects=vault_projects,
        knowledge_path_key=sighting.path_key,
    )
    if relation == "KNOWLEDGE_DISCOVERED" and not sighting.is_obsidian and not k_evidence:
        relation = "KNOWLEDGE_UNMATCHED"
    k_match = _valid_id_token(k_match)
    if relation == "KNOWLEDGE_PROJECT_MATCHED" and k_match is None:
        relation = "KNOWLEDGE_UNMATCHED"
        k_state = "UNMATCHED"
    required_review = (
        sighting.is_obsidian
        or relation == "KNOWLEDGE_AMBIGUOUS"
        or (relation == "KNOWLEDGE_UNMATCHED" and bool(k_evidence))
    )
    return DiscoveryCandidate(
        candidate_id=_candidate_id(kind, sighting.path, path_key=sighting.path_key),
        kind=kind,
        path=sighting.path.as_posix(),
        display_name=sighting.path.name,
        lifecycle=(
            "POLICY_REVIEW"
            if relation == "KNOWLEDGE_AMBIGUOUS" or sighting.is_obsidian
            else "CLASSIFIED"
        ),
        match_state=k_state,
        category=_category_for(
            kind=kind,
            match_state=k_state,
            connected=False,
            knowledge_relation=relation,
        ),
        why_matched=_why_from_evidence(k_evidence, k_state),
        match_evidence=[row.as_dict() for row in k_evidence],
        conflicting_evidence=[],
        required_review=required_review,
        required_action=(
            "Review Obsidian/knowledge relationship; discovery does not ingest"
            if sighting.is_obsidian or relation == "KNOWLEDGE_AMBIGUOUS"
            else None
        ),
        signals=list(sighting.signals),
        fingerprint={
            "canonical_path": sighting.path.as_posix(),
            "path_key": sighting.path_key,
            "path_fingerprint": root_identity_fingerprint(sighting.path),
            "directory_name": sighting.path.name,
            "obsidian": sighting.is_obsidian,
        },
        matched_project_id=k_match,
        knowledge_relation=relation,
    )


def _walk_authorized_estate(
    *,
    root: Path,
    root_key: str,
    stack: list[tuple[Path, int]],
    seen_dirs: set[str],
    ignored: list[dict[str, str]],
    permission_errors: list[dict[str, str]],
    include_projects: bool,
    include_knowledge: bool,
    max_depth: int,
    enumeration_order: Literal["name_asc", "name_desc"],
    volume_root_is_container: bool,
    project_sightings: list[ProjectSighting],
    knowledge_sightings: list[KnowledgeSighting],
    cache_entries: dict[str, Any],
    depth_limit_holder: list[bool],
    unsafe_escapes_holder: list[int],
) -> None:
    """Traverse the authorized estate. Resolve once per directory, then reuse keys."""
    perf = current_discovery_perf()
    while stack:
        current, depth = stack.pop()
        try:
            perf.path_resolve_calls += 1
            current_resolved = current.resolve(strict=False)
        except (OSError, RuntimeError):
            ignored.append({"path": current.as_posix(), "reason": "unresolvable_path"})
            continue
        key = canonical_path_key_resolved(current_resolved)
        if key in seen_dirs:
            continue
        if not is_canonical_under(key, root_key):
            unsafe_escapes_holder[0] += 1
            ignored.append(
                {
                    "path": current.as_posix(),
                    "reason": "symlink_or_reparse_escape",
                }
            )
            continue
        seen_dirs.add(key)

        try:
            names, lower_files = _dir_entries(current)
        except OSError as exc:
            permission_errors.append(
                {"path": current.as_posix(), "reason": type(exc).__name__}
            )
            continue
        if not names and not current.is_dir():
            permission_errors.append(
                {"path": current.as_posix(), "reason": "not_a_directory"}
            )
            continue

        with phase_timer("project_signal_collection", accumulate=True):
            project_signals = _score_project_signals(current, names, lower_files)
        with phase_timer("knowledge_signal_collection", accumulate=True):
            know_signals = _knowledge_signals(current, names, lower_files)

        try:
            perf.filesystem_stat_calls += 1
            st = current.stat()
            cache_entries[current_resolved.as_posix()] = {
                "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
                "signals": sorted(set(project_signals) | set(know_signals)),
            }
        except OSError:
            pass

        at_volume_root = volume_root_is_container and key == root_key
        if include_projects and _is_project_candidate(project_signals):
            if at_volume_root:
                ignored.append(
                    {
                        "path": current_resolved.as_posix(),
                        "reason": "authorized_volume_root_scope_container",
                    }
                )
            else:
                with phase_timer("cheap_project_sighting", accumulate=True):
                    project_sightings.append(
                        _cheap_project_sighting(
                            current_resolved,
                            path_key=key,
                            depth=depth,
                            root=root,
                            signals=project_signals,
                            names=names,
                        )
                    )

        if include_knowledge:
            is_obsidian = "obsidian_vault" in know_signals
            knowledge_only = bool(know_signals) and (
                is_obsidian
                or not _is_project_candidate(project_signals)
                or any(item.startswith("knowledge_dir:") for item in know_signals)
                or at_volume_root
            )
            if knowledge_only:
                knowledge_sightings.append(
                    KnowledgeSighting(
                        path=current_resolved,
                        path_key=key,
                        depth=depth,
                        region=estate_region_resolved(current_resolved, root),
                        signals=list(know_signals),
                        is_obsidian=is_obsidian,
                        names=set(names),
                    )
                )

        if depth >= max_depth:
            folded_at_bound = {n.casefold() for n in IGNORE_DIR_NAMES}
            for name in names:
                if name.casefold() in folded_at_bound:
                    continue
                child = current / name
                if _is_reparse_or_symlink(child):
                    continue
                try:
                    is_dir = child.is_dir()
                except OSError:
                    continue
                if is_dir:
                    depth_limit_holder[0] = True
                    break
            continue

        folded_ignore = {n.casefold() for n in IGNORE_DIR_NAMES}
        name_reverse = enumeration_order != "name_desc"
        for name in sorted(names, reverse=name_reverse):
            if name.casefold() in folded_ignore:
                ignored.append(
                    {
                        "path": (current / name).as_posix(),
                        "reason": f"ignore_policy:{name.casefold()}",
                    }
                )
                continue
            child = current / name
            if _is_reparse_or_symlink(child):
                if _reparse_escape(child, root):
                    unsafe_escapes_holder[0] += 1
                    ignored.append(
                        {
                            "path": child.as_posix(),
                            "reason": "symlink_or_reparse_escape",
                        }
                    )
                else:
                    ignored.append(
                        {
                            "path": child.as_posix(),
                            "reason": "reparse_or_symlink_not_descended",
                        }
                    )
                continue
            try:
                is_dir = child.is_dir()
            except OSError:
                permission_errors.append(
                    {"path": child.as_posix(), "reason": "permission_denied"}
                )
                continue
            if not is_dir:
                continue
            stack.append((child, depth + 1))


def discover_estate(
    authorized_root: Path,
    *,
    vault: Path | None = None,
    include_projects: bool = True,
    include_knowledge: bool = True,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_project_candidates: int = DEFAULT_MAX_PROJECT_CANDIDATES,
    max_knowledge_candidates: int = DEFAULT_MAX_KNOWLEDGE_CANDIDATES,
    prior_cache: dict[str, Any] | None = None,
    root_mode: str = ROOT_MODE_BOUNDED_DIRECTORY,
    host_os: str | None = None,
    environ: dict[str, str] | None = None,
    enumeration_order: Literal["name_asc", "name_desc"] = "name_asc",
) -> dict[str, Any]:
    """Bounded filesystem discovery under one authorized root.

    ``prior_cache`` is recorded for Lane H foundation only — never used to skip
    identity re-evaluation (P11: STALE_CACHE_TRUTH = 0).
    ``root_mode`` default is bounded-directory. Owner-authorized Windows
    non-system volume roots require an explicit mode; they never connect
    or ingest.
    ``enumeration_order`` is a test hook only. Selection must not depend on it.
    """
    _ = prior_cache  # intentionally unused for skip decisions
    perf = reset_discovery_perf()
    return _discover_estate_timed(
        authorized_root,
        vault=vault,
        include_projects=include_projects,
        include_knowledge=include_knowledge,
        max_depth=max_depth,
        max_project_candidates=max_project_candidates,
        max_knowledge_candidates=max_knowledge_candidates,
        root_mode=root_mode,
        host_os=host_os,
        environ=environ,
        enumeration_order=enumeration_order,
        perf=perf,
    )


def _discover_estate_timed(
    authorized_root: Path,
    *,
    vault: Path | None,
    include_projects: bool,
    include_knowledge: bool,
    max_depth: int,
    max_project_candidates: int,
    max_knowledge_candidates: int,
    root_mode: str,
    host_os: str | None,
    environ: dict[str, str] | None,
    enumeration_order: Literal["name_asc", "name_desc"],
    perf: DiscoveryPerf,
) -> dict[str, Any]:
    t_total = time.perf_counter()
    with phase_timer("root_policy"):
        policy = authorize_discovery_root(
            authorized_root,
            root_mode=root_mode,
            host_os=host_os,
            environ=environ,
        )
    root = policy.resolved
    root_key = canonical_path_key_resolved(root)
    with phase_timer("vault_identity_load"):
        if vault is not None:
            perf.path_resolve_calls += 1
            vault_resolved = vault.expanduser().resolve(strict=False)
        else:
            vault_resolved = None
        vault_projects = load_vault_project_identities(
            vault_resolved, authorized_root=root
        )

    projects: list[DiscoveryCandidate] = []
    knowledge: list[DiscoveryCandidate] = []
    ignored: list[dict[str, str]] = []
    permission_errors: list[dict[str, str]] = []
    unsafe_escapes = 0
    project_limit_reached = False
    knowledge_limit_reached = False
    depth_limit_reached = False
    cache_entries: dict[str, Any] = {}
    project_sightings: list[ProjectSighting] = []
    knowledge_sightings: list[KnowledgeSighting] = []
    volume_root_is_container = policy.volume_root_authorized

    stack: list[tuple[Path, int]] = [(root, 0)]
    seen_dirs: set[str] = set()
    depth_limit_holder = [False]
    unsafe_escapes_holder = [0]

    with phase_timer("filesystem_traversal"):
        _walk_authorized_estate(
            root=root,
            root_key=root_key,
            stack=stack,
            seen_dirs=seen_dirs,
            ignored=ignored,
            permission_errors=permission_errors,
            include_projects=include_projects,
            include_knowledge=include_knowledge,
            max_depth=max_depth,
            enumeration_order=enumeration_order,
            volume_root_is_container=volume_root_is_container,
            project_sightings=project_sightings,
            knowledge_sightings=knowledge_sightings,
            cache_entries=cache_entries,
            depth_limit_holder=depth_limit_holder,
            unsafe_escapes_holder=unsafe_escapes_holder,
        )
    depth_limit_reached = depth_limit_holder[0]
    unsafe_escapes = unsafe_escapes_holder[0]

    with phase_timer("project_preselection"):
        preselect_limit = hierarchical_preselect_budget(
            len(project_sightings), max_project_candidates
        )
        preselected_project_sightings, _ = select_bounded_project_sightings(
            project_sightings, preselect_limit
        )
    with phase_timer("project_enrichment"):
        for item in preselected_project_sightings:
            enrich_project_sighting(item)
    with phase_timer("final_project_selection"):
        selected_project_sightings, _ = select_bounded_project_sightings(
            preselected_project_sightings, max_project_candidates
        )
    selected_keys = {item.path_key for item in selected_project_sightings}
    suppressed_project_sightings = [
        item for item in project_sightings if item.path_key not in selected_keys
    ]
    project_limit_reached = len(project_sightings) > len(selected_project_sightings)
    with phase_timer("project_materialization"):
        projects = [
            _materialize_project_candidate(
                item, vault_projects=vault_projects, vault_resolved=vault_resolved
            )
            for item in selected_project_sightings
        ]
    resolve_before_knowledge = perf.path_resolve_calls
    under_before_knowledge = perf.under_authorized_calls
    with phase_timer("knowledge_selection"):
        selected_knowledge_sightings, suppressed_knowledge_sightings = (
            select_bounded_knowledge_sightings(
                knowledge_sightings, max_knowledge_candidates, projects
            )
        )
    knowledge_limit_reached = len(knowledge_sightings) > len(
        selected_knowledge_sightings
    )
    with phase_timer("knowledge_materialization"):
        knowledge = [
            _materialize_knowledge_candidate(item, projects, vault_projects)
            for item in selected_knowledge_sightings
        ]
        _sanitize_knowledge_relations(knowledge, projects, vault_projects)
    perf.add_phase(
        "path_resolve_calls_during_in_memory_selection",
        float(perf.path_resolve_calls - resolve_before_knowledge),
    )
    _ = under_before_knowledge

    projects.sort(key=lambda item: (item.path.casefold(), item.candidate_id))
    knowledge.sort(key=lambda item: (item.path.casefold(), item.candidate_id))
    ignored.sort(
        key=lambda row: (row.get("path", "").casefold(), row.get("reason", ""))
    )

    categories: dict[str, list[dict[str, Any]]] = {
        "DISCOVERED_PROJECTS": [],
        "NEW_KNOWLEDGE": [],
        "AMBIGUOUS_MATCHES": [],
        "UNMATCHED_KNOWLEDGE": [],
        "IGNORED": ignored,
        "CONNECTED": [],
    }
    for cand in projects:
        categories[cand.category].append(cand.to_dict())
    for cand in knowledge:
        categories[cand.category].append(cand.to_dict())

    truncation_causes: list[str] = []
    if depth_limit_reached:
        truncation_causes.append("max_depth_reached")
    if project_limit_reached and knowledge_limit_reached:
        truncation_causes.append("project_and_knowledge_limits_reached")
    elif project_limit_reached:
        truncation_causes.append("project_limit_reached")
    elif knowledge_limit_reached:
        truncation_causes.append("knowledge_limit_reached")
    if permission_errors:
        truncation_causes.append("permission_errors")
    scan_complete = not truncation_causes
    truncation_reason: str | None = (
        ",".join(truncation_causes) if truncation_causes else None
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "schema": REPORT_SCHEMA,
        "package_id": PACKAGE_ID,
        "directive_family": DIRECTIVE_FAMILY,
        "invariant": "DISCOVER != INGEST != TRUST != AUTHORITY",
        "discovery_identity_source_of_truth": "EXISTING_GOVERNED_ATLAS_STATE",
        "authorized_root": root.as_posix(),
        "authorized_root_mode": policy.authorized_root_mode,
        "volume_root_authorized": policy.volume_root_authorized,
        "volume_root_kind": policy.volume_root_kind,
        "vault": vault_resolved.as_posix() if vault_resolved is not None else None,
        "generated": {"by": "project-atlas"},
        "security": {
            "symlink_follow": False,
            "reparse_follow": False,
            "code_execution": False,
            "network_discovery": False,
            "whole_disk_scan": False,
            "volume_root_authorized": policy.volume_root_authorized,
            "unsafe_path_escapes_detected": unsafe_escapes,
            "unsafe_path_escapes_allowed": 0,
            "casefold_path_identity": _casefold_paths(),
        },
        "scan": {
            "scan_complete": scan_complete,
            "truncation_reason": truncation_reason,
            "truncation_causes": list(truncation_causes),
            "depth_limit_reached": depth_limit_reached,
            "project_limit_reached": project_limit_reached,
            "knowledge_limit_reached": knowledge_limit_reached,
            "max_depth": max_depth,
            "max_project_candidates": max_project_candidates,
            "max_knowledge_candidates": max_knowledge_candidates,
            "permission_errors": permission_errors,
            "dirs_visited": len(seen_dirs),
            "candidate_selection_policy": CANDIDATE_SELECTION_POLICY,
            "project_candidates_seen": len(project_sightings),
            "project_candidates_preselected": len(preselected_project_sightings),
            "project_candidates_enriched": sum(
                1 for item in project_sightings if item.enriched
            ),
            "project_candidates_emitted": len(projects),
            "project_candidates_suppressed": len(suppressed_project_sightings),
            "knowledge_candidates_seen": len(knowledge_sightings),
            "knowledge_candidates_emitted": len(knowledge),
            "knowledge_candidates_suppressed": len(suppressed_knowledge_sightings),
            "region_candidate_counts": _region_counts(project_sightings),
            "region_emitted_counts": _region_counts(selected_project_sightings),
            "operation_counters": perf.counters(),
            "path_resolve_calls_during_in_memory_selection": (
                perf.path_resolve_calls - resolve_before_knowledge
            ),
        },
        "counts": {
            "projects": len(projects),
            "knowledge": len(knowledge),
            "ignored": len(ignored),
            "required_review": sum(
                1 for c in (*projects, *knowledge) if c.required_review
            ),
            "connected": sum(1 for c in projects if c.category == "CONNECTED"),
        },
        "categories": categories,
        "candidates": {
            "projects": [c.to_dict() for c in projects],
            "knowledge": [c.to_dict() for c in knowledge],
        },
        "incremental_foundation": {
            "cache_schema": "estate-discovery-cache-v1",
            "entries_recorded": len(cache_entries),
            "cache_used_for_skip": False,
            "note": (
                "Cache is foundation-only and never authority; identity is "
                "always recomputed from live filesystem + governed vault state."
            ),
        },
        "_cache_entries": cache_entries,
        "_perf": {
            "phases": dict(perf.phases),
            "total": round(time.perf_counter() - t_total, 6),
            "counters": perf.counters(),
            "note": (
                "Diagnostic timings are not canonical truth. "
                "Counters are bounded and non-authoritative."
            ),
        },
    }
    return report


def write_discovery_report(report: dict[str, Any], output: Path) -> Path:
    payload = {k: v for k, v in report.items() if not k.startswith("_")}
    content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_atomic(output, content)
    return output


def write_discovery_cache(report: dict[str, Any], output: Path) -> Path:
    entries = report.get("_cache_entries")
    if not isinstance(entries, dict):
        entries = {}
    payload = {
        "schema_version": 1,
        "schema": "estate-discovery-cache-v1",
        "package_id": PACKAGE_ID,
        "authorized_root": report.get("authorized_root"),
        "cache_used_for_skip": False,
        "entries": entries,
        "generated": {"by": "project-atlas"},
    }
    content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_atomic(output, content)
    return output


def load_discovery_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    text = _safe_read_text(path, limit=8_000_000)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def review_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Actionable review rows (P12)."""
    rows: list[dict[str, Any]] = []
    candidates = report.get("candidates")
    if not isinstance(candidates, dict):
        return []
    for bucket in ("projects", "knowledge"):
        items = candidates.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("required_review"):
                continue
            rows.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "kind": item.get("kind"),
                    "path": item.get("path"),
                    "display_name": item.get("display_name"),
                    "match_state": item.get("match_state"),
                    "lifecycle": item.get("lifecycle"),
                    "category": item.get("category"),
                    "why_matched": item.get("why_matched") or [],
                    "why_connected": item.get("why_connected") or [],
                    "match_evidence": item.get("match_evidence") or [],
                    "conflicting_evidence": item.get("conflicting_evidence") or [],
                    "required_action": item.get("required_action"),
                    "matched_project_id": item.get("matched_project_id"),
                    "knowledge_relation": item.get("knowledge_relation"),
                    "required_review": True,
                }
            )
    rows.sort(
        key=lambda r: (
            str(r.get("path", "")).casefold(),
            str(r.get("candidate_id", "")),
        )
    )
    return rows


def find_candidate(report: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    candidates = report.get("candidates")
    if not isinstance(candidates, dict):
        return None
    for bucket in ("projects", "knowledge"):
        items = candidates.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("candidate_id") == candidate_id:
                return item
    return None


def _fingerprint_identity_tuple(fp: dict[str, Any]) -> tuple[Any, ...]:
    return (
        fp.get("atlas_project_id"),
        fp.get("atlas_project_uuid"),
        fp.get("marker_status"),
        fp.get("uuid_status"),
        fp.get("git_remote"),
        fp.get("package_name"),
        fp.get("path_key") or fp.get("canonical_path"),
    )


def connect_discovered_candidate(
    report: dict[str, Any],
    candidate_id: str,
    *,
    vault: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Connect an accepted project candidate with TOCTOU revalidation (P13)."""
    from project_atlas.connect import ConnectError, connect_project

    cand = find_candidate(report, candidate_id)
    if cand is None:
        raise EstateDiscoveryError(f"unknown candidate_id: {candidate_id}")
    kind = cand.get("kind")
    if kind != "project":
        raise EstateDiscoveryError(
            f"refusing to connect non-project candidate ({kind}); "
            "knowledge/obsidian require explicit policy acceptance "
            "(DISCOVER != INGEST)"
        )
    if cand.get("match_state") == "CONFLICTING":
        raise EstateDiscoveryError(
            "refusing to connect CONFLICTING candidate; resolve identity "
            "review first (no silent project unification)"
        )
    if cand.get("match_state") == "AMBIGUOUS":
        raise EstateDiscoveryError(
            "refusing to connect AMBIGUOUS candidate; resolve which project "
            "identity applies first"
        )

    path_raw = cand.get("path")
    if not isinstance(path_raw, str) or not path_raw:
        raise EstateDiscoveryError("candidate missing path")
    source = Path(path_raw)
    if not source.is_dir():
        raise EstateDiscoveryError(f"candidate path no longer exists: {source}")

    authorized = report.get("authorized_root")
    if isinstance(authorized, str) and authorized.strip():
        try:
            auth = Path(authorized).resolve(strict=False)
            if not _under_authorized(source.resolve(strict=False), auth):
                raise EstateDiscoveryError(
                    "candidate path is outside report authorized_root"
                )
        except OSError as exc:
            raise EstateDiscoveryError(
                f"unable to validate authorized_root containment: {exc}"
            ) from exc

    # TOCTOU: recompute live fingerprint + match; stale report cannot bypass.
    names, lower_files = _dir_entries(source)
    signals = _score_project_signals(source, names, lower_files)
    live_fp = _build_fingerprint(source, signals)
    raw_report_fp = cand.get("fingerprint")
    report_fp: dict[str, Any] = (
        raw_report_fp if isinstance(raw_report_fp, dict) else {}
    )
    vault_projects = load_vault_project_identities(
        vault,
        authorized_root=Path(authorized) if isinstance(authorized, str) else source,
    )
    live_state, _ev, live_conflicts, _mid, _muuid = match_fingerprint(
        live_fp, vault_projects
    )
    if live_state == "CONFLICTING":
        raise EstateDiscoveryError(
            "refusing connect: live identity revalidation is CONFLICTING "
            f"({live_conflicts[0].detail if live_conflicts else 'conflict'})"
        )
    if live_state == "AMBIGUOUS":
        raise EstateDiscoveryError(
            "refusing connect: live identity revalidation is AMBIGUOUS"
        )
    if _fingerprint_identity_tuple(live_fp) != _fingerprint_identity_tuple(report_fp):
        raise EstateDiscoveryError(
            "refusing connect: candidate identity changed since discovery "
            "report (stale report is not authority)"
        )
    if live_state != cand.get("match_state"):
        raise EstateDiscoveryError(
            f"refusing connect: live match_state {live_state} differs from "
            f"report {cand.get('match_state')}"
        )

    try:
        result = connect_project(source, vault=vault, dry_run=dry_run)
    except ConnectError as exc:
        raise EstateDiscoveryError(str(exc)) from exc
    return {
        "package_id": PACKAGE_ID,
        "candidate_id": candidate_id,
        "connect": result if isinstance(result, dict) else {"result": str(result)},
        "revalidated": True,
        "invariant": "DISCOVER != INGEST != TRUST != AUTHORITY",
        "note": "connect invoked explicitly after discovery; discovery alone never ingests",
    }


def format_discovery_human(report: dict[str, Any]) -> str:
    """Stranger-friendly summary: what should the user care about?"""
    lines: list[str] = []
    root = report.get("authorized_root", "?")
    lines.append(f"Atlas knowledge estate discovery under: {root}")
    mode = report.get("authorized_root_mode", ROOT_MODE_BOUNDED_TOKEN)
    lines.append(f"authorized_root_mode: {mode}")
    if report.get("volume_root_authorized"):
        kind = report.get("volume_root_kind") or VOLUME_KIND_NON_SYSTEM_WINDOWS
        lines.append(f"volume_root_authorized: true ({kind})")
        lines.append(
            "This is an explicit owner-authorized Windows volume scan, "
            "not an ordinary bounded-directory scan."
        )
    else:
        lines.append("volume_root_authorized: false")
    lines.append("DISCOVER != INGEST != TRUST != AUTHORITY")
    lines.append("")
    counts_raw = report.get("counts")
    counts: dict[str, Any] = counts_raw if isinstance(counts_raw, dict) else {}
    scan_raw = report.get("scan")
    scan: dict[str, Any] = scan_raw if isinstance(scan_raw, dict) else {}
    lines.append(
        f"Found {counts.get('projects', 0)} project candidate(s), "
        f"{counts.get('knowledge', 0)} knowledge candidate(s), "
        f"{counts.get('required_review', 0)} needing review, "
        f"{counts.get('connected', 0)} connected."
    )
    if not scan.get("scan_complete", True):
        lines.append("SCAN INCOMPLETE")
        if scan.get("depth_limit_reached"):
            lines.append(
                f"Depth limit reached (max_depth={scan.get('max_depth')})."
            )
            lines.append("Some files/directories were not inspected.")
        seen = scan.get("project_candidates_seen")
        emitted = scan.get("project_candidates_emitted")
        if (
            scan.get("project_limit_reached")
            and isinstance(seen, int)
            and isinstance(emitted, int)
        ):
            lines.append(
                f"Project candidate output bounded: emitted {emitted} of "
                f"{seen} seen ({scan.get('candidate_selection_policy')})."
            )
            preselected = scan.get("project_candidates_preselected")
            enriched = scan.get("project_candidates_enriched")
            if isinstance(preselected, int) and isinstance(enriched, int):
                lines.append(
                    f"Bounded enrichment: preselected {preselected}, "
                    f"enriched {enriched} (not every sighting)."
                )
        reason = scan.get("truncation_reason")
        if isinstance(reason, str) and reason and reason != "max_depth_reached":
            lines.append(f"Truncation: {reason}")
        lines.append("Results are not a complete estate inventory.")
    categories_raw = report.get("categories")
    categories: dict[str, Any] = (
        categories_raw if isinstance(categories_raw, dict) else {}
    )
    order = (
        "DISCOVERED_PROJECTS",
        "CONNECTED",
        "AMBIGUOUS_MATCHES",
        "NEW_KNOWLEDGE",
        "UNMATCHED_KNOWLEDGE",
    )
    for cat in order:
        rows = categories.get(cat)
        if not isinstance(rows, list) or not rows:
            continue
        lines.append("")
        lines.append(f"{cat} ({len(rows)})")
        for row in rows[:50]:
            if not isinstance(row, dict):
                continue
            why = row.get("why_matched")
            why0 = why[0] if isinstance(why, list) and why else ""
            lines.append(
                f"  - {row.get('display_name')} [{row.get('match_state')}] "
                f"{row.get('path')}"
            )
            if why0:
                lines.append(f"      why: {why0}")
            why_c = row.get("why_connected")
            if isinstance(why_c, list) and why_c:
                lines.append(f"      connected: {why_c[0]}")
            if row.get("required_review"):
                action = row.get("required_action") or "REQUIRED"
                lines.append(f"      review: {action}")
    ignored = categories.get("IGNORED")
    if isinstance(ignored, list) and ignored:
        lines.append("")
        lines.append(
            f"IGNORED ({len(ignored)}) - policy / safety (not listed in full)"
        )
    lines.append("")
    lines.append(
        "Next: atlas discover review   OR   "
        "atlas discover connect --candidate <id>"
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "CANDIDATE_SELECTION_POLICY",
    "DEFAULT_MAX_DEPTH",
    "DIRECTIVE_FAMILY",
    "IGNORE_DIR_NAMES",
    "INCREMENTAL_CACHE_RELATIVE",
    "PACKAGE_ID",
    "PRESELECT_MULTIPLIER",
    "REPORT_RELATIVE",
    "ROOT_MODE_BOUNDED_DIRECTORY",
    "ROOT_MODE_OWNER_AUTHORIZED_VOLUME",
    "AuthorizedRootDecision",
    "DiscoveryCandidate",
    "EstateDiscoveryError",
    "VaultProjectIdentity",
    "authorize_discovery_root",
    "canonical_path_key",
    "canonical_path_key_resolved",
    "connect_discovered_candidate",
    "current_discovery_perf",
    "discover_estate",
    "enrich_project_sighting",
    "find_candidate",
    "format_discovery_human",
    "hierarchical_preselect_budget",
    "is_filesystem_root",
    "is_unc_root",
    "is_windows_drive_volume_root",
    "is_windows_system_volume_root",
    "load_discovery_cache",
    "load_vault_project_identities",
    "mark_nested_components",
    "match_fingerprint",
    "normalize_root_mode",
    "prove_connected",
    "refuse_dangerous_authorized_root",
    "reset_discovery_perf",
    "review_candidates",
    "sanitize_git_remote_url",
    "select_bounded_knowledge_sightings",
    "select_bounded_project_sightings",
    "write_discovery_cache",
    "write_discovery_report",
]
