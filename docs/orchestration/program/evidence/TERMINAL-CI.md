# Terminal CI

## `4c5e0ffc` — the head containing the PATH fix

```
success  quality (ubuntu-latest, 3.12, full)
success  quality (windows-latest, 3.12, windows)
success  quality (ubuntu-latest, 3.13, compat)
success  control-plane
```

CI checkout subject: merge ref `9dc135dc`, **tree `73b1ac8a`** — identical
to the head's own tree, so CI tested exactly this content rather than a
merge that had drifted.

## `9962a672` — before the fix, for the record

```
failure    quality (ubuntu-latest, 3.12, full)      11 failed, 5926 passed
failure    quality (ubuntu-latest, 3.13, compat)    same 6 test functions
cancelled  quality (windows-latest, 3.12, windows)  superseded before it finished
success    control-plane
```

**Windows was never validated at the pre-fix head** — its job was cancelled,
which is not a result. Its first terminal verdict on this branch is the
`success` above.

Every failure was lane-owned and had one cause: six test functions assumed
`claude` and `codex` were on PATH. No unrelated failure appeared on either
job.
