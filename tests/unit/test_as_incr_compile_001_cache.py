"""AS-INCR-COMPILE-001 tip-safe cache invalidation / FR-013 no-op."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.compile_cache import (
    PACKAGE_ID,
    CompileCacheError,
    apply_byte_identical_noop,
    build_cache_receipt,
    combined_fingerprint,
    compute_invalidation_key,
    decide_cache_action,
    evaluate_compile_refresh,
    read_cache_receipt,
    receipt_to_json,
    write_cache_receipt,
)
from project_atlas.schema import validate_record

_FP_A = "a" * 64
_FP_B = "b" * 64
_FP_C = "c" * 64


def _fps(**kwargs: str) -> dict[str, str]:
    return dict(kwargs)


def test_ic_fr_001_receipt_schema_valid() -> None:
    receipt = build_cache_receipt(
        scope_id="proj-demo",
        input_fingerprints=_fps(claims=_FP_A, concepts=_FP_B),
        disposition="miss",
        artifact_paths=["projects/demo/overview.md"],
        notes=["fixture miss"],
    )
    assert receipt["package"] == PACKAGE_ID
    assert receipt["receipt_kind"] == "compile_cache_receipt"
    validate_record(receipt, "compile-cache-receipt")


def test_ic_fr_003_invalidation_key_deterministic() -> None:
    kwargs = {
        "scope_id": "scope-1",
        "input_fingerprints": _fps(z=_FP_B, a=_FP_A),
        "artifact_paths": ["b.md", "a.md"],
    }
    key_a = compute_invalidation_key(**kwargs)
    key_b = compute_invalidation_key(**kwargs)
    assert key_a == key_b
    assert len(key_a) == 64
    # Input map / artifact order must not affect the key.
    key_c = compute_invalidation_key(
        scope_id="scope-1",
        input_fingerprints=_fps(a=_FP_A, z=_FP_B),
        artifact_paths=["a.md", "b.md"],
    )
    assert key_c == key_a


def test_ic_fr_004_unchanged_hash_byte_identical_noop() -> None:
    """FR-013 — unchanged fingerprint → byte-identical no-op."""
    fps = _fps(model=_FP_A, sources=_FP_B)
    key = compute_invalidation_key(scope_id="s1", input_fingerprints=fps)
    previous = b"# generated note\nstable\n"
    result = evaluate_compile_refresh(
        scope_id="s1",
        input_fingerprints=fps,
        recorded_key=key,
        previous_output=previous,
        candidate_output=previous,
    )
    assert result["disposition"] == "hit"
    assert result["noop_disposition"] == "noop"
    assert result["output_bytes"] == previous
    assert result["impacted_artifacts"] == []
    assert apply_byte_identical_noop(previous_output=previous) == previous


def test_ic_fr_004_noop_rejects_divergent_candidate() -> None:
    fps = _fps(model=_FP_A)
    key = compute_invalidation_key(scope_id="s1", input_fingerprints=fps)
    with pytest.raises(CompileCacheError) as exc:
        evaluate_compile_refresh(
            scope_id="s1",
            input_fingerprints=fps,
            recorded_key=key,
            previous_output=b"old",
            candidate_output=b"new-rewrite",
        )
    assert exc.value.code == "fr013_byte_mismatch"


def test_ic_fr_005_changed_fingerprint_recompiles_impacted_set() -> None:
    fps_old = _fps(model=_FP_A)
    fps_new = _fps(model=_FP_C)
    old_key = compute_invalidation_key(
        scope_id="s1",
        input_fingerprints=fps_old,
        artifact_paths=["projects/s1/note.md"],
    )
    result = evaluate_compile_refresh(
        scope_id="s1",
        input_fingerprints=fps_new,
        artifact_paths=["projects/s1/note.md"],
        recorded_key=old_key,
        previous_output=b"stale-bytes",
        candidate_output=b"fresh-bytes",
    )
    assert result["disposition"] == "stale"
    assert result["noop_disposition"] == "recompile"
    assert result["impacted_artifacts"] == ["projects/s1/note.md"]
    assert result["output_bytes"] == b"fresh-bytes"
    assert combined_fingerprint(fps_new) != combined_fingerprint(fps_old)


def test_ic_fr_006_stale_artifact_never_fresh_hit() -> None:
    assert (
        decide_cache_action(
            recomputed_key=_FP_A,
            recorded_key=_FP_B,
            artifact_present=True,
        )
        == "stale"
    )
    assert (
        decide_cache_action(
            recomputed_key=_FP_A,
            recorded_key=None,
            artifact_present=True,
        )
        == "stale"
    )


def test_ic_fr_010_ambiguous_invalidation_fail_closed() -> None:
    with pytest.raises(CompileCacheError) as exc:
        compute_invalidation_key(scope_id="s1", input_fingerprints={})
    assert exc.value.code == "ambiguous_invalidation"
    assert (
        decide_cache_action(
            recomputed_key="not-a-hash",
            recorded_key=_FP_A,
            artifact_present=True,
        )
        == "ambiguous"
    )
    with pytest.raises(CompileCacheError):
        evaluate_compile_refresh(
            scope_id="s1",
            input_fingerprints=_fps(model=_FP_A),
            recorded_key="bad",
            previous_output=b"x",
        )


def test_ic_fr_011_receipt_json_deterministic() -> None:
    receipt_a = build_cache_receipt(
        scope_id="s1",
        input_fingerprints=_fps(b=_FP_B, a=_FP_A),
        disposition="hit",
        notes=["n1"],
    )
    receipt_b = build_cache_receipt(
        scope_id="s1",
        input_fingerprints=_fps(a=_FP_A, b=_FP_B),
        disposition="hit",
        notes=["n1"],
    )
    assert receipt_to_json(receipt_a) == receipt_to_json(receipt_b)
    assert list(receipt_a["input_fingerprints"]) == ["a", "b"]


def test_ic_fr_012_secret_notes_redacted() -> None:
    receipt = build_cache_receipt(
        scope_id="s1",
        input_fingerprints=_fps(model=_FP_A),
        disposition="miss",
        notes=["password=hunter2-should-redact"],
    )
    assert receipt["notes"] == ["cache note redacted (secret-shaped content)"]


def test_ic_fr_013_write_read_under_compile_cache(tmp_path: Path) -> None:
    receipt = build_cache_receipt(
        scope_id="proj-a",
        input_fingerprints=_fps(model=_FP_A),
        disposition="recompile",
        artifact_paths=["projects/proj-a/overview.md"],
    )
    path = write_cache_receipt(tmp_path, receipt)
    assert path.as_posix().endswith("generated/compile-cache/proj-a.json")
    loaded = read_cache_receipt(tmp_path, "proj-a")
    assert loaded is not None
    assert loaded["invalidation_key"] == receipt["invalidation_key"]
    validate_record(loaded, "compile-cache-receipt")


def test_ic_fr_014_015_forbidden_paths_rejected() -> None:
    with pytest.raises(CompileCacheError) as exc:
        compute_invalidation_key(
            scope_id="s1",
            input_fingerprints=_fps(model=_FP_A),
            artifact_paths=["generated/xproj/duplicate-candidates/x.json"],
        )
    assert exc.value.code == "path_policy_violation"
    with pytest.raises(CompileCacheError):
        compute_invalidation_key(
            scope_id="s1",
            input_fingerprints=_fps(model=_FP_A),
            artifact_paths=["generated/graph/incremental-state.json"],
        )
    with pytest.raises(CompileCacheError):
        compute_invalidation_key(
            scope_id="s1",
            input_fingerprints=_fps(model=_FP_A),
            artifact_paths=["generated/indexes/terms.json"],
        )


def test_miss_without_artifact_is_miss_not_hit() -> None:
    assert (
        decide_cache_action(
            recomputed_key=_FP_A,
            recorded_key=None,
            artifact_present=False,
        )
        == "miss"
    )


def test_matching_key_missing_artifact_forces_recompile() -> None:
    assert (
        decide_cache_action(
            recomputed_key=_FP_A,
            recorded_key=_FP_A,
            artifact_present=False,
        )
        == "recompile"
    )


def test_rejection_envelope_never_success_hit() -> None:
    err = CompileCacheError("boom", code="ambiguous_invalidation")
    payload = err.to_dict()
    assert payload["status"] == "ambiguous_invalidation"
    assert "disposition" not in payload
    assert json.loads(json.dumps(payload, sort_keys=True))["package"] == PACKAGE_ID
