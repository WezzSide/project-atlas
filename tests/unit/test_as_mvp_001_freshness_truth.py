"""AS-MVP-001-FRESHNESS-TRUTH-001 — epoch mtimes are unknown, not stale."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from project_atlas.portfolio import (
    _FUTURE_MTIME_TOLERANCE,
    DEFAULT_STALE_DAYS,
    is_untrusted_mtime,
    stale_knowledge,
)

REFERENCE = datetime(2026, 8, 1, tzinfo=UTC)


def _write_manifest(vault: Path, sources: list[dict[str, object]]) -> None:
    path = vault / "sources" / "manifests" / "source-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "sources": sources}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_unix_epoch_is_untrusted_mtime() -> None:
    assert is_untrusted_mtime(datetime(1970, 1, 1, tzinfo=UTC))
    assert not is_untrusted_mtime(datetime(2024, 1, 1, tzinfo=UTC))


def test_epoch_mtime_is_not_reported_stale(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-epoch",
                "path": "docs/copied.md",
                "likely_project": "nebula",
                "modified_at": "1970-01-01T00:00:00+00:00",
            },
            {
                "source_id": "src-fresh",
                "path": "docs/current.md",
                "likely_project": "nebula",
                "modified_at": "2026-07-01T00:00:00+00:00",
            },
        ],
    )
    report = stale_knowledge(vault, reference_date=REFERENCE, stale_after_days=180)
    nebula = report["projects"].get("nebula", {"stale_count": 0, "sources": []})
    assert nebula["stale_count"] == 0
    ids = {item["source_id"] for item in nebula["sources"]}
    assert "src-epoch" not in ids
    assert "src-fresh" in ids


def test_threshold_boundary_is_exact(tmp_path: Path) -> None:
    """``stale_after_days`` is an inclusive boundary: a source exactly that
    many days old is stale, one day younger is fresh. Pinned against an
    injected reference date, so the boundary cannot be silently widened by
    a later tolerance change or drift into a different answer over time.
    """
    vault = tmp_path / "vault"
    threshold = DEFAULT_STALE_DAYS
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-at-threshold",
                "path": "docs/at.md",
                "likely_project": "nebula",
                "modified_at": (REFERENCE - timedelta(days=threshold)).isoformat(),
            },
            {
                "source_id": "src-just-under",
                "path": "docs/under.md",
                "likely_project": "nebula",
                "modified_at": (REFERENCE - timedelta(days=threshold - 1)).isoformat(),
            },
        ],
    )
    report = stale_knowledge(vault, reference_date=REFERENCE, stale_after_days=threshold)
    freshness = {
        item["source_id"]: item["freshness"] for item in report["projects"]["nebula"]["sources"]
    }
    assert freshness == {"src-at-threshold": "stale", "src-just-under": "fresh"}
    assert report["projects"]["nebula"]["stale_count"] == 1


@pytest.mark.parametrize("bad_threshold", [0, -1, -5, -180])
def test_stale_after_days_rejects_non_positive(tmp_path: Path, bad_threshold: int) -> None:
    """P3: stale_knowledge() previously accepted ``stale_after_days <= 0``
    silently, while validation.py::_validate_freshness already rejected
    ``< 1`` at the CLI boundary -- the same conceptual parameter enforced
    two different postures. ``0`` is not a meaningful threshold (it would
    mark any source with any age at or past the reference instant "stale"
    regardless of how recently it was modified), so the internal API now
    fails closed here too, matching the CLI-facing invariant.

    Pinned directly against the values the contract names, not derived
    from the guard's own ``< 1`` expression: the intended invariant is
    ``stale_after_days`` must be a positive (>= 1) integer day count, and
    every non-positive value -- zero and negative alike -- is rejected the
    same way."""
    vault = tmp_path / "vault"
    _write_manifest(
        vault,
        [_source("src-recent", REFERENCE - timedelta(hours=1))],
    )
    with pytest.raises(ValueError, match="positive integer day count"):
        stale_knowledge(vault, reference_date=REFERENCE, stale_after_days=bad_threshold)


def test_stale_after_days_one_is_the_minimum_accepted_value(tmp_path: Path) -> None:
    """P3 boundary, the other side: ``1`` is the smallest value the contract
    accepts and must behave as an ordinary threshold, not raise -- pinning
    the exact boundary between "rejected" (0) and "accepted" (1) rather than
    inferring it from the guard's ``< 1`` condition. A source exactly one
    day old is stale under this threshold; one still within the day is
    fresh, matching the same inclusive-boundary contract
    ``test_threshold_boundary_is_exact`` pins for the default threshold."""
    vault = tmp_path / "vault"
    _write_manifest(
        vault,
        [
            _source("src-one-day-old", REFERENCE - timedelta(days=1)),
            _source("src-hours-old", REFERENCE - timedelta(hours=12)),
        ],
    )
    report = stale_knowledge(vault, reference_date=REFERENCE, stale_after_days=1)
    freshness = {
        item["source_id"]: item["freshness"] for item in report["projects"]["nebula"]["sources"]
    }
    assert freshness == {"src-one-day-old": "stale", "src-hours-old": "fresh"}


def test_offset_timestamps_compare_as_instants(tmp_path: Path) -> None:
    """A non-UTC offset timestamp is compared as the instant it denotes, so
    the same moment expressed in two timezones yields the same freshness."""
    vault = tmp_path / "vault"
    instant = REFERENCE - timedelta(days=DEFAULT_STALE_DAYS + 5)
    _write_manifest(
        vault,
        [
            {
                "source_id": "src-utc",
                "path": "docs/utc.md",
                "likely_project": "nebula",
                "modified_at": instant.isoformat(),
            },
            {
                "source_id": "src-offset",
                "path": "docs/offset.md",
                "likely_project": "nebula",
                "modified_at": instant.astimezone(timezone(timedelta(hours=9))).isoformat(),
            },
        ],
    )
    report = stale_knowledge(vault, reference_date=REFERENCE, stale_after_days=DEFAULT_STALE_DAYS)
    freshness = {
        item["source_id"]: item["freshness"] for item in report["projects"]["nebula"]["sources"]
    }
    assert freshness == {"src-utc": "stale", "src-offset": "stale"}


def _freshness(vault: Path, sources: list[dict[str, object]]) -> dict[str, str]:
    _write_manifest(vault, sources)
    report = stale_knowledge(vault, reference_date=REFERENCE, stale_after_days=DEFAULT_STALE_DAYS)
    project = report["projects"].get("nebula", {"sources": []})
    return {item["source_id"]: item["freshness"] for item in project["sources"]}


def _source(source_id: str, moment: datetime) -> dict[str, object]:
    return {
        "source_id": source_id,
        "path": f"docs/{source_id}.md",
        "likely_project": "nebula",
        "modified_at": moment.isoformat(),
    }


def test_future_timestamp_is_untrusted_not_fresh(tmp_path: Path) -> None:
    """AS-MVP FUTURE-TIMESTAMP TRUST: a stamp a full day or more after the
    reference instant is unverifiable metadata. It must not be reported fresh
    -- and must not fall through to "stale" either, which would be an equally
    wrong claim about a document nobody can date."""
    freshness = _freshness(
        tmp_path / "vault",
        [
            _source("src-future-day", REFERENCE + _FUTURE_MTIME_TOLERANCE),
            _source("src-future-far", REFERENCE + timedelta(days=400)),
            _source("src-future-decade", REFERENCE + timedelta(days=3650)),
        ],
    )
    assert freshness == {}, "future-dated sources are unknown, so they are not cited"


def test_future_tolerance_boundary(tmp_path: Path) -> None:
    """Sub-day drift between the stamping machine and the evaluating machine
    stays inside normal evaluation; the tolerance boundary itself is the first
    untrusted instant."""
    inside = REFERENCE + _FUTURE_MTIME_TOLERANCE - timedelta(seconds=1)
    at_threshold = REFERENCE + _FUTURE_MTIME_TOLERANCE
    freshness = _freshness(
        tmp_path / "vault",
        [
            _source("src-one-second-future", REFERENCE + timedelta(seconds=1)),
            _source("src-just-inside", inside),
            _source("src-at-threshold", at_threshold),
        ],
    )
    assert freshness == {
        "src-one-second-future": "fresh",
        "src-just-inside": "fresh",
    }
    assert "src-at-threshold" not in freshness


def test_timestamp_equal_to_reference_is_fresh(tmp_path: Path) -> None:
    """The reference instant itself is age zero, not a future stamp."""
    assert _freshness(tmp_path / "vault", [_source("src-now", REFERENCE)]) == {"src-now": "fresh"}


def test_normal_fresh_and_stale_are_unchanged(tmp_path: Path) -> None:
    """The trust bound must not disturb ordinary past-dated evaluation."""
    freshness = _freshness(
        tmp_path / "vault",
        [
            _source("src-recent", REFERENCE - timedelta(days=3)),
            _source("src-old", REFERENCE - timedelta(days=DEFAULT_STALE_DAYS + 10)),
        ],
    )
    assert freshness == {"src-recent": "fresh", "src-old": "stale"}


def test_future_offset_timestamp_is_not_a_false_positive(tmp_path: Path) -> None:
    """The same instant written with a different UTC offset is the same
    instant: representation must never make a source look future-dated."""
    recent = REFERENCE - timedelta(hours=1)
    freshness = _freshness(
        tmp_path / "vault",
        [
            _source("src-utc", recent),
            _source("src-plus-14", recent.astimezone(timezone(timedelta(hours=14)))),
            _source("src-minus-11", recent.astimezone(timezone(timedelta(hours=-11)))),
        ],
    )
    assert freshness == {
        "src-utc": "fresh",
        "src-plus-14": "fresh",
        "src-minus-11": "fresh",
    }


def test_is_untrusted_mtime_bounds() -> None:
    """The predicate keeps its lower bound with no reference supplied, and
    gains the upper bound only when an evaluation instant is given."""
    future = REFERENCE + timedelta(days=2)
    assert not is_untrusted_mtime(future)
    assert is_untrusted_mtime(future, reference=REFERENCE)
    assert is_untrusted_mtime(datetime(1970, 1, 1, tzinfo=UTC), reference=REFERENCE)
    assert not is_untrusted_mtime(REFERENCE - timedelta(days=1), reference=REFERENCE)


def test_future_mtime_tolerance_is_pinned_at_one_day() -> None:
    """The freshness contract is expressed in whole days (``stale_after_days``,
    ``age_days``), so the future-trust tolerance must stay a single whole day.
    A silent change to this constant would change H-006 semantics without any
    test that names the boundary explicitly failing (the other boundary tests
    derive their fixtures from the constant itself, so they would not catch
    a change to its value)."""
    assert timedelta(days=1) == _FUTURE_MTIME_TOLERANCE
