"""Explicit backlog acceptance contracts -- SOURCE_AUTHORITY = EXPLICIT.

AS-ORIGIN-ACCEPTANCE-001 (PR-D). A bare Markdown task-list checkbox
(``tasklist_adapter.py``) carries no evidence, no proposed scope, no
success criteria -- by design, nothing about a checkbox title is ever
inferred into any of those fields. ``policy.py``'s existing
``corroborating_signal`` gate and ``risk.py``'s existing
``OUT_OF_SPECIFICATION_COVERAGE`` disqualifier therefore correctly
refuse ``execution_ready`` for every such item today. That is the
CORRECT behavior for an item with no verifiable acceptance contract --
this module does not change it, weaken it, or add a second gating
pathway around it.

What this module adds is the explicit, reviewable alternative the
directive asks for: a sidecar acceptance-contract file, declared via
``.atlas-project.yaml`` the same way ``sources.py::origination_sources``
already is, mapping one ``(source_path, item_id)`` pair to real,
human-authored ``evidence`` / ``proposed_scope`` / ``success_criteria``
-- committed repository metadata, never inferred from prose or a
filename. ``apply_acceptance_contracts()`` merges those fields onto a
matching ``EligibleRoadmapItem`` (as ``contract_proposed_scope`` /
``contract_success_criteria`` overrides, and by widening ``evidence``)
strictly AFTER adapter parsing and BEFORE ``pipeline.py::_build_outcome()``
-- ``pipeline.py``, ``policy.py``, and ``risk.py`` are not modified by
this module at all. Whether an enriched item actually reaches
``execution_ready = TRUE`` is still decided entirely by those existing,
unmodified gates -- in particular, a contract's ``evidence`` paths still
have to genuinely carry a skip/xfail pytest marker for
``extract_corroborating_facts()`` to treat them as corroborating at all;
listing a file that doesn't is a contract that legitimately still fails
policy, not a bug in this module.

Without a contract: an item may still be originated (a proposal may
exist, it may even MATERIALIZE) but ``execution_ready`` stays exactly
what it is today. WITH an explicit, valid contract, the item gains real
evidence/scope/criteria and the existing gates decide from there.

A contract can never touch ``item.blockers`` or ``item.depends_on`` --
this module has no code path that writes either field. An item a
project has already declared blocked or dependency-gated (the existing
governance/owner-gate signal) stays blocked/gated regardless of any
contract attached to it; a contract widens *evidence*, never
*authority*.

Deliberately NOT in this schema (IV finding, PR #663 review): a
declared ``dependencies``/``forbidden_paths`` field that is validated
but never actually enforced downstream is a real trap for a future
contract author who reasonably assumes the docstring means what it
says. Neither can be safely wired today without a larger change this
PR does not make: ``dependencies`` would need item-status data
(``adapter.py``'s ``eligible_roadmap_items()`` computes and then
discards it) this module's current inputs do not carry, and
``forbidden_paths`` has no existing per-item plumbing anywhere in
``proposal.py``/``materialize.py``/the governed-DAG models to receive
it (only ``governor.lease()``'s own fixed, node-independent default
exists). Both are left for a genuine follow-up once that plumbing
exists, rather than shipped half-true.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from project_atlas.orchestration.origination.adapter import (
    _ITEM_ID_RE,
    EligibleRoadmapItem,
    _safe_project_file,
)
from project_atlas.secrets import scan_text

_MARKER_NAMES: tuple[str, ...] = (".atlas-project.yaml", ".atlas/project.yaml")
_MARKER_KEY = "origination_acceptance_contracts"
_REL_PATH_MAX = 256
_MAX_SCOPE_ENTRIES = 64
#: Evidence entries are capped tighter than proposed_scope (which
#: mirrors MutationSurface.paths's own 64-entry cap exactly) because
#: each one can mint its own SourceFact, and Provenance.consulted_digests
#: caps at 16 (proposal.py) -- 15 here leaves room for the one
#: always-present authoritative-source digest without ever exceeding
#: that downstream limit, regardless of what an item's own pre-existing
#: evidence (if any) additionally contributes (apply_acceptance_
#: contracts() independently re-checks the actual merged total too).
_MAX_EVIDENCE_ENTRIES = 15
_MAX_CONTRACTS = 512


def _reject_secret_scalars(value: object, *, where: str) -> None:
    """AS-SEC-SCAN-ORIGIN-YAML-CONTRACT-001: YAML ``\\u``-decoded secrets
    must not reach ``persist_proposed``."""
    if isinstance(value, str):
        if scan_text(value):
            raise AcceptanceContractConfigError(f"secret-content in {where}")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_secret_scalars(key, where=where)
            _reject_secret_scalars(child, where=where)
        return
    if isinstance(value, list):
        for child in value:
            _reject_secret_scalars(child, where=where)


class AcceptanceContractConfigError(ValueError):
    """A declared acceptance-contract sidecar is malformed, ambiguous, or
    refers to something that does not exist.

    Fails closed, exactly like :class:`~project_atlas.orchestration.
    origination.sources.OriginationSourceConfigError`: an unreadable or
    invalid declaration never silently falls back to "no contracts" --
    the caller must see the error. Covers, by construction or by an
    explicit check: unknown item_id, duplicate contract, path traversal,
    empty scope, missing acceptance evidence, missing success criteria,
    an evidence/scope path outside the project root, evidence exceeding
    downstream provenance capacity, and a contract that refers to a
    completed or nonexistent task. (`dependencies`/`forbidden_paths` --
    and any cycle-detection over them -- were removed entirely rather
    than half-wired; see the fields' own history for why.)
    """


def _safe_relative_path(value: str, *, field: str) -> str:
    posix = value.replace("\\", "/")
    if posix.startswith("./"):
        posix = posix[2:]
    if not posix or posix.startswith("/") or ".." in posix.split("/"):
        raise ValueError(f"{field} must be a safe relative path, got {value!r}")
    return posix


class AcceptanceContract(BaseModel):
    """One explicit, human-authored acceptance contract for one backlog
    item, keyed by the compound ``(source_path, item_id)`` pair -- never
    a bare ``item_id`` alone, so a contract can never accidentally apply
    to a same-named item declared by a *different* origination source
    (mirrors ``identity.py::origination_identity()``'s own compound-key
    design)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1, max_length=128)
    source_path: str = Field(min_length=1, max_length=_REL_PATH_MAX)
    #: Real evidence file paths. Required and non-empty -- a contract
    #: with no evidence at all is not a contract. Each path is still
    #: routed through the existing, unmodified
    #: ``extract_corroborating_facts()`` at outcome-build time, which
    #: only treats a path as genuinely corroborating when its content
    #: carries a skip/xfail pytest marker -- this schema cannot and does
    #: not pre-judge that; only path *shape* and *existence-inside-root*
    #: are checked here and in :func:`apply_acceptance_contracts`.
    evidence: tuple[str, ...] = Field(min_length=1, max_length=_MAX_EVIDENCE_ENTRIES)
    #: Explicit, human-authored mutation surface. Required and
    #: non-empty -- an acceptance contract's entire purpose is to bound
    #: what may change; an unbounded contract is a contradiction in
    #: terms, not a permissive default.
    proposed_scope: tuple[str, ...] = Field(min_length=1, max_length=_MAX_SCOPE_ENTRIES)
    #: Explicit, human-authored success criteria. Required and
    #: non-empty, for the same reason.
    success_criteria: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("item_id")
    @classmethod
    def _valid_item_id(cls, value: str) -> str:
        if not _ITEM_ID_RE.fullmatch(value):
            raise ValueError(f"item_id is not a valid identifier: {value!r}")
        return value

    @field_validator("source_path")
    @classmethod
    def _valid_source_path(cls, value: str) -> str:
        return _safe_relative_path(value, field="source_path")

    @field_validator("evidence", "proposed_scope")
    @classmethod
    def _valid_path_tuple(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        field_name = info.field_name or "path"
        cleaned = tuple(_safe_relative_path(entry, field=field_name) for entry in value)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError(f"{field_name} entries must be unique")
        return cleaned

    @field_validator("success_criteria")
    @classmethod
    def _non_blank_criteria(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(entry.strip() for entry in value)
        if any(not entry for entry in cleaned):
            raise ValueError("success_criteria entries must not be blank")
        return cleaned

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_path, self.item_id)


def _find_marker(project_root: Path) -> Path | None:
    for name in _MARKER_NAMES:
        candidate = project_root / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def load_acceptance_contracts(project_root: Path) -> tuple[AcceptanceContract, ...]:
    """Return every explicitly declared acceptance contract, or an empty
    tuple when a project has not opted in at all (no marker, no marker
    key) -- the honest, common, backward-compatible case identical to
    every project before PR-D existed.

    Raises :class:`AcceptanceContractConfigError` when the key IS
    present but malformed, unreadable, refers to a path outside the
    project root, contains a duplicate ``(source_path, item_id)``
    contract, or an unmatched item_id (see :func:`apply_acceptance_
    contracts`) -- an explicit declaration that cannot be understood
    must fail closed, not silently resolve to "no contracts".
    """
    marker = _find_marker(project_root)
    if marker is None:
        return ()
    try:
        raw = yaml.safe_load(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AcceptanceContractConfigError(f"unreadable project marker: {marker}") from exc
    if raw is None:
        # An entirely empty marker file -- genuinely no configuration at
        # all, matching load_origination_sources()'s own identical case.
        return ()
    if not isinstance(raw, dict):
        # IV finding (PR #663 review): this used to silently return ()
        # for the SAME malformed-marker shape (a scalar/list from a
        # templating error or truncated file) that
        # load_origination_sources() correctly raises for -- despite
        # this function's own docstring already claiming that parity.
        # Fail closed instead, exactly like the sibling function.
        raise AcceptanceContractConfigError(
            f"project marker must parse to a mapping, got {type(raw).__name__}: {marker}"
        )
    if _MARKER_KEY not in raw:
        return ()
    declared_path = raw[_MARKER_KEY]
    if not isinstance(declared_path, str) or not declared_path.strip():
        raise AcceptanceContractConfigError(
            f"{_MARKER_KEY} must be a non-empty string path in {marker}"
        )
    resolved = _safe_project_file(project_root, declared_path.strip())
    if resolved is None:
        raise AcceptanceContractConfigError(
            f"{_MARKER_KEY} path is unsafe or does not resolve to a real file "
            f"inside the project root: {declared_path!r}"
        )
    _, contracts_path = resolved
    try:
        contracts_raw = yaml.safe_load(contracts_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AcceptanceContractConfigError(
            f"unreadable acceptance-contracts file: {contracts_path}"
        ) from exc
    if not isinstance(contracts_raw, dict):
        raise AcceptanceContractConfigError(
            f"acceptance-contracts file must parse to a mapping, "
            f"got {type(contracts_raw).__name__}: {contracts_path}"
        )
    _reject_secret_scalars(contracts_raw, where=str(contracts_path))
    entries = contracts_raw.get("contracts")
    if not isinstance(entries, list) or not entries:
        raise AcceptanceContractConfigError(
            f"acceptance-contracts file must declare a non-empty 'contracts' list: {contracts_path}"
        )
    if len(entries) > _MAX_CONTRACTS:
        raise AcceptanceContractConfigError(
            f"acceptance-contracts file declares more than {_MAX_CONTRACTS} contracts: "
            f"{contracts_path}"
        )
    contracts: list[AcceptanceContract] = []
    seen: dict[tuple[str, str], int] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AcceptanceContractConfigError(
                f"contracts[{index}] must be a mapping in {contracts_path}"
            )
        try:
            contract = AcceptanceContract.model_validate(entry)
        except Exception as exc:  # pydantic ValidationError, kept generic at this boundary
            raise AcceptanceContractConfigError(
                f"contracts[{index}] is invalid in {contracts_path}: {exc}"
            ) from exc
        # Every evidence entry must resolve to a real file inside
        # project_root -- the schema layer above only checks path
        # *shape* (no traversal segments, not absolute); this is the
        # existence-and-containment check, exactly like
        # tasklist_adapter.py's own use of the same helper for the same
        # reason. proposed_scope is deliberately NOT checked for
        # existence here: a mutation surface names paths that may not
        # exist yet (the whole point of scope is to bound what may be
        # *created*) -- its shape is already guaranteed traversal-free
        # by the schema-layer validator above.
        for ref in contract.evidence:
            if _safe_project_file(project_root, ref) is None:
                raise AcceptanceContractConfigError(
                    f"contracts[{index}].evidence entry does not resolve to a "
                    f"real file inside the project root: {ref!r} in {contracts_path}"
                )
        key = contract.key
        if key in seen:
            raise AcceptanceContractConfigError(
                f"contracts[{index}] duplicates the contract for {contract.item_id!r} at "
                f"{contract.source_path!r} already declared at contracts[{seen[key]}] "
                f"in {contracts_path}"
            )
        seen[key] = index
        contracts.append(contract)
    return tuple(contracts)


def apply_acceptance_contracts(
    items: tuple[EligibleRoadmapItem, ...],
    contracts: tuple[AcceptanceContract, ...],
) -> tuple[EligibleRoadmapItem, ...]:
    """Merge each contract's ``evidence`` / ``proposed_scope`` /
    ``success_criteria`` onto the ``EligibleRoadmapItem`` it names.

    Fails closed with :class:`AcceptanceContractConfigError` when a
    contract's ``(source_path, item_id)`` does not match any item in
    ``items`` at all -- covering both "unknown item_id" and "contract
    refers to a completed/nonexistent task" (a completed item is never
    in ``items``, since ``eligible_roadmap_items``/``eligible_task_list_
    items`` only return NOT_STARTED/IN_PROGRESS + READY items in the
    first place).

    Never touches ``blockers`` or ``depends_on`` on any item -- a
    contract can widen evidence/scope, never clear an existing
    owner-gate or dependency edge the adapter already computed.

    Fails closed with :class:`AcceptanceContractConfigError` (rather
    than letting a raw ``pydantic.ValidationError`` escape from deep
    inside ``pipeline.py::_build_outcome()``) if the merged evidence set
    -- an item's own pre-existing evidence plus a matched contract's --
    would exceed what ``Provenance.consulted_digests`` (16 entries,
    ``proposal.py``) can hold once the one always-present authoritative-
    source digest is counted too (IV finding, PR #663 review: the
    schema-level ``_MAX_EVIDENCE_ENTRIES`` cap alone does not account
    for an item that already carried its own evidence before a contract
    was ever applied).
    """
    by_key = {(item.source_path, item.item_id): item for item in items}
    for contract in contracts:
        if contract.key not in by_key:
            raise AcceptanceContractConfigError(
                f"acceptance contract for {contract.item_id!r} at {contract.source_path!r} "
                "does not match any currently eligible item -- it may not exist, may "
                "already be completed, or the declared source_path may be wrong"
            )
    contract_by_key = {contract.key: contract for contract in contracts}
    merged: list[EligibleRoadmapItem] = []
    for item in items:
        matched = contract_by_key.get((item.source_path, item.item_id))
        if matched is None:
            merged.append(item)
            continue
        merged_evidence = tuple(dict.fromkeys((*item.evidence, *matched.evidence)))
        if len(merged_evidence) > _MAX_EVIDENCE_ENTRIES:
            raise AcceptanceContractConfigError(
                f"contract for {matched.item_id!r} at {matched.source_path!r} would "
                f"merge to {len(merged_evidence)} evidence entries, exceeding the "
                f"{_MAX_EVIDENCE_ENTRIES}-entry limit downstream provenance tracking "
                "can hold -- reduce the contract's own evidence list"
            )
        merged.append(
            replace(
                item,
                evidence=merged_evidence,
                contract_proposed_scope=matched.proposed_scope,
                contract_success_criteria=matched.success_criteria,
            )
        )
    return tuple(merged)


__all__ = [
    "AcceptanceContract",
    "AcceptanceContractConfigError",
    "apply_acceptance_contracts",
    "load_acceptance_contracts",
]
