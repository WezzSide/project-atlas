"""Release-bound worker image identity (EXECUTOR_PY312_DEPLOYMENT_CLOSURE).

``deploy-release.sh`` builds the worker image from the exact release revision,
proves the Python >= 3.12 contract inside that image, and only then writes
``worker-image.json`` into the release directory (before activation). The
controller of that release reads the binding and refuses to launch workers from
any image whose content identity differs from the binding, so a stale or moved
``latest``/tag can never be used silently, and a rollback (symlink swap back to
the previous release) restores the previous release's image binding with it.

A release without a binding (legacy release, developer checkout) keeps the
configured ``[worker] image`` -- the binding is never operator-configurable via
TOML (``image_id`` / ``image_revision`` are not accepted config keys).
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from controller.config import ConfigError, ControllerConfig

BINDING_FILENAME = "worker-image.json"
SOURCE_REVISION_FILENAME = ".source-revision"
IMAGE_REPOSITORY = "atlas-runner-worker"

_REV_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PYTHON_RE = re.compile(r"^3\.(1[2-9]|[2-9][0-9])(\.[0-9]+)?([A-Za-z0-9+.-]*)$")
_KEYS = {"schema_version", "revision", "tag", "image_id", "python_version"}


class WorkerImageError(ConfigError):
    """The release's worker-image binding is malformed or inconsistent."""


class WorkerImageMismatch(RuntimeError):
    """The image present on the host is not the one the release is bound to."""


@dataclasses.dataclass(frozen=True)
class WorkerImageBinding:
    revision: str
    tag: str
    image_id: str
    python_version: str


def expected_tag(revision: str) -> str:
    return f"{IMAGE_REPOSITORY}:{revision}"


def parse_binding(document: object) -> WorkerImageBinding:
    if not isinstance(document, dict):
        raise WorkerImageError("worker image binding must be a JSON object")
    if set(document) != _KEYS:
        raise WorkerImageError(f"worker image binding keys must be exactly {sorted(_KEYS)}")
    if document["schema_version"] != 1:
        raise WorkerImageError("unsupported worker image binding schema_version")
    revision, tag = document["revision"], document["tag"]
    image_id, python_version = document["image_id"], document["python_version"]
    if not isinstance(revision, str) or not _REV_RE.fullmatch(revision):
        raise WorkerImageError("binding revision must be a 40-hex commit")
    if tag != expected_tag(revision):
        raise WorkerImageError("binding tag must be the revision-specific worker image tag")
    if not isinstance(image_id, str) or not _IMAGE_ID_RE.fullmatch(image_id):
        raise WorkerImageError("binding image_id must be a sha256 content identity")
    if not isinstance(python_version, str) or not _PYTHON_RE.fullmatch(python_version):
        raise WorkerImageError("binding python_version must satisfy the >= 3.12 contract")
    return WorkerImageBinding(revision, tag, image_id, python_version)


def load_binding(release_root: Path) -> WorkerImageBinding | None:
    """Read and validate the release binding; ``None`` when the release has none."""
    path = release_root / BINDING_FILENAME
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkerImageError(f"cannot read worker image binding: {type(exc).__name__}") from exc
    binding = parse_binding(document)
    source = release_root / SOURCE_REVISION_FILENAME
    try:
        source_revision = source.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise WorkerImageError("bound release has no readable .source-revision") from exc
    if source_revision != binding.revision:
        raise WorkerImageError("worker image binding revision differs from the release revision")
    return binding


def bind_config(config: ControllerConfig, release_root: Path) -> ControllerConfig:
    """Return ``config`` with the worker image pinned to the release binding."""
    binding = load_binding(release_root)
    if binding is None:
        return config
    worker = dataclasses.replace(
        config.worker,
        image=binding.tag,
        image_id=binding.image_id,
        image_revision=binding.revision,
    )
    return dataclasses.replace(config, worker=worker)
