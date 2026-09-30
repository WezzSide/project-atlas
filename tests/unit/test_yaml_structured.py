"""Unit tests for safe bounded YAML and yamlpath locators (AS-EXT-001A, §7.4/§8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.domain import LocatorConfidence
from project_atlas.yaml_structured import (
    DEFAULT_YAML_LIMITS,
    DuplicateKeyError,
    MalformedEncodingError,
    MalformedYamlError,
    ResourceLimitError,
    UnsafeConstructionError,
    YamlSecurityLimits,
    iter_leaf_paths,
    load_safe_yaml,
    sequence_item_segment,
    yaml_path_locator,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "as-ext-001a"


def _fixture_bytes(rel: str) -> bytes:
    return (FIXTURES / rel).read_bytes()


# --- safe loading and security bounds (§8) ---------------------------------


def test_real_receipts_load_safely() -> None:
    for name in (
        "real/evidence-flat-as-core-002-post-merge-receipt.yaml",
        "real/f03-evidence-nested-as-core-003-receipt.yaml",
    ):
        tree = load_safe_yaml(_fixture_bytes(name))
        assert isinstance(tree, dict), name


def test_duplicate_keys_rejected_explicitly() -> None:
    with pytest.raises(DuplicateKeyError, match="status"):
        load_safe_yaml(_fixture_bytes("synthetic/duplicate-yaml-keys.yaml"))


def test_malformed_yaml_raises_structured_error() -> None:
    with pytest.raises(MalformedYamlError):
        load_safe_yaml(_fixture_bytes("synthetic/malformed-yaml.yaml"))


def test_alias_amplification_bounded() -> None:
    with pytest.raises(ResourceLimitError, match="max_node_references"):
        load_safe_yaml(_fixture_bytes("synthetic/alias-amplification.yaml"))


def test_no_arbitrary_object_construction() -> None:
    payload = b"!!python/object/apply:os.system ['echo pwned']\n"
    with pytest.raises((UnsafeConstructionError, MalformedYamlError)):
        load_safe_yaml(payload)


def test_malformed_encoding_diagnostic() -> None:
    with pytest.raises(MalformedEncodingError):
        load_safe_yaml(b"status: \xff\xfe certified\n")


def test_control_characters_rejected() -> None:
    with pytest.raises(MalformedYamlError):
        load_safe_yaml(b"status: certified\x07\n")


def test_file_size_limit_enforced() -> None:
    limits = YamlSecurityLimits(max_file_bytes=16)
    with pytest.raises(ResourceLimitError, match="max_file_bytes"):
        load_safe_yaml(b"status: certified and then some\n", limits)


def test_depth_limit_enforced() -> None:
    doc = "a:\n"
    for level in range(1, 6):
        doc += "  " * level + f"k{level}:\n"
    limits = YamlSecurityLimits(max_depth=3)
    with pytest.raises(ResourceLimitError, match="max_depth"):
        load_safe_yaml(doc.encode(), limits)


def test_node_limit_enforced() -> None:
    doc = "\n".join(f"key{i}: value{i}" for i in range(20)) + "\n"
    limits = YamlSecurityLimits(max_nodes=10)
    with pytest.raises(ResourceLimitError, match="max_nodes"):
        load_safe_yaml(doc.encode(), limits)


def test_scalar_limit_enforced() -> None:
    doc = "status: " + "x" * 100 + "\n"
    limits = YamlSecurityLimits(max_scalar_bytes=32)
    with pytest.raises(ResourceLimitError, match="max_scalar_bytes"):
        load_safe_yaml(doc.encode(), limits)


def test_sequence_limit_enforced() -> None:
    doc = "items:\n" + "".join(f"  - {i}\n" for i in range(10))
    limits = YamlSecurityLimits(max_sequence_items=4)
    with pytest.raises(ResourceLimitError, match="max_sequence_items"):
        load_safe_yaml(doc.encode(), limits)


def test_limits_actually_enforced_not_documented_only() -> None:
    assert DEFAULT_YAML_LIMITS.max_depth == 32
    assert DEFAULT_YAML_LIMITS.max_nodes == 4_096
    assert DEFAULT_YAML_LIMITS.max_node_references == 8_192
    # Distinct nodes never exceed expanded references, so the node bound must
    # be tighter than the reference bound or it would be unreachable.
    assert DEFAULT_YAML_LIMITS.max_nodes < DEFAULT_YAML_LIMITS.max_node_references


def test_default_node_bound_reachable_without_aliases() -> None:
    """An alias-free document trips max_nodes before max_node_references."""
    doc = "\n".join(f"key{i}: value{i}" for i in range(2_050)) + "\n"
    with pytest.raises(ResourceLimitError, match="max_nodes"):
        load_safe_yaml(doc.encode())


def test_parser_recursion_exhaustion_is_structured_error() -> None:
    """Pathological nesting surfaces as resource-limit, not RecursionError."""
    bomb = b"[" * 500 + b"]" * 500
    with pytest.raises(ResourceLimitError, match="max_depth"):
        load_safe_yaml(bomb)


# --- yamlpath locators (§7.4) -----------------------------------------------


def test_flat_locator() -> None:
    assert yaml_path_locator(("status",)) == "yamlpath:status"


def test_nested_locator() -> None:
    assert yaml_path_locator(("validation", "pytest_all")) == "yamlpath:validation.pytest_all"
    assert (
        yaml_path_locator(("verify_disposition", "status")) == "yamlpath:verify_disposition.status"
    )


def test_locator_has_no_value_line_or_absolute_path() -> None:
    locator = yaml_path_locator(("status",))
    assert "certified" not in locator
    assert not locator.startswith("yamlpath:/")
    assert all(not part.isdigit() for part in locator.split(":"))


def test_reserved_characters_quoted() -> None:
    assert yaml_path_locator(("weird.key",)) == 'yamlpath:["weird.key"]'
    assert yaml_path_locator(("a b",)) == 'yamlpath:["a b"]'


def test_unicode_nfc_equivalence() -> None:
    nfc_tree = load_safe_yaml(_fixture_bytes("synthetic/unicode-equivalent-keys.yaml"))
    nfd_tree = load_safe_yaml(_fixture_bytes("synthetic/unicode-equivalent-keys-nfd.yaml"))
    nfc_locators = [yaml_path_locator(path) for path, _ in iter_leaf_paths(nfc_tree)]
    nfd_locators = [yaml_path_locator(path) for path, _ in iter_leaf_paths(nfd_tree)]
    assert nfc_locators == nfd_locators


def test_mapping_order_independence() -> None:
    tree_a = load_safe_yaml(_fixture_bytes("synthetic/reordered-mapping-a.yaml"))
    tree_b = load_safe_yaml(_fixture_bytes("synthetic/reordered-mapping-b.yaml"))
    locators_a = sorted(yaml_path_locator(path) for path, _ in iter_leaf_paths(tree_a))
    locators_b = sorted(yaml_path_locator(path) for path, _ in iter_leaf_paths(tree_b))
    assert locators_a == locators_b


def test_indentation_independence() -> None:
    compact = load_safe_yaml(b"validation:\n  pytest_all: passed\n")
    wide = load_safe_yaml(b"validation:\n        pytest_all: passed\n")
    assert [p for p, _ in iter_leaf_paths(compact)] == [p for p, _ in iter_leaf_paths(wide)]


def test_sequence_stable_key_addressing() -> None:
    tree = load_safe_yaml(_fixture_bytes("synthetic/sequence-stable-key.yaml"))
    locators = {yaml_path_locator(path): value for path, value in iter_leaf_paths(tree)}
    assert locators["yamlpath:candidates[candidate_id=V2-006].status"] == "certified"
    assert locators["yamlpath:candidates[candidate_id=V2-005].status"] == "superseded"


def test_sequence_provisional_index() -> None:
    tree = load_safe_yaml(_fixture_bytes("synthetic/sequence-provisional-index.yaml"))
    segments = []
    for index, item in enumerate(tree["validation_runs"]):
        segment, confidence = sequence_item_segment(item, index)
        segments.append((segment, confidence))
    assert segments[0] == ("[0]", LocatorConfidence.PROVISIONAL)
    assert segments[1] == ("[1]", LocatorConfidence.PROVISIONAL)
    locator = yaml_path_locator(("validation_runs", 0, "outcome"))
    assert locator == "yamlpath:validation_runs[0].outcome"


def test_leaf_walk_deterministic() -> None:
    data = _fixture_bytes("real/f03-evidence-nested-as-core-003-receipt.yaml")
    first = list(iter_leaf_paths(load_safe_yaml(data)))
    second = list(iter_leaf_paths(load_safe_yaml(data)))
    assert first == second
    # Every path renders to a unique locator (no collision within one receipt).
    locators = [yaml_path_locator(path) for path, _ in first]
    assert len(locators) == len(set(locators))
