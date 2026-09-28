"""Final hardening matrix: IV source authenticity, edit freshness, canonical record context,
temporal CI ordering (owner directive 2026-09-28, blocking findings 1-3 + robustness fix)."""

from __future__ import annotations

import copy

from controller.merge_gate import _body_sha256, evaluate, parse_iv_record

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
    assert parse_iv_record(body)["verdict"] == "MISSING"
    d = evaluate(authority(body), snapshot([comment(body)]))
    assert d.verdict == "DENY" and "verdict=MISSING" in R(d)
    body = iv("~~~text\nIV_VERDICT=PASS\n~~~")
    assert parse_iv_record(body)["verdict"] == "MISSING"


# 8. IV_VERDICT=PASS inside HTML comment only => MISSING / DENY
def test_08_pass_inside_html_comment_only_is_missing():
    body = iv("<!-- IV_VERDICT=PASS -->")
    assert parse_iv_record(body)["verdict"] == "MISSING"
    d = evaluate(authority(body), snapshot([comment(body)]))
    assert d.verdict == "DENY" and "verdict=MISSING" in R(d)
    body = iv("<!--\nIV_VERDICT=PASS\n-->")
    assert parse_iv_record(body)["verdict"] == "MISSING"


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
        assert parse_iv_record(body)["verdict"] == "MISSING", line
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
        assert rec == {"verdict": "PASS", "p0": 0, "p1": 0}, line
        d = evaluate(authority(body), snapshot([comment(body)]))
        assert d.verdict == "ALLOW", (line, d.reasons)


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
    assert "NEWER_BLOCKING_EVIDENCE:review:7" in R(evaluate(authority(), s))
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
    assert parse_iv_record(body)["verdict"] == "MISSING"
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY"


def test_20_unterminated_fence_excludes_rest_of_body():
    body = iv("```\nIV_VERDICT=PASS")  # fence never closed
    assert parse_iv_record(body)["verdict"] == "MISSING"
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY"
    body = iv(
        "IV_VERDICT=PASS", extra="```text\nIV_VERDICT=FAIL"
    )  # canonical line before the open fence
    assert parse_iv_record(body)["verdict"] == "PASS"


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
        assert parse_iv_record(body)["verdict"] == "MISSING", body
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", body


def test_23_unterminated_html_comment_hides_rest_of_body():
    body = iv("<!--\nIV_VERDICT=PASS")
    assert parse_iv_record(body)["verdict"] == "MISSING"
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
    assert parse_iv_record(hidden)["verdict"] == "MISSING"
    assert evaluate(authority(hidden), snapshot([comment(hidden)])).verdict == "DENY"
    plain = iv().replace("\n", "\r\n")  # canonical record with CRLF endings still parses
    assert parse_iv_record(plain) == {"verdict": "PASS", "p0": 0, "p1": 0}
    assert evaluate(authority(plain), snapshot([comment(plain)])).verdict == "ALLOW"


def test_26_indented_code_at_body_start_heading_or_whitespace_line_is_not_record():
    rec = "    IV_VERDICT=PASS\n    BLOCKING_P0=0\n    BLOCKING_P1=0\n"
    for body in (rec, "# Report\n" + rec, "intro\n# Report\n" + rec, "intro\n \t \n" + rec):
        assert parse_iv_record(body)["verdict"] == "MISSING", body
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", body


def test_27_indented_continuation_lines_remain_visible_text():
    # a 4-space line directly under a paragraph/list item is a continuation, not a code block
    for body in (iv().replace("IV_VERDICT=PASS", "    IV_VERDICT=PASS"), iv("  IV_VERDICT=PASS")):
        assert parse_iv_record(body)["verdict"] == "PASS", body
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "ALLOW", body


def test_28_code_spans_of_any_backtick_run_length_are_not_record():
    for span in ("``IV_VERDICT=PASS``", "`` `IV_VERDICT=PASS` ``", "```IV_VERDICT=PASS```"):
        body = iv(span)
        assert parse_iv_record(body)["verdict"] == "MISSING", span
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", span
    multiline = iv("`note\nIV_VERDICT=PASS\nend`")  # a code span may span soft line breaks
    assert parse_iv_record(multiline)["verdict"] == "MISSING"


def test_29_code_span_cannot_cross_blank_line_so_record_after_it_is_visible():
    body = iv(extra="`unterminated\n\ntrailing text`")
    assert parse_iv_record(body)["verdict"] == "PASS"
    assert evaluate(authority(body), snapshot([comment(body)])).verdict == "ALLOW"


def test_30_stripped_context_sharing_a_line_with_record_text_is_not_whole_line_record():
    for line in ("`x` IV_VERDICT=PASS", "``` `x` ``` IV_VERDICT=PASS", "<!-- c -->IV_VERDICT=PASS"):
        body = iv(line)
        assert parse_iv_record(body)["verdict"] == "MISSING", line
        assert evaluate(authority(body), snapshot([comment(body)])).verdict == "DENY", line
