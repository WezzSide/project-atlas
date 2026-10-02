"""ATLAS-GLOBALIZE-AUTONOMY-2026-10-02: the global operating foundation is canonical,
stable, single-homed, resolvable and inherited by the agent entry points."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GLOBAL = ROOT / "docs" / "global"

CANONICAL = {
    "ATLAS_GLOBAL_AUTONOMOUS_OPERATING_CONTRACT": "ATLAS-GLOBAL-OPERATING-CONTRACT.md",
    "ATLAS_GLOBAL_STYLE_MISSION": "ATLAS-GLOBAL-STYLE-MISSION.md",
    "ATLAS_GLOBAL_GOALS": "ATLAS-GLOBAL-GOALS.md",
    "ATLAS_DIRECTIVE_TEMPLATE": "ATLAS-DIRECTIVE-TEMPLATE.md",
}

REQUIRED = (
    "README.md",
    *CANONICAL.values(),
    "baseline/2026-10-02-G1-G15-BASELINE.md",
    "baseline/2026-10-02-AUTONOMY-FRONTIER.md",
    "baseline/2026-10-02-STYLE-AUDIT.md",
    "baseline/2026-10-02-G1-G15-BASELINE-CORRECTION-1.md",
    "baseline/2026-10-02-AUTHORITY-COMPATIBILITY.md",
    "baseline/2026-10-02-OPERATIONAL-SLICE-AND-F2-BRIEF.md",
    "baseline/evidence/devq-0002-e1-claude-sdk-diagnostic.json",
)

PRINCIPLES = (
    "OC-A — OUTCOME_OVER_PROCEDURE",
    "OC-B — REVERSIBLE_IN_SCOPE_AUTONOMY",
    "OC-C — CONTINUOUS_USEFUL_PROGRESS",
    "OC-D — SELF_REMEDIATION",
    "OC-E — CURRENT_TRUTH_FIRST",
    "OC-F — FAIL_CLOSED",
    "OC-G — EXACT_EVIDENCE",
    "OC-H — IMPLEMENTATION_IS_NOT_CERTIFICATION",
    "OC-I — SAFE_CONCURRENCY",
    "OC-J — PRODUCTION_AND_PROVENANCE_PRESERVATION",
    "OC-K — MINIMIZE_OWNER_RELAY",
)

SEPARATED_DIMENSIONS = (
    "connectivity",
    "freshness",
    "liveness",
    "health",
    "execution",
    "implementation_success",
    "verification",
    "certification",
    "authorization",
)

TEMPLATE_SECTIONS = (
    "INHERITS:",
    "OUTCOME:",
    "SCOPE:",
    "INVARIANTS:",
    "AUTHORITY DELTA:",
    "SUCCESS:",
    "OPERATING EXPECTATION:",
)

GOAL_IDS = tuple(f"ATLAS-GOAL-G{n:02d}" for n in range(1, 16))

ENTRY_POINTS = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs/product/CODER-ALPHA-NORTH-STAR.md",
    "apps/web/README.md",
)

_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_FORBIDDEN = (
    "D:\\",
    "/mnt/d",
    "/home/",
    "ATLAS_VPS_FLEET_AUTONOMOUS = YES",
    "MERGE_AUTHORIZATION = GRANTED",
    "RELEASE_CERTIFIED = YES",
)


def _tracked_markdown() -> list[Path]:
    """Tracked *.md files; falls back to a pruned walk when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.md"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", "node_modules", ".venv"}
        return [p for p in ROOT.rglob("*.md") if not skip.intersection(p.parts)]
    return [ROOT / name for name in out.decode("utf-8").split("\0") if name]


def _docs() -> list[Path]:
    return sorted(GLOBAL.rglob("*.md"))


def _read(name: str) -> str:
    return (GLOBAL / name).read_text(encoding="utf-8")


def test_required_global_documents_exist() -> None:
    missing = [name for name in REQUIRED if not (GLOBAL / name).is_file()]
    assert missing == []


def test_each_canonical_id_is_declared_in_its_own_document() -> None:
    for canonical_id, name in CANONICAL.items():
        assert f"| Canonical ID | `{canonical_id}` |" in _read(name), name


def test_canonical_ids_are_not_redefined_elsewhere() -> None:
    pattern = re.compile(r"\|\s*Canonical ID\s*\|\s*`(ATLAS_[A-Z_]+)`")
    owners: dict[str, list[str]] = {}
    for path in _tracked_markdown():
        for match in pattern.finditer(path.read_text(encoding="utf-8", errors="replace")):
            owners.setdefault(match.group(1), []).append(path.relative_to(ROOT).as_posix())
    for canonical_id, name in CANONICAL.items():
        assert owners.get(canonical_id) == [f"docs/global/{name}"], canonical_id


def test_operating_contract_principles_and_priority_are_stable() -> None:
    text = _read("ATLAS-GLOBAL-OPERATING-CONTRACT.md")
    for principle in PRINCIPLES:
        assert text.count(f"### {principle}") == 1, principle
    assert (
        "correctness / trust  >  safety  >  evidence  >  completion  >  autonomy  >  speed" in text
    )
    assert "autonomy/policy.md" in text and "wins" in text
    assert "never grants authority" in text


def test_contract_authority_is_owner_issued_and_bypass_is_never_grantable() -> None:
    text = _read("ATLAS-GLOBAL-OPERATING-CONTRACT.md")
    assert "**Owner approval.**" in text and "**Verifiable issuance.**" in text
    assert "held off every agent host" in text and "pinned owner signer list" in text
    assert "An owner-account merge is *not* verifiable issuance" in text
    assert "An agent never issues an `AUTHORITY DELTA` to itself." in text
    assert "never overrides a platform or\nclassifier denial" in text
    assert "**Not grantable by any directive.**" in text
    non_grantable = text.split("**Not grantable by any directive.**", 1)[1]
    for item in ("permission classifier", "reading secret values", "`autonomy/policy.md`"):
        assert item in non_grantable, item
    assert "The non-grantable list overrides any delta, including an owner-issued one." in text
    assert "OC-A never applies to authority, trust or governance procedures" in text
    assert "raising the kill switch is always permitted" in text
    assert "establishes no\nstanding or continuous autonomy" in text
    template = _read("ATLAS-DIRECTIVE-TEMPLATE.md")
    assert "issued_by: owner @" in template
    assert "An agent never issues a delta to itself." in template


def test_loop_rules_keep_loop_scope_and_open_decisions_stay_open() -> None:
    text = _read("ATLAS-GLOBAL-OPERATING-CONTRACT.md")
    assert "Loop-specific mechanisms keep their loop scope." in text
    assert "Inside the loop, no iteration starts without a §5 grant" in text
    for scope in ("**RSI loop:** REDESIGN", "**DEVQ lineage:**", "**All other work:**"):
        assert scope in text, scope
    note = _read("baseline/2026-10-02-AUTHORITY-COMPATIBILITY.md")
    for decision in ("| **D1** |", "| **D2** |", "| **D3** |", "| **D4** |", "| **D5** |"):
        assert decision in note, decision
    brief = _read("baseline/2026-10-02-OPERATIONAL-SLICE-AND-F2-BRIEF.md")
    assert "This brief does not choose." in brief
    assert "**Never** fabricate or rewrite `workflow_conclusion`" in brief


def test_style_mission_keeps_dimensions_separate() -> None:
    text = _read("ATLAS-GLOBAL-STYLE-MISSION.md")
    for section in ("SM-TRUTH", "SM-SEPARATION", "SM-PROVENANCE", "SM-SYSTEM"):
        assert section in text, section
    for dimension in SEPARATED_DIMENSIONS:
        assert f"| `{dimension}` |" in text, dimension
    for state in ("`UNKNOWN` stays `UNKNOWN`", "`STALE` stays `STALE`", "`BLOCKED` stays"):
        assert state in text, state


def test_global_goals_are_exactly_g01_to_g15_and_baselined() -> None:
    goals = _read("ATLAS-GLOBAL-GOALS.md")
    rows = re.findall(r"^\| `(ATLAS-GOAL-G\d{2})` \|", goals, re.M)
    assert tuple(rows) == GOAL_IDS
    baseline = _read("baseline/2026-10-02-G1-G15-BASELINE.md")
    for n in range(1, 16):
        assert re.search(rf"^\| G{n:02d} ", baseline, re.M), f"G{n:02d} missing from baseline"
    counts = re.findall(r"^\| `(PROVEN|PARTIAL|NOT_PROVEN|UNKNOWN)` \| (\d+) \|", baseline, re.M)
    assert sum(int(count) for _, count in counts) == 15


def test_directive_template_sections() -> None:
    text = _read("ATLAS-DIRECTIVE-TEMPLATE.md")
    for section in TEMPLATE_SECTIONS:
        assert section in text, section
    for canonical_id in list(CANONICAL)[:3]:
        assert canonical_id in text, canonical_id


def test_relative_links_resolve() -> None:
    broken: list[str] = []
    for path in _docs():
        for target in _LINK.findall(path.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            if not (path.parent / target).exists():
                broken.append(f"{path.relative_to(ROOT).as_posix()} -> {target}")
    assert broken == []


def test_entry_points_inherit_the_foundation() -> None:
    missing = [
        name
        for name in ENTRY_POINTS
        if "docs/global/" not in (ROOT / name).read_text(encoding="utf-8")
    ]
    assert missing == []


def test_no_host_paths_contacts_or_authority_stamps() -> None:
    for path in _docs():
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN:
            assert token not in text, f"{path.name}: {token!r}"
        assert not _EMAIL.search(text), f"{path.name}: email-shaped string"


def test_preserved_devq_0002_e1_evidence_matches_recorded_digest() -> None:
    data = (GLOBAL / "baseline/evidence/devq-0002-e1-claude-sdk-diagnostic.json").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert digest == "980969989717458cd7167d438665d7a617e1023a4939f75fa5290c00c8b91b69"
    assert digest in _read("baseline/2026-10-02-AUTONOMY-FRONTIER.md")
