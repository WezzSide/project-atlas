# Enrollment — registering an agent and giving it work

One supported way to register an agent profile, associate its workspace and
role, assign an approved work program, and launch it under supervision.

`ENROLLMENT != AUTHORIZATION`. Enrolling records that an agent exists, which
runtime it is, where it works, and what it may narrow. It grants nothing.

`NEVER SILENTLY ADOPT`. There is no code path that discovers a running agent
and takes it over. An existing session enters a program only through an
explicit, recorded act — `program handoff`, for a session the runtime has
**stored**. A live process is never adopted at all.

## Commands

```bash
python -m project_atlas.orchestration.program.cli agent enroll \
  --registry ~/atlas-agents --agent-id claude-impl --role implementer \
  --adapter claude-code --workspace ~/repos/thing --enrolled-by wesley \
  --narrow '{"limits": {"max_seconds": 600}}'

python -m project_atlas.orchestration.program.cli agent assign \
  --registry ~/atlas-agents --agent-id claude-impl \
  --program PROGRAM.json --assigned-by wesley

python -m project_atlas.orchestration.program.cli agent launch \
  --registry ~/atlas-agents --agent-id claude-impl

python -m project_atlas.orchestration.program.cli agent list      --registry ~/atlas-agents
python -m project_atlas.orchestration.program.cli agent status    --registry ~/atlas-agents --agent-id claude-impl
python -m project_atlas.orchestration.program.cli agent set-status --registry ~/atlas-agents --agent-id claude-impl --status SUSPENDED
```

## The four identities

They have four different lifetimes. Conflating any two is how a supervisor
ends up doing something nobody authorized, so `agent status` reports each
separately with its lifetime stated.

| Identity | What it is | Lifetime | What it decides |
| --- | --- | --- | --- |
| **Agent identity and permissions** | `EnrolledAgent.agent_id` plus its narrowing | durable; outlives every run, session and supervisor | the principal the "implementer cannot verify its own work" gate is checked against |
| **Runtime / session identity** | `AttemptRecord.runtime_session_id` | one attempt | nothing — it carries no permission of its own |
| **Task ownership** | an `AgentLease` and its durable projection row | one task, while work is in flight | which worker may write a surface |
| **Supervisor process lifecycle** | the singleton lock, its instance id and pid | one process | which process may dispatch |

A supervisor exiting ends **none** of the first three. A durable lease stays
ACTIVE across a process death, and the next supervisor rehydrates it rather
than granting a second lease over the same surface — there is a test for
exactly that.

## How permissions resolve

```
program profile for the role
  ⊕ the task's own override        (narrowing only)
  ⊕ the agent's enrollment narrowing (narrowing only, same checker)
  ⊕ the agent's identity           (agent_id is the principal, not a permission)
```

Every permission still comes from the **approved program's** profile for that
role. An enrollment can tighten it and can never widen it; the attempt raises
`AuthorityExpansionError` with the same codes a task override would.

Where a limit is set on both sides, the **tighter** one wins field by field.
Both bounds were set on purpose, and honouring only one would silently discard
the other.

## Switching runtimes

An agent declares which runtime it *is*. Assigning it a role written for a
different runtime is refused with `RUNTIME_SUBSTITUTION_NOT_AUTHORIZED` unless
`--allow-runtime-substitution` is passed. Switching runtimes is a decision,
never a default, and the program's own profile is still what bounds it.

## Verification separation, re-checked after substitution

Two program profiles that look independent can resolve to **one** enrolled
agent. The loader's check runs on the program's placeholders, where they
differ; the check is therefore re-run after enrollment substitution, on the
real `agent_id`s. An agent cannot end up verifying its own work by the back
door of being enrolled into both roles.

## Suspension does not drop ownership

`set-status --status SUSPENDED` withholds dispatch. It deliberately does not
touch a lease: dropping ownership of a surface somebody may still be writing
is worse than pausing dispatch. Use `program reconcile` for the lease.
