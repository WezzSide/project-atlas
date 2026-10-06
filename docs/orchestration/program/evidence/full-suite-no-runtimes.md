# Full suite with neither runtime on PATH (reproduces CI)

CI has neither `claude` nor `codex` installed. Six of this package's tests
assumed they were, and passed locally for exactly that reason.

Reproduced by removing `~/.local/bin` and `~/.local/npm/bin` from PATH:

```
5937 passed, 10 skipped, 4 xfailed in 336.51s (0:05:36)
PYTEST_EXIT=0
```

Zero `FAILED`, zero `ERROR`, `PYTEST_EXIT=0`. The two extra skips against the
runtime-present run are the version-dependent capability assertions, which
now skip when the runtime is genuinely absent instead of failing.
