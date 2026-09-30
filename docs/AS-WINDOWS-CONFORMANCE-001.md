# AS-WINDOWS-CONFORMANCE-001 — `ATLAS_WINDOWS_CONFORMANCE_V1`

**Truth boundary:** `DOCUMENTATION != OBSERVATION`. `PLATFORM ASSUMPTION != CONTRACT`.
`COMMAND EXIT 0 != STATE PROVEN`. `PROCESS SPAWNED != PROCESS IDENTIFIED`.
`PID EXISTS != PROCESS IDENTITY PROVEN`. `PATH EXISTS != PATH SAFE`.
`WINDOWS != ONE UNIVERSAL FILESYSTEM CONFIGURATION`. `MISSING EVIDENCE != PASS`.
`UNKNOWN != HEALTHY`.

## What this is

A reusable, versioned harness that answers, with reproducible probes and
machine-readable evidence — not assumption — *"what does native Windows
actually do, on this host, for the operating-system semantics Atlas depends
on?"*

```
PLATFORM → OBSERVATION → CONFORMANCE → EVIDENCE → SAFE EXECUTION
```

This mission's own history is the reason it exists: D146 (launcher PID ≠
resident PID), the `os.kill(SIGTERM)` unreliability against detached
processes, the CON/NUL/COM1 reserved-name assumption that no longer holds
on current Windows builds, the `warn_unreachable=true` mypy platform-narrowing
gotcha, `PATH` executable shadowing an editable install — each was
rediscovered one defect at a time. This package exists so the *next* one
doesn't have to be rediscovered from scratch.

## What it is not

- Not a new authoritative Git observer (that role belongs to the unmerged
  ULT-01b lane — see below).
- Not production code. It lives under `scripts/windows_conformance/`
  (mirroring `scripts/atlas_dag/`'s convention), imported by inserting
  `scripts/` onto `sys.path` — not part of the `project_atlas` package, not
  installed, not on any product code path.
- Not a CI gate yet. `.github/workflows/ci.yml` is intentionally untouched
  by this package (PR #769 owns the Windows mypy/CI lane; a
  `WINDOWS_CONFORMANCE_CI_GATE` follow-up can wire this in once that lane is
  resolved).

## Reconciliation with existing Windows/platform mechanisms

Inspected before building anything, to avoid a second implementation of an
existing canonical primitive:

| Component | Disposition |
|---|---|
| `project_atlas.orchestration.sdk.host.no_window_creationflags()` | **REUSE** — this package's own `procutil.NO_WINDOW` constant follows the identical rationale; every subprocess call in this package sets it |
| `project_atlas.orchestration.sdk.resident_driver`/`resident_windows` (D146) | **MEASURE_ONLY** — the launcher-stub/PID-identity finding this package's WIN-PROC-001/002/003 probes formalize originated here; this package doesn't import or depend on it, it independently re-observes the same class of fact |
| `atlas_contracts.paths.safe_relative_component` | **REUSE** — WIN-PATH-* probes call it directly to compare `OS_ACCEPTS` vs `ATLAS_CONTRACT_ACCEPTS` |
| PR #709 (Windows tooling parity companion) | **DUPLICATE_AVOIDED** — governance/tooling-manifest content (Copilot instructions, role profiles), not platform-semantics primitives; nothing to depend on |
| PR #764/#765 (ULT-01b, live execution observation) | **DO_NOT_DEPEND_ON_UNMERGED** — unmerged, and a different concern (binding *observed* git/host/toolchain facts into `ExecutionIdentity` for provenance/proof purposes) from this package's (repeatable platform-conformance probes). Recorded as a future adapter opportunity: once ULT-01b lands, its observer could consume this package's Git/toolchain probes as one input, not the reverse. |
| PR #766 (D146 successor) / #767 (lock-receipt-shape residual) | **MEASURE_ONLY / NOT_TOUCHED** — frozen lanes; this package's git history predates and is independent of them |

## Layout

```
scripts/
  windows-conformance.py       # CLI entry point
  windows_conformance/
    __init__.py                 # SCHEMA_VERSION
    model.py                    # ProbeOutcome, stable_digest, volatile-key/path redaction
    procutil.py                 # shared real-process spawn/snapshot/cleanup helpers
    registry.py                 # aggregates every domain's probe list
    runner.py                   # executes the registry, builds the receipt
    report.py                   # renders WINDOWS-PLATFORM-REALITY.md from a receipt
    probes_process.py           # Domain A (process identity) + B (process termination)
    probes_path.py              # Domain D (path/filename reality)
    probes_links.py             # Domain E (links/reparse points)
    probes_io.py                # Domain F (atomic I/O) + G (file lock semantics)
    probes_tooling.py           # Domain H (executable resolution) + I (import provenance)
    probes_git.py                # Domain J (Git/worktree semantics)
tests/unit/test_windows_conformance_framework.py   # meta-tests + negative controls
```

## Result model

Every probe returns one `ProbeOutcome`, keeping three states distinct:

- **`result`**: `PASS | FAIL | NOT_APPLICABLE | NOT_TESTED` — whether the
  *conformance comparison* (observed platform behavior vs. Atlas's own
  contract) held. Never inferred from "the probe ran without raising".
- **`epistemic_state`**: `OBSERVED | DERIVED | UNKNOWN` — how confident the
  observation itself is. `UNKNOWN` is never silently promoted to a `result`
  of `PASS` (enforced structurally — see `test_result_never_pass_when_
  epistemic_state_is_unknown`).
- **`error`**: set only when the probe itself crashed — always a harness
  defect to fix in this mission, never a legitimate platform finding.

A permissive OS combined with a strict Atlas contract that correctly
refuses is a `PASS` ("Atlas stronger than the OS"), not a `FAIL` — a probe
only fails when Atlas's own contract accepts something genuinely unsafe.

## Receipt

`python scripts/windows-conformance.py run` writes
`generated/ops/windows-conformance/windows-conformance-receipt.json`
(`ATLAS_WINDOWS_CONFORMANCE_V1`, schema v1: repository/head/tree,
normalized host facts — OS/Python/git/PowerShell identity only, no raw
user-identifying paths — every probe outcome, and a `content_hash`) and
the derived `WINDOWS-PLATFORM-REALITY.md` human report (PROVEN COMPATIBLE
/ ATLAS-STRONGER-THAN-OS / PLATFORM DIFFERENCES / KNOWN DEFECTS / UNKNOWN
/ HIGH-VALUE FOLLOW-UPS — generated from the same result model, never
hand-maintained separately).

`content_hash` is computed over *stable* evidence only: PIDs, scratch
paths (including the random suffix `tempfile.mkdtemp()` generates, and
both the single- and doubled-backslash forms `OSError.__str__` can embed
one in), process-count timings, and racy incidental error lists are
excluded — `--repeat` runs the full harness twice and asserts the digest
matches, proving stable facts really are stable across runs of the very
same object, not merely convenient to eyeball.

## Domain-C: cleanup is proven, not trusted

Every probe that spawns a real process supplies a pre-spawn and
post-cleanup `Win32_Process` snapshot via `procutil.with_cleanup_proof` —
never inferred from a termination command's exit code. Each spawn gets a
freshly unique marker (`uuid4`-suffixed) so no probe's leak count can be
inflated by another probe's, or an earlier invocation's, residue.

**Window suppression is structural, not incidental.** Every subprocess
call in this package — not only `DETACHED_PROCESS` workloads — sets
`procutil.NO_WINDOW` (`CREATE_NO_WINDOW`). Running from a host without a
native Win32 console (an MSYS2/Git-Bash pty, for instance, which provides
no real console handle for a child process to inherit) makes Windows
allocate a brand-new, visible console window for *any* console-subsystem
child (`git.exe`, `cmd.exe`, `powershell.exe`, `taskkill.exe`, a bare
`python.exe`) that doesn't explicitly suppress it — this was a real,
user-visible incident during this package's own development, not a
theoretical one, and is why every call site is covered rather than only
the ones that seemed obviously risky at the time.

## Running it

```powershell
# one full run -> receipt + human report
python scripts/windows-conformance.py run

# twice, asserting normalized digest equivalence
python scripts/windows-conformance.py run --repeat

# meta-tests (framework mechanics + negative controls; skipped off-Windows)
python -m pytest tests/unit/test_windows_conformance_framework.py
```

No administrator elevation, no registry/global-PATH/global-package
mutation, no touching a process this harness did not itself spawn, no
persisted credentials, no writes outside a probe's own scratch root.

## Negative controls

Per-probe, load-bearing rather than decorative:

- `test_stable_digest_ignores_declared_volatile_keys` / `..._temp_path_...`
  — proves the digest is stable for the *reason claimed* (a matching value
  after normalization), and still changes when a real fact differs (a
  digest that never changes would trivially "pass" without meaning
  anything).
- `test_cleanup_proof_detects_a_genuine_leak` — spawns a process and
  deliberately does *not* terminate it, proving the leak-detection
  mechanism can see a real leak rather than always reporting `leak_free`.
- `test_path_contract_negative_control_reserved_name_still_refused` —
  proves `WIN-PATH-001`'s underlying contract call actually refuses `CON`,
  rather than trusting that it does.
- `test_result_never_pass_when_epistemic_state_is_unknown` — a structural
  invariant checked against every real probe outcome this host produces.

## Known harness-internal issues found and fixed while building this (not platform findings)

Recorded here rather than left silent, since each was initially mistaken
for something else:

1. A shared, constant leak-detection marker string let a process leaked by
   an *earlier* run silently inflate a *later* probe's leak count —
   fixed by making every spawn's marker `uuid4`-unique.
2. The `Win32_Process` enumeration query itself (a PowerShell `-Command`
   whose argument text contains the very marker string it's searching
   for) matched its own command line, making a true zero-leak result
   structurally unreachable — fixed by excluding `$PID` (the query
   PowerShell process's own).
3. `DETACHED_PROCESS` spawns without `CREATE_NO_WINDOW`, and — more
   broadly — *every* subprocess call made while running under a
   non-native-console host, popped visible console windows. See "Domain-C"
   above.
4. `OSError.__str__`'s repr-style path escaping meant the same scratch
   path could appear in evidence with either single or doubled
   backslashes depending which code path produced the string — the
   stable-digest path-redaction regex needed to match both forms.
5. The `if sys.platform == "win32": ... return` / POSIX-code-follows shape
   (this repo's own established D146/PR #769 lesson) reappeared in this
   package's own `procutil.py` and had to be corrected to `if/else` for
   the same `warn_unreachable = true` reason.

## Follow-ups (recorded, not started here)

- `WINDOWS_CONFORMANCE_CI_GATE` — wire a CI job into `.github/workflows/ci.yml`
  once PR #769's Windows mypy lane is resolved (deliberately not touched
  by this package).
- A ULT-01b adapter, once that lane merges, to let its `ExecutionIdentity`
  observer consume this package's Git/toolchain probes as one input.
- Broaden Domain F/G coverage (parallel-reader races, more sharing-flag
  combinations) if a real defect surfaces that needs it — this v1 covers
  the mission's required minimum meaningfully, not exhaustively.
