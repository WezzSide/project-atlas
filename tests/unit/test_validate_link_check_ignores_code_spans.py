"""`atlas validate`'s link checker must not flag `](...)`-shaped substrings
that are inert -- quoted inside a Markdown inline code span or fenced code
block -- as real navigable links. Only genuine links outside those regions
are real evidence of a broken reference.

Independently reproduced against current main (`4fb91beb`) via the real
`atlas validate` CLI, not assumed from historical PR #659's own claims:
authentic first-run dogfooding of `atlas validate` reported
``broken link: projects/project-atlas/claims.md -> OPENAI-MCP-DESIGN.md``
for a claim line whose entire value is inside one Markdown code span --
``knowledge_compiler._quote_source_text`` deliberately renders untrusted
source text as visibly inert Markdown, and a CommonMark-correct reader
never treats a `[...]()`-shaped substring inside a code span or fenced
block as a live link. The defect is entirely in ``validation.py``'s link
scan (``LINK.findall(text)``), which ran over raw file text with zero
code-span/fence awareness.

``BROKEN_LINK_ROOT_CAUSE = VALIDATION_FALSE_POSITIVE_LINK_CHECK_IGNORES_CODE_SPANS``
(historical PR #659's classification -- confirmed still correct, its patch
was reconstructed fresh here as a current-main successor rather than reused
mechanically). Historical #659 also caught a genuine correctness gap in its
first cut, closed via Copilot review: a fenced block's closing run must be
*at least* as long as its opening run (CommonMark's actual rule), not
merely "any run of 3+" -- a shorter accidental run inside the quoted
content (e.g. an excerpt that itself contains a 3-backtick fence) must
never be mistaken for the real close. That fix is preserved here.

New in this reconstruction, not covered by #659's own test suite: CommonMark
permits fenced code blocks delimited by *tildes* (`~~~`) as well as
backticks, closed only by a matching or longer run of the *same* character.
The reproduction below proved this gap independently (a tilde-fenced
`](...)`-shaped substring was still flagged as broken on the first
reconstruction pass, before tilde-fence masking was added) -- it is pinned
here as its own regression case, not folded silently into the backtick one.

An independent verifier rejected the first current-main successor head
(`e9cb8069`) with three findings, each independently reproduced here before
being remediated (not argued around):

- **F-700-1 (P1)**: ``_collect_reachable_notes`` (H-007 orphan reachability)
  called the raw, unmasked ``LINK.findall(text)`` -- a link inside an inert
  code span/fence could still create a reachability edge even though the
  same link was correctly ignored by the broken-link scan, an internally
  inconsistent validator. Fixed at the source by routing both passes
  through the same ``_mask_inert_markdown_regions`` helper -- one canonical
  transformation, not a second, driftable masking implementation.
- **F-700-2 (P2)**: the inline-code-span regex's backreference matched a
  *prefix* of a longer closing run (e.g. two opening backticks "closed" by
  the first two of a three-backtick run), silently absorbing the extra
  backtick and suppressing a real link that followed it. Fixed structurally,
  not just for the reported 2-vs-3 case: an atomic group commits to the
  opening run's *maximal* length (CommonMark tries the longest backtick
  string once; no same-length close anywhere means no span, never retried
  shorter), a trailing negative lookahead requires the closing run to be
  *exactly* that length, and a leading negative lookbehind stops a match
  from ever starting in the middle of a longer run one character over(a
  case broader testing found beyond the verifier's own report: without it,
  a genuinely unpaired 3-backtick run could still be spuriously
  reinterpreted as a paired 2-backtick span using its last two characters).
- **F-700-3 (P2)**: fence indentation accepted unlimited leading whitespace,
  so a 4-space-*indented* backtick block -- CommonMark's separate "indented
  code block" construct, not a fence -- was masked as if it were a
  top-level fence. Fixed: fence indentation is now capped at 0-3 spaces
  (CommonMark's actual rule) for *what counts as a fence*. Disclosed
  residual, not silently left unproven: if an over-indented run still has a
  same-length backtick run later in the file, the inline-code-span pass
  (a separate, correct construct on its own terms) can still mask the
  interior -- full CommonMark block-vs-inline disambiguation (blank-line
  detection, "indented code cannot interrupt a paragraph") is out of scope
  for this lightweight regex-based helper. The reported defect -- unlimited
  indentation accepted as a fence -- is fixed and independently confirmed
  for the isolated case (no coincidental matching closer elsewhere).

Two further defects were found independently while remediating the above,
neither part of the verifier's three findings, both fixed here and covered
by ``TestF700AdversarialClass``:
- The fenced-block closing pattern didn't tolerate a trailing ``\r`` before
  line-end, so on CRLF-encoded text the closing fence never matched and the
  ``\Z`` fallback swallowed (over-masked) the remainder of the document,
  including any real links after the fence. Fixed with an ``\r?`` tolerance
  in both the backtick and tilde closing patterns.
- ``TestF700_1ReachabilityConsistency``'s own fixture placed its orphan
  target directly alongside a reachable ``project.md``, which the
  pre-existing, legitimate ``_bundle_members_of_reachable_projects()``
  same-directory sweep always excludes from orphan detection regardless of
  link logic -- a fixture bug, not a ``validation.py`` bug, that made those
  tests pass/fail for the wrong reason. Fixed by nesting the orphan target
  one directory deeper, outside that sweep.

Durable rejection receipt:
https://github.com/B0LK13/project-atlas/pull/700#issuecomment-5560937437
"""

from __future__ import annotations

from pathlib import Path

from project_atlas.validation import _mask_inert_markdown_regions, validate


class TestMaskInertMarkdownRegions:
    """Unit coverage of the masking function itself."""

    def test_inline_single_backtick_span_is_masked(self) -> None:
        text = "See `[label](missing.md)` for details."
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked
        assert len(masked) == len(text), "byte-length must be preserved"

    def test_inline_multi_backtick_span_is_masked(self) -> None:
        text = "Also `` [label](missing.md) `` here."
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked
        assert len(masked) == len(text)

    def test_fenced_backtick_block_is_masked(self) -> None:
        text = "before\n```\n[link](missing.md)\n```\nafter"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked
        assert len(masked) == len(text)

    def test_fenced_tilde_block_is_masked(self) -> None:
        text = "before\n~~~\n[link](missing.md)\n~~~\nafter"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked
        assert len(masked) == len(text)

    def test_tilde_fence_not_closed_by_backticks(self) -> None:
        """A tilde fence's own contents may freely contain backticks --
        CommonMark's actual reason tilde fences exist -- and must not
        terminate the block early."""
        text = "~~~\nsome `inline code` and [link](missing.md)\n~~~\n"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked

    def test_backtick_fence_not_closed_by_tildes(self) -> None:
        text = "```\nsome ~~~ looking content and [link](missing.md)\n```\n"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked

    def test_closing_fence_must_be_at_least_as_long_as_opening(self) -> None:
        """Reviewer-found (#659) correctness gap: an outer 4-backtick fence
        containing a nested 3-backtick 'fence' inside the quoted content
        must not be closed early by the shorter nested run."""
        text = "````\nouter, containing:\n```\nnested\n```\nstill outer: [link](missing.md)\n````\n"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked

    def test_real_link_immediately_adjacent_to_inert_one_is_preserved(self) -> None:
        text = "`[inert](missing.md)` then [real](also-missing.md)"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked
        assert "](also-missing.md)" in masked

    def test_anchor_and_https_links_unaffected_by_masking(self) -> None:
        text = "[anchor](#section) and [external](https://example.com/x)"
        masked = _mask_inert_markdown_regions(text)
        assert masked == text

    def test_no_backtick_or_tilde_fast_path(self) -> None:
        """Performance short-circuit (#659 review): prose with neither fence
        character skips both regex passes and returns the identical object
        graph's content unchanged."""
        text = "Just plain prose with a [real link](missing.md) in it.\n"
        assert _mask_inert_markdown_regions(text) == text

    def test_multiline_fenced_block_is_fully_masked(self) -> None:
        text = (
            "before\n```python\ndef f():\n    "
            "return '[link](missing.md)'\n```\nafter [real](also-missing.md)"
        )
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked
        assert "](also-missing.md)" in masked


def _make_vault(tmp_path: Path) -> Path:
    from project_atlas.cli import EXIT_OK, main

    vault = tmp_path / "vault"
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    return vault


class TestValidateEndToEnd:
    """Authentic reproduction: the real `validate()` production function
    against a real vault on disk, no mocks."""

    def test_inert_code_span_link_is_not_flagged(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        project = vault / "projects" / "demo"
        project.mkdir(parents=True)
        (project / "notes.md").write_text(
            "`[label](inert-does-not-exist.md)`\n", encoding="utf-8"
        )
        (vault / "projects" / "index.md").write_text(
            (vault / "projects" / "index.md").read_text(encoding="utf-8")
            + "\n[demo](demo/notes.md)\n",
            encoding="utf-8",
        )
        result = validate(vault)
        errors = result["errors"]
        assert not any("inert-does-not-exist.md" in e for e in errors)

    def test_inert_tilde_fence_link_is_not_flagged(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        project = vault / "projects" / "demo"
        project.mkdir(parents=True)
        (project / "notes.md").write_text(
            "~~~\n[label](tilde-does-not-exist.md)\n~~~\n", encoding="utf-8"
        )
        (vault / "projects" / "index.md").write_text(
            (vault / "projects" / "index.md").read_text(encoding="utf-8")
            + "\n[demo](demo/notes.md)\n",
            encoding="utf-8",
        )
        result = validate(vault)
        errors = result["errors"]
        assert not any("tilde-does-not-exist.md" in e for e in errors)

    def test_genuine_broken_link_outside_any_inert_region_is_still_caught(
        self, tmp_path: Path
    ) -> None:
        """The fix must not weaken real link checking. A real, non-inert
        broken link must still fail validation."""
        vault = _make_vault(tmp_path)
        project = vault / "projects" / "demo"
        project.mkdir(parents=True)
        (project / "notes.md").write_text(
            "A real broken link: [gone](does-not-exist-for-real.md)\n",
            encoding="utf-8",
        )
        (vault / "projects" / "index.md").write_text(
            (vault / "projects" / "index.md").read_text(encoding="utf-8")
            + "\n[demo](demo/notes.md)\n",
            encoding="utf-8",
        )
        result = validate(vault)
        errors = result["errors"]
        assert any("does-not-exist-for-real.md" in e for e in errors)

    def test_dogfood_shaped_reproduction(self, tmp_path: Path) -> None:
        """Shape of the original authentic dogfood failure: a single-line
        claim value, entirely inside one code span, containing a
        same-directory relative link valid at its source location."""
        vault = _make_vault(tmp_path)
        project = vault / "projects" / "project-atlas"
        project.mkdir(parents=True)
        (project / "claims.md").write_text(
            "- `claim-001` roadmap-status: `**PROTOTYPE**. Complements "
            "[OPENAI-MCP-DESIGN.md](OPENAI-MCP-DESIGN.md).` _(source: x)_\n",
            encoding="utf-8",
        )
        (vault / "projects" / "index.md").write_text(
            (vault / "projects" / "index.md").read_text(encoding="utf-8")
            + "\n[project-atlas](project-atlas/claims.md)\n",
            encoding="utf-8",
        )
        result = validate(vault)
        errors = result["errors"]
        assert not any("OPENAI-MCP-DESIGN.md" in e for e in errors)

    def test_anchor_and_https_links_remain_unflagged(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        project = vault / "projects" / "demo"
        project.mkdir(parents=True)
        (project / "notes.md").write_text(
            "[anchor](#somewhere) and [external](https://example.com/x)\n",
            encoding="utf-8",
        )
        (vault / "projects" / "index.md").write_text(
            (vault / "projects" / "index.md").read_text(encoding="utf-8")
            + "\n[demo](demo/notes.md)\n",
            encoding="utf-8",
        )
        result = validate(vault)
        errors = result["errors"]
        assert not any("somewhere" in e or "example.com" in e for e in errors)

    def test_real_cli_pipeline_reproduction(self, tmp_path: Path) -> None:
        """Broadest proof: the actual discover -> ingest -> build-indexes ->
        validate pipeline, not just validate() in isolation."""
        from project_atlas.cli import EXIT_OK, main

        source = tmp_path / "source"
        source.mkdir()
        (source / "README.md").write_text(
            "See `[inert](does-not-exist.md)` for the inert case.\n",
            encoding="utf-8",
        )
        vault = tmp_path / "pipeline-vault"
        manifest = tmp_path / "manifest.json"
        assert main(["init", "--output", str(vault)]) == EXIT_OK
        assert (
            main(["discover", "--source", str(source), "--output", str(manifest)])
            == EXIT_OK
        )
        assert (
            main(
                [
                    "ingest",
                    "--manifest",
                    str(manifest),
                    "--vault",
                    str(vault),
                    "--source",
                    str(source),
                ]
            )
            == EXIT_OK
        )
        assert main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
        assert main(["validate", "--vault", str(vault)]) == EXIT_OK


class TestF700_1ReachabilityConsistency:
    """F-700-1 (P1): a Markdown link inert for the broken-link scan must not
    create an H-007 reachability edge either -- both passes now share one
    canonical ``_mask_inert_markdown_regions`` transformation, not two
    independent, driftable implementations."""

    def _vault_with_orphan_target(self, tmp_path: Path) -> Path:
        """A vault whose only project.md is reachable from the seeds, and a
        second, otherwise-orphaned note that only an inert link "points to"."""
        vault = _make_vault(tmp_path)
        project = vault / "projects" / "demo"
        project.mkdir(parents=True)
        (project / "project.md").write_text("# demo\n", encoding="utf-8")
        (vault / "projects" / "index.md").write_text(
            (vault / "projects" / "index.md").read_text(encoding="utf-8")
            + "\n[demo](demo/project.md)\n",
            encoding="utf-8",
        )
        # Nested, not directly alongside project.md -- otherwise
        # _bundle_members_of_reachable_projects()'s non-recursive
        # same-directory sweep would exclude it from orphan detection
        # regardless of link logic, making these tests pass/fail for the
        # wrong reason.
        nested = project / "nested"
        nested.mkdir()
        (nested / "orphan-note.md").write_text("# orphan\n", encoding="utf-8")
        return vault

    @staticmethod
    def _is_orphan(result: dict[str, object], rel: str) -> bool:
        """True when H-007 reported ``rel`` as an orphan -- the real,
        end-to-end observable shape ``_validate_orphans`` appends to
        ``findings``, not a private-function reach-in."""
        findings = result.get("findings")
        assert isinstance(findings, list)
        return any(
            f.get("rule_id") == "H-007-orphan" and f.get("path") == rel for f in findings
        )

    def test_inline_inert_link_does_not_create_reachability_edge(
        self, tmp_path: Path
    ) -> None:
        vault = self._vault_with_orphan_target(tmp_path)
        project_md = vault / "projects" / "demo" / "project.md"
        project_md.write_text(
            "# demo\n\nSee `[orphan](nested/orphan-note.md)` (inert, quoted).\n",
            encoding="utf-8",
        )
        result = validate(vault)
        # The orphan note must still be reported unreachable -- an inert
        # link must not rescue it from orphan status.
        assert self._is_orphan(result, "projects/demo/nested/orphan-note.md")

    def test_backtick_fence_inert_link_does_not_create_reachability_edge(
        self, tmp_path: Path
    ) -> None:
        vault = self._vault_with_orphan_target(tmp_path)
        project_md = vault / "projects" / "demo" / "project.md"
        project_md.write_text(
            "# demo\n\n```\n[orphan](nested/orphan-note.md)\n```\n", encoding="utf-8"
        )
        result = validate(vault)
        assert self._is_orphan(result, "projects/demo/nested/orphan-note.md")

    def test_tilde_fence_inert_link_does_not_create_reachability_edge(
        self, tmp_path: Path
    ) -> None:
        vault = self._vault_with_orphan_target(tmp_path)
        project_md = vault / "projects" / "demo" / "project.md"
        project_md.write_text(
            "# demo\n\n~~~\n[orphan](nested/orphan-note.md)\n~~~\n", encoding="utf-8"
        )
        result = validate(vault)
        assert self._is_orphan(result, "projects/demo/nested/orphan-note.md")

    def test_real_live_link_still_creates_reachability_edge(self, tmp_path: Path) -> None:
        """The fix must not weaken reachability either: a genuine,
        non-inert link must still mark its target reachable, i.e. NOT
        reported as an orphan."""
        vault = self._vault_with_orphan_target(tmp_path)
        project_md = vault / "projects" / "demo" / "project.md"
        project_md.write_text(
            "# demo\n\n[not orphaned](nested/orphan-note.md)\n", encoding="utf-8"
        )
        result = validate(vault)
        assert not self._is_orphan(result, "projects/demo/nested/orphan-note.md")

    def test_inert_only_reference_does_not_rescue_orphan_from_finding(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: an inert-only reference must not suppress the H-007
        orphan finding for the otherwise-unreachable note."""
        vault = self._vault_with_orphan_target(tmp_path)
        project_md = vault / "projects" / "demo" / "project.md"
        project_md.write_text(
            "# demo\n\n`[orphan](nested/orphan-note.md)`\n", encoding="utf-8"
        )
        result = validate(vault)
        assert self._is_orphan(result, "projects/demo/nested/orphan-note.md")


class TestF700_2InlineDelimiterMatrix:
    """F-700-2 (P2): inline code span delimiter-run boundaries, tested
    structurally rather than only for the exact 2-open/3-close case the
    verifier reported."""

    def test_one_open_one_close_is_inert(self) -> None:
        masked = _mask_inert_markdown_regions("`[live](missing.md)`")
        assert "](missing.md)" not in masked

    def test_two_open_two_close_is_inert(self) -> None:
        masked = _mask_inert_markdown_regions("``[live](missing.md)``")
        assert "](missing.md)" not in masked

    def test_two_open_three_close_is_not_incorrectly_masked(self) -> None:
        """The verifier's exact reproduction: two opening backticks
        "closed" by the first two of a longer run must not suppress the
        real link that follows."""
        masked = _mask_inert_markdown_regions("``[live](missing.md)```")
        assert "](missing.md)" in masked

    def test_three_open_two_close_is_not_incorrectly_masked(self) -> None:
        """Broader than the reported case: an opening run with no
        same-length close anywhere must never fall back to a shorter,
        spuriously-paired interpretation."""
        masked = _mask_inert_markdown_regions("```[live](missing.md)``")
        assert "](missing.md)" in masked

    def test_valid_three_open_three_close_still_masks(self) -> None:
        masked = _mask_inert_markdown_regions("```[live](missing.md)```")
        assert "](missing.md)" not in masked

    def test_real_broken_link_after_malformed_span_is_detected(self) -> None:
        masked = _mask_inert_markdown_regions(
            "``[decoy](nope.md)``` [real](missing.md)"
        )
        assert "](missing.md)" in masked

    def test_adjacent_delimiter_characters_boundary_correct(self) -> None:
        """A single backtick immediately followed by a 3-backtick fence-like
        run, both real delimiters at their own lengths, must each resolve
        independently rather than bleed into one another."""
        text = "`x` and ```[live](missing.md)```"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked  # the 3-backtick span is valid and masks it
        assert "x" not in masked.replace(" ", "")  # the single-backtick span is also masked


class TestF700_3FenceIndentationMatrix:
    """F-700-3 (P2): fence indentation must respect CommonMark's 0-3 space
    boundary, not accept unlimited leading whitespace."""

    @staticmethod
    def _fenced(indent: str) -> str:
        return f"para\n{indent}```\n{indent}[live](missing.md)\n{indent}```\nafter"

    def test_zero_space_fence(self) -> None:
        masked = _mask_inert_markdown_regions(self._fenced(""))
        assert "](missing.md)" not in masked

    def test_one_space_fence(self) -> None:
        masked = _mask_inert_markdown_regions(self._fenced(" "))
        assert "](missing.md)" not in masked

    def test_two_space_fence(self) -> None:
        masked = _mask_inert_markdown_regions(self._fenced("  "))
        assert "](missing.md)" not in masked

    def test_three_space_fence(self) -> None:
        masked = _mask_inert_markdown_regions(self._fenced("   "))
        assert "](missing.md)" not in masked

    def test_four_space_indented_block_is_not_a_top_level_fence(self) -> None:
        """The reported defect: unlimited indentation was accepted as a
        fence. Isolated case (no coincidental matching backtick run
        elsewhere) -- the link must remain live."""
        text = (
            "para line.\n\n"
            "    ```\n"
            "    [live](missing.md)\n"
            "    still four-space, never closes as a fence in this file\n"
        )
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" in masked

    def test_real_link_in_non_fenced_indented_block_is_not_lost(self) -> None:
        text = "    [live](missing.md) -- four-space indented, no backticks at all\n"
        masked = _mask_inert_markdown_regions(text)
        assert masked == text


class TestF700AdversarialClass:
    """Broader adversarial coverage requested alongside the three findings:
    whole-document overmask prevention, CRLF, and large inert spans."""

    def test_prose_only_document_is_never_touched(self) -> None:
        text = "Just prose. [real](missing.md) with no backticks anywhere.\n"
        assert _mask_inert_markdown_regions(text) == text

    def test_crlf_line_endings_do_not_break_fence_matching(self) -> None:
        text = "before\r\n```\r\n[link](missing.md)\r\n```\r\nafter [real](also-missing.md)"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing.md)" not in masked
        assert "](also-missing.md)" in masked
        assert len(masked) == len(text)

    def test_large_inert_span_is_masked_without_error(self) -> None:
        big_content = "\n".join(f"line {i} [x{i}](missing-{i}.md)" for i in range(2000))
        text = f"```\n{big_content}\n```\nafter [real](final-missing.md)"
        masked = _mask_inert_markdown_regions(text)
        assert "missing-0.md" not in masked
        assert "missing-1999.md" not in masked
        assert "](final-missing.md)" in masked
        assert len(masked) == len(text)

    def test_tilde_fence_wrapping_unclosed_backtick_does_not_swallow_later_live(
        self,
    ) -> None:
        """Successive whole-document fence passes let an inner unclosed
        backtick opener ``\\Z``-mask past the outer tilde closer, hiding a
        later live link from H-007. Reproduced on #700 HEAD ``076f4643``:
        ``LINK.findall`` after mask was empty. Document-order masking
        keeps the live link visible."""
        text = "~~~\n```\nstill inside tilde\n~~~\n[live](missing-after-nested.md)\n"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing-after-nested.md)" in masked
        assert len(masked) == len(text)

    def test_backtick_info_string_containing_backtick_is_not_a_fence(self) -> None:
        """CommonMark forbids backticks in a backtick fence info string.
        ``[^\n]*`` treated `` ```foo`bar `` as an opener and ``\\Z``-swallowed
        the following live link. Reproduced on #700 HEAD ``076f4643``."""
        text = "```foo`bar\nnot a fence\n[live](missing-after-info.md)\n"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing-after-info.md)" in masked
        assert len(masked) == len(text)

    def test_backtick_fence_wrapping_unclosed_tilde_does_not_swallow_later_live(
        self,
    ) -> None:
        """Symmetric control: leftmost backtick fence must close on its
        own closer, not let an inner unclosed tilde run to EOF."""
        text = "```\n~~~\nstill inside backtick\n```\n[live](missing-after-tilde-inner.md)\n"
        masked = _mask_inert_markdown_regions(text)
        assert "](missing-after-tilde-inner.md)" in masked
        assert len(masked) == len(text)
