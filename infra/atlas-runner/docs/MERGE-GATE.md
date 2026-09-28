# Merge gate — evidence-freshness recheck at the merge instant

Closes the governance defect observed on PR #1025 (2026-09-28): a blocking independent
verification (IV) comment (`5867783462`, 10:05:00Z) existed before the merge (10:05:38Z),
yet the merge proceeded because the decision relied on an earlier admission snapshot.

`controller/merge_gate.py` is a read-only, fail-closed gate that must be evaluated
immediately before any merge mutation. It never merges. Required invariant:

| check | DENY reason |
|---|---|
| candidate HEAD / TREE exactly as bound by the authority | `HEAD_DRIFT` / `TREE_DRIFT` |
| base exact, or explicitly rebound (`rebind_base`) | `BASE_DRIFT` |
| every required CI run terminal SUCCESS on the exact head, newest run wins | `CI_MISSING` / `CI_NOT_TERMINAL_SUCCESS` |
| positive IV evidence is the exact record the authority bound (`iv_binding`: comment id + author + body sha256 + updated_at; optional `trusted_iv_authors` allowlist), unedited since binding and bound before the decision | `IV_BINDING_MISSING` / `IV_EVIDENCE_NOT_FOUND` / `IV_AUTHOR_MISMATCH` / `IV_AUTHOR_UNTRUSTED` / `IV_BODY_HASH_MISMATCH` / `IV_EDITED` / `IV_EDITED_AFTER_AUTHORITY` / `IV_BINDING_NOT_BEFORE_AUTHORITY` |
| that record names HEAD+TREE and its **canonical record lines** say `IV_VERDICT=PASS`, `BLOCKING_P0=0`, `BLOCKING_P1=0` (see *Inert Markdown contexts* below: only visible source lines are eligible; duplicate/contradictory or malformed record lines never count) | `IV_NOT_BOUND_TO_CANDIDATE` / `IV_NOT_PASS` |
| `required_checks` present, non-empty, well-formed | `NO_REQUIRED_CHECKS` / `MALFORMED_REQUIRED_CHECKS` |
| malformed timestamps / unknown review states / collector failures | `MALFORMED_EVIDENCE` / `EVIDENCE_COLLECTION_FAILED` (DENY, never crash-to-allow) |
| no blocking evidence (P0/P1>0, IV FAIL, REJECTED, security) in issue comments **or review-thread comments** whose **effective, edit-aware** timestamp (`updated_at`) is at or after (`>=`) the authority decision | `NEWER_BLOCKING_EVIDENCE` |
| no blocking review (`CHANGES_REQUESTED`, or a blocking marker in a review body) at **any** time — reviews expose no `updated_at`, so their bodies cannot be proven unedited and are never merely "older" | `BLOCKING_REVIEW` |
| the bound comment body satisfies the canonical-body grammar (below) | `IV_BODY_NOT_CANONICAL` |
| every required CI run's newest exact-head run chosen by validated timezone-aware timestamps (malformed → DENY) | `CI_NOT_TERMINAL_SUCCESS` / `MALFORMED_EVIDENCE` |
| authority bound to this PR, not consumed, snapshot newer than the decision | `PR_MISMATCH` / `AUTHORITY_ALREADY_CONSUMED` / `SNAPSHOT_OLDER_THAN_AUTHORITY` |

Every decision is a receipt (`atlas-merge-gate-receipt/v1`) carrying the authority binding,
the observed identity, required CI run ids, the latest IV id and the newest evidence id, so
the merge log can prove which evidence was current at the mutation instant.

```
python -m controller.merge_gate --pr 1026 --authority authority.json --receipt receipt.json
# exit 0 = ALLOW, 2 = DENY. authority.json: {"pr","head","tree","base","decided_at",
#   "required_checks":["ci","atlas-runner-ci"],
#   "iv_binding":{"iv_evidence_id","iv_author","iv_updated_at","iv_body_sha256"},
#   "trusted_iv_authors"?: [...], "rebind_base"?: sha, "consumed": false}
```

`tests/test_merge_gate_authenticity.py` proves IV source authenticity (bound id/author/hash/updated_at, allowlist), edit freshness (post-authority edits invalidate), canonical record context (fence/indented/HTML-comment/code-span/URL/quoted tokens never count, CRLF bodies included) and temporal CI ordering. `tests/test_merge_gate_successor.py` closes the fail-open surfaces found by the independent IV of the first revision (canonical verdict parsing, mandatory `required_checks`, same-second boundary, `--paginate --slurp` collection, SHA case normalization, fail-closed malformed evidence). `tests/test_merge_gate.py` replays the #1025 race deterministically (blocking verdict after
admission, before merge) and covers drift, stale CI, unbound IV, consumed authority and the
read-only collector. Merging this hardening change itself requires separate authority.
## Canonical-body grammar (invariant `INERT_MARKDOWN_CONTEXT_CAN_NEVER_ESTABLISH_POSITIVE_IV`)

The primary guarantee is **structural, not semantic**: the gate never tries to decide what GitHub would render.
A comment is admissible as positive IV evidence only if its body satisfies `body_is_canonical`:

- none of the characters `<` `[` `]` `~` `$` `\` and no backtick run of length ≥ 2 anywhere in the body;
- no non-blank line indented ≥ 4 columns (tab stops of 4) and no line starting a blockquote (`>` at ≤ 3 indent).

Every CommonMark/GitHub construct that can hide text needs one of those: raw HTML blocks and inline HTML, comments,
declarations, CDATA, autolinks (`<`); links, images, reference definitions, titles and alt text (`[` `]`); fenced code
(``` ``` ``` / `~~~`); multi-backtick code spans; math (`$`); backslash escapes that re-pair code spans; indented code
(≥ 4 columns, also inside list items); blockquotes. With them absent the only remaining span construct is the
single-backtick code span, which `_strip_code_spans` handles exactly, and every other construct (headings, emphasis,
list items, thematic breaks, tables, entities) renders its text visibly. A non-canonical body yields
`parse_iv_record → NON_CANONICAL` and `IV_BODY_NOT_CANONICAL` (DENY) — regardless of what the scanner below would see.
Verifier lanes therefore write records as plain Markdown; single-backtick SHAs (`HEAD `…``) remain fine.

## Inert Markdown context scanner (defence in depth)

Positive record lines are taken only from the **visible** part of the bound comment. A bounded, line-based
CommonMark *block* scanner (`_strip_block_contexts`, a state machine — not a renderer and not a regex over the
whole body) replaces every inert line with the placeholder `[stripped]`; `_strip_code_spans` then removes
CommonMark code spans. Bodies are CRLF/CR-normalised first. Inert contexts:

- fenced code: opener of >= 3 backticks or tildes at <= 3 columns indent (a backtick fence's info string may not
  contain a backtick); closed only by the same character with a run **>= the opener length**; unterminated
  fences swallow the rest of the body;
- indented code: >= 4 columns (tab stops of 4) whenever no paragraph is open — at body start, after a blank or
  whitespace-only line, an ATX heading, a thematic break, a setext underline, a closed fence, a closed HTML block
  or comment — and the block continues across internal blank lines; a 4-space line directly under a paragraph or
  list item is a visible continuation;
- HTML comments (`<!-- … -->`, terminated or not; `<!-->` never closes itself) and hidden/removed raw HTML
  containers (`pre`, `code`, `script`, `style`, `textarea`, `noscript`, `iframe`, `xmp`, `plaintext`, `noembed`,
  `noframes`, `svg`, `math`), anywhere on a line, whole lines until the closer, terminated or not;
- CommonMark HTML block types 3–5 (`<? … ?>`, `<!DECL … >`, `<![CDATA[ … ]]>`) until their closer, and types 6–7
  (a block-level tag or a lone complete tag at line start, e.g. `<details>`, `<div hidden>`) until the next blank
  line — their rendering is sanitizer-dependent, so they are inert by rule;
- link reference definitions (`[label]: …`) through the end of their paragraph — a multi-line title is consumed by the
  renderer and never shown;
- code spans of any backtick-run length, including multi-line spans (never across a blank line); an unmatched run
  of >= 3 backticks strips the rest of its paragraph (conservative);
- blockquote lines, ordered-list items and table cells never match the record grammar.

The replacement is a visible placeholder, never whitespace, so record-looking text that shares a source line with a
stripped construct cannot become a whole-line record. Stripping only ever removes *positive* evidence: blocking
markers are searched in the raw body, so a blocker hidden in a fence or comment still blocks. The rules err towards
false DENY (e.g. table cells, `<kbd>`, a paragraph after a blank line inside a list item) and never towards ALLOW.
Only `\n` separates record lines (form feed, vertical tab, U+2028/2029, NEL are not line breaks here).
Adversarial corpus: `tests/test_merge_gate_authenticity.py` tests 22–24, 25–30, 31–42, 45–51.

## Authority model for positive IV evidence (invariant `UNTRUSTED_ACTOR_CANNOT_UNILATERALLY_ESTABLISH_POSITIVE_IV`)

Positive IV evidence is never *discovered* in the PR; it is *named* by the merge authority. The authority object —
issued by the owner/authority issuer, never derived from the PR — carries `iv_binding` = exact comment id + author
login + body SHA-256 + `updated_at`, together with the bound HEAD/TREE. `evaluate` reads the binding **only** from
the authority object; nothing in the snapshot (comments, reviews, extra fields) can supply, widen or override it.
The bound comment must exist exactly once, match all four binding fields byte-for-byte, have been last edited
strictly before `decided_at`, name the candidate HEAD and TREE in its visible text, and carry a canonical
`IV_VERDICT=PASS` / `BLOCKING_P0=0` / `BLOCKING_P1=0` record with no blocking marker.

Threat-model boundary: an actor who can comment on the PR but has no authority over the authority object cannot
establish PASS — they cannot cause their comment to be bound, an impostor comment with an identical body is not
eligible (different id/author), editing any bound comment changes `updated_at`/SHA-256 and invalidates it, and any
blocking marker they post at or after `decided_at` denies regardless of author. The residual risk is an **authority
issuance error**: an issuer binding an untrusted author's comment. `trusted_iv_authors` (optional allowlist,
`IV_AUTHOR_UNTRUSTED` when present and the bound author is not listed) is defence in depth against exactly that
error and is expected in production authorities; it is not what prevents unilateral establishment. Tests 43–44
prove both halves. Parser robustness findings therefore only change how the *already bound* verifier's own comment
is interpreted; they are classified by that authority impact, not by verifier label.
