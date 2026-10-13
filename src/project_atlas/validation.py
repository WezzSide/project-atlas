"""Strict structural and provenance validation for the Core slice.

AS-VAL-001 (H-006 / H-007): additive freshness and orphan checks. Freshness
uses objective timestamps only (ADR-005 pattern); orphan detection is
report-only by default (VAL-INV-002). Corrupt freshness metadata fails
closed — never silently normalized to fresh/stale.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from project_atlas.authority_registry import persisted_authority_binding_matches_live
from project_atlas.domain import Severity, ValidationFinding, ValidationGate
from project_atlas.domain.authority_semantics import AuthoritativeStateRecord
from project_atlas.domain.temporal import CurrentStateRecord
from project_atlas.portfolio import (
    DEFAULT_STALE_DAYS,
    build_portfolio_payloads,
    is_untrusted_mtime,
)
from project_atlas.schema import SchemaValidationError, validate_record
from project_atlas.source_identity import canonical_source_sha256

LINK = re.compile(r"\]\(([^)]+)\)")
# A fenced code block (``_quote_source_text``'s multi-line "source-excerpt"
# shape: a fence line, verbatim content, a matching closing fence) or an
# inline code span (one or more backticks, content, a closing run of
# backticks -- CommonMark's actual rules, via the \1 backreference).
# Content inside either renders as inert literal text, never a live link,
# even when it contains a `](...)`-shaped substring.
#
# Fence indentation is capped at 0-3 spaces (CommonMark: a fence indented
# 4+ spaces is not a fence at all -- it is an indented code block, a
# separate construct this helper does not attempt to recognize). Tabs in
# the leading whitespace are deliberately excluded rather than expanded to
# CommonMark's tab-stop columns: Atlas's own generated content never emits
# tab-indented fences, and getting tab-stop math wrong risks the opposite,
# more dangerous failure mode (masking real links) more than it risks
# missing a hypothetical hand-authored tab-indented fence.
#
# The closing fence must be *at least* as long as the opening one (``\1``
# followed by zero or more further fence characters), not merely "any run
# of 3+": ``_quote_source_text`` widens the fence beyond the longest
# backtick run already present in the quoted content specifically so a
# shorter accidental run inside that content (e.g. someone's excerpt
# quoting another 3-backtick fence) can never be mistaken for the real
# close. This "at least as long" rule is fenced blocks' own CommonMark
# semantics, distinct from inline code spans below.
#
# CommonMark permits two independent fence characters, backtick and tilde,
# and a fence of one character is never closed by the other -- so this is
# two homogeneous patterns, not one class matching either character (which
# would wrongly accept a mixed-character run like "`~`" as a fence, and
# would let a backtick fence be closed by tildes or vice versa).
#
# Backtick fences further forbid backticks in the info string (CommonMark:
# otherwise a line-starting inline code span would be taken as a fence).
# The opener therefore uses ``[^`\n]*``, not ``[^\n]*``.
#
# The two patterns are applied in document order -- leftmost opener wins
# -- rather than as successive whole-document passes. A later pass that
# does not see the other delimiter's closer would let an inner unclosed
# opener fall through to ``\Z`` and blank every live link after the outer
# fence. Tilde fences exist so authors can quote backtick fences,
# including an opener that never closes.
#
# The closing-fence alternative's trailing ``\r?`` tolerates CRLF line
# endings: Python's ``re.M`` ``$`` anchors immediately before a bare
# ``\n``, not before a ``\r`` that precedes it, so on CRLF text the
# un-consumed ``\r`` made the closing line fail to match at all, falling
# through to the ``\Z`` alternative and masking (over-masking) the rest of
# the document -- including any real links after the fence. Independently
# found via an adversarial CRLF regression test, not part of the verifier's
# three reported findings.
_FENCED_CODE_BLOCK = re.compile(
    r"^ {0,3}(`{3,})[^`\n]*\n.*?(?:^ {0,3}\1`*[ \t]*\r?$|\Z)", re.M | re.S
)
_FENCED_CODE_BLOCK_TILDE = re.compile(
    r"^ {0,3}(~{3,})[^\n]*\n.*?(?:^ {0,3}\1~*[ \t]*\r?$|\Z)", re.M | re.S
)
# Inline code spans require the closing backtick run to be *exactly* as
# long as the opening one (CommonMark: "a code span begins with a backtick
# string and ends with a backtick string of equal length" -- unlike fenced
# blocks' "at least as long"). Two structural requirements this single
# backreference alone does not give:
#
# 1. ``(?!`)`` immediately after ``\1``: without it, ``\1`` is satisfied by
#    matching a *prefix* of a longer closing run (e.g. two open backticks
#    are "closed" by the first two of three), silently absorbing the
#    extra backtick into the span and suppressing a real link that follows
#    it. Independently reproduced: ``` ``[live](missing.md)``` ``` (2
#    open, 3 close) wrongly masked the link before this fix.
# 2. ``(?>(`+))`` atomic grouping around the opening capture: a plain
#    greedy ``(`+)`` backtracks to a *shorter* opening run when the
#    maximal one has no valid same-length close later in the text (e.g.
#    3 open, only 2 close anywhere) -- re-trying with 2 backticks
#    captured and 1 left over as ordinary content, which can then find a
#    same-length close that should never have counted, since the actual
#    3-backtick run opened no valid span at all. Atomic grouping commits
#    to the maximal run once found, matching CommonMark's actual
#    algorithm (try the longest backtick string once; no match anywhere
#    means no code span, full stop -- never retry shorter).
# 3. ``(?<!`)`` before the opening group: atomicity alone only stops
#    backtracking *within one match attempt at a fixed start position*.
#    ``re.sub`` still retries at the *next* character position when a
#    match fails there -- which, for "```[live](missing.md)``" (3 open,
#    only 2 close anywhere), let a retry starting one character in treat
#    the last two of those three opening backticks as a fresh 2-backtick
#    opener, closed by the real 2-backtick run at the end -- a match
#    CommonMark's own algorithm never considers, because "```" is one
#    indivisible backtick-string token, not "1 stray backtick + a
#    2-backtick string". The lookbehind refuses to *start* a match
#    immediately after another backtick, so a match can only ever begin
#    at the first character of a maximal run; independently confirmed
#    this closes that case without the atomic group above.
_INLINE_CODE_SPAN = re.compile(r"(?<!`)(?>(`+))(?:(?!\1)[\s\S])*?\1(?!`)")

# AS-H-010 process exit codes for ``atlas validate`` (argparse usage remains 2).
VALIDATION_EXIT_OK = 0
VALIDATION_EXIT_ERROR = 1

# Vault-relative entry points that seed orphan reachability (H-007).
_ORPHAN_SEED_PATHS = (
    "index.md",
    "projects/index.md",
    "sources/index.md",
    "01-portfolio/index.md",
)
_ORPHAN_LAYER_ROOTS = frozenset({"projects", "01-portfolio"})
_ORPHAN_EXCLUDED_ROOTS = frozenset(
    {"sources", "00-system", "templates", "state", "review", "receipts", "generated"}
)


def _mask_inert_markdown_regions(text: str) -> str:
    """Replace fenced code blocks and inline code spans with equal-length
    blank runs (never removing bytes -- character offsets used elsewhere in
    ``text`` stay valid) so the link checker never mistakes a `](...)`-
    shaped substring inside quoted, verbatim source content for a real
    navigable Markdown link.

    ``knowledge_compiler._quote_source_text`` deliberately renders untrusted
    claim text inside a code span or fenced block specifically so embedded
    Markdown (headings, links, directives) is inert, not live -- a single-
    line claim value containing a same-directory relative link that was
    valid at its *source* location (e.g. "Complements
    [OPENAI-MCP-DESIGN.md](OPENAI-MCP-DESIGN.md).") is correctly rendered
    as literal, non-navigable text once quoted; the previous unconditional
    regex scan did not know that and reported it as broken. Content outside
    any code span/fence is unaffected and still checked exactly as before.
    """
    if "`" not in text and "~" not in text:
        return text
    masked = _mask_fenced_code_blocks(text)
    return _INLINE_CODE_SPAN.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), masked)


def _earliest_fence(text: str, pos: int) -> re.Match[str] | None:
    """Return the leftmost backtick or tilde fence starting at or after ``pos``."""
    backtick = _FENCED_CODE_BLOCK.search(text, pos)
    tilde = _FENCED_CODE_BLOCK_TILDE.search(text, pos)
    if backtick is None:
        return tilde
    if tilde is None:
        return backtick
    return backtick if backtick.start() <= tilde.start() else tilde


def _mask_fenced_code_blocks(text: str) -> str:
    """Mask fenced blocks in document order so an inner unclosed opener
    cannot ``\\Z``-swallow past an earlier outer fence of the other type.
    """
    pieces: list[str] = []
    pos = 0
    while pos < len(text):
        match = _earliest_fence(text, pos)
        if match is None:
            pieces.append(text[pos:])
            break
        pieces.append(text[pos : match.start()])
        pieces.append(re.sub(r"[^\n]", " ", match.group(0)))
        pos = match.end()
    return "".join(pieces)


def validate(
    vault: Path,
    *,
    reference_now: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_DAYS,
) -> dict[str, Any]:
    """Validate vault structure, provenance, freshness (H-006), and orphans (H-007).

    ``reference_now`` is injected once by the caller (CLI or tests). Wall-clock
    values never appear in deterministic finding payloads (NFR-001 / VAL-FR-002).
    """
    errors: list[str] = []
    findings: list[dict[str, Any]] = []
    now = reference_now if reference_now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    for required in ("index.md", "projects/index.md", "sources/index.md", "01-portfolio/index.md"):
        if not (vault / required).is_file():
            errors.append(f"missing required generated file: {required}")
    for markdown in sorted(vault.rglob("*.md")):
        if ".tmp" in markdown.parts:
            continue
        relative = markdown.relative_to(vault)
        if relative.parts[0] == "sources":
            # Layer A imported evidence is raw, immutable source content: its
            # links are relative to the source repository, not the Vault, so
            # link resolution applies to generated layers only (AS-EXT-001A).
            continue
        text = markdown.read_text(encoding="utf-8")
        for target in LINK.findall(_mask_inert_markdown_regions(text)):
            if target.startswith(("http://", "https://", "#")):
                continue
            candidate = (markdown.parent / target.split("#", 1)[0]).resolve()
            try:
                candidate.relative_to(vault.resolve())
            except ValueError:
                errors.append(f"link escapes vault: {markdown.relative_to(vault)} -> {target}")
            else:
                if not candidate.is_file():
                    errors.append(f"broken link: {markdown.relative_to(vault)} -> {target}")
        if markdown.match("projects/*/concepts.md"):
            _validate_okf_concept_note(vault, markdown, errors)
    _validate_knowledge_state(vault, errors)
    _validate_portfolio(vault, errors)
    _validate_graph_acceptance(vault, errors)
    _validate_graph_resolution(vault, errors)
    _validate_freshness(
        vault,
        errors,
        findings,
        reference_now=now,
        stale_after_days=stale_after_days,
    )
    _validate_orphans(vault, errors, findings)
    findings.sort(key=lambda item: (item["rule_id"], item["finding_id"], item.get("path") or ""))
    return {
        "ok": not errors,
        "errors": errors,
        "findings": findings,
        "markdown_files": len(list(vault.rglob("*.md"))),
    }


def validation_exit_code(result: Mapping[str, Any]) -> int:
    """AS-H-010: map validation result severities to process exit codes.

    Normative matrix (H010-FR-001..007):

    - any ``Severity.ERROR`` finding → ``1``
    - legacy ``errors`` non-empty or ``ok is False`` → ``1`` (fail-closed)
    - ``WARNING`` / ``INFO`` findings alone → ``0`` (explicit non-failing policy)
    - clean result → ``0``

    Deterministic: identical payloads yield identical exit codes (NFR-001).
    Argparse usage exit ``2`` is owned by the CLI layer, not this helper.
    """
    if result.get("ok") is False:
        return VALIDATION_EXIT_ERROR
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return VALIDATION_EXIT_ERROR
    findings = result.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            severity = finding.get("severity")
            if severity == Severity.ERROR or severity == Severity.ERROR.value:
                return VALIDATION_EXIT_ERROR
    return VALIDATION_EXIT_OK


def _validate_graph_acceptance(vault: Path, errors: list[str]) -> None:
    """Optional AS-GRAPH-001 checks when acceptance receipts exist (legacy no-op)."""
    root = vault / "generated" / "graph" / "acceptance"
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("receipt must be an object")
            validate_record(payload, "graph-acceptance-receipt")
            authority = payload.get("authority", {})
            if not isinstance(authority, dict) or authority.get("level") != "derived":
                errors.append(
                    f"graph acceptance authority must be derived: {path.relative_to(vault)}"
                )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            SchemaValidationError,
        ) as exc:
            errors.append(f"invalid graph acceptance receipt {path.relative_to(vault)}: {exc}")


def _validate_graph_resolution(vault: Path, errors: list[str]) -> None:
    """Optional AS-GRAPH-002 checks when derived resolution outputs exist."""
    roots = (
        vault / "generated" / "graph" / "resolved",
        vault / "generated" / "graph" / "quarantine-candidates",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            # Explanations live under resolved/<project>/explanations/.
            record_kind = (
                "graph-identity-explanation"
                if "explanations" in path.parts
                else "graph-resolved-node"
            )
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("record must be an object")
                validate_record(payload, record_kind)
                if record_kind == "graph-resolved-node":
                    authority = payload.get("authority", {})
                    if not isinstance(authority, dict) or authority.get("level") != "derived":
                        errors.append(
                            "graph resolution authority must be derived: "
                            f"{path.relative_to(vault)}"
                        )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ValueError,
                SchemaValidationError,
            ) as exc:
                errors.append(
                    f"invalid graph resolution record {path.relative_to(vault)}: {exc}"
                )


def _validate_okf_concept_note(vault: Path, path: Path, errors: list[str]) -> None:
    """Validate OKF frontmatter resources and the concept record contract."""
    relative = path.relative_to(vault)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        errors.append(f"OKF concept note lacks frontmatter: {relative}")
        return
    try:
        _, raw_frontmatter, _ = text.split("---", 2)
        frontmatter = yaml.safe_load(raw_frontmatter)
        if not isinstance(frontmatter, dict):
            raise ValueError("frontmatter must be an object")
        validate_record(frontmatter, "concept-record")
    except (ValueError, yaml.YAMLError, SchemaValidationError) as exc:
        errors.append(f"invalid OKF concept note {relative}: {exc}")
        return
    resource = frontmatter.get("resource")
    if isinstance(resource, str):
        _validate_vault_resource(vault, resource, errors, relative)
    for source in frontmatter.get("sources", []):
        if isinstance(source, dict):
            _validate_vault_resource(vault, str(source.get("resource", "")), errors, relative)


def _validate_vault_resource(
    vault: Path, resource: str, errors: list[str], owner: Path
) -> None:
    candidate = (vault / resource).resolve()
    try:
        candidate.relative_to(vault.resolve())
    except ValueError:
        errors.append(f"OKF resource escapes Vault: {owner} -> {resource}")
    else:
        if not candidate.is_file():
            errors.append(f"OKF resource target missing: {owner} -> {resource}")


def _validate_knowledge_state(vault: Path, errors: list[str]) -> None:
    """Validate AS-CORE-003 machine state and provenance confinement."""
    sources: dict[str, str | None] = {}
    sources_by_lineage: dict[str, str | None] = {}
    source_state = vault / "state" / "sources.json"
    if source_state.is_file():
        try:
            raw = json.loads(source_state.read_text(encoding="utf-8"))
            source_items = [item for item in raw.get("sources", []) if isinstance(item, dict)]
            for item in source_items:
                if item.get("source_lineage_id"):
                    sources_by_lineage[str(item["source_lineage_id"])] = item.get(
                        "current_content_sha256", item.get("sha256")
                    )
                if item.get("source_id"):
                    source_id = str(item["source_id"])
                    if source_id in sources and sources[source_id] != item.get("sha256"):
                        sources[source_id] = None
                    else:
                        sources[source_id] = item.get("sha256")
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            errors.append(f"invalid source state for knowledge validation: {exc}")
    for path in _json_files(vault, "state", "claims"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for claim in raw.get("claims", []):
                validate_record(claim, "claim")
                for ref in claim.get("provenance", []):
                    _validate_provenance(vault, ref, sources, sources_by_lineage, errors, path)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            SchemaValidationError,
            AttributeError,
            TypeError,
        ) as exc:
            errors.append(f"invalid claim state {path.relative_to(vault)}: {exc}")
    for path in _json_files(vault, "state", "concepts"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for concept in raw.get("concepts", []):
                validate_record(concept, "concept-record")
                for ref in concept.get("sources", []):
                    _validate_provenance(vault, ref, sources, sources_by_lineage, errors, path)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            SchemaValidationError,
            AttributeError,
            TypeError,
        ) as exc:
            errors.append(f"invalid concept state {path.relative_to(vault)}: {exc}")
    for path in _json_files(vault, "state", "authority"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for authority in raw.get("authorities", []):
                validate_record(authority, "authority-record")
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            SchemaValidationError,
            AttributeError,
            TypeError,
        ) as exc:
            errors.append(f"invalid authority state {path.relative_to(vault)}: {exc}")
    for path in _json_files(vault, "review", "conflicts"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for conflict in raw.get("entries", []):
                validate_record(conflict, "conflict-record")
                for ref in conflict.get("provenance", []):
                    _validate_provenance(vault, ref, sources, sources_by_lineage, errors, path)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            SchemaValidationError,
            AttributeError,
            TypeError,
        ) as exc:
            errors.append(f"invalid conflict state {path.relative_to(vault)}: {exc}")
    for path in _json_files(vault, "review", "pending"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for review in raw.get("entries", []):
                validate_record(review, "review-entry")
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            SchemaValidationError,
            AttributeError,
            TypeError,
        ) as exc:
            errors.append(f"invalid review state {path.relative_to(vault)}: {exc}")
    for path in _json_files(vault, "state", "claim-lifecycle"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for lifecycle in raw.get("claims", []):
                validate_record(lifecycle, "claim-lifecycle")
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            SchemaValidationError,
            AttributeError,
            TypeError,
        ) as exc:
            errors.append(f"invalid lifecycle state {path.relative_to(vault)}: {exc}")
    # AS-CORE-007-FR-011: additive checks when 005/006 state files exist.
    # Missing files on legacy vaults are not errors.
    _validate_current_and_authoritative_state(vault, errors)
    _validate_indexes(vault, errors)
    _validate_injection_findings(vault, errors)


def _validate_current_and_authoritative_state(vault: Path, errors: list[str]) -> None:
    """AS-CORE-007-FR-011: validate 005/006 state files when present (additive)."""
    for path in _json_files(vault, "state", "current-state"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                errors.append(f"invalid current-state root {path.relative_to(vault)}")
                continue
            for item in raw.get("current_states", []):
                CurrentStateRecord.model_validate(item)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            AttributeError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(f"invalid current-state {path.relative_to(vault)}: {exc}")
    for path in _json_files(vault, "state", "authoritative-state"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                errors.append(f"invalid authoritative-state root {path.relative_to(vault)}")
                continue
            file_reg = raw.get("authority_registry_version")
            if file_reg is not None and not persisted_authority_binding_matches_live(
                recorded_registry_version=file_reg
            ):
                errors.append(
                    f"authoritative-state registry version does not match live "
                    f"owner-certified registry {path.relative_to(vault)}"
                )
            for item in raw.get("authoritative_states", []):
                auth_record = AuthoritativeStateRecord.model_validate(item)
                if not persisted_authority_binding_matches_live(
                    recorded_trust_root=auth_record.trust_root,
                    recorded_registry_version=auth_record.registry_version,
                ):
                    errors.append(
                        f"authoritative-state trust binding does not match live "
                        f"owner-certified registry {path.relative_to(vault)}"
                    )
                    continue
                if any(
                    not persisted_authority_binding_matches_live(
                        recorded_trust_root=evidence.trust_root,
                        recorded_registry_version=evidence.registry_version,
                    )
                    for evidence in auth_record.evidence
                ):
                    errors.append(
                        f"authoritative-state evidence trust binding does not "
                        f"match live owner-certified registry {path.relative_to(vault)}"
                    )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            AttributeError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(f"invalid authoritative-state {path.relative_to(vault)}: {exc}")


def _validate_injection_findings(vault: Path, errors: list[str]) -> None:
    """Check AS-SEC-001 quarantine report integrity."""
    path = vault / "generated" / "reports" / "injection-findings.json"
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("invalid injection-findings schema_version")
        findings = raw.get("findings")
        if not isinstance(findings, list):
            raise ValueError("findings must be a list")
        quarantined_ids: set[str] = set()
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError("finding must be an object")
            for key in ("source_id", "path", "rule", "confidence", "disposition"):
                if key not in finding:
                    raise ValueError(f"missing injection finding field: {key}")
            if finding["disposition"] != "quarantined":
                raise ValueError(
                    f"injection finding disposition must be 'quarantined': {finding['source_id']}"
                )
            if "text" in finding:
                raise ValueError(
                    f"injection finding must not carry matched text: {finding['source_id']}"
                )
            quarantined_ids.add(str(finding["source_id"]))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid injection findings report: {exc}")
        return
    # Verify no quarantined source was projected into trusted Layer B/C state.
    for path in _json_files(vault, "state", "claims"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for claim in raw.get("claims", []):
            for ref in claim.get("provenance", []):
                if str(ref.get("source_id", "")) in quarantined_ids:
                    errors.append(
                        f"quarantined source {ref.get('source_id')} appears in claims: "
                        f"{path.relative_to(vault)}"
                    )
    for path in _json_files(vault, "state", "concepts"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for concept in raw.get("concepts", []):
            for ref in concept.get("sources", []):
                if str(ref.get("source_id", "")) in quarantined_ids:
                    errors.append(
                        f"quarantined source {ref.get('source_id')} appears in concepts: "
                        f"{path.relative_to(vault)}"
                    )


def _validate_portfolio(vault: Path, errors: list[str]) -> None:
    """Reject drift between generated/portfolio/*.json and canonical state
    (AS-MVP-001), mirroring the build-indexes drift-rejection convention."""
    portfolio_root = vault / "generated" / "portfolio"
    if not portfolio_root.is_dir():
        return
    stale_path = portfolio_root / "stale-knowledge.json"
    reference_date = datetime.now(UTC)
    if stale_path.is_file():
        try:
            raw = json.loads(stale_path.read_text(encoding="utf-8"))
            candidate = raw.get("reference_date")
            if isinstance(candidate, str):
                reference_date = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            errors.append(
                "invalid generated/portfolio/stale-knowledge.json: unparsable reference_date"
            )
            return
    try:
        expected = build_portfolio_payloads(vault, reference_date=reference_date)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"unable to recompute portfolio for drift check: {exc}")
        return
    quarantined_ids = set()
    injection_path = vault / "generated" / "reports" / "injection-findings.json"
    if injection_path.is_file():
        raw = _json_or_default(injection_path)
        quarantined_ids = {
            str(finding.get("source_id"))
            for finding in raw.get("findings", [])
            if isinstance(finding, dict)
        }
    for name, expected_payload in sorted(expected.items()):
        path = portfolio_root / name
        if not path.is_file():
            errors.append(f"missing generated portfolio output: generated/portfolio/{name}")
            continue
        try:
            actual_payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"invalid generated portfolio output generated/portfolio/{name}: {exc}")
            continue
        if actual_payload != expected_payload:
            errors.append(f"portfolio drift detected: generated/portfolio/{name}")
        _validate_no_quarantined_leakage(name, actual_payload, quarantined_ids, errors)


def _validate_no_quarantined_leakage(
    name: str, payload: Any, quarantined_ids: set[str], errors: list[str]
) -> None:
    """AS-SEC-001 boundary: no quarantined source_id may appear as a cited
    provenance reference inside any portfolio output (safe aggregate counts
    are permitted; individual references to quarantined sources are not)."""
    if not quarantined_ids:
        return
    serialized = json.dumps(payload)
    for source_id in quarantined_ids:
        if f'"{source_id}"' in serialized:
            errors.append(
                f"quarantined source {source_id} referenced in generated/portfolio/{name}"
            )


def _json_or_default(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _json_files(vault: Path, *parts: str) -> list[Path]:
    directory = vault.joinpath(*parts)
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def _validate_indexes(vault: Path, errors: list[str]) -> None:
    """Check generated lexical indexes against canonical machine state."""
    legacy_root = vault / "indexes"
    if legacy_root.exists():
        errors.append(
            "obsolete generated index directory: indexes; remove it and rebuild "
            "under generated/indexes"
        )
    index_root = vault / "generated" / "indexes"
    if not index_root.is_dir():
        return
    state_ids: dict[str, set[str]] = {
        "claims": set(),
        "concepts": set(),
        "conflicts": set(),
        "authority": set(),
        "sources": set(),
    }
    for path in _json_files(vault, "state", "claims"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        state_ids["claims"].update(
            str(item["claim_id"])
            for item in raw.get("claims", [])
            if isinstance(item, dict) and item.get("claim_id")
        )
    for path in _json_files(vault, "state", "concepts"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        state_ids["concepts"].update(
            str(item["concept_id"])
            for item in raw.get("concepts", [])
            if isinstance(item, dict) and item.get("concept_id")
        )
    for path in _json_files(vault, "review", "conflicts"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        state_ids["conflicts"].update(
            str(item["conflict_id"])
            for item in raw.get("entries", [])
            if isinstance(item, dict) and item.get("conflict_id")
        )
    for path in _json_files(vault, "state", "authority"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        state_ids["authority"].update(
            str(item["authority_id"])
            for item in raw.get("authorities", [])
            if isinstance(item, dict) and item.get("authority_id")
        )
    source_path = vault / "state" / "sources.json"
    if source_path.is_file():
        raw = json.loads(source_path.read_text(encoding="utf-8"))
        state_ids["sources"].update(
            str(item["source_lineage_id"])
            for item in raw.get("sources", [])
            if isinstance(item, dict) and item.get("source_lineage_id")
        )
    for kind in ("claims", "concepts", "conflicts", "authority", "sources"):
        path = index_root / f"{kind}.json"
        if not path.is_file():
            errors.append(f"missing generated lexical index: generated/indexes/{kind}.json")
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            indexed = raw.get("ids", [])
            if sorted(indexed) != sorted(state_ids[kind]):
                errors.append(f"index/state mismatch: generated/indexes/{kind}.json")
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            errors.append(f"invalid generated lexical index generated/indexes/{kind}.json: {exc}")
    provenance_path = index_root / "provenance.json"
    if not provenance_path.is_file():
        errors.append("missing generated lexical index: generated/indexes/provenance.json")
    else:
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            for key in ("by_source_lineage_id", "by_receipt_id"):
                if not isinstance(provenance.get(key), dict):
                    errors.append(
                        "invalid generated lexical index "
                        f"generated/indexes/provenance.json: {key}"
                    )
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            errors.append(
                "invalid generated lexical index "
                f"generated/indexes/provenance.json: {exc}"
            )


def _validate_provenance(
    vault: Path,
    reference: dict[str, Any],
    sources: dict[str, str | None],
    sources_by_lineage: dict[str, str | None],
    errors: list[str],
    owner: Path,
) -> None:
    resource = str(reference.get("resource", ""))
    candidate = (vault / resource).resolve()
    try:
        candidate.relative_to(vault.resolve())
    except ValueError:
        errors.append(f"provenance escapes Vault: {owner.relative_to(vault)} -> {resource}")
        return
    if not candidate.is_file():
        errors.append(f"provenance target missing: {owner.relative_to(vault)} -> {resource}")
        return
    source_id = str(reference.get("source_id", ""))
    source_lineage_id = reference.get("source_lineage_id")
    expected = (
        sources_by_lineage.get(str(source_lineage_id))
        if source_lineage_id
        else sources.get(source_id)
    )
    actual = _sha256(candidate)
    if expected and actual != expected:
        errors.append(f"provenance hash mismatch: {owner.relative_to(vault)} -> {source_id}")
    supplied = reference.get("sha256")
    # Agent-event provenance uses the package aggregate hash, while the
    # resource is the human-readable event projection. Package integrity is
    # verified at ingestion; it is not the byte hash of this projection.
    if supplied and not reference.get("receipt_id") and actual != supplied:
        errors.append(f"claim hash mismatch: {owner.relative_to(vault)} -> {source_id}")


def _sha256(path: Path) -> str:
    """Return the same canonical source hash used during discovery."""
    return canonical_source_sha256(path)


def _finding(
    *,
    finding_id: str,
    rule_id: str,
    severity: Severity,
    gate: ValidationGate,
    message: str,
    path: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic ValidationFinding payload (metadata only)."""
    return ValidationFinding(
        finding_id=finding_id,
        rule_id=rule_id,
        severity=severity,
        gate=gate,
        message=message,
        path=path,
    ).model_dump(mode="json")


def _stable_finding_id(*parts: str) -> str:
    """Build an ID_PATTERN-safe deterministic finding id."""
    cleaned: list[str] = []
    for part in parts:
        token = re.sub(r"[^A-Za-z0-9._-]+", ".", part).strip(".-_")
        cleaned.append(token or "x")
    return ".".join(cleaned)


def _parse_freshness_timestamp(
    value: Any, *, reference: datetime | None = None
) -> tuple[datetime | None, str | None]:
    """Parse objective freshness timestamps.

    Returns ``(datetime, None)`` on success, ``(None, "missing")`` when absent,
    ``(None, "untrusted")`` for Unix-epoch / pre-1980 metadata,
    ``(None, "untrusted_future")`` for metadata dated a full day or more after
    ``reference``, or ``(None, "corrupt")`` when present but unparseable.
    Untrusted and corrupt values are never coerced to fresh/stale
    (fail-closed; no silent normalization, and no clamping to ``reference``).
    """
    if value is None or value == "":
        return None, "missing"
    if not isinstance(value, str):
        return None, "corrupt"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "corrupt"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if is_untrusted_mtime(parsed):
        return None, "untrusted"
    if reference is not None and is_untrusted_mtime(parsed, reference=reference):
        return None, "untrusted_future"
    return parsed, None


def _manifest_sources(vault: Path) -> list[dict[str, Any]]:
    path = vault / "sources" / "manifests" / "source-manifest.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    return [item for item in raw.get("sources", []) if isinstance(item, dict)]


def _portfolio_freshness_by_source(vault: Path) -> dict[str, str] | None:
    """Return on-disk portfolio freshness labels keyed by source_id, or None."""
    path = vault / "generated" / "portfolio" / "stale-knowledge.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    labels: dict[str, str] = {}
    projects = raw.get("projects", {})
    if not isinstance(projects, dict):
        return None
    for project_payload in projects.values():
        if not isinstance(project_payload, dict):
            continue
        for item in project_payload.get("sources", []):
            if not isinstance(item, dict):
                continue
            source_id = item.get("source_id")
            freshness = item.get("freshness")
            if isinstance(source_id, str) and isinstance(freshness, str):
                labels[source_id] = freshness
    return labels


def _freshness_quarantined_source_ids(vault: Path) -> set[str]:
    """AS-SEC-001 source ids that must not be individually cited in stale-knowledge.

    Secret-findings.json is a top-level array; injection-findings.json wraps
    ``{"findings": [...]}``. Both shapes are accepted.
    """
    ids: set[str] = set()
    secret_path = vault / "generated" / "reports" / "secret-findings.json"
    injection_path = vault / "generated" / "reports" / "injection-findings.json"
    for path in (secret_path, injection_path):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        rows: list[Any]
        if isinstance(raw, dict):
            findings = raw.get("findings", [])
            rows = findings if isinstance(findings, list) else []
        elif isinstance(raw, list):
            rows = raw
        else:
            continue
        for finding in rows:
            if not isinstance(finding, dict):
                continue
            source_id = finding.get("source_id")
            if isinstance(source_id, str) and source_id:
                ids.add(source_id)
    return ids


def _validate_freshness(
    vault: Path,
    errors: list[str],
    findings: list[dict[str, Any]],
    *,
    reference_now: datetime,
    stale_after_days: int,
) -> None:
    """H-006: objective freshness checks (ADR-005 pattern).

    Fail-closed on unknown/corrupt timestamps and on portfolio laundering
    (marked fresh while objectively stale). Honestly reported stale findings
    are emitted as warnings so existing portfolio-aware vaults stay consistent
    without silent skip of the freshness gate.
    """
    if stale_after_days < 1:
        errors.append("freshness threshold must be a positive integer day count")
        return
    sources = _manifest_sources(vault)
    if not sources:
        return
    portfolio_labels = _portfolio_freshness_by_source(vault)
    quarantined_ids = _freshness_quarantined_source_ids(vault)
    for entry in sorted(sources, key=lambda item: str(item.get("source_id", ""))):
        source_id = entry.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            continue
        if source_id in quarantined_ids:
            # AS-SEC-001: quarantined sources are excluded from stale-knowledge
            # individual citation. Do not demand they appear there (H-006-silent).
            continue
        rel_path = str(entry.get("path", ""))
        # Never echo raw secret-bearing content; path/id metadata only (NFR-004).
        modified_raw = entry.get("modified_at")
        modified_at, status = _parse_freshness_timestamp(
            modified_raw, reference=reference_now
        )
        if status in ("untrusted", "untrusted_future"):
            reason = (
                "modified_at dated a full day or more after the evaluation "
                "instant is unverifiable metadata, not age"
                if status == "untrusted_future"
                else "epoch/pre-1980 modified_at is missing metadata, not age"
            )
            finding = _finding(
                finding_id=_stable_finding_id("H-006-untrusted", source_id),
                rule_id="H-006-untrusted",
                severity=Severity.WARNING,
                gate=ValidationGate.FRESHNESS,
                message=f"freshness untrusted for source {source_id}: {reason}",
                path=rel_path or None,
            )
            findings.append(finding)
            if portfolio_labels is not None and portfolio_labels.get(source_id) == "fresh":
                # A "fresh" label surviving in the on-disk portfolio for a
                # source now known untrusted is the same laundering defect as
                # the stale case below: the report keeps asserting freshness
                # evidence has withdrawn (e.g. built under a since-corrected
                # future clock). Reuse H-006-launder rather than let a WARNING
                # be the only signal that a "fresh" claim is no longer valid.
                launder = _finding(
                    finding_id=_stable_finding_id("H-006-launder", source_id),
                    rule_id="H-006-launder",
                    severity=Severity.ERROR,
                    gate=ValidationGate.FRESHNESS,
                    message=(
                        f"freshness laundering: source {source_id} marked fresh "
                        f"in portfolio but objectively {status}"
                    ),
                    path=rel_path or None,
                )
                findings.append(launder)
                errors.append(launder["message"])
            continue
        if status == "corrupt":
            finding = _finding(
                finding_id=_stable_finding_id("H-006-corrupt", source_id),
                rule_id="H-006-corrupt",
                severity=Severity.ERROR,
                gate=ValidationGate.FRESHNESS,
                message=(
                    "corrupt modified_at for source "
                    f"{source_id}: refuse silent normalization"
                ),
                path=rel_path or None,
            )
            findings.append(finding)
            errors.append(finding["message"])
            continue
        if status == "missing" or modified_at is None:
            finding = _finding(
                finding_id=_stable_finding_id("H-006-unknown", source_id),
                rule_id="H-006-unknown",
                severity=Severity.ERROR,
                gate=ValidationGate.FRESHNESS,
                message=(
                    f"freshness unknown for source {source_id}: "
                    "missing modified_at (never assumed fresh/stale)"
                ),
                path=rel_path or None,
            )
            findings.append(finding)
            errors.append(finding["message"])
            continue
        age_days = (reference_now - modified_at).days
        freshness = "stale" if age_days >= stale_after_days else "fresh"
        if freshness == "fresh":
            continue
        finding = _finding(
            finding_id=_stable_finding_id("H-006-stale", source_id),
            rule_id="H-006-stale",
            severity=Severity.WARNING,
            gate=ValidationGate.FRESHNESS,
            message=(
                f"source {source_id} is stale under threshold "
                f"{stale_after_days}d (objective age_days={age_days})"
            ),
            path=rel_path or None,
        )
        findings.append(finding)
        if portfolio_labels is not None:
            reported = portfolio_labels.get(source_id)
            if reported == "fresh":
                launder = _finding(
                    finding_id=_stable_finding_id("H-006-launder", source_id),
                    rule_id="H-006-launder",
                    severity=Severity.ERROR,
                    gate=ValidationGate.FRESHNESS,
                    message=(
                        f"freshness laundering: source {source_id} marked fresh "
                        "in portfolio but objectively stale"
                    ),
                    path=rel_path or None,
                )
                findings.append(launder)
                errors.append(launder["message"])
            elif reported is None:
                silent = _finding(
                    finding_id=_stable_finding_id("H-006-silent", source_id),
                    rule_id="H-006-silent",
                    severity=Severity.ERROR,
                    gate=ValidationGate.FRESHNESS,
                    message=(
                        f"stale source {source_id} missing from portfolio "
                        "stale-knowledge report"
                    ),
                    path=rel_path or None,
                )
                findings.append(silent)
                errors.append(silent["message"])


def _posix_rel(path: Path, vault: Path) -> str:
    return path.relative_to(vault).as_posix()


def _resolve_md_link(owner: Path, target: str, vault: Path) -> Path | None:
    if target.startswith(("http://", "https://", "#")):
        return None
    bare = target.split("#", 1)[0]
    if not bare:
        return None
    candidate = (owner.parent / bare).resolve()
    try:
        candidate.relative_to(vault.resolve())
    except ValueError:
        return None
    if candidate.is_file() and candidate.suffix.lower() == ".md":
        return candidate
    return None


def _orphan_candidates(vault: Path) -> list[Path]:
    candidates: list[Path] = []
    for root_name in sorted(_ORPHAN_LAYER_ROOTS):
        root = vault / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if ".tmp" in path.parts:
                continue
            rel = _posix_rel(path, vault)
            if rel in _ORPHAN_SEED_PATHS:
                continue
            # Index hubs under layer roots are navigation seeds, not orphans.
            if path.name == "index.md":
                continue
            candidates.append(path)
    return candidates


def _collect_reachable_notes(vault: Path) -> set[str]:
    """BFS from indexes + generated navigation; fail closed on vault escape."""
    vault_resolved = vault.resolve()
    reachable: set[str] = set()
    queue: list[Path] = []
    for rel in _ORPHAN_SEED_PATHS:
        seed = vault / rel
        if seed.is_file():
            queue.append(seed)
            reachable.add(rel)
    nav_root = vault / "generated" / "navigation"
    if nav_root.is_dir():
        for path in sorted(nav_root.rglob("*.md")):
            queue.append(path)
            reachable.add(_posix_rel(path, vault))
    seen: set[str] = set(reachable)
    while queue:
        current = queue.pop(0)
        try:
            text = current.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for target in LINK.findall(_mask_inert_markdown_regions(text)):
            resolved = _resolve_md_link(current, target, vault)
            if resolved is None:
                # Escaping targets are already reported by the link validator.
                continue
            try:
                resolved.relative_to(vault_resolved)
            except ValueError:
                continue
            rel = _posix_rel(resolved, vault)
            if rel in seen:
                continue
            top = Path(rel).parts[0] if rel else ""
            # Layer A / system / templates are not Layer B/C orphan targets;
            # following links into generated/navigation remains allowed.
            if top in _ORPHAN_EXCLUDED_ROOTS and top != "generated":
                continue
            seen.add(rel)
            reachable.add(rel)
            queue.append(resolved)
    return reachable


def _bundle_members_of_reachable_projects(vault: Path, reachable: set[str]) -> set[str]:
    """Project-bundle members of a reachable project.md are not orphans.

    Prevents false orphans on protected/generated siblings (concepts.md,
    claims.md, …) that are part of a reachable project hub (ADV: false orphan
    on protected human regions / bundle mates).
    """
    members: set[str] = set()
    for rel in reachable:
        parts = Path(rel).parts
        if len(parts) >= 3 and parts[0] == "projects" and parts[-1] == "project.md":
            project_dir = vault / "projects" / parts[1]
            if not project_dir.is_dir():
                continue
            for path in sorted(project_dir.glob("*.md")):
                members.add(_posix_rel(path, vault))
    return members


def _index_member_paths(vault: Path) -> set[str]:
    """Paths referenced from lexical concept indexes (membership, not delete)."""
    members: set[str] = set()
    for path in _json_files(vault, "state", "concepts"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        for concept in raw.get("concepts", []):
            if not isinstance(concept, dict):
                continue
            resource = concept.get("resource")
            if isinstance(resource, str) and resource.endswith(".md"):
                members.add(resource.replace("\\", "/"))
    return members


def _validate_orphans(
    vault: Path,
    errors: list[str],
    findings: list[dict[str, Any]],
) -> None:
    """H-007: detect unreachable Layer B/C notes (report-only; VAL-INV-002)."""
    candidates = _orphan_candidates(vault)
    if not candidates:
        return
    reachable = _collect_reachable_notes(vault)
    bundle_members = _bundle_members_of_reachable_projects(vault, reachable)
    index_members = _index_member_paths(vault)
    covered = reachable | bundle_members | index_members
    for path in candidates:
        rel = _posix_rel(path, vault)
        # Path safety: every candidate must remain inside the vault root.
        try:
            path.resolve().relative_to(vault.resolve())
        except ValueError:
            message = f"orphan scan path escapes vault: {rel}"
            finding = _finding(
                finding_id=_stable_finding_id("H-007-escape", rel),
                rule_id="H-007-escape",
                severity=Severity.ERROR,
                gate=ValidationGate.STRUCTURAL,
                message=message,
                path=rel,
            )
            findings.append(finding)
            errors.append(message)
            continue
        if rel in covered:
            continue
        finding = _finding(
            finding_id=_stable_finding_id("H-007-orphan", rel),
            rule_id="H-007-orphan",
            severity=Severity.WARNING,
            gate=ValidationGate.STRUCTURAL,
            message=f"orphan note (no inbound link / index membership): {rel}",
            path=rel,
        )
        findings.append(finding)
        # Report-only: do not append to errors (VAL-INV-002).

