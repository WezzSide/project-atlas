"""ATLAS-GLOBALIZE-AUTONOMY-2026-10-02: the global operating foundation is canonical,
stable, single-homed, resolvable and inherited by the agent entry points."""

from __future__ import annotations

import hashlib
import re
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
    for path in ROOT.rglob("*.md"):
        if any(part in {".git", "node_modules", ".venv"} for part in path.parts):
            continue
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
