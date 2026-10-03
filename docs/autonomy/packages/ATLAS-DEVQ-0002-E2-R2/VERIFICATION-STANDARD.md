# ATLAS-DEVQ-0002-E2-R2: owner verification standard (2026-10-03)

Owner decision, to be applied consistently when the final repair result is verified. It is not part of the sealed package and changes no sealed byte.

**Exposure.** Location or token data is EXPOSED if it is reachable from the publicly raised `AdapterError` through any of:

- the exception message;
- `repr` / public rendering;
- `__cause__`;
- `__context__`;
- traceback-visible exception chaining.

**Not exposure.** A caught urllib `HTTPError` that temporarily contains Location data does not constitute exposure if the final `AdapterError` is raised after that exception has been fully detached and the underlying exception is no longer reachable through the raised error or the traceback chain.

## Open owner decision (not decided here)

The two paragraphs above are the owner's words. What follows is the preparer's reading of a gap, flagged by the independent v3 package review; it is a question, not a ruling.

On the repair base `aee2026b`, three **same-origin** cases keep an `HTTPError` chained to the raised `AdapterError` (`raise ... from exc`). The Location is not in its `str`, `repr` or formatted traceback, but it is held in attributes of the chained exception:

| Case | Where the Location sits |
|---|---|
| redirect loop limit (`-> 302`) | `__cause__.headers`, `__cause__.url` |
| POST 307 / 308 (urllib refuses to follow) | `__cause__.headers` |
| redirect followed, then a 4xx/5xx | `__cause__.url` |

The sealed repair instruction (`RESOLVE:P1-1 …`) covers redirects that urllib rejects **before the handler runs** and says "Keep 4xx/5xx … unchanged". A result that follows it literally leaves these three cases as they are. The instruction cannot be changed without changing the repair WorkItem seal `a5c75996…f205`.

The owner needs to say, before the result is verified, which of these holds:

- **A.** Attribute-only reachability on a chained exception in these same-origin cases is outside final acceptance (exposure means message, repr, and what a rendered traceback shows; the chain itself may remain for 4xx/5xx and same-origin 3xx).
- **B.** It is in scope. Then a literal-compliance result would fail final verification on these cases, with no bounded attempt left.

## Required regressions (owner)

 Non-http Location scheme; malformed / invalid bracketed-host Location. The previous E2 candidate `aee2026b…` must fail the new tests and the final repair candidate must pass. Existing redirect, 404, URLError, timeout and artifact-download semantics from the inherited acceptance contract are preserved.
