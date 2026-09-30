# Safety contract

Source projects are evidence.

Fail closed on:

- MOVE / DELETE / RENAME / SOURCE_MODIFY
- GIT_CLEAN / GIT_RESET / HISTORY_REWRITE
- AUTO_COMMIT / AUTO_PUSH / AUTO_MERGE
- COPY / GOLDENIZE without a later owner-authorized implementation
- UNC paths
- path traversal (`..`)
- symlink / junction escape
- report output written inside the source root
- execution of project test or build scripts
- execution of repo-local git helpers (`core.fsmonitor`, filters, hooks, `diff.external`)
- treating failed or unreadable git inspection as clean / GOLDEN

Secrets: record pattern metadata only. Never echo matched content.

Report-relative identities use `/` on every OS (`REPORT_RELATIVE_IDENTITY = POSIX_STYLE`).
Absolute filesystem access stays platform-native.

Inaccessible files, directories, junctions, and reparse points are skipped with
`INACCESSIBLE_PATH` and siblings continue. Root-level source-root failure stays
terminal.

```text
INACCESSIBLE != SAFE
INACCESSIBLE != GOLDEN
SKIPPED != SCANNED
PARTIAL_DISCOVERY != COMPLETE_DISCOVERY
```
