"""Deterministic source discovery for the Atlas Core vertical slice."""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from atlas_contracts.event_package import EventPackageInventory, inspect_event_package
from atlas_contracts.paths import safe_relative_path
from project_atlas.domain.sources import SourceRecord
from project_atlas.domain.vocabulary import ClassificationState
from project_atlas.logging import get_logger
from project_atlas.quarantine import scan_identifier
from project_atlas.secrets import scan_text
from project_atlas.source_identity import (
    TEXT_SOURCE_EXTENSIONS,
    canonical_source_sha256,
    canonicalize_project_path,
    validate_project_uuid,
)

_log = get_logger("discovery")

SUPPORTED_EXTENSIONS = TEXT_SOURCE_EXTENSIONS
SENSITIVE_NAMES = {".env", "credentials.json", "secrets.pem", "id_rsa", "id_ed25519"}
DEFAULT_EXCLUDES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", ".tmp",
    # Coder Alpha / local dogfood: in-tree vault + bind metadata must never
    # re-enter discovery as project sources (AS-CODER-ALPHA-CONNECT-001).
    ".atlas-vault", ".atlas",
    # Dogfood/real estates: fixture corpora must not masquerade as live projects
    # when connecting a repository root (AS-CODER-ALPHA-WEB-001 dogfood).
    # Discovering a fixture directory itself as --source remains valid because
    # "fixtures" is then the root, not a relative path part.
    "fixtures",
}

# AS-CORE-003: static media-type map. mimetypes.guess_type() depends on the
# host OS mime database (Linux maps .yaml, Windows does not), which made
# manifest media_type platform-dependent and broke NFR-001 determinism.
_MEDIA_TYPES: dict[str, str] = {
    ".html": "text/html",
    ".json": "application/json",
    ".md": "text/markdown",
    ".toml": "application/toml",
    ".txt": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def _media_type(path: Path) -> str:
    """Return the deterministic media type for a discovered source file."""
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _sha256(path: Path) -> str:
    """Return the streaming canonical SHA-256 of a source file."""
    return canonical_source_sha256(path)


def _project_context(path: Path, root: Path) -> tuple[str | None, str | None]:
    current = path.parent
    while True:
        for marker in (current / ".atlas-project.yaml", current / ".atlas" / "project.yaml"):
            if marker.is_file():
                try:
                    data = yaml.safe_load(marker.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, yaml.YAMLError) as exc:
                    # D-057: controlled fail-closed — no raw YAML traceback.
                    raise ValueError(
                        f"INVALID_PROJECT_MARKER: invalid project marker YAML: "
                        f"{marker.relative_to(root)}"
                    ) from exc
                if data is None:
                    data = {}
                if not isinstance(data, dict):
                    raise ValueError(
                        f"INVALID_PROJECT_MARKER: project marker must be an object: "
                        f"{marker.relative_to(root)}"
                    )
                project_data = data.get("project")
                value = project_data.get("id") if isinstance(project_data, dict) else None
                raw_uuid = data.get("project_uuid")
                project_uuid = None
                if raw_uuid is not None:
                    project_uuid = validate_project_uuid(str(raw_uuid))
                if isinstance(value, str) and value:
                    findings = scan_identifier(value)
                    if findings:
                        raise ValueError(
                            f"adversarial project identifier in {marker.relative_to(root)}: "
                            f"{findings[0].rule} ({findings[0].redacted_hint})"
                        )
                    # AS-SEC-SCAN-DISCOVER-YAML-001: yaml.safe_load decodes
                    # quoted escapes after raw scan_text misses (\u0041KIA…).
                    # scan_identifier is injection-only and does not apply
                    # NFR-004 secret patterns. Scan the decoded id before it
                    # becomes likely_project / compatibility source_id.
                    secret_findings = scan_text(value)
                    if secret_findings:
                        raise ValueError(
                            f"secret-shaped project identifier in "
                            f"{marker.relative_to(root)}: "
                            f"{secret_findings[0].pattern} "
                            f"({secret_findings[0].redacted_hint})"
                        )
                    return str(value), project_uuid
                return None, project_uuid
        if current == root or current.parent == current:
            return None, None
        current = current.parent


def _project_id(path: Path, root: Path) -> str | None:
    """Return the compatibility display/project scope identifier."""
    return _project_context(path, root)[0]


def _compatibility_source_id(
    *,
    canonical_relative: str,
    project_id: str | None,
    root: Path,
) -> str:
    """Mint a vault-unique compatibility ``source_id`` (D-050 R3).

    Path-only IDs collide when multiple project roots share a vault and the same
    relative paths (``README.md``). Scope by governed ``project.id`` when the
    marker is present; otherwise by a stable fingerprint of the discover root.
    Durable lineage remains ``source_lineage_id`` (project UUID + path + SHA).
    """
    if isinstance(project_id, str) and project_id.strip():
        material = f"project:{project_id.strip()}|{canonical_relative}"
    else:
        root_fp = hashlib.sha256(
            root.resolve().as_posix().casefold().encode("utf-8")
        ).hexdigest()[:16]
        material = f"root:{root_fp}|{canonical_relative}"
    return "source-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _excluded(relative: str, path: Path, *, excludes: list[str]) -> str | None:
    if any(part in DEFAULT_EXCLUDES for part in PurePosixPath(relative).parts):
        return "default-excluded-directory"
    if any(
        PurePosixPath(relative).match(pattern) or fnmatch.fnmatch(relative, pattern)
        for pattern in excludes
    ):
        return "configured-exclusion"
    if path.name in SENSITIVE_NAMES or path.name.startswith(".env"):
        return "sensitive-metadata-only"
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "unsupported-format"
    return None


def _non_portable_reason(relative: str) -> str | None:
    """Reason a Linux-legal relative path cannot be represented portably.

    Linux permits names the shared path contract (CODEX-SEC-004/014/017/018)
    refuses on every platform: colons, control characters, Windows reserved
    basenames, trailing dots or spaces. A backslash is a legal Linux filename
    character too, but the contract reinterprets it as a separator, so the
    round trip must be lossless or the recorded path would name a different
    file than the one discovered. Such sources are recorded as excluded
    evidence here rather than failing the whole run closed at the ingest
    boundary, which is where they used to surface (a Linux-only dead end:
    discovery emitted a path ingestion could never accept).
    """
    try:
        segments = safe_relative_path(relative, label="source path")
    except ValueError:
        return "non-portable-path"
    if "/".join(segments) != relative:
        return "non-portable-path"
    return None


def _reportable(relative: str) -> str:
    """An ASCII-safe rendering of a path for log messages.

    Paths reaching the log may be undecodable (lone surrogates) or merely
    non-ASCII, and a diagnostic must never itself raise while reporting a
    problem. Encoding to ASCII (not UTF-8) is what escapes both: UTF-8 can
    encode an accented name happily, and the resulting bytes then fail an
    ASCII decode.

    Control characters are ASCII, so `backslashreplace` leaves them intact.
    They are escaped explicitly here because a newline in a reported path
    would otherwise end the warning early and forge what reads as a second
    log record.

    The reachable input set is wider than the escaping-symlink diagnostic
    that made it obvious. That one reports a *physical target outside the
    source root*, a path this repository never constrained -- but an in-root
    name carrying a control character reaches a log by several routes too:
    the undecodable-filename branch logs before portability is evaluated, a
    record excluded as `non-portable-path` is still reported if it later
    collides canonically, and the reserved-scope, inaccessible-scope and
    unreadable-path diagnostics all report paths verbatim. That list is
    illustrative, not exhaustive -- which is exactly why escaping belongs
    here in the shared helper rather than at any one call site.
    """
    ascii_only = relative.encode("ascii", "backslashreplace").decode("ascii")
    return "".join(
        char if char.isprintable() else f"\\x{ord(char):02x}" for char in ascii_only
    )


#: Errnos the discovery contract already treats as "this scope cannot be read"
#: rather than as a fault. The first four are exactly what `pathlib` itself
#: ignores when answering is_file()/is_dir() (see `pathlib._ignore_error`); the
#: next two mean the scope exists but may not be entered; ENAMETOOLONG means
#: the kernel will not address the entry at all (an absolute path past
#: PATH_MAX, which Linux happily lets a tree grow into) -- a property of that
#: one path, not a fault. Anything else -- ENOMEM, EIO, ENFILE -- is a genuine
#: failure and must stay visible rather than be absorbed into a "keep going"
#: path. Every `except OSError` in this module filters through
#: `_is_inaccessible_scope`: an unreadable path is reported (or recorded as
#: excluded evidence) and the run continues; a fault propagates, because
#: "skipped: Input/output error" with exit 0 would report a corruption
#: signal as an ordinary permission problem.
_INACCESSIBLE_SCOPE_ERRNOS = frozenset(
    {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EBADF,
        errno.ELOOP,
        errno.EACCES,
        errno.EPERM,
        errno.ENAMETOOLONG,
    }
)


def _is_inaccessible_scope(exc: OSError) -> bool:
    """True when `exc` means an unreadable scope, not an unexpected fault."""
    return exc.errno in _INACCESSIBLE_SCOPE_ERRNOS


def _reachable_is_dir(path: Path) -> bool | None:
    """`path.is_dir()`, or None when the answer itself is unreachable.

    `pathlib` only swallows ENOENT/ENOTDIR/EBADF/ELOOP, so `is_dir()` raises
    EACCES for an entry whose parent is readable but not traversable (mode
    0444). Guarding only `iterdir()` therefore left a whole class of
    unreadable scopes still aborting the run.
    """
    try:
        return path.is_dir()
    except OSError as exc:
        if not _is_inaccessible_scope(exc):
            raise
        return None


def _report_unreadable_event_scope(path: Path, root: Path) -> None:
    """Report an agent-event scope whose packages could not be inventoried.

    This diagnostic is emitted by the inventory itself rather than inherited
    from `discover()`'s walk. Relying on the walk was unsound: with the inbox
    at mode 0111 the walk cannot *list* it -- so it reports only `.atlas-inbox`
    and never reaches the descendants -- while this inventory needs only `+x`
    and traverses straight past, dropping an unreadable child silently. It
    also states a different fact: event packages were not inventoried, which
    is not what "contents not inventoried" tells a reader about a source walk.
    """
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - path is always under root here
        relative = str(path)
    _log.warning(
        "agent-event scope not readable, packages not inventoried: %s",
        _reportable(relative),
    )


def _symlinked_scope_target(path: Path) -> str | None:
    """Physical target when `path` is itself a symbolic link, else None.

    The reserved agent-event scope is identified by where it physically sits.
    `discover()`'s walk never follows a link, so a package reached through
    one is inventoried under its real path as ordinary sources -- and if the
    inventory followed the same link it would count the package a second
    time, or (a link to outside the root) inventory content the walk refused.

    Detection is by `lstat` (`is_symlink`), which is what makes the answer
    about the entry itself rather than about whatever it points at -- the
    loader's own refusal of a symlinked package inspects an already-resolved
    path and so never fires. `realpath` is used only afterwards, to name the
    physical target in the diagnostic; the decision never depends on it.
    """
    try:
        if not path.is_symlink():
            return None
    except OSError as exc:
        if not _is_inaccessible_scope(exc):
            raise
        return None  # unreadable: the caller's reachability probe reports it
    return os.path.realpath(path)


def _is_listable(path: Path) -> bool:
    """True when this directory's entries can actually be enumerated.

    `Path.rglob` swallows the failure for a directory it cannot read, so an
    inaccessible subtree would otherwise vanish from the inventory with no
    record and no diagnostic. Probing here is what makes that loss
    observable.
    """
    try:
        with os.scandir(path) as entries:
            next(iter(entries), None)
    except OSError as exc:
        if not _is_inaccessible_scope(exc):
            raise
        return False
    return True


def _uninventoried_symlink_target(path: Path, root: Path) -> str | None:
    """Physical target of a symlink whose content the walk will never reach.

    A symlink is never evidence in its own right: following one would either
    duplicate a document already inventoried under its real path, or escape
    the source root. When the target resolves *inside* the root that
    duplication argument holds and nothing is lost, so this returns None.

    When it resolves *outside* the root, the content is real, readable and
    reachable at a path under the root, yet `rglob` neither yields it (a file
    symlink is skipped as non-regular) nor descends it (a directory symlink is
    not followed) -- so a real document, or an entire subtree behind a
    directory symlink, would disappear with no record and no diagnostic.
    Returns the physical path so the exclusion can be reported.
    """
    try:
        resolved = Path(os.path.realpath(path))
        if not resolved.exists():
            return None  # broken link: there is no document to lose
        if resolved.is_relative_to(root):
            return None  # inventoried under its own real path
        if not (resolved.is_file() or resolved.is_dir()):
            return None  # FIFO, device, socket: not a document
    except OSError as exc:
        if not _is_inaccessible_scope(exc):
            raise
        return None
    return resolved.as_posix()


def _is_recordable(relative: str) -> bool:
    """False when a path cannot appear in the UTF-8 JSON manifest at all.

    Linux filenames are byte strings, not text. A name that is not valid
    UTF-8 decodes to lone surrogates (``surrogateescape``), which cannot be
    encoded back out -- it would break both the inventory hash and the
    manifest write, aborting the whole run over one file.
    """
    try:
        relative.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _discover_agent_events(root: Path) -> list[dict[str, Any]]:
    """Inventory Control Plane packages without importing its implementation.

    A symbolic link anywhere on the chain from the root to a package directory
    is never followed (see `_symlinked_scope_target`). The scope or project
    behind one is refused with a diagnostic naming the alias and its physical
    target; a linked event directory is recorded as an `invalid` row, so the
    refusal reaches the manifest and ingestion quarantines it without loading.

    The chain is tested before any probe that would follow it. Asking
    `is_dir()` first would stat *through* an escaping link before the refusal
    ran, and would answer False for a dangling or file-targeted scope link --
    dropping it as "no scope at all", with no diagnostic naming the alias.
    """
    inbox = root / ".atlas-inbox" / "agent-events"
    for scope in (inbox.parent, inbox):
        target = _symlinked_scope_target(scope)
        if target is not None:
            _log.warning(
                "agent-event scope is a symbolic link, packages not inventoried: %s -> %s",
                _reportable(scope.relative_to(root).as_posix()),
                _reportable(target),
            )
            return []
    inbox_is_dir = _reachable_is_dir(inbox)
    if inbox_is_dir is None:
        _report_unreadable_event_scope(inbox, root)
        return []
    if not inbox_is_dir:
        return []
    inventories: list[EventPackageInventory] = []
    try:
        project_dirs = sorted(inbox.iterdir(), key=lambda path: path.name.lower())
    except OSError as exc:
        if not _is_inaccessible_scope(exc):
            raise
        _report_unreadable_event_scope(inbox, root)
        return []
    for project_dir in project_dirs:
        target = _symlinked_scope_target(project_dir)
        if target is not None:
            _log.warning(
                "symbolic link in reserved agent-event scope, packages beneath it "
                "not inventoried: %s -> %s",
                _reportable(project_dir.relative_to(root).as_posix()),
                _reportable(target),
            )
            continue
        project_is_dir = _reachable_is_dir(project_dir)
        if project_is_dir is None:
            _report_unreadable_event_scope(project_dir, root)
            continue
        if not project_is_dir:
            # AS-INT-001: only `<project-id>/<event-id>/` package directories
            # are valid here, and `discover()` excludes this whole subtree from
            # `sources` so package components are not double-counted as
            # ordinary documentation. A real file dropped in here therefore
            # reaches neither inventory -- it must not do so silently. No
            # source identity and no agent event are synthesized for it: it
            # has neither a project_id nor an event_id, and inventing either
            # would fabricate routed evidence.
            _log.warning(
                "unexpected non-package entry in reserved agent-event scope: %s "
                "(only <project-id>/<event-id>/ package directories are valid here)",
                _reportable(project_dir.relative_to(root).as_posix()),
            )
            continue
        try:
            event_dirs = sorted(project_dir.iterdir(), key=lambda path: path.name.lower())
        except OSError as exc:
            if not _is_inaccessible_scope(exc):
                raise
            _report_unreadable_event_scope(project_dir, root)
            continue
        for event_dir in event_dirs:
            target = _symlinked_scope_target(event_dir)
            if target is not None:
                # The loader's contract, enforced where it can actually see
                # the link. Identity is taken from the path exactly as it is
                # for any other invalid package; no content is read.
                _log.warning(
                    "symlinked event package recorded as invalid: %s -> %s",
                    _reportable(event_dir.relative_to(root).as_posix()),
                    _reportable(target),
                )
                inventories.append(
                    EventPackageInventory(
                        project_id=project_dir.name,
                        event_id=event_dir.name,
                        package_path=event_dir.relative_to(root).as_posix(),
                        classification="agent-event",
                        status="invalid",
                        errors=["event package directory is missing or symlinked"],
                    )
                )
                continue
            event_is_dir = _reachable_is_dir(event_dir)
            if event_is_dir is None:
                _report_unreadable_event_scope(event_dir, root)
                continue
            if not event_is_dir:
                _log.warning(
                    "unexpected non-package entry in reserved agent-event scope: %s "
                    "(only <project-id>/<event-id>/ package directories are valid here)",
                    _reportable(event_dir.relative_to(root).as_posix()),
                )
                continue
            relative = event_dir.relative_to(root).as_posix()
            try:
                inventories.append(
                    inspect_event_package(root, project_dir.name, event_dir.name, relative)
                )
            except OSError as exc:
                if not _is_inaccessible_scope(exc):
                    raise
                # No inventory row: a partially-read package would be
                # fabricated evidence.
                _report_unreadable_event_scope(event_dir, root)
                continue
    by_event_id: dict[str, list[EventPackageInventory]] = {}
    for inventory in inventories:
        by_event_id.setdefault(inventory.event_id, []).append(inventory)
    result: list[dict[str, Any]] = []
    for inventory in inventories:
        peers = by_event_id[inventory.event_id]
        if len(peers) > 1:
            hashes = [peer.component_sha256 for peer in peers]
            identical = bool(hashes[0]) and all(peer_hash == hashes[0] for peer_hash in hashes)
            inventory = inventory.model_copy(
                update={
                    "status": "valid" if identical else "conflicting",
                    "errors": [
                        *inventory.errors,
                        "identical duplicate event_id in inbox"
                        if identical
                        else "conflicting duplicate event_id in inbox",
                    ],
                }
            )
        result.append(inventory.model_dump(mode="json"))
    return result


def discover(
    root: Path,
    *,
    excludes: list[str] | None = None,
    max_file_size: int = 10 * 1024 * 1024,
) -> dict[str, Any]:
    """Discover files under ``root`` into a deterministic JSON manifest."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"source root is not a directory: {root}")
    records: list[dict[str, Any]] = []
    event_root = root / ".atlas-inbox" / "agent-events"
    seen_canonical: dict[str, str] = {}
    for path in sorted(
        root.rglob("*"),
        # The case-folded key alone ties on case variants (README.md vs
        # readme.md); a stable sort then inherits directory-entry order, so
        # the inventory hash depended on creation order (NFR-001). Appending
        # the raw path breaks every tie totally, which is also what makes
        # "first in deterministic order wins" true for collision handling.
        key=lambda item: (item.as_posix().lower(), item.as_posix()),
    ):
        relative = path.relative_to(root).as_posix()
        try:
            # The first metadata access, not `stat()` below: a directory that
            # lists but does not traverse (mode 0444) makes even is_file()
            # raise EACCES. One unreachable entry must never abort the run.
            is_link = path.is_symlink()
            regular_file = path.is_file() and not is_link
            directory = path.is_dir() and not is_link
        except OSError as exc:
            if not _is_inaccessible_scope(exc):
                raise
            _log.warning(
                "skipped unreadable path: %s (%s)", _reportable(relative), exc.strerror
            )
            continue
        if directory and not _is_listable(path):
            # Evidence that a scope exists and could not be inspected. Its
            # contents are deliberately not invented -- they were never read.
            _log.warning(
                "inaccessible discovery scope, contents not inventoried: %s",
                _reportable(relative),
            )
            continue
        if not regular_file:
            if is_link:
                escaped = _uninventoried_symlink_target(path, root)
                if escaped is not None:
                    _log.warning(
                        "symlink target outside the source root is not inventoried: "
                        "%s -> %s",
                        _reportable(relative),
                        _reportable(escaped),
                    )
            continue
        if _reachable_is_dir(event_root) and path.is_relative_to(event_root):
            continue
        if not _is_recordable(relative):
            # Reported rather than recorded: a sanitized path would be a
            # claim about a file that does not exist under that name, and
            # could collide with one that does.
            _log.warning(
                "skipped source with undecodable filename: %s", _reportable(relative)
            )
            continue
        reason = _excluded(relative, path, excludes=excludes or [])
        if reason is None:
            reason = _non_portable_reason(relative)
        try:
            stat = path.stat()
        except OSError as exc:
            if not _is_inaccessible_scope(exc):
                raise
            # No metadata at all, so no evidence-backed record can be made --
            # but the skip must still be observable, or this is exactly the
            # silent loss the scope probe above exists to prevent.
            _log.warning(
                "skipped unmeasurable path: %s (%s)", _reportable(relative), exc.strerror
            )
            continue
        if reason is None and stat.st_size > max_file_size:
            reason = "oversized"
        canonical_relative = canonicalize_project_path(relative)
        collided_with = seen_canonical.get(canonical_relative)
        if collided_with is not None:
            # Two distinct Linux files can share one canonical path: NFC/NFD
            # normalization equivalents, or a literal backslash that the
            # canonical form reads as a separator. Identity is deliberately
            # host-independent (AS-ID-001), so the canonical space cannot
            # represent both, and emitting both would abort the whole run at
            # the CODEX-SEC-002 duplicate-identity guard. The first in
            # deterministic order keeps the identity; the collider is
            # reported, never recorded under a synthesized identity the
            # contract does not define.
            _log.warning(
                "skipped canonical-path collision: %s collides with %s (canonical %s)",
                _reportable(relative),
                _reportable(collided_with),
                _reportable(canonical_relative),
            )
            continue
        seen_canonical[canonical_relative] = relative
        if reason == "sensitive-metadata-only":
            digest = None
        else:
            try:
                digest = _sha256(path)
            except OSError as exc:
                if not _is_inaccessible_scope(exc):
                    # A read that fails mid-stream with EIO is not an
                    # "unreadable" file: recording it as one would classify
                    # a corruption signal as a permission problem.
                    raise
                # Linux routinely exposes files whose content the caller
                # cannot read (mode 000, foreign ownership under a readable
                # directory). Record the real stat metadata without a digest
                # instead of aborting the entire discovery run.
                digest = None
                reason = reason or "unreadable"
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        project_id, project_uuid = _project_context(path, root)
        source_id = _compatibility_source_id(
            canonical_relative=canonical_relative,
            project_id=project_id,
            root=root,
        )
        source_record = SourceRecord(
            source_id=source_id,
            project_uuid=project_uuid,
            source_lineage_id=None,
            path=relative,
            media_type=_media_type(path),
            sha256=digest,
            size_bytes=stat.st_size,
            modified_at=modified,
            likely_project=project_id,
            classification_state=(
                ClassificationState.EXCLUDED if reason else ClassificationState.UNCLASSIFIED
            ),
            exclusion_reason=reason,
        )
        records.append(source_record.model_dump(mode="json"))
    groups: dict[str, list[str]] = {}
    for record in records:
        digest = record.get("sha256")
        if isinstance(digest, str):
            groups.setdefault(digest, []).append(str(record["source_id"]))
    duplicates = {digest: ids for digest, ids in sorted(groups.items()) if len(ids) > 1}
    semantic = {
        "schema_version": 1,
        "source_root": str(root),
        "sources": records,
        "duplicates": duplicates,
        "agent_events": _discover_agent_events(root),
    }
    inventory_hash = hashlib.sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    semantic["inventory_sha256"] = inventory_hash
    return semantic


def write_manifest(manifest: dict[str, Any], output: Path) -> None:
    """Atomically write a stable manifest."""
    output.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if output.is_file() and output.read_text(encoding="utf-8") == content:
        return
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, output)
