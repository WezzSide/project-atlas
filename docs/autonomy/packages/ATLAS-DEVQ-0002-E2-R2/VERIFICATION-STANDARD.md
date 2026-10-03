# ATLAS-DEVQ-0002-E2-R2: owner verification standard (2026-10-03)

Owner decision, to be applied consistently when the final repair result is verified. It is not part of the sealed package and changes no sealed byte.

**Exposure.** Location or token data is EXPOSED if it is reachable from the publicly raised `AdapterError` through any of:

- the exception message;
- `repr` / public rendering;
- `__cause__`;
- `__context__`;
- traceback-visible exception chaining.

**Not exposure.** A caught urllib `HTTPError` that temporarily contains Location data does not constitute exposure if the final `AdapterError` is raised after that exception has been fully detached and the underlying exception is no longer reachable through the raised error or the traceback chain.

## Owner decision: OPTION B (2026-10-03)

For HARDEN-DEVLOOP-001, Location/token data is exposed when it remains reachable from the publicly raised `AdapterError` through: message; `__cause__`; `__context__`; traceback-visible chaining; **attributes of an exception reachable through `__cause__` / `__context__`**.

These therefore count as exposure even when `str()`, `repr()` or the default rendered traceback does not print them: `AdapterError.__cause__.headers["Location"]`, `AdapterError.__cause__.url`, and equivalent Location-bearing state reachable via `__context__`.

"Keep 4xx/5xx unchanged" means preservation of the public/functional error contract (error class, status mapping, public message / observable API behaviour). It does not require retaining an underlying Location-bearing `HTTPError` object when retaining it violates the sealed redaction requirement. This is an interpretation of the existing sealed acceptance contract, not a new product requirement. The repair WorkItem, work seal, acceptance-contract bytes and package bytes are not changed on account of this decision.

**Attempt accounting (owner).** Attempt 3 is consumed when implementation-bearing model execution actually begins. Sealed-base assertion refusal, deterministic checkout/preflight refusal, runner provisioning failure, setup failure and provider/authentication failure before model invocation do not consume it when exact evidence proves no model request began. If the evidence cannot establish whether model invocation occurred, fail closed: treat attempt 3 as consumed. GitHub "re-run jobs" on a failed execution is not a retry mechanism.

## Required regressions (owner)

Non-http Location scheme; malformed / invalid bracketed-host Location. The previous E2 candidate `aee2026b…` must fail the new tests and the final repair candidate must pass. Existing redirect, 404, URLError, timeout and artifact-download semantics from the inherited acceptance contract are preserved.
