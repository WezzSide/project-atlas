#!/usr/bin/env bash
# A reproducible operator journey through the Atlas entry point.
#
# Enrol two workers on two different real runtimes, approve a program, run it,
# inspect it, pause and resume it, prove restart reconciliation, and read the
# durable view -- every step through `atlas ...`, nothing through a private API.
#
# Usage:  operator-journey.sh <scratch-dir>
# Needs:  claude (logged in), codex (codex login). Costs a handful of launches.
#
# Never merges, never grants an owner gate, never raises a limit.
set -euo pipefail

SCRATCH="${1:?usage: operator-journey.sh <scratch-dir>}"
ATLAS=(python -m project_atlas.cli)
REG="$SCRATCH/registry"
WS="$SCRATCH/workspace"
STATE="$SCRATCH/state"
PROGRAM="$SCRATCH/program.json"

step() { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }

step "0. a disposable workspace"
rm -rf "$SCRATCH"
mkdir -p "$WS"
git -C "$WS" init --quiet
git -C "$WS" config user.email operator@example.invalid
git -C "$WS" config user.name Operator
echo "disposable operator-journey workspace" > "$WS/README.md"
git -C "$WS" add README.md
git -C "$WS" commit --quiet -m seed
PIN="$(git -C "$WS" rev-parse HEAD)"

step "1. what can this machine actually run?"
"${ATLAS[@]}" program capabilities

step "2. write the approved program"
python - "$PROGRAM" "$WS" "$PIN" <<'PY'
import json, sys, pathlib
out, ws, pin = sys.argv[1], sys.argv[2], sys.argv[3]

def task(tid, role, out_file, depends=()):
    return {
        "task_id": tid,
        "title": f"{role} writes {out_file}",
        "instruction": (
            f"Create a file named exactly {out_file} in the current working "
            f"directory whose entire content is the single line: {tid.upper()}-OK\n"
            "Do not create or modify any other file. Stop when it exists."
        ),
        "profile_ref": role,
        "depends_on": list(depends),
        "mutation_paths": [out_file],
        "surface_id": tid,
        "surface_semantic": tid.upper().replace("-", "_"),
        "capabilities_required": ["IMPLEMENT"],
        "acceptance": [{
            "check_id": f"{tid}-content", "kind": "FILE_MATCHES",
            "description": f"{out_file} contains {tid.upper()}-OK",
            "path": out_file, "pattern": f"{tid.upper()}-OK",
        }],
    }

payload = {
    "schema_version": 1,
    "program": {
        "program_id": "operator-journey",
        "objective": "Two enrolled workers on two real runtimes, run under supervision.",
        "approved_by": "wesley",
        "approval_reference": "docs/orchestration/program/OPERATOR-JOURNEY.md",
        "workspace_root": ws,
        "base_pin": pin,
        "limits": {
            "max_task_launches": 8, "max_attempts_per_task": 2,
            "max_task_seconds": 420, "max_program_seconds": 2400,
            "max_cycles": 20, "max_idle_cycles": 3,
            "idle_sleep_seconds": 0.0, "max_concurrent_workers": 2,
        },
        "tasks": [
            task("claude-first", "claude-role", "claude-1.txt"),
            task("claude-second", "claude-role", "claude-2.txt", ("claude-first",)),
            task("codex-first", "codex-role", "codex-1.txt"),
            task("codex-second", "codex-role", "codex-2.txt", ("codex-first",)),
        ],
    },
    "profile_defaults": {"credential": "SUBSCRIPTION_OAUTH"},
    "profiles": {
        "claude-role": {
            "agent_id": "placeholder-claude", "adapter": "claude-code",
            "adapter_min_version": "2.1.259", "capabilities": ["IMPLEMENT"],
            "model": "sonnet", "permission_mode": "acceptEdits",
            "tools": ["Read", "Write", "Edit", "Glob", "Grep"],
            "allowed_mutation_prefixes": ["claude-1.txt", "claude-2.txt"],
            "limits": {"max_seconds": 420, "max_attempts": 2}, "env_allowlist": [],
        },
        "codex-role": {
            "agent_id": "placeholder-codex", "adapter": "codex",
            "capabilities": ["IMPLEMENT"], "permission_mode": "acceptEdits",
            "allowed_mutation_prefixes": ["codex-1.txt", "codex-2.txt"],
            "limits": {"max_seconds": 420, "max_attempts": 2}, "env_allowlist": [],
        },
    },
}
pathlib.Path(out).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
PY

step "3. validate before anything runs"
"${ATLAS[@]}" program validate --program "$PROGRAM"

step "4. which account will each profile use?"
"${ATLAS[@]}" program credentials --program "$PROGRAM"

step "5. enrol the two workers"
"${ATLAS[@]}" agent enroll --registry "$REG" --agent-id claude-worker \
  --role claude-role --adapter claude-code --workspace "$WS" \
  --enrolled-by wesley --description "real Claude Code worker"
"${ATLAS[@]}" agent enroll --registry "$REG" --agent-id codex-worker \
  --role codex-role --adapter codex --workspace "$WS" \
  --enrolled-by wesley --description "real Codex worker"
"${ATLAS[@]}" agent list --registry "$REG"

step "6. assign the approved program to each"
"${ATLAS[@]}" agent assign --registry "$REG" --agent-id claude-worker \
  --program "$PROGRAM" --assigned-by wesley
"${ATLAS[@]}" agent assign --registry "$REG" --agent-id codex-worker \
  --program "$PROGRAM" --assigned-by wesley

step "7. the four identities, side by side"
"${ATLAS[@]}" agent status --registry "$REG" --agent-id claude-worker

step "8. pause BEFORE starting, to show dispatch is withheld"
# Pausing an unstarted program is legitimate and is the safest moment to do it.
# An earlier draft of this script ran a throwaway `start` here to create the
# durable record -- which quietly executed the whole program, and made step 9's
# narrative false. The record is now initialised by the pause itself.
"${ATLAS[@]}" program control --program "$PROGRAM" --state-root "$STATE" \
  --action pause --requested-by wesley
"${ATLAS[@]}" program start --program "$PROGRAM" --state-root "$STATE" --registry "$REG"

step "9. resume and run to completion, both runtimes, no prompt between tasks"
"${ATLAS[@]}" program control --program "$PROGRAM" --state-root "$STATE" \
  --action resume --requested-by wesley
"${ATLAS[@]}" program start --program "$PROGRAM" --state-root "$STATE" --registry "$REG"

step "10. the durable operator view"
"${ATLAS[@]}" program control --program "$PROGRAM" --state-root "$STATE"

step "11. compact status"
"${ATLAS[@]}" program status --program "$PROGRAM" --state-root "$STATE" --registry "$REG"

step "12. restart is not replay: starting again launches nothing"
"${ATLAS[@]}" program start --program "$PROGRAM" --state-root "$STATE" --registry "$REG"

step "13. revoke authority, then prove the next dispatch is refused"
# Suspending the worker is not itself proof: the program above has completed
# and has nothing left to dispatch, so a start would launch nothing either way.
# The proof needs a task that WOULD otherwise run, so this uses a second,
# one-task program bound to the now-suspended agent. No model call happens --
# that is the point, and it costs nothing to demonstrate.
"${ATLAS[@]}" agent set-status --registry "$REG" --agent-id claude-worker --status SUSPENDED
"${ATLAS[@]}" agent list --registry "$REG"

python - "$SCRATCH/revoked.json" "$WS" "$PIN" <<'PY2'
import json, sys, pathlib
out, ws, pin = sys.argv[1], sys.argv[2], sys.argv[3]
pathlib.Path(out).write_text(json.dumps({
    "schema_version": 1,
    "program": {
        "program_id": "revoked-authority-probe",
        "objective": "A task that would run, if its agent still had authority.",
        "approved_by": "wesley",
        "approval_reference": "docs/orchestration/program/OPERATOR-JOURNEY.md",
        "workspace_root": ws, "base_pin": pin,
        "limits": {"max_cycles": 4, "idle_sleep_seconds": 0.0, "max_task_launches": 1},
        "tasks": [{
            "task_id": "would-have-run", "title": "would have run",
            "instruction": "Create never-written.txt containing NOPE.",
            "profile_ref": "claude-role", "mutation_paths": ["never-written.txt"],
            "surface_id": "revoked", "surface_semantic": "REVOKED",
            "capabilities_required": ["IMPLEMENT"],
            "acceptance": [{"check_id": "n", "kind": "FILE_EXISTS",
                            "description": "never-written.txt exists",
                            "path": "never-written.txt"}],
        }],
    },
    "profile_defaults": {"credential": "SUBSCRIPTION_OAUTH"},
    "profiles": {"claude-role": {
        "agent_id": "placeholder-claude", "adapter": "claude-code",
        "capabilities": ["IMPLEMENT"], "permission_mode": "acceptEdits",
        "allowed_mutation_prefixes": ["never-written.txt"],
        "limits": {"max_seconds": 60, "max_attempts": 1}, "env_allowlist": [],
    }},
}, indent=2, sort_keys=True), encoding="utf-8")
PY2

# Bind the suspended agent explicitly. `program start --registry` skips
# non-ACTIVE agents, so this uses `agent launch`, which names one agent and
# therefore reaches the authority check rather than quietly running unbound.
"${ATLAS[@]}" agent assign --registry "$REG" --agent-id claude-worker \
  --program "$SCRATCH/revoked.json" --assigned-by wesley 2>&1 | tail -5 || true
"${ATLAS[@]}" agent launch --registry "$REG" --agent-id claude-worker \
  --state-root "$SCRATCH/revoked-state" 2>&1 | tail -8 || true
echo "--- did anything get written? ---"
ls "$WS/never-written.txt" 2>&1 || echo "never-written.txt: absent, as required"

step "14. the durable event log"
"${ATLAS[@]}" program events --program "$PROGRAM" --state-root "$STATE" --limit 40

step "15. the workspace the workers actually produced"
for f in claude-1.txt claude-2.txt codex-1.txt codex-2.txt; do
  printf '%-16s %s\n' "$f" "$(cat "$WS/$f" 2>/dev/null || echo MISSING)"
done
