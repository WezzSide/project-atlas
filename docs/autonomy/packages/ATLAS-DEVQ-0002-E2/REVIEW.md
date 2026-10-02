# ATLAS-DEVQ-0002-E2: independent package reviews

These reviews were run by fresh sessions, separate from the preparer. They were read-only and dispatched nothing.

| Version | package_sha256 | Result |
|---|---|---|
| v1 (commit `99689171`) | `54a81ccbc9afaf75d3d602dbd224016a605cf7f5949326423188a2044b276565` | PASS, P0 0 / P1 0, with P2s. Main issues: POST 307/308 tests would not fail on the base; exception-chain redaction; portability; turn budget. **Superseded, never dispatched** |
| **v2 (current)** | **`b8035f9b0cb24e6ff9423b466e117d0dc8c8577eac6835147df5ceddb1bf62fe`** | **PASS, P0 0 / P1 0** |

## v2 evidence
- **Identity and determinism.** The package rebuilds byte-identically from the committed spec using the `dev_package.py` that is on `main` (blob `c2ca7995…`). The sealed base `64195b71` equals `origin/main`. The MANIFEST digests all recompute.
- **Statement.** Every owner requirement for HARDEN-DEVLOOP-001 is present, with no repair language and "attempt 2/3".
- **Feasibility, using a scratch reference fix that was never committed:**
  - On the base: the 5 GET cross-origin tests (301/302/303/307/308) fail, and the preservation tests pass.
  - With the fix: all four acceptance commands pass.
  - The refused redirect is raised `from None` and reveals no Location or query data.
- **Command contract.** Every acceptance command passes `dev_package` checks. Its first token is in the executor allowlist on `main` (`pytest`, `ruff`, `mypy`).

## Residual risks (P2)
- **Turn budget.** The workflow wrapper also asks the agent to "run the infra runner test suite", and `--max-turns 20` is still in force. E1 used 25 turns. If E2 trips the envelope, #1043 preserves the candidate, but the run stays red and the result cannot be ingested automatically (see `README.md`).
- **Portability.** Windows and Python 3.13 portability will be shown only by the required CI jobs.
