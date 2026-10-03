# ATLAS-DEVQ-0002-E2-R2: owner verification standard (2026-10-03)

Owner decision, to be applied consistently when the final repair result is verified. It is not part of the sealed package and changes no sealed byte.

**Exposure.** Location or token data is EXPOSED if it is reachable from the publicly raised `AdapterError` through any of:

- the exception message;
- `repr` / public rendering;
- `__cause__`;
- `__context__`;
- traceback-visible exception chaining.

**Not exposure.** A caught urllib `HTTPError` that temporarily contains Location data does not constitute exposure if the final `AdapterError` is raised after that exception has been fully detached and the underlying exception is no longer reachable through the raised error or the traceback chain.

**Consequence for the open review question.** An `HTTPError` that stays chained to the raised `AdapterError` (as `__cause__` or `__context__`) and carries the Location in its message, `.headers` or `.url` is reachable from the raised error, so it counts. One that was caught and detached does not.

**Required regressions.** Non-http Location scheme; malformed / invalid bracketed-host Location. The previous E2 candidate `aee2026b…` must fail the new tests and the final repair candidate must pass. Existing redirect, 404, URLError, timeout and artifact-download semantics from the inherited acceptance contract are preserved.
