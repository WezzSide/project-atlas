"""AS-BACKUP-001 Verified Atlas Snapshot (create / verify / restore).

Operational durability only — snapshot ≠ authority / claim / temporal /
graph truth (BACKUP-001-INV-001). Cold bundles MUST BACK UP D1+D2+D3+D4+D6;
D5 is optional warm cache and never certifies knowledge alone
(BACKUP-001-INV-002). EPHEMERAL TX/TMP never enter cold bundles
(BACKUP-001-INV-003). Certification is fixture / disposable only
(BACKUP-001-INV-006).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from atlas_contracts.identity import ensure_under_root, resolve_under_root
from project_atlas.schema import validate_record

SCHEMA_MANIFEST = "atlas.backup.manifest.v1"
SCHEMA_META = "atlas.backup.meta.v1"
SCHEMA_RECEIPT = "atlas.backup.receipt.v1"
GENERATED_BY = "atlas-backup-001"

DomainId = Literal["D1", "D2", "D3", "D4", "D5", "D6"]
RestoreTier = Literal["T0", "T1", "T2", "T3", "T4"]

COLD_DOMAINS: tuple[DomainId, ...] = ("D1", "D2", "D3", "D4", "D6")
DOMAIN_DIR: dict[DomainId, str] = {
    "D1": "d1-raw-evidence",
    "D2": "d2-vault",
    "D3": "d3-state",
    "D4": "d4-cp-receipts",
    "D5": "d5-derived",
    "D6": "d6-config",
}

_EPHEMERAL_NAME_SUFFIXES = (".atlas-stage", ".atlas-backup")
_EPHEMERAL_DIR_NAMES = {
    ".tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".hg",
    ".svn",
}
_HUMAN_BEGIN = re.compile(r"<!--\s*BEGIN HUMAN:\s*([^\s>]+)\s*-->")
_HUMAN_END = re.compile(r"<!--\s*END HUMAN:\s*([^\s>]+)\s*-->")


class BackupError(ValueError):
    """Fail-closed backup / restore error (non-zero CLI exit)."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _posix(relative: Path | str) -> str:
    return PurePosixPath(str(relative).replace("\\", "/")).as_posix()


def _json_dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_atomic(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _resolve_safe_root(path: Path, *, label: str) -> Path:
    """AT-013 / BACKUP-001-FR-011: reject filesystem root and home."""
    try:
        resolved = path.expanduser().resolve(strict=False)
    except OSError as exc:
        raise BackupError(f"unsafe {label} path {path}: {exc}") from exc
    if resolved == Path(resolved.anchor):
        raise BackupError(f"refusing filesystem root as {label}: {resolved}")
    home = Path.home().resolve()
    if resolved == home or resolved == home / "":
        raise BackupError(f"refusing home directory as {label}: {resolved}")
    return resolved


def _is_ephemeral(relative: PurePosixPath) -> bool:
    parts = relative.parts
    if any(part in _EPHEMERAL_DIR_NAMES for part in parts):
        return True
    name = relative.name
    if name.startswith(".") and name.endswith(_EPHEMERAL_NAME_SUFFIXES):
        return True
    if name.endswith(_EPHEMERAL_NAME_SUFFIXES):
        return True
    # Mid-promote orphans: .<filename>.<tx>.atlas-stage / .atlas-backup
    return ".atlas-stage" in name or ".atlas-backup" in name


def parse_promote_orphan_name(name: str) -> tuple[str, str, str] | None:
    """Parse `.{canonical}.{txn}.atlas-stage|atlas-backup` basenames (AS-CORE2-009).

    Returns ``(canonical_filename, transaction_id, kind)`` where ``kind`` is
    ``atlas-stage`` or ``atlas-backup``. Returns ``None`` when the name does
    not match the `_promote` artifact convention (uuid.hex txn = 32 hex).
    """
    if not name.startswith("."):
        return None
    for kind in ("atlas-stage", "atlas-backup"):
        suffix = f".{kind}"
        if not name.endswith(suffix):
            continue
        body = name[1 : -len(suffix)]
        if len(body) < 33 or body[-33] != ".":
            return None
        txn = body[-32:]
        if any(ch not in "0123456789abcdef" for ch in txn):
            return None
        canonical = body[:-33]
        if not canonical:
            return None
        return canonical, txn, kind
    return None


def find_promote_orphans(root: Path) -> list[str]:
    """Return vault-relative paths of mid-promote EPHEMERAL orphans (FR-003).

    AS-CORE2-009: scanner reused by interrupted-write recovery preflight.
    """
    root = root.resolve()
    found: list[str] = []
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            relative = PurePosixPath(_posix(path.relative_to(root)))
        except ValueError:
            continue
        name = relative.name
        if name.endswith(".atlas-stage") or name.endswith(".atlas-backup"):
            found.append(relative.as_posix())
    return found


#: Layer-B vault content directories (concept notes + navigation). This MUST
#: enumerate every top-level OKF category area emitted by the scaffold
#: (``project_atlas.scaffold.DIRECTORIES``) so cold bundles are byte-complete
#: (BACKUP-001-FR-002). Prior to the RECOVERY_GATE fix the OKF category dirs
#: (capabilities/decisions/infrastructure/standards/technologies) were absent
#: here and their populated notes were silently dropped from every bundle (F1).
_D2_VAULT_DIRS: frozenset[str] = frozenset(
    {
        "projects",
        "00-system",
        "01-portfolio",
        "templates",
        "review",
        # OKF category areas (Layer B canonical concept notes).
        "capabilities",
        "infrastructure",
        "technologies",
        "decisions",
        "standards",
    }
)

#: Top-level vault-root files that are navigation / Layer-B content (D2).
_D2_VAULT_FILES: frozenset[str] = frozenset({"index.md", "README.md", "log.md"})


def classify_vault_path(relative: str) -> DomainId | None:
    """Map a vault-relative POSIX path to a primary backup domain.

    Returns a cold/warm domain for every persisted, non-ephemeral vault path.
    ``None`` means "this path is not recognised vault content"; callers that
    build cold bundles MUST fail closed on ``None`` rather than silently
    omitting the bytes (BACKUP-001-FR-002 completeness contract).
    """
    rel = _posix(relative).lstrip("/")
    if not rel or rel in {".", "./"}:
        return None
    parts = PurePosixPath(rel).parts
    if not parts:
        return None
    head = parts[0]

    if head == "sources":
        if len(parts) >= 2 and parts[1] in {
            "imported-documents",
            "repositories",
            "manifests",
        }:
            return "D1"
        # sources/index.md and similar live as vault navigation (D2).
        return "D2"

    if head in _D2_VAULT_DIRS:
        return "D2"
    if head in _D2_VAULT_FILES:
        return "D2"
    if head == "state":
        return "D3"
    if head == "receipts":
        # Knowledge-compile receipts (receipts/claims/*.json) are integrity
        # proofs produced alongside D3 state; they travel with the state plane
        # (restore tier T2+) and are NOT control-plane (D4) receipts.
        return "D3"
    if head == "routing":
        return "D4"
    if head == "generated":
        return "D5"
    if head == ".atlas":
        return "D6"
    if head in {".atlas-project.yaml", "atlas.toml", "pyproject.toml"}:
        return "D6"
    if len(parts) == 1 and head.endswith((".toml", ".yaml", ".yml", ".json")):
        # Top-level operator config adjacent to vault root.
        return "D6"
    return None


def classify_cp_path(relative: str) -> DomainId | None:
    """Map a control-plane-relative path (D4 / D6 subset)."""
    rel = _posix(relative).lstrip("/")
    parts = PurePosixPath(rel).parts
    if not parts:
        return None
    head = parts[0]
    if head == "routing":
        return "D4"
    if head in {"config", ".atlas"}:
        return "D6"
    if head == "agent-readiness.yaml" or rel.endswith("agent-readiness.yaml"):
        return "D6"
    if head in {"schemas", "references", "skill"}:
        return "D4"
    return "D4"


def _require_under_root_no_reparse(
    root: Path,
    candidate: Path,
    *,
    kind: Literal["source", "identity"],
) -> Path:
    """Fail closed on symlink / junction / reparse escapes (SEC-ADV004B-A-001/002).

    Windows directory junctions report ``Path.is_symlink() is False``; containment
    must use ``os.path.realpath`` (via ``ensure_under_root``) before any read.
    File symlinks are refused even when a platform realpath quirk would miss them.
    """
    if candidate.is_symlink():
        if kind == "identity":
            raise BackupError(
                "refusing vault identity outside vault root "
                f"(symlink/reparse escape): {candidate}"
            )
        raise BackupError(
            "refusing snapshot source outside root "
            f"(symlink/junction escape): {candidate}"
        )
    try:
        safe = ensure_under_root(root, candidate, label=f"backup {kind}")
    except ValueError as exc:
        if kind == "identity":
            raise BackupError(
                "refusing vault identity outside vault root "
                f"(symlink/reparse escape): {candidate}"
            ) from exc
        raise BackupError(
            "refusing snapshot source outside root "
            f"(symlink/junction escape): {candidate}"
        ) from exc
    # Belt: compare realpath containment explicitly (junction / reparse).
    real_root = Path(os.path.realpath(root))
    real_candidate = Path(os.path.realpath(candidate))
    try:
        real_candidate.relative_to(real_root)
    except ValueError as exc:
        if kind == "identity":
            raise BackupError(
                "refusing vault identity outside vault root "
                f"(symlink/reparse escape): {candidate}"
            ) from exc
        raise BackupError(
            "refusing snapshot source outside root "
            f"(symlink/junction escape): {candidate}"
        ) from exc
    return safe


def read_vault_logical_id(vault: Path) -> str:
    """Return stable vault logical identity (FR-005 / FR-013).

    SEC-ADV004B-A-002: refuse symlinked / reparse ``.atlas/vault.json`` whose
    realpath escapes the vault root before stamping MANIFEST/META identity.
    """
    identity_path = vault / ".atlas" / "vault.json"
    if not identity_path.exists():
        raise BackupError(f"missing vault identity: {identity_path}")
    # Gate before read_text — is_file() follows symlinks and would hide escapes.
    safe_identity = _require_under_root_no_reparse(vault, identity_path, kind="identity")
    if not safe_identity.is_file():
        raise BackupError(f"missing vault identity: {identity_path}")
    try:
        raw = json.loads(safe_identity.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"unreadable vault identity: {identity_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BackupError(f"vault identity must be an object: {identity_path}")
    for key in ("vault_logical_id", "vault_uuid", "vault_id"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise BackupError(f"vault identity lacks vault_logical_id/uuid/id: {identity_path}")


def collect_identity_samples(vault: Path) -> dict[str, Any]:
    """Capture identity samples for restore compare (FR-005)."""
    samples: dict[str, Any] = {
        "vault_logical_id": read_vault_logical_id(vault),
        "project_uuids": [],
        "source_lineage_ids": [],
    }
    projects: list[str] = []
    for marker in sorted(vault.rglob(".atlas-project.yaml")):
        try:
            text = marker.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for line in text.splitlines():
            if (":" in line) and ("project_uuid" in line or "uuid:" in line):
                # Lightweight extract — YAML-safe enough for fixtures.
                value = line.split(":", 1)[1].strip().strip("'\"")
                if value and value not in projects:
                    projects.append(value)
    identity_dir = vault / ".atlas" / "projects"
    if identity_dir.is_dir():
        for path in sorted(identity_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict):
                for key in ("project_uuid", "uuid", "project_id"):
                    project_value = raw.get(key)
                    if (
                        isinstance(project_value, str)
                        and project_value
                        and project_value not in projects
                    ):
                        projects.append(project_value)
    samples["project_uuids"] = sorted(projects)

    lineages: list[str] = []
    sources = vault / "state" / "sources.json"
    if sources.is_file():
        try:
            raw = json.loads(sources.read_text(encoding="utf-8"))
            for item in raw.get("sources", []) if isinstance(raw, dict) else []:
                if isinstance(item, dict) and item.get("source_lineage_id"):
                    lineages.append(str(item["source_lineage_id"]))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    samples["source_lineage_ids"] = sorted(set(lineages))
    return samples


def _iter_files(root: Path) -> Iterator[tuple[PurePosixPath, Path]]:
    """Yield relative member paths under ``root``.

    SEC-ADV004B-A-001: every candidate is gated with realpath containment
    (``ensure_under_root`` + explicit symlink refuse) before the caller may
    ``read_bytes``. File symlinks and directory junctions that escape the
    approved root fail closed — ``Path.is_symlink()`` alone misses junctions.
    """
    root = Path(os.path.realpath(root))
    for path in sorted(root.rglob("*")):
        # File symlinks report is_file() True after follow; junctions do not
        # set is_symlink() on the directory — realpath gate below catches both.
        if not path.is_file():
            continue
        try:
            relative = PurePosixPath(_posix(path.relative_to(root)))
        except ValueError as exc:
            raise BackupError(f"path escapes root {root}: {path}") from exc
        if _is_ephemeral(relative):
            continue
        safe = _require_under_root_no_reparse(root, path, kind="source")
        yield relative, safe


def _assert_inside(root: Path, target: Path) -> None:
    """Containment check immediately before sensitive open/write (SEC-004/017/018)."""
    try:
        ensure_under_root(root, target, label="backup path")
    except ValueError as exc:
        raise BackupError(f"refusing path outside root: {target}") from exc


def validate_protected_markers(text: str, *, path: str) -> None:
    """Fail closed on unbalanced HUMAN protection markers (FR-007)."""
    stack: list[str] = []
    for line in text.splitlines():
        begin = _HUMAN_BEGIN.search(line)
        if begin:
            stack.append(begin.group(1))
            continue
        end = _HUMAN_END.search(line)
        if end:
            name = end.group(1)
            if not stack or stack[-1] != name:
                raise BackupError(f"unbalanced protection markers in {path}")
            stack.pop()
    if stack:
        raise BackupError(f"unbalanced protection markers in {path}")


def _member_record(
    *,
    domain: DomainId,
    relative_path: str,
    digest: str,
    size: int,
) -> dict[str, Any]:
    return {
        "domain": domain,
        "path": relative_path,
        "sha256": digest,
        "size": size,
    }


def _deterministic_snapshot_id(members: list[dict[str, Any]], vault_logical_id: str) -> str:
    payload = _json_dump(
        {
            "members": [
                {"domain": m["domain"], "path": m["path"], "sha256": m["sha256"]}
                for m in members
            ],
            "vault_logical_id": vault_logical_id,
        }
    )
    return _sha256_bytes(payload.encode("utf-8"))[:16]


def create_snapshot(
    vault: Path,
    output: Path,
    *,
    cp: Path | None = None,
    include_d5: bool = False,
    snapshot_id: str | None = None,
    allow_orphans: bool = False,
) -> dict[str, Any]:
    """Create a verified external recovery bundle (BACKUP-001-FR-001/002/003/010)."""
    vault_root = _resolve_safe_root(vault, label="vault")
    if not vault_root.is_dir():
        raise BackupError(f"vault root is not a directory: {vault_root}")
    out_root = _resolve_safe_root(output, label="bundle output")
    if out_root.exists() and any(out_root.iterdir()):
        raise BackupError(f"bundle output is not empty: {out_root}")

    orphans = find_promote_orphans(vault_root)
    if orphans and not allow_orphans:
        raise BackupError(
            "refusing snapshot while mid-promote orphans present "
            f"(AS-CORE2-009 clean required): {orphans[:5]}"
        )

    vault_logical_id = read_vault_logical_id(vault_root)
    identity_samples = collect_identity_samples(vault_root)

    members: list[dict[str, Any]] = []
    staged: list[tuple[DomainId, str, bytes]] = []

    for relative, path in _iter_files(vault_root):
        domain = classify_vault_path(relative.as_posix())
        if domain is None:
            # Fail-closed completeness contract (BACKUP-001-FR-002): a
            # persisted, non-ephemeral vault file with no domain would be
            # silently dropped from the cold bundle — that is the F1 defect
            # that degraded restore. Refuse the whole bundle instead so a new,
            # unclassified content type can never be lost. Declared exclusions
            # (EPHEMERAL, filtered in _iter_files; warm D5 below) are explicit,
            # never silent.
            raise BackupError(
                "refusing incomplete cold bundle: unclassified persisted vault "
                f"content would be omitted (BACKUP-001-FR-002): {relative.as_posix()}"
            )
        if domain == "D5" and not include_d5:
            continue
        if domain == "D4" and cp is not None:
            # CP tree supplied separately — skip vault-local routing when external.
            continue
        data = path.read_bytes()
        digest = _sha256_bytes(data)
        if domain == "D2" and path.suffix.lower() == ".md":
            try:
                validate_protected_markers(data.decode("utf-8"), path=relative.as_posix())
            except UnicodeError as exc:
                raise BackupError(f"non-utf8 D2 markdown: {relative}") from exc
        members.append(
            _member_record(
                domain=domain,
                relative_path=relative.as_posix(),
                digest=digest,
                size=len(data),
            )
        )
        staged.append((domain, relative.as_posix(), data))

    if cp is not None:
        cp_root = _resolve_safe_root(cp, label="control-plane")
        if not cp_root.is_dir():
            raise BackupError(f"control-plane root is not a directory: {cp_root}")
        cp_orphans = find_promote_orphans(cp_root)
        if cp_orphans and not allow_orphans:
            raise BackupError(
                "refusing snapshot while CP mid-promote orphans present: "
                f"{cp_orphans[:5]}"
            )
        for relative, path in _iter_files(cp_root):
            domain = classify_cp_path(relative.as_posix())
            if domain is None:
                continue
            if domain == "D5" and not include_d5:
                continue
            data = path.read_bytes()
            digest = _sha256_bytes(data)
            # Prefix CP members to avoid colliding with vault-relative paths.
            member_path = f"cp/{relative.as_posix()}"
            members.append(
                _member_record(
                    domain=domain,
                    relative_path=member_path,
                    digest=digest,
                    size=len(data),
                )
            )
            staged.append((domain, member_path, data))

    members.sort(key=lambda item: (item["domain"], item["path"]))
    staged.sort(key=lambda item: (item[0], item[1]))

    if not any(m["domain"] == "D6" for m in members):
        raise BackupError("cold bundle missing D6 config/identity members")
    for required in COLD_DOMAINS:
        if required == "D4" and cp is None and not any(
            m["domain"] == "D4" for m in members
        ):
            # Allow empty D4 when no routing tree exists (fixture T2); META records omission.
            continue
        if required in {"D1", "D2", "D3"} and not any(m["domain"] == required for m in members):
            raise BackupError(f"cold bundle missing required domain members: {required}")

    sid = snapshot_id or _deterministic_snapshot_id(members, vault_logical_id)
    domains_included = sorted({m["domain"] for m in members})

    bundle_root = out_root
    bundle_root.mkdir(parents=True, exist_ok=True)

    for domain, rel_path, data in staged:
        target = bundle_root / "domains" / DOMAIN_DIR[domain] / PurePosixPath(rel_path)
        _assert_inside(bundle_root, target)
        _write_atomic(target, data)

    manifest: dict[str, Any] = {
        "schema": SCHEMA_MANIFEST,
        "schema_epoch": 1,
        "snapshot_id": sid,
        "vault_logical_id": vault_logical_id,
        "domains_included": domains_included,
        "member_count": len(members),
        "members": members,
        "truth_plane": "operational",
        "authority_plane": "none",
        "note": "BACKUP/RESTORE = OPERATIONAL DURABILITY ≠ PROJECT AUTHORITY",
        "generated": {"by": GENERATED_BY},
    }
    meta: dict[str, Any] = {
        "schema": SCHEMA_META,
        "schema_epoch": 1,
        "snapshot_id": sid,
        "vault_logical_id": vault_logical_id,
        "domains_included": domains_included,
        "include_d5": include_d5,
        "identity_samples": identity_samples,
        "tool_version": "1.0.0",
        "generated": {"by": GENERATED_BY},
    }
    receipt: dict[str, Any] = {
        "schema": SCHEMA_RECEIPT,
        "kind": "backup",
        "outcome": "ok",
        "snapshot_id": sid,
        "vault_logical_id": vault_logical_id,
        "domains_included": domains_included,
        "member_count": len(members),
        "manifest_sha256": "",  # filled after write
        "identity_samples": {
            "vault_logical_id": identity_samples["vault_logical_id"],
            "project_uuid_count": len(identity_samples["project_uuids"]),
            "source_lineage_id_count": len(identity_samples["source_lineage_ids"]),
        },
        "truth_plane": "operational",
        "authority_plane": "none",
        "generated": {"by": GENERATED_BY},
    }

    validate_record(manifest, "backup-manifest")
    validate_record(meta, "backup-meta")

    manifest_text = _json_dump(manifest)
    meta_text = _json_dump(meta)
    _write_atomic(bundle_root / "MANIFEST.json", manifest_text)
    _write_atomic(bundle_root / "META.json", meta_text)
    receipt["manifest_sha256"] = _sha256_bytes(manifest_text.encode("utf-8"))
    validate_record(receipt, "backup-receipt")
    _write_atomic(bundle_root / "RECEIPT.json", _json_dump(receipt))

    # Post-write verify (FR-001).
    verify_bundle(bundle_root)
    return {
        "bundle": str(bundle_root),
        "snapshot_id": sid,
        "vault_logical_id": vault_logical_id,
        "member_count": len(members),
        "domains_included": domains_included,
        "receipt": receipt,
        "manifest": manifest,
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BackupError(f"missing required bundle file: {path.name}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"unreadable {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BackupError(f"{path.name} must be a JSON object")
    return raw


def verify_bundle(bundle: Path) -> dict[str, Any]:
    """Verify MANIFEST digests against member bytes (FR-001 / FR-007)."""
    root = _resolve_safe_root(bundle, label="bundle")
    if not root.is_dir():
        raise BackupError(f"bundle is not a directory: {root}")
    manifest = _load_json(root / "MANIFEST.json")
    meta = _load_json(root / "META.json")
    validate_record(manifest, "backup-manifest")
    validate_record(meta, "backup-meta")

    if manifest.get("vault_logical_id") != meta.get("vault_logical_id"):
        raise BackupError("MANIFEST/META vault_logical_id mismatch")
    if manifest.get("snapshot_id") != meta.get("snapshot_id"):
        raise BackupError("MANIFEST/META snapshot_id mismatch")

    members = manifest.get("members")
    if not isinstance(members, list) or not members:
        raise BackupError("MANIFEST members missing or empty")

    for member in members:
        if not isinstance(member, dict):
            raise BackupError("MANIFEST member must be an object")
        domain_raw = member.get("domain")
        rel = member.get("path")
        expected = member.get("sha256")
        if domain_raw not in DOMAIN_DIR:
            raise BackupError(f"unknown domain in MANIFEST: {domain_raw}")
        domain = domain_raw  # type: DomainId
        if not isinstance(rel, str) or not rel:
            raise BackupError("MANIFEST member path missing")
        if ".." in PurePosixPath(rel).parts or rel.startswith(("/", "\\")):
            raise BackupError(f"unsafe member path in MANIFEST: {rel}")
        if _is_ephemeral(PurePosixPath(rel)):
            raise BackupError(f"EPHEMERAL path must not appear in MANIFEST: {rel}")
        member_path = root / "domains" / DOMAIN_DIR[domain] / PurePosixPath(rel)
        _assert_inside(root, member_path)
        if not member_path.is_file():
            raise BackupError(f"missing manifest member: {domain}/{rel}")
        actual = _sha256_file(member_path)
        if actual != expected:
            raise BackupError(f"digest mismatch for {domain}/{rel}")
        if domain == "D2" and member_path.suffix.lower() == ".md":
            validate_protected_markers(
                member_path.read_text(encoding="utf-8"),
                path=str(rel),
            )

    return {
        "ok": True,
        "snapshot_id": manifest["snapshot_id"],
        "vault_logical_id": manifest["vault_logical_id"],
        "member_count": len(members),
        "domains_included": list(manifest.get("domains_included", [])),
    }


def _tier_domains(tier: RestoreTier) -> tuple[DomainId, ...]:
    if tier == "T0":
        return ("D6",)
    if tier == "T1":
        return ("D1", "D2", "D6")
    if tier == "T2":
        return ("D1", "D2", "D3", "D6")
    if tier == "T3":
        return ("D1", "D2", "D3", "D4", "D6")
    return ("D1", "D2", "D3", "D4", "D5", "D6")  # T4 warm


def _target_is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    if not path.is_dir():
        return False
    return not any(path.iterdir())


def restore_bundle(
    bundle: Path,
    output: Path,
    *,
    tier: RestoreTier = "T3",
    expected_vault_logical_id: str | None = None,
    allow_nonempty: bool = False,
    scaffold: bool = False,
) -> dict[str, Any]:
    """Restore a verified bundle onto an empty disposable target (FR-004…013).

    Never silently heals digest / identity / marker failures.

    Empty-target / scaffold tension (fail-closed resolution): restore always
    demands an EMPTY target (the guard below is unchanged). Scaffold parity —
    the full ``atlas init`` directory skeleton, including structural OKF
    subdirectories that carry no member bytes — cannot be obtained by running
    ``atlas init`` first, because that would leave the target non-empty and
    the guard would (correctly) refuse. ``scaffold=True`` composes the
    deterministic scaffold *into* restore: the empty-target guard is evaluated
    first, then the scaffold skeleton is laid onto the still-approved empty
    target, then verified bundle members overwrite any scaffold defaults with
    the authoritative captured bytes. This never weakens the empty-target,
    wrong-mount, digest, or marker guards.
    """
    verified = verify_bundle(bundle)
    root = _resolve_safe_root(bundle, label="bundle")
    target = _resolve_safe_root(output, label="restore target")

    if not allow_nonempty and not _target_is_empty(target):
        raise BackupError(
            f"refusing restore onto non-empty target (fixture certify requires empty): {target}"
        )

    manifest = _load_json(root / "MANIFEST.json")
    meta = _load_json(root / "META.json")
    bundle_vid = str(manifest["vault_logical_id"])

    if expected_vault_logical_id is not None and expected_vault_logical_id != bundle_vid:
        raise BackupError(
            "wrong-mount refuse: expected vault_logical_id "
            f"{expected_vault_logical_id!r} != bundle {bundle_vid!r}"
        )

    # If target already has identity (dirty remount), refuse mismatch (RS-06).
    existing_identity = target / ".atlas" / "vault.json"
    if existing_identity.is_file():
        try:
            existing_vid = read_vault_logical_id(target)
        except BackupError:
            raise BackupError("wrong-mount refuse: unreadable target vault identity") from None
        if existing_vid != bundle_vid:
            raise BackupError(
                f"wrong-mount refuse: target vault_logical_id {existing_vid!r} "
                f"!= bundle {bundle_vid!r}"
            )
        if not allow_nonempty:
            raise BackupError("refusing restore onto existing vault without allow_nonempty")

    if scaffold:
        # Lay the deterministic vault skeleton onto the (guard-approved empty)
        # target before member restore. Bundle members overwrite scaffold
        # defaults byte-for-byte, so captured content always wins. Import
        # locally to avoid a module import cycle (scaffold has no backup dep).
        from project_atlas.scaffold import ScaffoldError, create_scaffold

        try:
            create_scaffold(target, mint_identity=False)
        except ScaffoldError as exc:
            raise BackupError(f"restore scaffold failed: {exc}") from exc

    allowed = set(_tier_domains(tier))
    members = [m for m in manifest["members"] if isinstance(m, dict) and m.get("domain") in allowed]
    members.sort(key=lambda item: (item["domain"], item["path"]))

    target.mkdir(parents=True, exist_ok=True)

    restored = 0
    for member in members:
        domain_raw = member.get("domain")
        if domain_raw not in DOMAIN_DIR:
            raise BackupError(f"unknown domain during restore: {domain_raw}")
        domain: DomainId = domain_raw  # narrowed by DOMAIN_DIR membership
        rel = str(member["path"])
        try:
            src = resolve_under_root(
                root / "domains" / DOMAIN_DIR[domain],
                rel,
                label="backup member",
            )
        except ValueError as exc:
            raise BackupError(f"refusing path outside root: {rel}") from exc
        _assert_inside(root, src)
        data = src.read_bytes()
        actual = _sha256_bytes(data)
        if actual != member["sha256"]:
            raise BackupError(f"digest mismatch during restore: {domain}/{rel}")

        # CP-prefixed paths restore under vault root without the cp/ prefix when
        # the path is cp/routing/... → routing/...
        dest_rel = rel[3:] if rel.startswith("cp/") else rel
        try:
            dest = resolve_under_root(target, dest_rel, label="restore destination")
        except ValueError as exc:
            raise BackupError(f"refusing path outside root: {dest_rel}") from exc
        _assert_inside(target, dest)
        if domain == "D2" and dest.suffix.lower() == ".md":
            validate_protected_markers(data.decode("utf-8"), path=dest_rel)
        _write_atomic(dest, data)
        restored += 1

    # Post-restore identity stability check.
    restored_samples = collect_identity_samples(target)
    expected_samples = meta.get("identity_samples")
    if isinstance(expected_samples, dict):
        if restored_samples["vault_logical_id"] != expected_samples.get("vault_logical_id"):
            raise BackupError("identity drift: vault_logical_id mismatch after restore")
        if restored_samples["project_uuids"] != list(expected_samples.get("project_uuids", [])):
            raise BackupError("identity drift: project_uuids mismatch after restore")
        if restored_samples["source_lineage_ids"] != list(
            expected_samples.get("source_lineage_ids", [])
        ):
            raise BackupError("identity drift: source_lineage_ids mismatch after restore")

    receipt: dict[str, Any] = {
        "schema": SCHEMA_RECEIPT,
        "kind": "restore",
        "outcome": "ok",
        "snapshot_id": verified["snapshot_id"],
        "vault_logical_id": bundle_vid,
        "domains_included": sorted(allowed & set(manifest.get("domains_included", []))),
        "member_count": restored,
        "tier": tier,
        "manifest_sha256": _sha256_file(root / "MANIFEST.json"),
        "identity_samples": {
            "vault_logical_id": restored_samples["vault_logical_id"],
            "project_uuid_count": len(restored_samples["project_uuids"]),
            "source_lineage_id_count": len(restored_samples["source_lineage_ids"]),
        },
        "truth_plane": "operational",
        "authority_plane": "none",
        "generated": {"by": GENERATED_BY},
    }
    validate_record(receipt, "backup-receipt")
    _write_atomic(target / ".atlas" / "restore-receipt.json", _json_dump(receipt))

    return {
        "target": str(target),
        "snapshot_id": verified["snapshot_id"],
        "vault_logical_id": bundle_vid,
        "member_count": restored,
        "tier": tier,
        "identity_samples": restored_samples,
        "receipt": receipt,
    }


def compare_member_digests(
    bundle: Path,
    vault: Path,
    *,
    domains: Iterable[DomainId] | None = None,
) -> dict[str, Any]:
    """Compare restored vault file digests to bundle MANIFEST (HASH COMPARE)."""
    verified = verify_bundle(bundle)
    root = Path(bundle).resolve()
    manifest = _load_json(root / "MANIFEST.json")
    wanted = set(domains) if domains is not None else set(manifest.get("domains_included", []))
    mismatches: list[str] = []
    checked = 0
    for member in manifest["members"]:
        if member["domain"] not in wanted:
            continue
        rel = str(member["path"])
        dest_rel = rel[3:] if rel.startswith("cp/") else rel
        path = vault.joinpath(*PurePosixPath(dest_rel).parts)
        if not path.is_file():
            mismatches.append(f"missing: {dest_rel}")
            continue
        actual = _sha256_file(path)
        checked += 1
        if actual != member["sha256"]:
            mismatches.append(f"digest mismatch: {dest_rel}")
    if mismatches:
        raise BackupError(f"compare failed: {mismatches[:10]}")
    return {
        "ok": True,
        "checked": checked,
        "snapshot_id": verified["snapshot_id"],
        "vault_logical_id": verified["vault_logical_id"],
    }


def protected_region_digest(text: str) -> str:
    """SHA-256 of concatenated HUMAN protected regions (byte fidelity probe)."""
    regions: list[str] = []
    current: list[str] | None = None
    current_name: str | None = None
    for line in text.splitlines(keepends=True):
        begin = _HUMAN_BEGIN.search(line)
        if begin:
            current = []
            current_name = begin.group(1)
            continue
        end = _HUMAN_END.search(line)
        if end and current is not None:
            if end.group(1) != current_name:
                raise BackupError("unbalanced protection markers during digest")
            regions.append("".join(current))
            current = None
            current_name = None
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        raise BackupError("unbalanced protection markers during digest")
    return _sha256_bytes("".join(regions).encode("utf-8"))


def copy_tree(src: Path, dest: Path) -> None:
    """Copy a disposable fixture tree (tests / drills only)."""
    if dest.exists():
        raise BackupError(f"copy destination exists: {dest}")
    shutil.copytree(src, dest)
