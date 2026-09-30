"""AS-OBSIDIAN-CAPTURE-001-F10 — graph must not write what canonical refuses.

Two generated-span-preserving writers should agree on *whether* a document is
safe to write. They did not. `graph_projections._merge_protected_regions` keeps
a historical contract for a prior note with no HUMAN regions -- preserve text
outside the generated span, where the canonical core returns the fresh render --
and that branch splices by hand instead of delegating, so it never reaches the
canonical structural parse.

Its own marker validation compares HUMAN marker **counts and names**, never
**order**. A rendered document whose markers are reversed (`END` before `BEGIN`)
or crossed (`a b /a /b`) therefore passed and was **written**, while the
canonical core refuses it as `unpaired`. Graph accepted what canonical rejected
-- fail-open-shaped relative to canonical, and the disagreement is about policy,
not wording.

Found by independent verification of F9 as a refusal-set divergence, measured
there but fixed here: it is a different defect class from F9's diagnostic
uniformity, so it gets its own package.

Scope. Only the *fail-open* direction is closed. The reverse -- canonical
accepts, graph refuses -- is F4's **disclosed and intentional** contract: graph
refuses rather than silently discard the text outside the generated span. That
asymmetry is deliberate and is asserted here so it cannot be removed by accident.

Reachability. An earlier revision of this docstring called this "defence in
depth at a writer boundary, not a live corruption path", needing a broken
renderer. That was wrong and is retracted: nothing in the render path escapes
marker text, so a relationship field carrying HUMAN markers reaches the render
verbatim, and against a prior note with no HUMAN regions the pre-fix writer
persisted it and left the projection permanently unrefreshable. See
``test_f10_a_poisoned_field_cannot_brick_the_projection`` below, which
reproduces exactly that.
"""

from __future__ import annotations

import itertools
import pathlib
import re

import pytest

import project_atlas.graph_projections as gp
from project_atlas.graph_projections import GraphProjectionError
from project_atlas.protected_regions import (
    GENERATED_END,
    GENERATED_START,
    ProtectedRegionError,
    merge_protected_regions,
)

HUMAN_OPEN = "<!-- BEGIN HUMAN: notes -->"
HUMAN_CLOSE = "<!-- END HUMAN: notes -->"

#: A prior note with NO HUMAN regions -- the branch that bypasses the canonical
#: structural parse. With a generated span, so the splice path is taken.
NO_HUMAN_PRIOR = f"{GENERATED_START}\nold\n{GENERATED_END}\nouter text\n"

#: Renders the canonical core refuses as structurally unparseable.
MALFORMED_RENDER = {
    "reversed-end-before-begin": (
        f"{GENERATED_START}\nx\n{GENERATED_END}\n{HUMAN_CLOSE}\n{HUMAN_OPEN}\n"
    ),
    "crossed-a-b": (
        f"{GENERATED_START}\nx\n{GENERATED_END}\n"
        "<!-- BEGIN HUMAN: a -->\n<!-- BEGIN HUMAN: b -->\n"
        "<!-- END HUMAN: a -->\n<!-- END HUMAN: b -->\n"
    ),
    "unclosed-region": f"{GENERATED_START}\nx\n{GENERATED_END}\n{HUMAN_OPEN}\nbody\n",
}


@pytest.mark.parametrize("label", sorted(MALFORMED_RENDER))
def test_f10_graph_refuses_a_render_canonical_refuses(label: str) -> None:
    """The whole point: graph must not accept what canonical rejects."""
    rendered = MALFORMED_RENDER[label]
    with pytest.raises(ProtectedRegionError):
        merge_protected_regions(existing=NO_HUMAN_PRIOR, rendered=rendered, path="n.md")
    with pytest.raises(GraphProjectionError):
        gp._merge_protected_regions(existing=NO_HUMAN_PRIOR, rendered=rendered, path="n.md")


def test_f10_a_poisoned_field_cannot_brick_the_projection(tmp_path: pathlib.Path) -> None:
    """End-to-end: the defect this package exists to close, through the real writer.

    An earlier revision bound ``before = NO_HUMAN_PRIOR`` and then asserted
    ``before == NO_HUMAN_PRIOR`` -- two names for the same immutable ``str``, so
    it could not fail. Both review bots and verification caught it. This is the
    test it should have been, and it pins the consequence, not the mechanism.

    Reachability, established by reproduction rather than argument. Nothing in
    the render path escapes marker text: ``_redact_text`` strips secrets and
    truncates but never touches HTML comments, so a relationship field holding
    HUMAN marker text reaches the render verbatim. Combined with a prior note
    that has no HUMAN regions -- an operator who deleted their block, or a note
    predating HUMAN emission -- the pre-fix writer WROTE the poisoned document,
    after which every later refresh, including a clean one with no relationships
    at all, was permanently refused. The projection could not self-heal.
    """
    poison = f"{HUMAN_CLOSE} x {HUMAN_OPEN}"
    record = {
        "project_id": "demo",
        "relationship_id": "r1",
        "relationship_type": "depends_on",
        "source_entity_id": poison,
        "target_entity_id": "b",
        "relationship_fingerprint": "f" * 8,
        "link_quality": "inferred",
        "provenance": {},
    }
    clean = dict(record, source_entity_id="a")

    vault = tmp_path / "vault"
    vault.mkdir()
    gp.write_projection_outputs(
        gp.materialize_projections(project_id="demo", relationships=[clean], health=None),
        vault=vault,
    )
    note = vault / "generated/graph/projections/demo/relationships.md"
    # Strip the HUMAN regions: this is the state that reaches the branch which
    # splices by hand instead of delegating to the canonical core.
    note.write_text(
        re.sub(
            re.escape(HUMAN_OPEN) + r".*?" + re.escape(HUMAN_CLOSE),
            "",
            note.read_text(),
            flags=re.S,
        )
    )
    assert HUMAN_OPEN not in note.read_text()
    before = note.read_bytes()

    with pytest.raises(GraphProjectionError):
        gp.write_projection_outputs(
            gp.materialize_projections(project_id="demo", relationships=[record], health=None),
            vault=vault,
        )

    assert note.read_bytes() == before, "the poisoned render must not be written"
    assert not list(vault.rglob("*.atlas-stage")), "no staging residue may survive"

    # The consequence that makes this more than a diagnostic nicety: the
    # projection must remain refreshable afterwards.
    gp.write_projection_outputs(
        gp.materialize_projections(project_id="demo", relationships=[], health=None), vault=vault
    )
    gp.write_projection_outputs(
        gp.materialize_projections(project_id="demo", relationships=[clean], health=None),
        vault=vault,
    )


def test_f10_a_well_formed_render_is_still_accepted() -> None:
    """Positive control: the fix must not widen refusal."""
    rendered = f"{GENERATED_START}\nfresh\n{GENERATED_END}\n{HUMAN_OPEN}\nkept\n{HUMAN_CLOSE}\n"
    merged = gp._merge_protected_regions(existing=NO_HUMAN_PRIOR, rendered=rendered, path="n.md")
    assert "fresh" in merged
    assert "outer text" in merged, "the disclosed graph contract must still preserve outside text"


def test_f10_the_disclosed_f4_asymmetry_is_deliberate_and_remains() -> None:
    """graph refuses where canonical accepts, and that direction is intentional.

    F4 disclosed it: a prior note with a generated span and a render offering
    none would have canonical return the fresh document, discarding the text
    outside the span. Graph refuses instead. Asserted so it cannot be removed
    by someone "fixing the asymmetry" wholesale.
    """
    rendered = "no span at all\n"
    assert (
        merge_protected_regions(existing=NO_HUMAN_PRIOR, rendered=rendered, path="n.md") == rendered
    )
    with pytest.raises(GraphProjectionError):
        gp._merge_protected_regions(existing=NO_HUMAN_PRIOR, rendered=rendered, path="n.md")


def test_f10_no_fail_open_divergence_across_a_differential_sweep() -> None:
    """No shape may have canonical refuse while graph accepts.

    The sweep varies *both* sides, because this defect lives on the rendered
    side and a corpus that only varies the prior note cannot see it.
    """
    fragments = [
        GENERATED_START + "\n",
        GENERATED_END + "\n",
        HUMAN_OPEN + "\n",
        HUMAN_CLOSE + "\n",
        "text\n",
    ]
    fail_open: list[tuple[str, str]] = []
    disclosed = 0
    pairs = 0
    for existing_parts in itertools.product(fragments, repeat=2):
        for rendered_parts in itertools.product(fragments, repeat=2):
            existing, rendered = "".join(existing_parts), "".join(rendered_parts)
            pairs += 1
            try:
                merge_protected_regions(existing=existing, rendered=rendered, path="n.md")
                canonical_refused = False
            except ProtectedRegionError:
                canonical_refused = True
            try:
                gp._merge_protected_regions(existing=existing, rendered=rendered, path="n.md")
                graph_refused = False
            except GraphProjectionError:
                graph_refused = True
            if canonical_refused and not graph_refused:
                fail_open.append((existing, rendered))
            elif graph_refused and not canonical_refused:
                disclosed += 1

    assert pairs == 625, pairs
    assert not fail_open, (
        f"graph accepted {len(fail_open)} shapes canonical refused: {fail_open[:3]}"
    )
    # The disclosed F4 direction is expected to remain; a sweep finding none of
    # it would mean the corpus stopped exercising that contract.
    assert disclosed > 0, "the sweep no longer reaches the disclosed graph contract"
