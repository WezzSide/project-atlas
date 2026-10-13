# AS-ORCH-PRIMARY-LOCK-IDENTITY-CONTRACT-001

Canonical technical contract for the primary-governor lock and its identity
layer in `project_atlas.orchestration.sdk.resident_driver` (the OS-lock
primitive lives in `project_atlas.orchestration.sdk.os_lock`). Produced by
`D-CODEX-ATLAS-WINDOWS-PRIMARY-LOCK-HARDENING-AND-INTEGRATION-READINESS-002`
on top of the atomic-lock fix for #773 and the identity-binding remediation
for R1/R2/R3 (PR #780). Scope is deliberately narrow: this document
describes the lock/identity contract only, not the resident driver's wider
scheduling behavior (see `AS-ORCH-SELF-WAKE-RESIDENT-DRIVER-001.md` for
that).

## Core laws

```
LOCK_FACT != HOLDER_IDENTITY
  "is a lease held" and "who confirmably holds it" are two different
  questions with two different answers -- see PrimaryLockState.

RECEIPT != AUTHORITY
  The JSON receipt (resident-primary-receipt.json) is observability only.
  It is never consulted to decide exclusivity. The OS lock
  (resident-primary.lock, via os_lock) is the sole arbiter of
  ACTIVE_PRIMARY_GOVERNOR_COUNT <= 1.

HELD + IDENTITY UNKNOWN
  is a valid, expected, first-class state. It must never collapse to
  either "no holder" or a fabricated/sentinel PID.

PID 0 (from read_primary_lock_pid())
  means "not confirmed" -- covers BOTH "no holder" and "held, identity
  unknown". It does NOT mean "unheld". Callers that must tell those apart
  use read_primary_lock_state().held instead.

LOCK ACQUISITION SUCCESS
  does not depend on receipt publication success. acquire_primary_lock()
  returns True the moment the real OS lock is won, regardless of whether
  _publish_receipt() subsequently succeeds.

RECEIPT PUBLICATION FAILURE
  degrades observability (identity reads back UNKNOWN), never lock
  ownership. _publish_receipt() unlinks the previous receipt FIRST, so a
  failed publish leaves the receipt ABSENT, never a stale prior holder's
  content.

STALE RECEIPT
  must never become confirmed identity for a DIFFERENT current holder.
  read_primary_lock_state() brackets its receipt read between two lock
  probes specifically to catch this (see "Reader races" below).

CALLERS THAT MAY SPAWN A GOVERNOR
  must gate on read_primary_lock_state().held, never on
  read_primary_lock_pid() > 0. PID UNKNOWN != SAFE TO SPAWN.
```

## State model

| State | OS lock | Receipt | `read_primary_lock_state()` |
|---|---|---|---|
| `UNHELD` | free | absent/stale, irrelevant | `held=False, pid=None, UNKNOWN` |
| `HELD_IDENTITY_CONFIRMED` | held | parses, positive pid, pid alive | `held=True, pid=<pid>, CONFIRMED` |
| `HELD_IDENTITY_UNKNOWN` | held | any state below | `held=True, pid=None, UNKNOWN` |
| `HELD_RECEIPT_MISSING` | held | absent | → `HELD_IDENTITY_UNKNOWN` |
| `HELD_RECEIPT_MALFORMED` | held | not valid JSON / not an object | → `HELD_IDENTITY_UNKNOWN` |
| `HELD_PID_INVALID` | held | parses, `pid` field not a positive int | → `HELD_IDENTITY_UNKNOWN` |
| `HELD_PID_DEAD` | held | parses, positive pid, but not alive | → `HELD_IDENTITY_UNKNOWN` |
| `PUBLICATION_FAILED` | held | absent (unlinked, write failed) | → `HELD_IDENTITY_UNKNOWN` |
| `TURNOVER_RACE_WINDOW` | held (by new holder) | stale (previous holder's, not yet overwritten) | → `HELD_IDENTITY_UNKNOWN` (bracket probe; see residual risk) |
| `CRASHED_OWNER` | free (OS released it) | stale, irrelevant | `held=False, pid=None, UNKNOWN` (same as `UNHELD`) |
| `RELEASE_IN_PROGRESS` / just after | free (release drops the fd) | left in place, unmodified | `held=False, ...` -- receipt is never touched by `release_primary_lock()` |

`acquire_primary_lock()` transitions: `UNHELD → HELD_IDENTITY_CONFIRMED`
(publish succeeds) or `UNHELD → PUBLICATION_FAILED` (i.e.
`HELD_IDENTITY_UNKNOWN`, publish failed) -- both return `True`, the OS
lock is won either way. A second call from the SAME process while still
held is idempotent-True via an in-process fd registry (`_HELD_LOCK_FDS`),
short-circuiting before any OS call.

`release_primary_lock()` transitions any held state → `UNHELD`. Idempotent
and safe to call even when this process never held the lock.

A crashed holder (`SIGKILL`/`TerminateProcess`, no `release_primary_lock()`
call) transitions `HELD_* → UNHELD` automatically: the OS releases the
lock the moment the process's file descriptors close, with no stale-lock
detection or reclamation protocol needed, and none possible to get wrong.

## Reader races (bracket probe)

`read_primary_lock_state()` probes the OS lock, THEN reads the receipt,
THEN probes again, and only trusts the receipt if the probe BEFORE the
read agreed the lock was held. This closes the more probable turnover
race (a receipt from a holder who had already released, read while the
lock happened to be genuinely free at the read's start) by reporting
`UNKNOWN` rather than misattributing.

**Documented residual risk**: an adversarial-enough interleaving --the
previous holder releases and a new one re-acquires within the gap between
the two probes, and the receipt read lands exactly inside the new
holder's own (very small) unlink-to-publish window inside
`_publish_receipt()` -- can in principle still misattribute the OLD
holder's PID to the NEW holder's lease. This requires two independent,
sub-microsecond-scale timing coincidences to land simultaneously. It only
ever affects the observability `pid`/`identity` fields, never `held`
(which is what actually enforces `ACTIVE_PRIMARY_GOVERNOR_COUNT <= 1`).
Fully closing it would require binding identity to a lock-generation
token published atomically with the OS lock acquisition itself --
deliberately not implemented: `os_lock`'s handle-lifetime-based design
does not carry such a token, to keep the lock primitive itself
content-agnostic and the receipt genuinely optional. Not reproduced in
real wall-clock time in this mission; reproduced only via scripted
`probe_is_locked()` injection (see
`test_reader_race_bracket_rejects_receipt_from_before_a_gap`), which is
what the bracket is proven to catch.

## PID reuse

Identity confirmation requires the receipt's `pid` field to be both
positive AND currently alive (`pid_is_alive`). Because `_publish_receipt`
unlinks the previous receipt before writing a new one, an ordinary
release-then-reacquire cycle can never leave a stale receipt whose PID
was later reused by an unrelated process sitting next to a genuinely-held
lock. The one path where this remains possible: the receipt's own
`unlink()` call itself fails (not merely the subsequent write) AND the
stale PID it names is later reused by an unrelated live process AND the
lock is, separately, genuinely held by someone else. This stacks three
independent, individually rare conditions; deferred as a documented
residual rather than implemented, given it only affects an observability
field and `unlink()` failing on a small, not-externally-held file is
itself an exceptional filesystem condition this module does not attempt
to reason about further. An age-based (`at` timestamp) staleness check
was considered and rejected: the receipt is written once at acquisition
time, not refreshed periodically, so an age check would false-negative a
legitimate long-lived holder unless paired with a periodic refresh --
out of this contract's narrow scope.

## Platform model

- **Windows**: `os_lock` uses `msvcrt.locking()`, a *mandatory* (not
  advisory) byte-range lock on byte 0 of `resident-primary.lock`. Any I/O
  touching that byte range -- even a plain read from the SAME process via
  a different handle -- is denied while held. This is why the receipt
  lives in a wholly separate file: sharing one file with the lock made
  the receipt unreadable while held (discovered mid-mission; see PR #780
  history).
- **POSIX**: `os_lock` uses `fcntl.flock()`, whole-file advisory locking
  tied to the open file description. A second `open()` in the SAME
  process (a different fd/open file description) is treated as a
  different lock owner, same as a different process -- `os_lock`'s
  in-process idempotency (`_HELD_LOCK_FDS`) is what makes same-process
  reacquisition work correctly regardless of this, by never issuing a
  second OS-level lock call for a lease this process already holds.
- **Both**: the lock is tied to an open file descriptor's lifetime --
  process exit (including a crash) closes the fd and releases the lock
  automatically. `pid_is_alive()` (used only for identity confirmation,
  never for exclusivity) shells out to `tasklist` on Windows and calls
  `os.kill(pid, 0)` on POSIX; the Windows path is a real subprocess spawn
  (~150-200ms measured, see "Performance" below), always with
  `no_window_creationflags()`.
- **Cross-environment boundary (documented, not fixed)**: a Windows-native
  process and a process accessing the same underlying file through WSL's
  9P/DrvFs bridge are NOT guaranteed to share OS-level byte-range locks
  with each other. This repository's own Windows and Linux/WSL validation
  runs always use separate roots for exactly this reason; Atlas does not
  claim simultaneous same-root coordination across that specific
  boundary.

## Security / trust boundary

Receipt content never decides lock ownership, which already bounds the
impact of any receipt tampering to observability, never exclusivity --
`ACTIVE_PRIMARY_GOVERNOR_COUNT <= 1` cannot be broken by writing to the
receipt file, only the OS lock can grant that, and that requires real OS
permission to lock the file.

**Genuine finding, not fixed (documented residual)**: the receipt's `pid`
field is not bound to the identity of the process that is actually
holding the OS lock at read time -- `read_primary_lock_state()` confirms
identity from two independently-checked facts (the OS lock is held by
*someone*; the receipt names *some* positive, currently-alive PID), not
from a proof that they are the same process. A co-located process that
already has filesystem write access to Atlas's own runtime state
directory (i.e., is already inside Atlas's trust boundary -- in practice,
the same OS user account) can overwrite `resident-primary-receipt.json`
with an arbitrary *live* PID while a different, real process holds the
actual lock, causing this function to report a CONFIRMED identity for
the wrong process. This is not exploitable by an attacker outside that
trust boundary (no filesystem write access, no effect), and does not let
a second process become primary -- it only makes the observability `pid`
field lie about *which* real process the (still-real, still-exclusive)
lock belongs to. A local process with this level of access could already
do far more damage to Atlas's other state; this is not treated as a
priority fix, and is recorded here as the trust-boundary analysis
Workstream L asked for. A future mitigation, if ever warranted, would
bind the receipt to a token only the actual OS-lock holder could produce
(the same lock-generation-token idea already deferred for the reader-race
residual above) -- not implemented now.

Malformed/hostile receipt *shapes* (non-dict JSON, negative/huge/non-int
`pid`, empty file) are all already handled -- see the state table above --
without ever producing a false `CONFIRMED` or a crash from this module's
own read path (issue #767's own parser is a separate, narrower concern;
not implemented here, see that doc/issue).

A malicious symlink substituting the lock file path itself is a general
"an attacker with write access to your state directory can redirect your
own file operations" concern, not specific to this lock design; the same
trust-boundary reasoning above applies and no additional mitigation is
implemented here.

## Path identity

The OS lock is scoped to the underlying file object, not the path string
used to open it -- two different spellings of the same path (case
differences on Windows' default case-insensitive NTFS, a POSIX symlink)
that the OS itself resolves to the same file are correctly mutually
exclusive regardless of spelling, because arbitration happens at the OS
level, not in this module's Python code. The only place a path-spelling
difference matters is the in-process same-process-idempotency registry
(`_HELD_LOCK_FDS`, keyed by `Path.resolve()`); every current production
caller always passes the identical `root` value across its own
acquire/release calls, so this is not a live risk with today's callers.

## Performance (measured, native Windows, no contention)

| Operation | Latency |
|---|---|
| `read_primary_lock_state()`, unheld | ~0.17 ms |
| `read_primary_lock_state()`, held + malformed/no receipt (no `pid_is_alive` call) | ~1.7 ms |
| `read_primary_lock_state()` / `read_primary_lock_pid()`, held + confirming a real PID | ~180-195 ms (dominated by `pid_is_alive`'s `tasklist` subprocess spawn) |
| `acquire_primary_lock()`, already held (idempotent, in-process short-circuit) | ~0.75 ms |
| `acquire_primary_lock()` + `release_primary_lock()`, fresh cycle | ~5.1 ms |

The confirming-identity path is the only one with meaningful latency, and
it is bounded (one subprocess spawn, not a retry loop). Watchdog-style
callers polling every 0.5s (`ensure_resident_alive`) absorb this
comfortably; a caller polling much faster should be aware of it.

## Caller guidance

Use `read_primary_lock_state()` and check `.held` for any decision that
must not create a second governor while one is live, even with
unconfirmed identity (this is `ensure_resident_alive()`'s own contract).

Use `read_primary_lock_pid()` only where a caller genuinely wants "a
confirmed real PID, or nothing" and is not making a spawn/no-spawn
decision from it (e.g. purely informational display).

Never write new code that treats `read_primary_lock_pid() == 0` as proof
the lock is unheld.

## Caller/dependency audit (production)

| Caller | Uses `.held`? | Uses PID identity? | Can spawn a second governor if wrong? |
|---|---|---|---|
| `resident_driver.run_resident_loop()` (canonical governor entry, via `acquire_primary_lock`/`release_primary_lock` directly) | N/A -- calls `acquire_primary_lock` itself, doesn't read state | N/A | governs itself; not a reader of another's identity |
| `resident_windows.ensure_resident_alive()` (watchdog) | yes (`read_primary_lock_state(...).held`) | yes, for the returned `pid` field only (may be `None`) | no -- gated on `.held`, not PID |
| `resident_windows.detach_resident_driver()` | no (spawns, then polls `read_primary_lock_pid()` for its own return value only) | yes | no -- this is the spawn path itself, not a duplicate-prevention decision |
| `resident_status.status_claims_live()` | no -- independent, self-reported status-file heartbeat channel (own `GOVERNOR_PID` + freshness window), not derived from the primary lock at all | n/a | out of scope for this contract; already staleness-protected via a 30s heartbeat window, not solely `pid_is_alive` |
| `resident_windows.resident_pid_alive()` | no (thin wrapper over `status_claims_live`) | n/a | dead code -- defined, zero production callers found repo-wide |
| Tests (`test_d146_remediation_adv.py`, `test_primary_governor_atomic_lock.py`) | mixed, by design | mixed, by design | test-only |

No remaining production caller assumes `pid > 0 <=> lock held`.
