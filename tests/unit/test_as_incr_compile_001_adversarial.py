"""AS-INCR-COMPILE-001 adversarial / invariant defenses (IC-ADV-*)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from project_atlas.compile_cache import (
    CompileCacheError,
    assert_cache_path_allowed,
    build_cache_receipt,
    compute_invalidation_key,
    evaluate_compile_refresh,
    receipt_to_json,
)

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "project_atlas" / "compile_cache.py"
_SCHEMA = _ROOT / "src" / "project_atlas" / "schemas" / "compile-cache-receipt.schema.json"
_FP_A = "a" * 64
_FP_B = "b" * 64


def test_ic_adv_001_stale_never_reported_as_hit() -> None:
    old_key = compute_invalidation_key(scope_id="s1", input_fingerprints={"model": _FP_A})
    result = evaluate_compile_refresh(
        scope_id="s1",
        input_fingerprints={"model": _FP_B},
        recorded_key=old_key,
        previous_output=b"artifact",
    )
    assert result["disposition"] == "stale"
    assert result["noop_disposition"] == "recompile"
    assert result["disposition"] != "hit"


def test_ic_adv_002_unchanged_hash_no_byte_rewrite() -> None:
    key = compute_invalidation_key(scope_id="s1", input_fingerprints={"model": _FP_A})
    previous = b"byte-identical\n"
    result = evaluate_compile_refresh(
        scope_id="s1",
        input_fingerprints={"model": _FP_A},
        recorded_key=key,
        previous_output=previous,
        candidate_output=previous,
    )
    assert result["output_bytes"] == previous
    with pytest.raises(CompileCacheError) as exc:
        evaluate_compile_refresh(
            scope_id="s1",
            input_fingerprints={"model": _FP_A},
            recorded_key=key,
            previous_output=previous,
            candidate_output=b"rewritten",
        )
    assert exc.value.code == "fr013_byte_mismatch"


def test_ic_adv_003_cache_hit_not_authority_winner() -> None:
    receipt = build_cache_receipt(
        scope_id="s1",
        input_fingerprints={"model": _FP_A},
        disposition="hit",
    )
    assert "authority_disposition" not in receipt
    assert "authority_winner" not in receipt
    assert "claim_id" not in receipt
    assert receipt["disposition"] == "hit"
    assert receipt["noop_disposition"] == "noop"
    # Hit is operational only — schema forbids authority smuggling keys.
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    props = schema["properties"]
    assert "authority_disposition" not in props
    assert "authority_winner" not in props


def test_ic_adv_004_trust_score_fields_forbidden() -> None:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    props = schema["properties"]
    for needle in ("trust", "trust_score", "confidence", "confidence_score", "subjective_trust"):
        assert needle not in props
    with pytest.raises(CompileCacheError) as exc:
        from project_atlas.compile_cache import _reject_forbidden_keys

        _reject_forbidden_keys({"trust_score": 0.9}, path="receipt")
    assert exc.value.code == "forbidden_field"


def test_ic_adv_005_model_modules_not_rewritten() -> None:
    """Surface policy: INCR owns compile_cache.py only — compilers untouched."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
    assert "project_atlas.knowledge_compiler" not in imports
    assert "project_atlas.semantic_compiler" not in imports
    # Goldens / allow-list symbols must not be mutated from this module.
    src = _MODULE.read_text(encoding="utf-8")
    assert "allowlist" not in src.lower()
    assert "MODEL-001" in src or "MODEL-001A/B/C" in src


def test_ic_adv_006_007_graph_xproj_path_dual_own_forbidden() -> None:
    for bad in (
        "generated/graph/foo.json",
        "generated/xproj/duplicate-candidates/x.json",
        "generated/xproj/indexes/y.json",
        "generated/indexes/terms.json",
    ):
        with pytest.raises(CompileCacheError) as exc:
            assert_cache_path_allowed(bad)
        assert exc.value.code == "path_policy_violation"
    allowed = assert_cache_path_allowed("generated/compile-cache/scope-1.json")
    assert allowed.as_posix() == "generated/compile-cache/scope-1.json"


def test_ic_adv_008_ret001_not_owned() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    assert "retrieval.py" not in src
    assert "AS-RET-001" in src  # documented exclude only


def test_ic_adv_009_ambiguous_never_silent_skip() -> None:
    with pytest.raises(CompileCacheError) as exc:
        evaluate_compile_refresh(
            scope_id="s1",
            input_fingerprints={"model": _FP_A},
            recorded_key="zzzz",
            previous_output=b"x",
        )
    rejection = exc.value.to_dict()
    assert rejection["status"] == "ambiguous_invalidation"
    assert rejection.get("disposition") != "hit"
    assert "noop" not in rejection.get("status", "")


def test_ic_adv_010_secret_leakage_redacted() -> None:
    receipt = build_cache_receipt(
        scope_id="s1",
        input_fingerprints={"model": _FP_A},
        disposition="miss",
        notes=["Authorization: Bearer sk-live-should-redact"],
    )
    blob = receipt_to_json(receipt)
    assert "sk-live" not in blob
    assert "Bearer" not in blob
    assert "redacted" in blob


def test_ic_adv_011_nondeterministic_ordering_forbidden() -> None:
    a = build_cache_receipt(
        scope_id="s1",
        input_fingerprints={"z": _FP_B, "a": _FP_A},
        disposition="miss",
        artifact_paths=["z.md", "a.md"],
    )
    b = build_cache_receipt(
        scope_id="s1",
        input_fingerprints={"a": _FP_A, "z": _FP_B},
        disposition="miss",
        artifact_paths=["a.md", "z.md"],
    )
    assert receipt_to_json(a) == receipt_to_json(b)
    assert list(a["input_fingerprints"]) == ["a", "z"]
    assert a["artifact_paths"] == ["a.md", "z.md"]


def test_ic_adv_012_ext001a_and_graph_incr_not_owned() -> None:
    src = _MODULE.read_text(encoding="utf-8")
    assert "compilation.py" in src  # documented non-confusion
    assert "graph-incremental-state" in src
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "compilation" not in node.module
            assert "graph_quarantine" not in node.module
            assert "graph_projections" not in node.module


def test_ic_adv_013_cli_untouched_serialize() -> None:
    """Optional CLI deferred — INCR must not race other cli.py sole writers."""
    cli = (_ROOT / "src" / "project_atlas" / "cli.py").read_text(encoding="utf-8")
    assert "compile_cache" not in cli
    assert "AS-INCR-COMPILE-001" not in cli


def test_ic_adv_014_rel001_must_not_open() -> None:
    # Module must not implement a release plane; docs forbid REL-001.
    src = _MODULE.read_text(encoding="utf-8")
    assert "release_certified" not in src.lower()
    assert "AS-REL-001" not in src
    docs = (_ROOT / "docs" / "AS-INCR-COMPILE-001-compile-cache.md").read_text(encoding="utf-8")
    assert "AS-REL-001 MUST NOT OPEN" in docs


def test_ic_adv_015_no_wall_clock_in_receipt() -> None:
    receipt = build_cache_receipt(
        scope_id="s1",
        input_fingerprints={"model": _FP_A},
        disposition="hit",
    )
    assert "at" not in receipt["generated"]
    assert "timestamp" not in receipt
    assert "generated_at" not in receipt
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    gen_props = schema["properties"]["generated"]["properties"]
    assert list(gen_props.keys()) == ["by"]
