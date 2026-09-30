"""Final hardening matrix: IV source authenticity, edit freshness, canonical record context,
temporal CI ordering (owner directive 2026-09-28, blocking findings 1-3 + robustness fix)."""

from __future__ import annotations

import copy
import json

from controller.merge_gate import (
    RECORD_LINE_RE,
    _body_sha256,
    _strip_non_record_context,
    body_is_canonical,
    evaluate,
    parse_iv_record,
)

HEAD = "dc9b083d32dbd5f4e524b95e89e3d0357d38bf1e"
TREE = "1e54af5bd618a5f7b618a611682f8c8c29e97094"
BASE = "28d4519cd9beb174d23ecca8715554417104e8d3"
T0 = "2026-09-28T10:00:00Z"
IV_AT = "2026-09-28T09:45:59Z"
VERIFIER = "atlasrunnerapp[bot]"


def iv(verdict_line: str = "IV_VERDICT=PASS", p0: int = 0, p1: int = 0, extra: str = "") -> str:
    return (
        f"FRESH INDEPENDENT IV\nHEAD `{HEAD}` TREE `{TREE}` base `{BASE}`\n"
        f"BLOCKING_P0={p0}\nBLOCKING_P1={p1}\n{verdict_line}\n{extra}"
    )


def comment(body, cid=1, author=VERIFIER, created=IV_AT, updated=None):
    return {
        "id": cid,
        "created_at": created,
        "updated_at": updated or created,
        "author": author,
        "body": body,
    }


def binding(body, cid=1, author=VERIFIER, at=IV_AT, sha=None):
    return {
        "iv_evidence_id": cid,
        "iv_author": author,
        "iv_updated_at": at,
        "iv_body_sha256": sha or _body_sha256(body),
    }


def run(name, **over):
    r = {
        "name": name,
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "success",
        "id": abs(hash(name)) % 10**6,
        "completed_at": "2026-09-28T09:40:00Z",
    }
    r.update(over)
    return r


DEFAULT_IV = iv()


def authority(body=DEFAULT_IV, **over):
    a = {
        "pr": 1025,
        "head": HEAD,
        "tree": TREE,
        "base": BASE,
        "decided_at": T0,
        "required_checks": ["ci", "atlas-runner-ci"],
        "iv_binding": binding(body),
        "trusted_iv_authors": [VERIFIER],
        "consumed": False,
    }
    a.update(over)
    return a


def snapshot(comments=None, **over):
    s = {
        "observed_at": "2026-09-28T10:05:30Z",
        "pr": {
            "number": 1025,
            "state": "OPEN",
            "merged": False,
            "head": HEAD,
            "tree": TREE,
            "base": BASE,
        },
        "check_runs": [run("ci"), run("atlas-runner-ci")],
        "comments": comments if comments is not None else [comment(iv())],
        "reviews": [],
    }
    s.update(over)
    return s


def R(d):
    return " ".join(d.reasons)


# 1. untrusted author with exact HEAD/TREE, P0=0/P1=0, canonical PASS => DENY
def test_01_untrusted_author_cannot_establish_pass():
    body = iv()
    s = snapshot([comment(body, author="random-user")])
    d = evaluate(authority(body), s)  # authority bound to VERIFIER, comment posted by someone else
    assert d.verdict == "DENY" and "IV_AUTHOR_MISMATCH:random-user" in R(d)
    # even if the authority itself names the random user, the allowlist rejects it
    d = evaluate(authority(body, iv_binding=binding(body, author="random-user")), s)
    assert d.verdict == "DENY" and "IV_AUTHOR_UNTRUSTED" in R(d)


# 2. trusted but wrong / non-bound verifier author => DENY
def test_02_trusted_but_non_bound_author_denies():
    body = iv()
    s = snapshot([comment(body, author="other-verifier[bot]")])
    a = authority(body, trusted_iv_authors=[VERIFIER, "other-verifier[bot]"])
    d = evaluate(a, s)
    assert d.verdict == "DENY" and "IV_AUTHOR_MISMATCH:other-verifier[bot]" in R(d)


# 3. authority-bound exact id + author + hash + updated_at => eligible
def test_03_exact_bound_record_is_eligible():
    d = evaluate(authority(), snapshot())
    assert d.verdict == "ALLOW", d.reasons
    assert d.receipt["latest_iv"]["body_sha256"] == _body_sha256(iv())
    assert d.receipt["iv_binding"]["iv_evidence_id"] == 1


# 4. correct IV comment body edited after authority issuance => DENY
def test_04_body_edited_after_authority_denies():
    edited = iv(extra="typo fixed")
    s = snapshot([comment(edited, updated="2026-09-28T10:00:30Z")])
    d = evaluate(authority(), s)
    assert d.verdict == "DENY"
    assert "IV_BODY_HASH_MISMATCH" in R(d) and "IV_EDITED_AFTER_AUTHORITY" in R(d)


# 5. edited comment preserving PASS but changing body hash => DENY
def test_05_edit_preserving_pass_but_changing_hash_denies():
    edited = iv(extra="Residual risk note appended.")
    s = snapshot([comment(edited, updated="2026-09-28T09:50:00Z")])  # edit still before authority
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and "IV_BODY_HASH_MISMATCH:1" in R(d) and "IV_EDITED:1" in R(d)


# 6. pre-authority created comment edited post-authority => DENY (even with identical body)
def test_06_pre_authority_comment_edited_post_authority_denies():
    s = snapshot([comment(iv(), created=IV_AT, updated="2026-09-28T10:00:00Z")])
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and "IV_EDITED_AFTER_AUTHORITY:1" in R(d)
    # and a blocking marker introduced by a post-authority edit of an OLD comment is newer evidence
    s = snapshot(
        [
            comment(iv()),
            comment(
                "MERGE_AUTHORITY_INVALIDATED",
                cid=2,
                created="2026-09-28T08:00:00Z",
                updated="2026-09-28T10:01:00Z",
            ),
        ]
    )
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and "NEWER_BLOCKING_EVIDENCE:comment:2" in R(d)


# 7. IV_VERDICT=PASS inside fenced code only => MISSING / DENY
def test_07_pass_inside_code_fence_only_is_missing():
    body = iv("```\nIV_VERDICT=PASS\n```")
    assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}
    d = evaluate(authority(body), snapshot([comment(body)]))
    assert d.verdict == "DENY" and "verdict=NON_CANONICAL" in R(d)
    body = iv("~~~text\nIV_VERDICT=PASS\n~~~")
    assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}


# 8. IV_VERDICT=PASS inside HTML comment only => MISSING / DENY
def test_08_pass_inside_html_comment_only_is_missing():
    body = iv("<!-- IV_VERDICT=PASS -->")
    assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}
    d = evaluate(authority(body), snapshot([comment(body)]))
    assert d.verdict == "DENY" and "verdict=NON_CANONICAL" in R(d)
    body = iv("<!--\nIV_VERDICT=PASS\n-->")
    assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}


# 9. PASS token inside URL / example / quoted / inline-code text => MISSING
def test_09_pass_in_url_example_quoted_code_is_missing():
    for line in (
        "see https://example.com/?IV_VERDICT=PASS&x=1",
        "example: the verifier writes IV_VERDICT=PASS on its own line",
        'the record must contain "IV_VERDICT=PASS"',
        "the record must contain `IV_VERDICT=PASS`",
        "IV_VERDICT=PASS (example)",
        "IV_VERDICT=PASS-ish",
    ):
        body = iv(line)
        assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}, line
        d = evaluate(authority(body), snapshot([comment(body)]))
        assert d.verdict == "DENY", line


# 10. exact canonical record outside excluded contexts => PASS when all else holds
def test_10_canonical_record_outside_excluded_contexts_allows():
    for line in (
        "IV_VERDICT=PASS",
        "**IV_VERDICT=PASS**",
        "- IV_VERDICT=PASS",
        "IV_VERDICT = PASS.",
    ):
        body = iv(line, extra="```\nIV_VERDICT=FAIL\n```\n<!-- IV_VERDICT=FAIL -->")
        rec = parse_iv_record(body)
        assert rec["verdict"] == "NON_CANONICAL", line
        d = evaluate(authority(body), snapshot([comment(body)]))
        # the scanner ignores the hidden FAILs, but such a body is not canonical: typed DENY
        assert d.verdict == "DENY" and d.reasons[0] == (
            "IV_BODY_NOT_CANONICAL:1:forbidden token '``'"
        ), (line, d.reasons)
        plain = iv(line)
        assert evaluate(authority(plain), snapshot([comment(plain)])).verdict == "ALLOW", line


# 11. contradictory canonical PASS/FAIL => DENY
def test_11_contradictory_canonical_verdicts_deny():
    body = iv("IV_VERDICT=PASS\nIV_VERDICT=FAIL")
    assert parse_iv_record(body)["verdict"] == "AMBIGUOUS"
    d = evaluate(authority(body), snapshot([comment(body)]))
    assert d.verdict == "DENY" and "verdict=AMBIGUOUS" in R(d)
    body = iv("IV_VERDICT=PASS", extra="BLOCKING_P1=1")  # duplicate key with different value
    assert parse_iv_record(body)["p1"] is None
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY"
    body = iv("IV_VERDICT=MAYBE")
    assert parse_iv_record(body)["verdict"] == "MALFORMED"
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY"


# 12. two plausible IV records; only the authority-bound one is eligible
def test_12_only_bound_record_is_eligible_among_plausible_records():
    bound, other = iv(), iv(extra="second plausible record")
    s = snapshot([comment(bound, cid=1), comment(other, cid=2, created="2026-09-28T09:50:00Z")])
    assert evaluate(authority(bound), s).verdict == "ALLOW"
    # binding to id 2 with id 1's hash fails; binding to 2 with its own hash passes
    assert "IV_BODY_HASH_MISMATCH:2" in R(
        evaluate(authority(bound, iv_binding=binding(bound, cid=2)), s)
    )
    a2 = authority(other, iv_binding=binding(other, cid=2, at="2026-09-28T09:50:00Z"))
    assert evaluate(a2, s).verdict == "ALLOW"
    # a later untrusted look-alike cannot substitute for the bound record
    s2 = snapshot([comment(other, cid=2, author="impostor")])
    assert "IV_EVIDENCE_NOT_FOUND:1" in R(evaluate(authority(bound), s2))


# 13/14/15. bound evidence id but wrong author / wrong body sha / different updated_at => DENY
def test_13_bound_id_wrong_author_denies():
    s = snapshot([comment(iv(), author="someone-else")])
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and "IV_AUTHOR_MISMATCH:someone-else" in R(d)


def test_14_bound_id_wrong_body_sha_denies():
    d = evaluate(authority(iv_binding=binding(iv(), sha="0" * 64)), snapshot())
    assert d.verdict == "DENY" and "IV_BODY_HASH_MISMATCH:1" in R(d)


def test_15_bound_id_different_updated_at_denies():
    d = evaluate(authority(iv_binding=binding(iv(), at="2026-09-28T09:46:00Z")), snapshot())
    assert d.verdict == "DENY" and "IV_EDITED:1@" in R(d)
    # a binding whose own timestamp is not before the decision is invalid by construction
    d = evaluate(authority(iv_binding=binding(iv(), at=T0)), snapshot([comment(iv(), updated=T0)]))
    assert d.verdict == "DENY" and "IV_BINDING_NOT_BEFORE_AUTHORITY" in R(d)


# 16. lexical vs temporal ordering => newest run selected temporally
def test_16_ci_newest_run_is_temporal_not_lexical():
    older_success = run("ci", id=1, completed_at="2026-09-28T09:00:00Z")
    newer_failure = run(
        "ci", id=2, conclusion="failure", completed_at="2026-09-28T07:30:00-02:00"
    )  # == 09:30Z
    s = snapshot(check_runs=[older_success, newer_failure, run("atlas-runner-ci")])
    d = evaluate(authority(), s)
    assert d.verdict == "DENY" and "CI_NOT_TERMINAL_SUCCESS:ci:completed/failure" in R(d)
    assert d.receipt["required_ci"]["ci"] == 2
    # lexically "2026-09-28T09:00:00Z" > "2026-09-28T07:30:00-02:00" would have picked the success
    newest_success = run("ci", id=3, completed_at="2026-09-28T09:45:00Z")
    s = snapshot(check_runs=[older_success, newer_failure, newest_success, run("atlas-runner-ci")])
    assert evaluate(authority(), s).verdict == "ALLOW"


# 17. malformed CI timestamp => DENY, never exception-to-ALLOW
def test_17_malformed_ci_timestamp_denies():
    for bad in ("yesterday", "", None, "2026-09-28T09:00:00"):
        s = snapshot(
            check_runs=[run("ci", completed_at=bad, created_at=bad), run("atlas-runner-ci")]
        )
        d = evaluate(authority(), s)
        assert d.verdict == "DENY" and d.reasons[0].startswith("MALFORMED_EVIDENCE"), bad


# 18. existing invariants remain (spot check: race, drift, required_checks, same-second)
def test_18_existing_invariants_hold_with_binding():
    s = snapshot(
        [
            comment(iv()),
            comment(
                "IV verdict: FAIL — BLOCKING_P0=0, BLOCKING_P1=1",
                cid=9,
                created="2026-09-28T10:05:00Z",
            ),
        ]
    )
    assert "NEWER_BLOCKING_EVIDENCE:comment:9" in R(evaluate(authority(), s))
    s = snapshot()
    s["pr"]["head"] = "82ec1c0aa163a7a3c4bfa747cc1a9a24eddabf2d"
    assert "HEAD_DRIFT" in R(evaluate(authority(), s))
    assert "NO_REQUIRED_CHECKS" in evaluate(authority(required_checks=[]), snapshot()).reasons
    s = snapshot(
        reviews=[
            {"id": 7, "submitted_at": T0, "author": "r", "state": "CHANGES_REQUESTED", "body": ""}
        ]
    )
    assert "BLOCKING_REVIEW:7@" in R(evaluate(authority(), s))
    a, sn = authority(), snapshot()
    a2, sn2 = copy.deepcopy(a), copy.deepcopy(sn)
    assert evaluate(a, sn).to_dict() == evaluate(a2, sn2).to_dict()
    assert "IV_BINDING_MISSING" in evaluate(authority(iv_binding=None), snapshot()).reasons
    assert (
        "IV_BINDING_MISSING"
        in evaluate(authority(iv_binding={"iv_evidence_id": 1}), snapshot()).reasons
    )


# 19-21. verifier P2s on 906dbaae closed: blockquote, unterminated fence, malformed authority sha
def test_19_blockquote_is_quote_context_not_record():
    body = iv("> IV_VERDICT=PASS")
    assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY"


def test_20_unterminated_fence_excludes_rest_of_body():
    body = iv("```\nIV_VERDICT=PASS")  # fence never closed
    assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY"
    body = iv(
        "IV_VERDICT=PASS", extra="```text\nIV_VERDICT=FAIL"
    )  # canonical line before the open fence: still not an admissible body
    assert parse_iv_record(body)["verdict"] == "NON_CANONICAL"


def test_21_malformed_authority_sha_denies_even_if_snapshot_matches():
    s = snapshot()
    s["pr"]["base"] = "not-a-sha"
    d = evaluate(authority(base="not-a-sha"), s)
    assert d.verdict == "DENY" and d.reasons[0].startswith("MALFORMED_EVIDENCE")
    d = evaluate(authority(rebind_base="short"), snapshot())
    assert d.verdict == "DENY" and d.reasons[0].startswith("MALFORMED_EVIDENCE")
    assert evaluate(authority(rebind_base=None), snapshot()).verdict == "ALLOW"


# 22-24. verifier P2s on 50302e0e closed: indented/pre-code blocks, unterminated HTML comment,
# case-exact record values
def test_22_indented_code_and_pre_code_blocks_are_not_record_context():
    for body in (
        iv("\n    IV_VERDICT=PASS"),  # CommonMark indented code block
        iv("<pre>\nIV_VERDICT=PASS\n</pre>"),
        iv("<code>\nIV_VERDICT=PASS"),  # unterminated block
    ):
        assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}, body
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", body


def test_23_unterminated_html_comment_hides_rest_of_body():
    body = iv("<!--\nIV_VERDICT=PASS")
    assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY"


def test_24_record_values_are_case_exact():
    for line in ("IV_VERDICT=pass", "IV_VERDICT=Pass", "iv_verdict=PASS"):
        body = iv(line)
        assert parse_iv_record(body)["verdict"] in {"MISSING", "MALFORMED"}, line
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", line


# 25-30. verifier P2s on 7a97b5c2 closed: CRLF bodies, indented code at block boundaries,
# CommonMark code spans of any backtick-run length, no whitespace-only context replacement
def test_25_crlf_bodies_are_normalised_before_context_stripping():
    hidden = "x\r\n\r\n    IV_VERDICT=PASS\r\n    BLOCKING_P0=0\r\n    BLOCKING_P1=0\r\n"
    assert parse_iv_record(hidden)["verdict"] in {"MISSING", "NON_CANONICAL"}
    assert evaluate(authority(hidden), snapshot([comment(hidden)])).verdict == "DENY"
    plain = iv().replace("\n", "\r\n")  # canonical record with CRLF endings still parses
    assert parse_iv_record(plain) == {"verdict": "PASS", "p0": 0, "p1": 0}
    assert evaluate(authority(plain), snapshot([comment(plain)])).verdict == "ALLOW"


def test_26_indented_code_at_body_start_heading_or_whitespace_line_is_not_record():
    rec = "    IV_VERDICT=PASS\n    BLOCKING_P0=0\n    BLOCKING_P1=0\n"
    for body in (rec, "# Report\n" + rec, "intro\n# Report\n" + rec, "intro\n \t \n" + rec):
        assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}, body
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", body


def test_27_indented_continuation_lines_remain_visible_text():
    # a 4-space line directly under a paragraph/list item is a continuation, not a code block
    for body in (iv().replace("IV_VERDICT=PASS", "    IV_VERDICT=PASS"), iv("  IV_VERDICT=PASS")):
        _visible(body, body)  # 4-column continuation is visible but not canonical -> typed DENY


def test_28_code_spans_of_any_backtick_run_length_are_not_record():
    for span in ("``IV_VERDICT=PASS``", "`` `IV_VERDICT=PASS` ``", "```IV_VERDICT=PASS```"):
        body = iv(span)
        assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}, span
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", span
    multiline = iv("`note\nIV_VERDICT=PASS\nend`")  # a code span may span soft line breaks
    assert parse_iv_record(multiline)["verdict"] in {"MISSING", "NON_CANONICAL"}


def test_29_code_span_cannot_cross_blank_line_so_record_after_it_is_visible():
    body = iv(extra="`unterminated\n\ntrailing text`")
    # the scanner alone would keep the record visible; the grammar rejects unpaired backticks
    stripped = _strip_non_record_context(body)
    assert any(RECORD_LINE_RE.match(ln) for ln in stripped.split("\n"))
    assert parse_iv_record(body)["verdict"] == "NON_CANONICAL"
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY"


def test_30_stripped_context_sharing_a_line_with_record_text_is_not_whole_line_record():
    for line in ("`x` IV_VERDICT=PASS", "``` `x` ``` IV_VERDICT=PASS", "<!-- c -->IV_VERDICT=PASS"):
        body = iv(line)
        assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}, line
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", line


# 31-42. v6 (12b562ad) verifier P2-A closed by the state-machine block scanner. Invariant:
# INERT_MARKDOWN_CONTEXT_CAN_NEVER_ESTABLISH_POSITIVE_IV. Each inert wrapper must yield MISSING
# and DENY; each visible form must still parse (fail-closed direction only).
REC = "IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n"
IND = "".join("    " + ln + "\n" for ln in REC.splitlines())
BOUND = f"HEAD `{HEAD}` TREE `{TREE}`\n"


def _inert(body: str, label: str) -> None:
    """A record inside inert context is never PASS: the grammar rejects the body (NON_CANONICAL);
    the scanner alone (defence in depth) sees MISSING."""
    assert parse_iv_record(body)["verdict"] in {"MISSING", "NON_CANONICAL"}, (label, body)
    stripped = _strip_non_record_context(body)
    assert not any(
        RECORD_LINE_RE.match(ln) and "IV_VERDICT" in ln for ln in stripped.split("\n")
    ), (label, stripped)
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", label


def _visible(body: str, label: str) -> None:
    """The scanner sees the record (parse PASS); evaluate ALLOWs only when the body also satisfies
    the canonical-body grammar, otherwise it is a typed IV_BODY_NOT_CANONICAL DENY."""
    d = evaluate(authority(body), snapshot([comment(body)]))
    if body_is_canonical(body) is None:
        assert parse_iv_record(body) == {"verdict": "PASS", "p0": 0, "p1": 0}, (label, body)
        assert d.verdict == "ALLOW", (label, d.reasons)
    else:
        assert parse_iv_record(body)["verdict"] == "NON_CANONICAL", (label, body)
        assert d.verdict == "DENY" and any(
            r.startswith("IV_BODY_NOT_CANONICAL") for r in d.reasons
        ), (label, d.reasons)


def test_31_indented_code_after_every_block_boundary_is_inert():
    for label, prefix in (
        ("body start", ""),
        ("single leading newline", "\n"),
        ("after blank line", "para\n\n"),
        ("after whitespace-only line", "para\n \t \n"),
        ("after ATX heading", "# Report\n"),
        ("after thematic break", "para\n\n---\n"),
        ("after setext underline", "Title\n===\n"),
        ("after closed backtick fence", "```\nx\n```\n"),
        ("after closed tilde fence", "~~~\nx\n~~~\n"),
        ("after closed HTML comment", "<!-- c -->\n"),
        ("after closed pre block", "<pre>x</pre>\n"),
        ("after list item + blank line", "- item\n\n"),
        ("after blockquote + blank line", "> quote\n\n"),
    ):
        _inert(prefix + IND + "\n" + BOUND, label)


def test_32_indented_block_continues_across_internal_blank_lines_and_tab_stops():
    _inert("    first chunk\n\n" + IND + "\n" + BOUND, "block across blank line")
    _inert("\tIV_VERDICT=PASS\n\tBLOCKING_P0=0\n\tBLOCKING_P1=0\n\n" + BOUND, "tab indent")
    for mix in (" \t", "  \t", "   \t", "\t "):
        body = "".join(mix + ln + "\n" for ln in REC.splitlines()) + "\n" + BOUND
        _inert(body, f"tab-stop mix {mix!r}")


def test_33_continuation_and_shallow_indentation_stay_visible():
    _visible(
        BOUND + "para\n    IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n",
        "paragraph continuation",
    )
    _visible(
        BOUND + "- item\n    IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n", "list continuation"
    )
    _visible(BOUND + "\n   IV_VERDICT=PASS\n   BLOCKING_P0=0\n   BLOCKING_P1=0\n", "3-space indent")
    _visible(
        BOUND + "  - IV_VERDICT=PASS\n  - BLOCKING_P0=0\n  - BLOCKING_P1=0\n", "nested list items"
    )
    _visible(BOUND + "# Title\n" + REC, "record after heading")


def test_34_fence_lengths_indentation_and_info_strings():
    _inert(
        BOUND + "````\nIV_VERDICT=PASS\n```\nBLOCKING_P0=0\nBLOCKING_P1=0\nIV_VERDICT=PASS\n````\n",
        "4-fence not closed by 3",
    )
    _inert(BOUND + "```\n" + REC + "````\n", "3-fence closed by 4 then nothing visible")
    _inert(BOUND + "   ```\n" + REC + "```\n", "fence indented 3")
    _inert(BOUND + "~~~~~\n" + REC + "~~~\n", "5-tilde not closed by 3")
    _inert(BOUND + "~~~ `info`\n" + REC + "~~~\n", "tilde fence with backtick info string")
    _inert(BOUND + "``` `x`\n" + REC + "```\n", "backtick pseudo-fence is a code span")
    _inert(BOUND + "```python\n" + REC, "unterminated fence swallows the rest")
    _visible(BOUND + "```\ncode\n```\n" + REC, "record after a closed fence")
    _inert(BOUND + "    ```\n" + REC, "literal triple backtick in a paragraph strips its rest")


def test_35_blockquotes_and_nested_containers_are_never_records():
    _inert(BOUND + "> " + REC.replace("\n", "\n> "), "blockquote lines")
    _inert(BOUND + "> ```\n> " + REC.replace("\n", "\n> ") + "```\n", "blockquote with fence")
    _inert(BOUND + "1. " + REC.replace("\n", "\n1. "), "ordered list marker")
    _inert(BOUND + "| IV_VERDICT=PASS |\n| BLOCKING_P0=0 |\n| BLOCKING_P1=0 |\n", "table cells")


def test_36_crlf_and_cr_bodies_match_lf_semantics():
    for eol in ("\r\n", "\r"):
        _inert((BOUND + "x\n\n" + IND).replace("\n", eol), f"indented code with {eol!r}")
        _inert((BOUND + "```\n" + REC + "```\n").replace("\n", eol), f"fence with {eol!r}")
        _visible((BOUND + REC).replace("\n", eol), f"canonical with {eol!r}")


def test_37_html_blocks_and_comments_terminated_or_not():
    for label, body in (
        ("comment one line", "<!-- " + REC.replace("\n", " ") + "-->\n"),
        ("comment multi-line", "<!--\n" + REC + "-->\n"),
        ("comment unterminated", "<!--\n" + REC),
        ("pre", "<pre>\n" + REC + "</pre>\n"),
        ("PRE uppercase unterminated", "<PRE>\n" + REC),
        ("code", "<code>\n" + REC + "</code>\n"),
        ("script", "<script>\n" + REC + "</script>\n"),
        ("style unterminated", "<style>\n" + REC),
        ("textarea", "<textarea>\n" + REC + "</textarea>\n"),
    ):
        _inert(BOUND + body, label)
    _visible(BOUND + "<!-- note -->\n" + REC, "record after a closed comment")


def test_38_code_spans_of_any_run_length_including_multiline():
    for span in (
        "`IV_VERDICT=PASS`",
        "``IV_VERDICT=PASS``",
        "```IV_VERDICT=PASS```",
        "`` `IV_VERDICT=PASS` ``",
        "`start\nIV_VERDICT=PASS\nend`",
        "``start\nIV_VERDICT=PASS\nend``",
    ):
        _inert(BOUND + "BLOCKING_P0=0\nBLOCKING_P1=0\n" + span + "\n", span)
    _visible(BOUND + REC + "`unterminated\n\ntrailing text`\n", "span cannot cross a blank line")
    _visible(BOUND + REC + "a ` b\n", "single unmatched backtick is literal")


def test_39_malformed_or_unclosed_constructs_fail_closed():
    for label, body in (
        ("mid-line triple backtick unterminated", "foo ```\n" + REC),
        ("mid-line triple backtick unterminated after bound line", BOUND + "x ```\n" + REC),
        ("unterminated tilde fence", "~~~\n" + REC),
        ("unclosed pre", "<pre>\n" + REC),
        ("unclosed comment mid-line", "text <!-- \n" + REC),
    ):
        _inert(BOUND + body, label)


def test_40_record_text_adjacent_to_stripped_constructs_is_not_whole_line():
    for label, body in (
        ("after span", "`x` IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n"),
        ("after pseudo-fence span", "``` `x` ``` IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n"),
        ("after comment", "<!-- c -->IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n"),
        ("before comment", "IV_VERDICT=PASS <!-- c -->\nBLOCKING_P0=0\nBLOCKING_P1=0\n"),
        ("after pre", "<pre>x</pre> IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n"),
        ("before span", "IV_VERDICT=PASS `x`\nBLOCKING_P0=0\nBLOCKING_P1=0\n"),
    ):
        _inert(BOUND + body, label)


def test_41_hidden_blockers_still_block_and_visible_record_still_binds():
    # stripping is one-directional: it can only remove positive evidence, never a blocker
    body = iv(extra="```\nIV_VERDICT=FAIL\n```\n<!-- BLOCKING_P0=1 -->\n\n    REJECTED\n")
    _visible(body, "hidden FAILs ignored by the scanner; body not canonical")
    body = iv()
    later = comment("<!-- SECURITY_BLOCKER -->", cid=9, created="2026-09-28T10:00:01Z")
    d = evaluate(authority(body), snapshot([comment(body), later]))
    assert d.verdict == "DENY" and d.reasons[0].startswith("NEWER_BLOCKING_EVIDENCE:comment:9")


def test_42_sha_binding_reads_only_visible_text():
    hidden = f"```\nHEAD {HEAD} TREE {TREE}\n```\n" + REC
    d = evaluate(authority(hidden), snapshot([comment(hidden)]))
    assert d.verdict == "DENY" and any(r.startswith("IV_NOT_BOUND_TO_CANDIDATE") for r in d.reasons)
    assert any(r.startswith("IV_BODY_NOT_CANONICAL") for r in d.reasons)
    for form in (
        f"HEAD `{HEAD}` TREE `{TREE}`",
        f"{HEAD} {TREE}",
    ):
        body = form + "\n" + REC
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "ALLOW", form


# 43-44. P2-B adjudication: UNTRUSTED_ACTOR_CANNOT_UNILATERALLY_ESTABLISH_POSITIVE_IV. The binding
# (id, author, sha256, updated_at) is read ONLY from the authority object; nothing in the snapshot
# (which any commenter can influence) can supply, widen or override it.
def test_43_binding_and_trust_come_only_from_the_authority_object():
    body = iv()
    attacker = comment(body, cid=2, author="mallory")
    attacker["trusted"] = True
    attacker["iv_binding"] = binding(body, cid=2, author="mallory")
    attacker["author_association"] = "OWNER"
    s = snapshot([attacker])
    s["iv_binding"] = binding(body, cid=2, author="mallory")
    s["trusted_iv_authors"] = ["mallory"]
    # authority binds the verifier's comment id 1, which is absent from the snapshot
    d = evaluate(authority(body), s)
    assert d.verdict == "DENY" and d.reasons[0].startswith("IV_EVIDENCE_NOT_FOUND")
    # authority without an allowlist still binds exactly one id/author/hash/updated_at tuple
    a = authority(body)
    a.pop("trusted_iv_authors")
    d = evaluate(a, s)
    assert d.verdict == "DENY" and d.reasons[0].startswith("IV_EVIDENCE_NOT_FOUND")
    # only the bound tuple is eligible, whatever else the snapshot claims
    assert evaluate(a, snapshot([comment(body), attacker])).verdict == "ALLOW"
    assert evaluate(authority(body), snapshot([comment(body), attacker])).verdict == "ALLOW"


def test_44_allowlist_is_defence_in_depth_against_authority_issuer_error():
    body = iv()
    mallory = comment(body, cid=1, author="mallory")
    # issuer mistakenly binds mallory's comment: exact binding alone would admit it ...
    a = authority(body, iv_binding=binding(body, cid=1, author="mallory"))
    a.pop("trusted_iv_authors")
    assert evaluate(a, snapshot([mallory])).verdict == "ALLOW"
    # ... the allowlist (when present) turns that issuer error into a typed DENY
    a["trusted_iv_authors"] = [VERIFIER]
    d = evaluate(a, snapshot([mallory]))
    assert d.verdict == "DENY" and "IV_AUTHOR_UNTRUSTED" in d.reasons


# 45-48. v7 (8deffd0d) verifier P1/P2/P3 closed: link reference definitions, CommonMark HTML block
# types 3-7, sanitizer-removed containers, and non-LF line separators.
def test_45_link_reference_definition_titles_are_inert():
    for title in ("'\n" + REC + "'", '"\n' + REC + '"', "(\n" + REC + ")"):
        _inert(BOUND + "\n[ref]: /u " + title + "\n", title)
        _inert("para\n\n[ref]: <u> " + title + "\n\n" + BOUND, "after paragraph " + title)
    _visible(BOUND + "see [ref]: not a definition\n" + REC, "bracket text inside a paragraph")


def test_46_html_block_types_3_to_7_and_removed_containers_are_inert():
    for label, body in (
        ("CDATA", "<![CDATA[\n" + REC + "]]>\n"),
        ("processing instruction", "<?php\n" + REC + "?>\n"),
        ("declaration", "<!DOCTYPE html\n" + REC + ">\n"),
        ("unterminated declaration", "<!X\n" + REC),
        ("noscript", "<noscript>\n" + REC + "</noscript>\n"),
        ("iframe unterminated", "<iframe>\n" + REC),
        ("xmp", "<xmp>\n" + REC + "</xmp>\n"),
        ("plaintext", "<plaintext>\n" + REC),
        ("svg", "<svg>\n" + REC + "</svg>\n"),
        ("math", "<math>\n" + REC + "</math>\n"),
        ("details block", "<details>\n" + REC + "</details>\n"),
        ("div hidden", "<div hidden>\n" + REC + "</div>\n"),
        ("lone custom tag line", "<x-widget>\n" + REC + "</x-widget>\n"),
        ("closing tag line", "</div>\n" + REC),
        ("degenerate comment", "<!-->\n" + REC + "\n-->\n"),
    ):
        _inert(BOUND + body, label)
    _visible(BOUND + "<div>\nhidden until blank\n\n" + REC, "record after the HTML block ended")
    _visible(BOUND + "a <b>bold</b> word\n" + REC, "inline HTML inside a paragraph is visible")


def test_47_only_lf_separates_record_lines():
    for sep in ("\x0c", "\x0b", chr(0x2028), chr(0x2029), "\x85", "\x1e"):
        body = BOUND + "`x`" + sep + "IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n"
        _inert(body, f"separator {sep!r}")


def test_48_visible_forms_unchanged_by_scanner_extensions():
    _visible(BOUND + "<!-- note -->\n\n" + REC, "after closed comment + blank")
    _visible(BOUND + "Intro paragraph.\n\n" + REC, "plain paragraph then record")
    _visible(BOUND + "- **IV_VERDICT=PASS**\n- BLOCKING_P0=0\n- BLOCKING_P1=0\n", "bold list")


# 49-54. v8 (6447d01f) verifier findings closed structurally: canonical-body admissibility
# grammar for positive evidence, review-thread comment channel, time-independent review blockers.
def _not_canonical(body: str, label: str) -> None:
    assert body_is_canonical(body) is not None, label
    d = evaluate(authority(body), snapshot([comment(body)]))
    assert d.verdict == "DENY" and any(r.startswith("IV_BODY_NOT_CANONICAL") for r in d.reasons), (
        label,
        d.reasons,
    )


def test_49_canonical_body_grammar_rejects_every_text_hiding_token():
    for label, extra in (
        ("raw html", "<b>x</b>"),
        ("comment", "<!-- x -->"),
        ("autolink", "<https://example.test>"),
        ("link", "[t](/u)"),
        ("image", "![t](/u)"),
        ("ref def", "[r]: /u"),
        ("bare bracket", "see [1]"),
        ("tilde", "~~~"),
        ("double backtick", "``x``"),
        ("triple backtick", "```"),
        ("math", "$x$"),
        ("backslash", "a \\` b"),
        ("indented 4", "    code"),
        ("tab indent", "\tcode"),
        ("blockquote", "> quote"),
    ):
        _not_canonical(iv(extra=extra), label)
    assert body_is_canonical(iv()) is None
    assert evaluate(authority(iv()), snapshot([comment(iv())])).verdict == "ALLOW"


def test_50_realistic_canonical_iv_body_allows():
    body = (
        f"# FRESH INDEPENDENT IV\n\nHEAD `{HEAD}` TREE `{TREE}` base `{BASE}`\n\n"
        "- governance matrix 15/15 PASS\n- own tests: 222 passed\n\n"
        "IV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n\nMERGE_AUTHORIZATION = NOT_GRANTED\n"
    )
    assert body_is_canonical(body) is None
    assert parse_iv_record(body) == {"verdict": "PASS", "p0": 0, "p1": 0}
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "ALLOW"


def test_51_v8_verifier_p1_classes_are_all_non_canonical():
    ind2 = "".join("  " + ln + "\n" for ln in REC.splitlines())
    for label, body in (
        ("list tilde fence", "- ~~~\n" + ind2 + "  ~~~\n"),
        ("list cdata", "- <![CDATA[\n" + ind2 + "  ]]>\n"),
        ("list pi", "- <?\n" + ind2 + "  ?>\n"),
        ("reopen comment same line", "<!-- x --> <!--\n" + REC + "\n-->\n"),
        ("reopen script same line", "<script>x</script> <script>\n" + REC + "</script>\n"),
        ("inline pi multi-line", "x <?\n" + REC + "\n?>\n"),
        ("open tag attributes", "<a\n" + REC + "\nx=1>\n"),
        ("link title multi-line", '[t](/u "\n' + REC + '")\n'),
        ("image alt multi-line", "![\n" + REC + "\n](/u)\n"),
        ("escaped ref label", '[a\\]b]: /u "\n' + REC + '"\n'),
        ("ref def in list", '- [x]: /u "\n' + ind2 + '  "\n'),
        (
            "escaped backtick re-pairing",
            "\\`a `\nIV_VERDICT=PASS\nBLOCKING_P0=0\nBLOCKING_P1=0\n`x`\n",
        ),
    ):
        _not_canonical(BOUND + body, label)


def test_52_review_thread_comments_are_a_blocking_channel_but_never_bound_evidence():
    body = iv()
    s = snapshot([comment(body)])
    s["review_comments"] = [
        {
            "id": 501,
            "created_at": "2026-09-28T09:00:00Z",
            "updated_at": "2026-09-28T10:00:00Z",
            "author": "reviewer",
            "body": "BLOCKING_P1=1",
        }
    ]
    d = evaluate(authority(body), s)
    assert (
        d.verdict == "DENY"
        and "NEWER_BLOCKING_EVIDENCE:review_comment:501@2026-09-28T10:00:00Z" in d.reasons
    )
    s["review_comments"][0]["updated_at"] = "2026-09-28T09:59:59Z"
    assert evaluate(authority(body), s).verdict == "ALLOW"
    # a review-thread comment cannot satisfy the binding even with the bound id
    s = snapshot([])
    s["review_comments"] = [comment(body, cid=1)]
    d = evaluate(authority(body), s)
    assert d.verdict == "DENY" and d.reasons[0].startswith("IV_EVIDENCE_NOT_FOUND")


def test_53_review_blockers_are_time_independent():
    body = iv()
    for state, text in (("CHANGES_REQUESTED", ""), ("COMMENTED", "IV_VERDICT=FAIL")):
        s = snapshot(
            [comment(body)],
            reviews=[
                {
                    "id": 7,
                    "submitted_at": "2026-09-28T08:00:00Z",  # long before the authority
                    "author": "r",
                    "state": state,
                    "body": text,
                }
            ],
        )
        d = evaluate(authority(body), s)
        assert d.verdict == "DENY" and "BLOCKING_REVIEW:7@2026-09-28T08:00:00Z" in d.reasons, state
        assert d.receipt["newer_blocking"] == [{"id": 7, "at": "2026-09-28T08:00:00Z"}]
    s = snapshot(
        [comment(body)],
        reviews=[{"id": 8, "submitted_at": T0, "author": "r", "state": "APPROVED", "body": "ok"}],
    )
    assert evaluate(authority(body), s).verdict == "ALLOW"


def test_54_collector_reads_review_thread_comments_and_tolerates_deleted_users():
    from controller.merge_gate import collect_snapshot

    calls: list[list[str]] = []

    class Done:
        def __init__(self, out):
            self.stdout = out

    def runner(args, **_):
        calls.append(list(args))
        path = args[2]
        if path.startswith("repos/o/r/pulls/5/comments"):
            out = [[{"id": 9, "created_at": T0, "user": None, "body": "x"}]]
        elif path.startswith("repos/o/r/pulls/5/reviews"):
            out = [[]]
        elif path.startswith("repos/o/r/issues/5/comments"):
            out = [[{"id": 1, "created_at": T0, "user": None, "body": "y"}]]
        elif path.startswith("repos/o/r/actions/runs"):
            out = [{"workflow_runs": []}]
        elif path.startswith("repos/o/r/git/commits/"):
            out = {"tree": {"sha": TREE}}
        else:
            out = {
                "number": 5,
                "state": "open",
                "merged": False,
                "head": {"sha": HEAD},
                "base": {"sha": BASE},
            }
        return Done(json.dumps(out))

    snap = collect_snapshot("o/r", 5, runner)
    assert snap["review_comments"] == [
        {"id": 9, "created_at": T0, "updated_at": T0, "author": None, "body": "x"}
    ]
    assert snap["comments"][0]["author"] is None
    assert any("pulls/5/comments" in c[2] for c in calls)


# 55-57. v9 (e6a88989) verifier P2/P3 closed by grammar tightening: tables, list-wrapped
# blockquotes, list-item-initial indented code, cross-line backtick pairing, bidi controls.
def test_55_tables_blockquotes_tabs_and_bidi_controls_are_not_canonical():
    for label, extra in (
        ("table row hiding SHAs", "| a | b |\n|---|---|\n| x | y | z |"),
        ("list-wrapped blockquote", "- > quoting\nIV_VERDICT=PASS"),
        ("plain gt", "a -> b"),
        ("tab", "-\t\tIV_VERDICT=PASS"),
        ("rlo", "\u202eIV_VERDICT=PASS"),
        ("lri", "\u2066x"),
    ):
        _not_canonical(iv(extra=extra), label)


def test_56_backticks_must_pair_on_one_line():
    _not_canonical(iv(extra="- `a\n- `\nIV_VERDICT=PASS\n` x"), "cross-item pairing")
    _not_canonical(iv(extra="`multi\nline`"), "multi-line span")
    body = iv(extra="see `x` and `y` here")
    assert body_is_canonical(body) is None
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "ALLOW"


def test_57_list_item_initial_indented_code_is_not_a_record_line():
    for line in ("-     IV_VERDICT=PASS", "*      IV_VERDICT=PASS"):
        body = iv(line)
        assert body_is_canonical(body) is None
        assert parse_iv_record(body)["verdict"] == "MISSING", line
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", line
    for line in ("- IV_VERDICT=PASS", "-   IV_VERDICT=PASS", "   IV_VERDICT=PASS"):
        assert parse_iv_record(iv(line))["verdict"] == "PASS", line
