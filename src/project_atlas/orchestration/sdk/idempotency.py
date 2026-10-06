"""Stable idempotency keys for Atlas-created SDK runs."""

from __future__ import annotations

import hashlib
import re

from project_atlas.orchestration.autonomy.models import CANONICAL_REPOSITORY_IDENTITY
from project_atlas.orchestration.autonomy.trust import repository_identities_match
from project_atlas.orchestration.sdk.models import AgentRole, SdkRuntimeError

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def build_idempotency_key(
    *,
    repository_identity: str = CANONICAL_REPOSITORY_IDENTITY,
    dag_generation: int,
    node_id: str,
    role: AgentRole | str,
    attempt: int,
) -> str:
    """Derive a stable key so retries never duplicate work."""
    if not repository_identities_match(repository_identity, CANONICAL_REPOSITORY_IDENTITY):
        raise SdkRuntimeError("foreign repository rejected", code="FOREIGN_REPO")
    if not _SAFE.fullmatch(node_id):
        raise SdkRuntimeError("unsafe node_id", code="UNSAFE_NODE")
    if attempt < 1 or attempt > 10_000:
        raise SdkRuntimeError("attempt out of range", code="BAD_ATTEMPT")
    if dag_generation < 0 or dag_generation > 1_000_000:
        raise SdkRuntimeError("dag_generation out of range", code="BAD_GENERATION")
    role_value = role.value if isinstance(role, AgentRole) else str(role)
    if not _SAFE.fullmatch(role_value):
        raise SdkRuntimeError("unsafe role", code="UNSAFE_ROLE")
    material = (
        f"{CANONICAL_REPOSITORY_IDENTITY}|g{dag_generation}|{node_id}|{role_value}|a{attempt}"
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    # Keep a human-readable prefix for ops, bounded length for SDK.
    return f"atlas-{role_value.lower()}-{digest}"
