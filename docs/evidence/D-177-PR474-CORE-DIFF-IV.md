# D-177 Lane H — Genuine independent core-diff IV (PR #474)

**Mode:** READ-ONLY except pytest (no merge, no PR create, no push)  
**Repo:** `WezzSide/project-atlas`  
**As-of local:** 2026-08-25  
**Lane:** H — CORE_DIFF_IV for SHADOW-C-002 encoding-safe attention output  
**MERGE_AUTHORIZATION:** `NOT_GRANTED`

---

## Tip binding (exact)

| Field | Value |
| --- | --- |
| PR | [#474](https://github.com/WezzSide/project-atlas/pull/474) |
| Title | fix(cli): encoding-safe attention output for cp1252/cp850 (SHADOW-C-002) |
| HEAD | `68201eb0801eec50e5e5d44ddc73b05c9a967569` |
| TREE | `e06d2a8e0a8e85e5a19d8f2c71ed90c7531f3ade` |
| BASE main (now, post-#504) | `a17949c6df9b4d004ffe03eb47b0934e3735204d` |
| PR merge-base (historical) | `f0e0c979e8ead0fdad4cc51682c560299db0a074` |
| Worktree | `D:\atlas-worktrees\d177-474-iv` (detached @ HEAD exact) |
| HEAD_MATCH | **EXACT** (`rev-parse HEAD` + `HEAD^{tree}`) |
| CI (gh tip) | required `ci` jobs SUCCESS on exact HEAD; `mergeable=MERGEABLE` |

Core path delta vs current main `a17949c…` (docs-only PR noise excluded from this IV):

- `src/project_atlas/terminal_io.py` — **ADDED** (57 lines)
- `src/project_atlas/cli.py` — attention `care_about` loop: `print` → `human_print` (+ import)
- `tests/unit/test_terminal_io_c002.py` — **ADDED** (11 tests)

---

## Verdict block

```
#474_CORE_DIFF_IV     = PASS
CERTIFICATION         = CERTIFIED_PENDING_OWNER
MERGE_AUTHORIZATION   = NOT_GRANTED
HEAD                  = 68201eb0801eec50e5e5d44ddc73b05c9a967569
TREE                  = e06d2a8e0a8e85e5a19d8f2c71ed90c7531f3ade
BASE_MAIN_NOW         = a17949c6df9b4d004ffe03eb47b0934e3735204d
PYTEST                = 11/11 PASS (tests/unit/test_terminal_io_c002.py, --no-cov)
INDEPENDENT_HARNESS   = 36/36 PASS (falsify attempt included)
METHOD                = NOT argparse --help triage alone
```

---

## Independent verification matrix (beyond --help)

Method: inspect tip sources + `PYTHONPATH=D:\atlas-worktrees\d177-474-iv\src` pytest + separate adversarial harness (strict `TextIOWrapper` streams; CLI `main()` with mocked `classify_attention`).

| Claim | Evidence | Result |
| --- | --- | --- |
| `terminal_io.py` / `human_print` | Module present; `adapt_human_text` → decorative map → `backslashreplace`; `human_print` writes adapted text + flush | **PASS** |
| Attention-command wrapping in `cli.py` | `args.command == "attention"`: JSON branch `print(json.dumps(...))`; human branch `human_print(...)` only for care_about lines carrying U+2192 | **PASS** |
| cp1252 | adapt + human_print + CLI TTY/redirect: U+2192 → `->`; no `UnicodeEncodeError` | **PASS** |
| cp850 | same as cp1252 | **PASS** |
| UTF-8 | arrow preserved in adapt, human_print, CLI TTY + redirect | **PASS** |
| Redirected output | `isatty=False` cp1252/utf-8 CLI attention runs EXIT_OK with content | **PASS** |
| JSON behavior | `--json` under cp1252: schema intact; fields not arrow-substituted; arrow *inside* payload text remains semantic (json.dumps escapes), not `->` | **PASS** |
| Unmapped Unicode fallback | ascii stream + U+2603 / café: `backslashreplace` (`\u2603`, `\xe9`); **≠** `errors="ignore"` silent drop; never `errors="ignore"` in code path | **PASS** |

### Falsify attempt (encoding-safe attention output)

| Probe | Expected if fix real | Observed |
| --- | --- | --- |
| Raw `stream.write("x → y\n")` on cp1252 strict | `UnicodeEncodeError` | **crashed** (control) |
| `human_print("x → y")` on same cp1252 strict | survives as `x -> y\n` | **survived** |
| CLI attention human path cp1252/cp850 | fallback `->`, no crash | **PASS** |
| CLI attention UTF-8 | preserve U+2192 | **PASS** |
| CLI `--json` with U+2192 inside why text | payload keeps arrow semantics; no human decorative rewrite | **PASS** |
| Spy: `adapt_human_text` invoked from `human_print` | one call with stream encoding | **PASS** |

**Falsify outcome:** could **not** break encoding-safe attention human output under the probes above. Control (raw write) still fails as expected.

---

## Pytest (tip)

```
cd D:\atlas-worktrees\d177-474-iv
$env:PYTHONPATH = "D:\atlas-worktrees\d177-474-iv\src"
python -m pytest tests/unit/test_terminal_io_c002.py -v --tb=short --no-cov
→ 11 passed
```

Note: default site `project_atlas` resolves to main workspace without `terminal_io`; tip IV **requires** worktree `PYTHONPATH` (or editable install of tip).

---

## Scope / residual notes (non-blocking for this IV)

- Only the attention `care_about` separator line is wrapped; other `print` lines in that block have no U+2192 (C-002 scope).
- Tip is behind current main (`a17949c…` not ancestor of HEAD); merge will need normal rebase/conflict check by owner — **out of scope** for this core-diff IV; does not change tip behavior certification.
- No merge performed. No PR created/updated by this lane.

---

## Authorization

```
#474_CORE_DIFF_IV   = PASS
CERTIFICATION       = CERTIFIED_PENDING_OWNER
MERGE_AUTHORIZATION = NOT_GRANTED
```
