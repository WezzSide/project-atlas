# D-177 — Lanes G/H/I/J — QUEUE REBIND + UNIQUE_REQUIRED + #499–503

```
DIRECTIVE              = D-177
LANES                  = G (owner queue) / H (#474 IV note) / I (#471 recert) / J (unique MCP)
MODE                   = READ-ONLY
REPO                   = WezzSide/project-atlas
AS_OF_UTC              = 2026-08-25T11:45:00Z
MERGE_AUTHORIZATION    = NOT_GRANTED
NEW_PRS                = FORBIDDEN
MERGES                 = FORBIDDEN
OPTIONAL_SCOPE_EXPANSION = FORBIDDEN
```

`CI_PASS ≠ AUTHORIZED`. Tip Model B clean ≠ certified for #474. Tip OID unchanged ≠ #471 recert waived.

---

## LIVE_MAIN pin (post-#504)

| Field | Expected | Observed | Match |
| --- | --- | --- | --- |
| MERGE_COMMIT / HEAD | `a17949c6df9b4d004ffe03eb47b0934e3735204d` | `origin/main` = same | **YES** |
| TREE | `e646392c12fa525dcfd017c33e1b6226c5bfb40a` | `git rev-parse a17949c6…^{tree}` | **YES** |
| #504 tip HEAD | `303f565759467a7c2d5a119563ce298f081be7d3` | `gh pr view 504` | **YES** |
| #504 state | MERGED | MERGED @ 2026-08-25T11:30:54Z | **YES** |
| #504 product delta | `authentic_estate.py` + D149 tests + remediation evidence | vs pre-main `f65e94f3…` | confirmed |
| POST_MERGE_CI | required for seal | run `32842710975` **in_progress** (control-plane SUCCESS; quality jobs running) | **NOT SEALED YET** |

```
PRE_MAIN_HEAD          = f65e94f3f2dcf0cee96cd9932069792e320032de
POST_MAIN_HEAD         = a17949c6df9b4d004ffe03eb47b0934e3735204d
POST_MAIN_TREE         = e646392c12fa525dcfd017c33e1b6226c5bfb40a
#504_PRODUCT_OVERLAP_471_475 = NONE
```

---

## Tip pin verification (live `gh` + `git`)

| PR | Live HEAD | Live TREE | Unchanged vs D-174/D-175/D-176? | behind main |
| --- | --- | --- | --- | --- |
| **471** | `21e8c279c47fe29f0d70d4593ee324d5f5aa9d56` | `04f9ba2a02521c00b060e9930c201af2ca3ce41c` | **YES** | 15 |
| **472** | `0a933607001b2f6bb7e3c1842ac02bb4fc84a781` | `47cc68dbfa5754f5935927ccfd56d1cd82ef95d9` | **YES** | 15 |
| **473** | `566f19d87ce05c983654b11740bd7197cb3c9dd4` | `c937cda4aa867f20dedb6626fbf65342cf760da5` | **YES** | 15 |
| **474** | `68201eb0801eec50e5e5d44ddc73b05c9a967569` | `e06d2a8e0a8e85e5a19d8f2c71ed90c7531f3ade` | **YES** | 15 |
| **475** | `0ef155bb8cfd2a2d8539abca54986ab1f888cd19` | `8a5d8ba8e68c340b3e9cc1293f2e0206742969f2` | **YES** | 15 |

---

## Model B merge-tree vs post-#504 main

Three-way: `git merge-tree --write-tree --messages <POST_MAIN> <tip>`

| PR | MERGE_TREE_CLEAN | Merge-tree OID / conflict | Product overlap #504 | MODEL_B tip pin | RECERT after #504? |
| --- | --- | --- | --- | --- | --- |
| **471** | **NO** | CONFLICT `WORKLOG.md` only | NO | OID/TREE unchanged | **FULL RECERT REQUIRED** (directive; not waived by clean product / Model B ancestry) |
| **472** | **YES** | `e91ae1b5e593891b6972b3183a707622e5d9a9b8` | NO | VALID | **NO** (tip OID/TREE unchanged; no #504 product overlap) |
| **473** | **YES** | `6216555e0ba61754ff888ff4ed938df9512da98b` | NO | VALID | **NO** |
| **474** | **YES** | `9a10a9a5e5ebe4033dbbf69a0a78c0a592a7a3bd` (auto-merge `cli.py`; no content conflict) | NO | VALID tip pin | **NO tip RECERT** for #504 movement — **but `#474_CORE_DIFF_IV = INCOMPLETE` → NOT CERTIFIED** |
| **475** | **YES** | `b3bb3201b73dcc0a891517308a13553937abde7f` | NO | VALID (scoped) | **NO** |

```
MODEL_B_ROLLUP_AFTER_504 =
  Tips 472/473/474/475: merge-tree CLEAN; tip OID/TREE unchanged → Model B ancestry rebind OK
  Tip 471: WORKLOG-only conflict (same class as post-476); product tip OID unchanged
  #504 did not invalidate tip-bound CI/IV bindings by path overlap

RECERT_NEEDED_BECAUSE_OF_504_ALONE = NO for #472/#473/#474/#475 tip movement
#471_FULL_RECERT                   = REQUIRED (D-177 directive — substantial freshness absent from main;
                                     prior IV @ 375301b4 INVALIDATED; exact-head Claude IV for 21e8c279
                                     still outstanding)
#474_CERTIFIED                     = NO
#474_CORE_DIFF_IV                  = INCOMPLETE
```

GitHub mergeable (live): #471 CONFLICTING/DIRTY; #472/#473/#474/#475 MERGEABLE/BLOCKED (protection).

---

## DEMO_CRITICAL classification (#471–#475)

Criterion (D-177 §5): without it, a major advertised Atlas 2.x capability cannot be demonstrated through its intended product surface.

| PR | Class | Why |
| --- | --- | --- |
| **472** | **DEMO_CRITICAL** | Architecture is demo-journey stage 7; live main has lens module but **no** `GET /v1/architecture` / `web_api/architecture.py`. #472 is the intended LIVE_API product surface (+ secret redaction). |
| **473** | **NOT_DEMO_CRITICAL** | Workflow-metrics compiler is core-truth/ops telemetry; not a demo-journey stage; demo can proceed without ops metrics honesty wrap. |
| **474** | **DEMO_CRITICAL** | Windows cp1252/cp850 `atlas attention` human output encoding (SHADOW-C-002). Demo host is win32; without `terminal_io.human_print`, attention path can fail user-visible Unicode. |
| **471** | **DEMO_CRITICAL** | Agent context/handoff freshness ADV (D-177 Lane E). Prevents frozen packs silently reading current after estate/manifest drift — required for honest agent-resume demo. |
| **475** | **NOT_DEMO_CRITICAL** | Isolation ADV **test harness** only; claim-scope wording fix planned; does not add a missing product surface. |

```
#472_STATE = tip CERTIFIED_PENDING_OWNER_AUTHORIZATION preserved under Model B
             (D-170-CLAUDE-IV-472 @ 0a933607…; CI 32767991081 SUCCESS exact-head)
             merge-tree CLEAN vs post-504 main; MERGE_AUTHORIZATION = NOT_GRANTED

#473_STATE = tip technically certified under Model B (D-170-CLAUDE-IV-473-R2);
             NOT_DEMO_CRITICAL → do not prioritize for demo distance

#474_STATE = NOT CERTIFIED / NOT CERTIFIED_PENDING_OWNER_AUTHORIZATION
             #474_CORE_DIFF_IV = INCOMPLETE (D-176 Lane B scope still required)
             Model B merge-tree CLEAN ≠ certification

#471_STATE = FULL RECERT REQUIRED (code + IV + WORKLOG rebind); not metadata-only
             WORKLOG conflict vs post-504 main remains

#475_STATE = production tip valid under Model B; SECRET_ECHO claim-scope correction
             still PLAN_RECORDED (D-176 Lane C); NOT_DEMO_CRITICAL
```

---

## Lane H — #474 CORE DIFF IV (status only; no IV executed here)

```
#474_CORE_DIFF_IV      = INCOMPLETE
#474_CERTIFIED         = NO
DO_NOT_CALL_CERTIFIED  = YES

IV_SCOPE_STILL_REQUIRED =
  - terminal_io.py / human_print / adapt_human_text
  - attention-command wrapping (cli.py human_print path)
  - cp1252 / cp850 / UTF-8 streams
  - redirected output
  - JSON payloads must NOT route through human_print
  - unmapped Unicode → backslashreplace (never errors=ignore)

OUTSIDE_DELTA          = IV-474-P2-01 argparse --help UnicodeEncodeError (still P2; do not fold)
```

---

## Lane I — #471 FULL RECERT plan (required)

```
TRIGGER                = D-177 directive (not optional WORKLOG tidy)
TIP_HEAD               = 21e8c279c47fe29f0d70d4593ee324d5f5aa9d56
TIP_TREE               = 04f9ba2a02521c00b060e9930c201af2ca3ce41c
PRODUCT_ABSENT_ON_MAIN = estate_binding / freshness lens on agent_handoff (confirmed absent)
PRIOR_IV               = @ 375301b4 INVALIDATED — do not reuse
WORKLOG                = CONFLICT vs post-504 main (rebind on recert carrier or pre-merge)

RECERT_CHECKLIST       =
  1. Resolve/rebind WORKLOG.md onto post-504 main (tip rewrite → new HEAD/TREE)
  2. Exact-head CI SUCCESS on new tip
  3. Independent Claude IV on exact new HEAD (freshness ADV gates)
  4. Only then CERTIFIED_PENDING_OWNER_AUTHORIZATION may be claimed
  5. #419 remains DEPENDENCY_GATED_ON_471_INTEGRATION (no activation now)
```

---

## Lane J — UNIQUE_REQUIRED supersession leftovers

Live main MCP registry tools (post-504): `atlas.brief.read`, `atlas.compat_anchor`, `atlas.estate.scan`, `atlas.explain.receipt.read`, `atlas.knowledge.query.read`, `atlas.ops.health.read`, `atlas.projects.list.read`, `atlas.provider.generate`, `atlas.schema`, `atlas.vault.write` — **no** graph/mission/workspace/project-attention/unknown/changed MCP.

HTTP on main already covers: `/v1/graph`, `/v1/mission`, `/v1/workspace`, `/v1/project-attention`. CLI covers `unknown` / `changed` / `attention`. Unknown/changed HTTP+web remain on open draft **#482** (not landed).

| Unique item | Source | Class | Why |
| --- | --- | --- | --- |
| MCP `atlas.graph.read` | #490 leftover (not in #480+#492) | **UNIQUE_REQUIRED_NON_DEMO** | Graph demonstrable via `/v1/graph` + `web_api/graph.py` on main; MCP is parity wrapper |
| MCP `atlas.mission.read` / `atlas.workspace.read` | #490 leftover | **UNIQUE_REQUIRED_NON_DEMO** | HTTP `/v1/mission` + `/v1/workspace` already on main |
| MCP `atlas.project-attention.read` | #485 leftover | **UNIQUE_REQUIRED_NON_DEMO** | HTTP `/v1/project-attention` + CLI `attention` on main |
| MCP `atlas.unknown.read` / `atlas.changed.read` | #479 leftover (not in #482 registry) | **DEFER_3X** | CLI `unknown`/`changed` demo the honesty lenses; HTTP/web via #482 when that umbrella is demo-scoped; MCP leftover does not shorten demo distance |

```
IMPLEMENTATION_CARRIERS_FOR_UNIQUE_MCP = FORBIDDEN unless reclassified DEMO_CRITICAL
DEMO_CRITICAL_UNIQUE_MCP_NOW           = 0
```

---

## #499–#503 expansion classification

Live tips fetched; all OPEN; all **WORKLOG.md CONFLICT** vs post-504 main (merge-tree dirty). Each adds vault-scoped read lens + web page + MCP + `demo_readiness.py` touch — must **not** auto-expand frozen demo scope.

| PR | Title (short) | Class | Why |
| --- | --- | --- | --- |
| **499** | receipt-revocation read lens | **DEFER_AFTER_DEMO** | Honesty wrap over AS-INT-011 index; not a demo-journey blocker |
| **500** | incremental-connect receipt read | **DEFER_AFTER_DEMO** | Ops receipt inspector; connect already demoable without this lens |
| **501** | inventory-drift read lens | **DUPLICATIVE** | Drift stage already consumed via `evaluate_connect_inventory_drift` / demo_readiness; PR is first-class wrapper surface |
| **502** | event-tombstone read lens | **DEFER_AFTER_DEMO** | AS-INT-010 visibility; optional after core demo |
| **503** | conversation-capture read lens | **DEFER_AFTER_DEMO** | Quarantine inventory; not Truth Core / not demo-critical |

```
#499_503_DEMO_CRITICAL_COUNT = 0
#499_503_MAY_DELAY_DEMO      = NO — classify and park; do not prioritize vs #472/#474/#471
```

---

## Owner priority after #504 (demo distance)

| Priority | PR | DEMO? | Certifiable now? | Blocker |
| --- | --- | --- | --- | --- |
| 1 | **472** | DEMO_CRITICAL | **YES** (tip IV+CI bind; Model B clean) | owner auth only (+ prefer post-504 CI seal green) |
| 2 | **474** | DEMO_CRITICAL | **NO** | `#474_CORE_DIFF_IV` incomplete |
| 3 | **471** | DEMO_CRITICAL | **NO** | FULL RECERT + WORKLOG conflict |
| — | 473 | NOT_DEMO_CRITICAL | tip yes | out of demo-critical path |
| — | 475 | NOT_DEMO_CRITICAL | tip scoped | claim-scope plan only |
| — | 499–503 | not demo-critical | no (WORKLOG dirty) | defer/duplicative |

---

## ONE next owner PR recommendation

```
NEXT_RECOMMENDED_OWNER_PR    = 472
NEXT_RECOMMENDED_OWNER_HEAD  = 0a933607001b2f6bb7e3c1842ac02bb4fc84a781
NEXT_RECOMMENDED_OWNER_TREE  = 47cc68dbfa5754f5935927ccfd56d1cd82ef95d9
MERGE_TREE_VS_POST_504       = e91ae1b5e593891b6972b3183a707622e5d9a9b8 (CLEAN)
WHY                          = Only DEMO_CRITICAL queue item that is tip-certifiable
                               under Model B after #504 (architecture LIVE_API missing
                               on main; Claude IV + exact-head CI preserved).
MERGE_AUTHORIZATION          = NOT_GRANTED
POST_504_CI_SEAL             = INCOMPLETE (run 32842710975 in_progress) — seal before
                               treating main as demo-integration base if policy requires
                               POST_MERGE_CI=PASS first.
```

If owner refuses #472 until seal: **NONE READY** among remaining demo-critical items until:

- **#474:** independent CORE DIFF IV PASS on exact `68201eb0…` (or new tip if rewritten)
- **#471:** FULL RECERT (WORKLOG rebind → new HEAD/TREE → CI → Claude IV)

```
ALTERNATE_IF_472_BLOCKED = NONE READY
IV_NEEDED                = #474_CORE_DIFF_IV and/or #471 FULL RECERT
```

---

## Compact rollup

```
LANE_G = REBOUND (#471–#475 tips live; Model B clean for 472–475; 471 WORKLOG conflict)
LANE_H = #474_CORE_DIFF_IV=INCOMPLETE; NOT CERTIFIED
LANE_I = #471 FULL RECERT REQUIRED (plan recorded; not executed)
LANE_J = unique MCP → NON_DEMO ×3 + unknown/changed MCP DEFER_3X; carriers forbidden

#499_503 = 0 DEMO_CRITICAL (defer/duplicative)

NEXT_OWNER_PR = #472 @ 0a933607… / 47cc68db…
MERGE_AUTHORIZATION = NOT_GRANTED
```

Evidence cross-refs: `D-176-PARALLEL-LANES.md`, `D-175-POST476-QUEUE.md`, `D-174-MODEL-B-QUEUE-MATRIX.md`, `D-170-CLAUDE-IV-472.md`.
