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

"Keep 4xx/5xx unchanged" means preservation of the public/functional error contract (error class, status mapping, public message / observable API behaviour). It does not require retaining an underlying Location-bearing `HTTPError` object when retaining it violates the sealed redaction requirement. This is an interpretation of the existing sealed acceptance contract, not a new product requirement. When this decision was made the owner directed that the repair WorkItem, work seal and package bytes not change on its account (v3). The owner then separately authorized exactly one re-seal (`AUTHORIZE_ATLAS_DEVQ_0002_E2_R2_RESEAL`, 2026-10-03) to put the detach rule into the executor-visible contract; that is v4 (work seal `04da4ab26b13c2fdda5e0fbeb54ca74987dc4a6114190276115e505720e76b92`, package `b449999a1801456c9e9bcaa49c40410e85965e316b852ee569e8fd56338a6835`).

**Attempt accounting (owner).** Attempt 3 is consumed when implementation-bearing model execution actually begins. Sealed-base assertion refusal, deterministic checkout/preflight refusal, runner provisioning failure, setup failure and provider/authentication failure before model invocation do not consume it when exact evidence proves no model request began. If the evidence cannot establish whether model invocation occurred, fail closed: treat attempt 3 as consumed. GitHub "re-run jobs" on a failed execution is not a retry mechanism.

## Required regressions (owner)

Non-http Location scheme; malformed / invalid bracketed-host Location. The previous E2 candidate `aee2026b…` must fail the new tests and the final repair candidate must pass. Existing redirect, 404, URLError, timeout and artifact-download semantics from the inherited acceptance contract are preserved.

## Verification notes for the final result (preparer's proposal, for the owner to confirm)

- Accept either message for a redirect whose Location urllib rejects itself (non-http scheme): `refused …` naming only method and path, or the same `-> <status>` message. The sealed prompt supports both readings.
- Apply "new tests must fail on `aee2026b`" to the tests that resolve P1-1 and P1-2; preservation tests are expected to pass on the base.
- `read_json_artifact` is treated as outside the sealed repair scope.
