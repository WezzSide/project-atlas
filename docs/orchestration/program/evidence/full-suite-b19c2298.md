# Full repository suite — exact result at b19c2298

Recovered from the run's own captured output, not from a handoff summary.
Command: `python -m pytest -p no:randomly --no-cov -rf` in the lane worktree.

```
......                                                                   [100%]
5898 passed, 8 skipped, 4 xfailed in 338.55s (0:05:38)
PYTEST_EXIT=0
```

`-rf` prints a short summary of every failure. The captured output contains
**zero** `FAILED` lines, **zero** `ERROR` lines and no "short test summary"
section, which is what a clean run looks like with that flag. `PYTEST_EXIT=0`.

This total predates the `cli.py` registration; see
`full-suite-integrated.md` for the re-run at the integrated head.
