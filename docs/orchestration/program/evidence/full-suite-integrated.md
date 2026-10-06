# Full repository suite — exact result at the integrated head

Recovered from the run's own captured output. Command:
`python -m pytest -p no:randomly --no-cov -rf` in the lane worktree.

```
5935 passed, 8 skipped, 4 xfailed in 350.79s (0:05:50)
PYTEST_EXIT=0
```

`-rf` prints a short summary of every failure. The captured output contains
**zero** `FAILED` lines and **zero** `ERROR` lines.

| | before integration (`b19c2298`) | integrated head |
| --- | --- | --- |
| passed | 5898 | **5935** |
| skipped | 8 | 8 |
| xfailed | 4 | 4 |
| failed | 0 | **0** |

The +37 are this mission's own tests. Package total: 141 -> 178.
