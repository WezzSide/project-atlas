# D-063 — Local D-049 revalidation freeze

**Directive:** D-PROJECT-ATLAS-CLOUD-KNOWLEDGE-ESTATE-DISCOVERY-063  
**PR:** https://github.com/WezzSide/project-atlas/pull/346

## Frozen tip (NO TIP MUTATION after this receipt)

Local validates this exact hardened implementation tip:

```
PR_346 = https://github.com/WezzSide/project-atlas/pull/346
PR_346_HEAD = 9c71cc2c71779678f79037c0c279390355015d63
PR_346_TREE = 10539a861dc9a5b32ebf00862d6710a66f3725cd
LOCAL_D049_REVALIDATION_READY = YES
```

A docs-only freeze receipt commit may sit on top of this tip on the PR branch.
If the branch tip differs, Local must still verify against the HEAD/TREE above
(`git checkout 9c71cc2c71779678f79037c0c279390355015d63`).

Base:

```
BASE = 072f1395ee310a876e93d633264f3ece43cecc3c
```

## Cloud status at freeze

```
CODER_ALPHA_ACCEPTANCE = PASS
D_049_EXECUTION_GATE = OPEN
D_049_STATE = IN_PROGRESS
D_049_ACCEPTANCE = NOT_YET_EVALUATED
D_042_EXECUTION_GATE = CLOSED
HIGH_OPEN = 0
```

Independent IV (Cloud):

```
CAN DISCOVERY CAUSE TWO DISTINCT PROJECTS AS ONE = NO
CAN DISCOVERY LABEL CONNECTED WITHOUT BIND PROOF = NO
CAN STALE REPORT BYPASS IDENTITY TRUTH = NO
CAN DISCOVERY ESCAPE AUTHORIZED ROOT = NO
CAN PARTIAL SCAN LOOK COMPLETE = NO
```

## Local IV scope (Windows / adversarial only)

1. exact frozen HEAD/TREE
2. junction / reparse escape
3. long paths / spaces / Unicode
4. Windows path case aliases
5. multi-project discovery recall
6. same-name / copied-marker isolation
7. ambiguous/conflicting matching
8. stale-report connect
9. Obsidian boundary
10. ignore policy
11. bounded scan honesty
12. fresh stranger CLI/Web journey

Do not request Local broad historical Coder Alpha replay.  
Do not merge Wave 1 until Local Windows IV completes.
