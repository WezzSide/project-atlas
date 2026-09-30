"""D-147R — exact-main closure integrity (live main vs certification target)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

PACKAGE_ID: Final[str] = "AS-D147R-EXACT-MAIN-CLOSURE-001"

_RUNBOOK_REL = Path("docs/productization/CLEAN-MACHINE-PREP-RUNBOOK.md")
_RUNBOOK_HEAD_RE = re.compile(r'\$TARGET_HEAD\s*=\s*"([0-9a-f]{40})"')
_RUNBOOK_TREE_LINE_RE = re.compile(r"`TREE`\s*=\s*`([0-9a-f]{40})`")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_POST_CERT_METADATA_PREFIXES: Final[tuple[str, ...]] = (
    "docs/",
    "tests/",
    "src/project_atlas/orchestration/",
)


class GitObjectPin(BaseModel):
    """HEAD paired with the tree object git resolves for that commit."""

    model_config = ConfigDict(extra="forbid")

    head: str = Field(min_length=40, max_length=40)
    tree: str = Field(min_length=40, max_length=40)


class ClosureIntegrity(BaseModel):
    """Live integrated main vs immutable certification target."""

    model_config = ConfigDict(extra="forbid")

    package_id: str = PACKAGE_ID
    live_main_head: str
    live_main_tree: str
    certification_target_head: str
    certification_target_tree: str
    certification_target_is_ancestor_of_live_main: bool
    live_head_tree_coherent: bool
    cert_target_head_tree_coherent: bool
    operational_pin_head: str | None = None
    operational_pin_tree: str | None = None
    operational_pins_match_cert_target: bool = False
    live_main_advanced_past_cert_target: bool = False
    post_cert_delta_metadata_only: bool = True
    live_matches_integrated_main: bool = True
    semantic_model: str = "IMMUTABLE_CERTIFICATION_TARGET"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def git_object_pin(repo: Path, rev: str) -> GitObjectPin:
    head = _git(repo, "rev-parse", rev)
    tree = _git(repo, "rev-parse", f"{head}^{{tree}}")
    return GitObjectPin(head=head, tree=tree)


def validate_head_tree_coherence(pin: GitObjectPin, repo: Path) -> bool:
    """git rev-parse HEAD^{tree} must equal declared TREE."""
    try:
        actual = _git(repo, "rev-parse", f"{pin.head}^{{tree}}")
    except subprocess.CalledProcessError:
        return False
    return actual == pin.tree


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
    )
    return proc.returncode == 0


def delta_paths(repo: Path, ancestor: str, descendant: str) -> list[str] | None:
    if ancestor == descendant:
        return []
    proc = subprocess.run(
        ["git", "diff", "--name-only", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def is_metadata_only_post_cert_delta(repo: Path, ancestor: str, descendant: str) -> bool:
    """Post-cert main advance must touch orchestration/docs/tests surfaces only."""
    if ancestor == descendant:
        return True
    if not is_ancestor(repo, ancestor, descendant):
        return False
    paths = delta_paths(repo, ancestor, descendant)
    if paths is None:
        return False
    for path in paths:
        if not any(path.startswith(prefix) for prefix in _POST_CERT_METADATA_PREFIXES):
            return False
    return True


def is_integrated_main_head(repo: Path, head: str) -> bool:
    """Live tip must match origin/main when that ref is available."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/remotes/origin/main"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return True
    return head == proc.stdout.strip()


def read_operational_pins(repo: Path) -> tuple[str | None, str | None]:
    runbook = repo / _RUNBOOK_REL
    if not runbook.is_file():
        return None, None
    text = runbook.read_text(encoding="utf-8")
    head_m = _RUNBOOK_HEAD_RE.search(text)
    tree_m = _RUNBOOK_TREE_LINE_RE.search(text)
    return (
        head_m.group(1) if head_m else None,
        tree_m.group(1) if tree_m else None,
    )


def inspect_closure_integrity(
    repo: Path,
    *,
    certification_target_head: str,
    certification_target_tree: str | None = None,
) -> ClosureIntegrity:
    live = git_object_pin(repo, "HEAD")
    cert_tree = certification_target_tree or _git(
        repo, "rev-parse", f"{certification_target_head}^{{tree}}"
    )
    cert_pin = GitObjectPin(head=certification_target_head, tree=cert_tree)
    pin_head, pin_tree = read_operational_pins(repo)
    cert_ancestor = is_ancestor(repo, certification_target_head, live.head)
    pins_match = pin_head == certification_target_head and pin_tree == cert_tree
    advanced = live.head != certification_target_head and cert_ancestor
    metadata_only = is_metadata_only_post_cert_delta(repo, certification_target_head, live.head)
    integrated_main = is_integrated_main_head(repo, live.head)
    return ClosureIntegrity(
        live_main_head=live.head,
        live_main_tree=live.tree,
        certification_target_head=cert_pin.head,
        certification_target_tree=cert_pin.tree,
        certification_target_is_ancestor_of_live_main=cert_ancestor,
        live_head_tree_coherent=validate_head_tree_coherence(live, repo),
        cert_target_head_tree_coherent=validate_head_tree_coherence(cert_pin, repo),
        operational_pin_head=pin_head,
        operational_pin_tree=pin_tree,
        operational_pins_match_cert_target=pins_match,
        live_main_advanced_past_cert_target=advanced,
        post_cert_delta_metadata_only=metadata_only,
        live_matches_integrated_main=integrated_main,
        semantic_model="IMMUTABLE_CERTIFICATION_TARGET",
    )


def closure_integrity_pass(integrity: ClosureIntegrity) -> bool:
    base = all(
        [
            integrity.live_head_tree_coherent,
            integrity.cert_target_head_tree_coherent,
            integrity.certification_target_is_ancestor_of_live_main,
            integrity.operational_pins_match_cert_target,
            integrity.live_matches_integrated_main,
        ]
    )
    if not base:
        return False
    if integrity.live_main_advanced_past_cert_target:
        return integrity.post_cert_delta_metadata_only
    return True


def _live_main_classification(integrity: ClosureIntegrity) -> str:
    if integrity.live_main_head == integrity.certification_target_head:
        return "AT_TARGET"
    if not integrity.certification_target_is_ancestor_of_live_main:
        return "FAIL_NOT_ANCESTOR"
    if not integrity.live_matches_integrated_main:
        return "FAIL_NOT_INTEGRATED_MAIN"
    if integrity.post_cert_delta_metadata_only:
        return "PASS"
    return "FAIL_UNCERTIFIED_DELTA"


def closure_integrity_report(integrity: ClosureIntegrity) -> dict[str, Any]:
    ok = closure_integrity_pass(integrity)
    return {
        "package_id": PACKAGE_ID,
        "closure_integrity_pass": ok,
        "HEAD_TREE_COHERENCE": "PASS"
        if (integrity.live_head_tree_coherent and integrity.cert_target_head_tree_coherent)
        else "FAIL",
        "LIVE_MAIN_CLASSIFICATION": _live_main_classification(integrity),
        "CERTIFICATION_PIN_SEMANTICS": (
            "PASS" if integrity.operational_pins_match_cert_target else "FAIL"
        ),
        "semantic_model": integrity.semantic_model,
        "live_main": integrity.live_main_head,
        "live_main_tree": integrity.live_main_tree,
        "certification_target_head": integrity.certification_target_head,
        "certification_target_tree": integrity.certification_target_tree,
        "certification_target_is_ancestor_of_live_main": (
            integrity.certification_target_is_ancestor_of_live_main
        ),
        "post_cert_delta_metadata_only": integrity.post_cert_delta_metadata_only,
        "live_matches_integrated_main": integrity.live_matches_integrated_main,
        "merge_authorized": False,
    }


def reject_mixed_head_tree_packet(head: str, tree: str, repo: Path) -> bool:
    """True when packet must be rejected (incoherent HEAD/TREE pair)."""
    if not _SHA40_RE.fullmatch(head) or not _SHA40_RE.fullmatch(tree):
        return True
    pin = GitObjectPin(head=head, tree=tree)
    return not validate_head_tree_coherence(pin, repo)


def cert_evidence_applies_to_head(
    evidence: dict[str, Any],
    main_head: str,
    repo: Path,
) -> bool:
    """Certification evidence applies to head or metadata-only post-cert descendant."""
    if not evidence:
        return False
    pins = [
        str(evidence.get("MERGE_COMMIT") or ""),
        str(evidence.get("RELEASE_MAIN_SHA") or ""),
        str(evidence.get("POST_MERGE_MAIN") or ""),
        str(evidence.get("INITIAL_MAIN") or ""),
        str(evidence.get("CERTIFICATION_TARGET_HEAD") or ""),
    ]
    if main_head in pins:
        return True
    return any(
        len(pin) == 40
        and is_ancestor(repo, pin, main_head)
        and is_metadata_only_post_cert_delta(repo, pin, main_head)
        for pin in pins
    )
