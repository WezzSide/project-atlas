# Atlas 2031 — Knowledge + Development convergence (strategic horizon)

| Field | Value |
|---|---|
| Directive | `D-PROJECT-ATLAS-ULTIMATE-KNOWLEDGE-DEVELOPMENT-CONVERGENCE-001` |
| Status | **STRATEGIC_HORIZON** — additive successor/horizon extension under Atlas 3; not canonical runtime semantics |
| Parent | Ultimate Atlas Master Update Package; ULT-00 reconciliation (`CURRENT-STATE.md`) |
| Precedence | Owner directives > re-derived `main` truth > AGENTS/CLAUDE/GOVERNANCE/SECURITY > Atlas 3 canon (`docs/atlas-3/NORTH-STAR.md`) > this document > history |
| `MERGE_AUTHORIZATION` | NOT_GRANTED |

This document records the five-year product direction in repository-native
form so that later foundation packages can be checked against it. It does
not promote any capability. Every section carries a maturity label from the
fixed vocabulary:

```text
LANDED · ISOLATED_RUNTIME · CONTRACT_ONLY · PROPOSED · STRATEGIC_HORIZON · EXTERNAL_BLOCKED · SUPERSEDED
```

Nothing labelled `STRATEGIC_HORIZON` is authorized for implementation by
this document. Do not read horizon prose as shipped capability.

## 1. Terminology (three things that are all casually called a "control plane")

| Term | Meaning | Status |
|---|---|---|
| **Knowledge Control Plane (KCP)** | The product concept from `docs/plan.md`: Atlas as the portfolio-wide system that turns scattered project information into structured knowledge, evidence, status, decisions, context, relationships and portfolio intelligence. An architectural/product plane, not one runtime module. | LANDED (concept); Core pipeline + lenses are its current runtime |
| **Agent Governance Control Plane** | `atlas-vault-documentation/` and the `AS-CTRL-001` lifecycle: bootstrap → preflight → session → agent-event evidence → validation → postflight → receipt → close. | LANDED (sibling deliverable) — must not be silently replaced by the KCP |
| **Development Plane** | The Ultimate Atlas development architecture: program intelligence, task/context compilation, specialist agent compilation, capability execution, sandboxing, testing, verification, proof, CI, evaluation, outcome learning. | ISOLATED_RUNTIME (proof/IV/ADV/start) + PROPOSED (ULT waves); not a new Truth Core |

## 2. Product identity, 2031

```text
ATLAS = THE VERIFIABLE INTELLIGENCE LAYER
        FOR PEOPLE, PROJECTS, KNOWLEDGE AND AGENTS
```

Developer tooling is the initial wedge and stays the first product. It is
not the ceiling. The long-term audience is anyone whose work involves
learning, building, researching, reasoning, experimenting, deciding,
inventing, coordinating, remembering, or verifying — including agents.

Potential product line (STRATEGIC_HORIZON, not a commitment): Atlas
Developer · Atlas Knowledge · Atlas Learn · Atlas Research · Atlas Innovate ·
Atlas Team · Atlas Agent OS. These are **not** seven architectures. They share
one evidence substrate, one truth system, one temporal model, one provenance
model, one identity foundation, one twin vocabulary, one context architecture,
one proof model, and one capability vocabulary. There is no
`LearnTruthCore`, `ResearchTruthCore`, `DeveloperTruthCore` or
`PersonalTruthCore` — one truth architecture with domain projections.

Successor product language (product-strategy copy, not runtime semantics):

| Program | Line |
|---|---|
| Coder Alpha | Never explain your project to an AI twice. |
| Atlas 3 | The verifiable shared reality layer between software projects, humans and autonomous agents. |
| Ultimate Atlas horizon | Atlas — your verifiable intelligence layer. *Learn. Build. Research. Decide. Remember. Prove.* |

## 3. The most important architectural rule: one substrate, many planes

```text
                     HUMAN — goals / questions / decisions / work
                                      │
                           ┌──────────▼──────────┐
                           │     INTENT PLANE     │
                           └──────────┬──────────┘
════════════════════════════════════════════════════════════════════
                       SHARED ATLAS SUBSTRATE
   Evidence → Truth → Time → Project/Object Twin → Causality
                    → Context → Proof → Outcomes
════════════════════════════════════════════════════════════════════
        ┌─────────────────────┬──────────────────────┐
        ▼                     ▼                      ▼
 Knowledge Control       Development             Agent OS
      Plane                 Plane                  Plane
  Learn · Research     Developer · QA          Skills · Models
  Innovate · Decisions Security · DevOps       Tools · MCP · A2A
  Personal             Architecture
        └─────────────────────┼──────────────────────┘
                              ▼
                         Team / Org
```

Every future foundation proposal must answer the **convergence test**:

```text
Can this primitive serve both development and knowledge work?
  YES → is there a shared substrate for it?
  NO  → is the domain distinction fundamental, or accidental duplication?
```

Naturally shared: Evidence, Attestation, Time, Context, Question, Decision,
Outcome, Proof. Legitimately domain-specific: AST, compiler, test runner,
scientific instrument, learning-assessment type. Do not over-generalize the
domain-specific tools; do not duplicate the shared ones.

Shared foundational primitives to keep domain-neutral at the lowest layer:
Identity · Evidence · Provenance · Time · Object · Relationship · Context ·
Capability · Execution · Attestation · Proof · Outcome · Evaluation. This does
not mean broadening any immediate package; it means not making the lowest
contracts needlessly coding-only.

## 4. Atlas stores the evolution of understanding

Computers persist files, messages and documents. Atlas should eventually
persist *understanding*: the shared epistemic loop

```text
UNKNOWN → QUESTION → INVESTIGATION → HYPOTHESIS → EVIDENCE → CONTRADICTION
→ DECISION → IMPLEMENTATION / EXPERIMENT → OBSERVATION → VERIFICATION
→ KNOWLEDGE → SUPERSESSION
```

which manifests as bug → hypothesis → patch → test → proof (developer),
question → explanation → practice → assessment → demonstrated understanding
(student), research question → hypothesis → experiment → result → replication
(researcher), assumption → decision → execution → outcome → retrospective
knowledge (organization). Status: LANDED for the developer/project loop's
truth and time layers; the rest is STRATEGIC_HORIZON.

## 5. Universal object and relationship model (PROPOSED vocabulary)

Files remain evidence; users reason about objects. Object families, each of
which must declare source of authority, evidence requirements,
derived-or-canonical, temporal semantics, project/user scope and privacy
class before it exists in any store:

| Family | Objects | Status |
|---|---|---|
| Project / work | Project, Repository, Component, Service, Environment, File, Symbol, Requirement, Task, Mission, PR, Commit, Build, Deployment, Incident, Artifact | LANDED twin vocabulary (19 nodes, `atlas3/domain.py`); Mission declared-only |
| Knowledge | Source, Concept, Claim, Question, Unknown, Conflict, Assumption, Decision, Idea, Hypothesis, Finding, Experiment, Result, Method, Paper, Dataset, Reference | Claim/Decision/Conflict/Unknown LANDED in Truth Core; the rest PROPOSED |
| Human / learning | Person, Team, Skill, Competency, LearningGoal, LearningActivity, Assessment, Demonstration, Curriculum | STRATEGIC_HORIZON (privacy design first) |
| Intelligence | Agent, AgentGenome, Skill, Model, Tool, Capability, ContextPack, Trajectory, Attestation, Proof, Eval, Outcome | Attestation/Proof v2 = AT3-103 (ISOLATED_RUNTIME); ContextPack partial; others PROPOSED |

Relationships extend the landed 15 (`CONTAINS, IMPLEMENTS, DEPENDS_ON,
CLAIMS, CONTRADICTS, SUPERSEDES, VALIDATES, INVALIDATES, CAUSED_BY,
DECIDED_BY, DEPLOYED_AS, OBSERVED_IN, OWNED_BY, DERIVED_FROM, BLOCKS`) with
`DEFINES, REFERENCES, SUPPORTS, CALLS, TESTED_BY, AFFECTS, LEARNED_FROM,
DEMONSTRATED_BY, REQUIRES, INSPIRED_BY, RELATED_TO` (PROPOSED). Every material
derived relationship requires provenance; no edge becomes truth because an
LLM emitted it (`GRAPH != AUTHORITY`).

## 6. Knowledge Control Plane target (PROPOSED pipeline, LANDED three layers)

```text
CAPTURE → NORMALIZE → IDENTIFY → SOURCE → RELATE → TIME-BOUND → CONFLICT-CHECK
→ CONTEXTUALIZE → PROJECT → SEARCH → EXPLAIN → REVIEW / PROMOTE
```

The three-layer contract is unchanged and mandatory: Layer A evidence (docs,
code, papers, messages, agent events, CI, experiments, datasets, notes),
Layer B canonical knowledge (only through the governed promotion process),
Layer C derived intelligence (twins, program graph, learning twin, research
graph, idea graph, Pulse, attention, context packs, recommendations,
simulations, trajectories). **Layer C never silently writes Layer B.**

Universal search should increasingly resolve to objects ("2 claims, 1
decision, 3 evidence sources, 1 commit, 1 experiment, 2 conflicts") rather
than "13 matching files", only where the evidence supports that
representation. Universal semantics to preserve: `start <object>` = compile
the smallest evidence-backed current context to begin work on that object;
`proof` = a machine-checkable evidence structure supporting a precisely
bounded claim (not "developer CI result only"); `outcome` = what happened
after an action, with domain profiles (merged/reverted/regressed/survived;
demonstrated/retained/forgotten/mastered; supported/refuted/replicated/
inconclusive). Current `atlas start` and `atlas proof` remain scoped to
their landed contracts.

## 7. Knowledge-plane capabilities (all STRATEGIC_HORIZON unless noted)

| Capability | One-line contract | Guard |
|---|---|---|
| Attention Engine (evolves Pulse, LANDED) | what changed / stale / conflicts / broke / blocked / became possible / assumption weakened / opportunity / decision needed / deserves attention now | "why does Atlas think this matters?" always answerable from evidence + explicit rules |
| Open Questions graph | questions across projects, learning, research, ideas, decisions; new evidence resurfaces old questions | no auto-resolution |
| Decision intelligence (decisions lens LANDED) | decision, alternatives, evidence, assumptions, constraints, actor, rationale, expected vs actual outcomes, later invalidating evidence; "would we still decide this today?" | never rewrites history |
| Counterfactual explorer | "what if we chose B?" | always `SIMULATED` / `INFERRED` |
| Knowledge Time Machine (kdiff LANDED) | what did we believe on X; when did Y go stale; what evidence changed this decision | reuse the temporal engine; never a second clock |
| Knowledge branching / mergeable knowledge | competing hypotheses coexist; diff → compatible changes / conflicts / provenance → review / merge | not Git branches mechanically |
| Personal Intelligence Twin | user-controlled, local-first, source-backed, exportable, selectively shareable | never a covert behavioural profile; privacy design before any implementation |
| Atlas Learn | Learning Twin (known / partial / misconception / unknown / demonstrated from evidence), adaptive curriculum, "teach Atlas" mode | knowledge states from assessments/demonstrations, not solely an LLM guess |
| Verified Skill Passport | claimed vs learned vs demonstrated vs recently demonstrated; selective disclosure without exposing private source | attestation + privacy design first; AT3-103 must not preclude it and must not become credential infrastructure |
| Atlas Research | research question → literature, claims, evidence, contradictions, methods, hypotheses, experiments, datasets, results, replications, open questions | sources remain primary evidence |
| Literature graph + Contradiction engine | claim ← supported by / contradicted by / replicated by; conflict dimensions (population, method, definition, time, dataset, sample, environment, measurement, assumptions) | Atlas never picks a winner |
| Hypothesis engine + Experiment designer | evidence + unknowns + contradictions + failures → candidates → critics → tournament; design → feasibility → controls → confounders → protocol → analysis plan | hypotheses stay `INFERRED`; execution reuses ExecutionIdentity / EvidenceAttestation / Proof / Outcome from the Development Plane |
| Atlas Lab | instruments, simulations, robots, scientific agents | documentation only; no physical-lab control in foundation work |
| Idea engine + Innovation radar | ideas as first-class derived objects (origin, evidence, objections, feasibility, experiments, outcomes); world change → twin → material relevance | explainable relevance; opted-in sources only |
| Reproduction engine | `REPRODUCE`: claim → proof → source tree → environment → tool → command → result (software) and figure → dataset → analysis → source → environment → result (research) | foundation begins with AT3-103 |
| Research network / proof-native publishing | Atlas-native claims, sources, datasets, methods, results, proof, reproduction packages; "has this been independently reproduced?" | not in current foundation |

## 8. Development-plane end state (PROPOSED; wave order in `CONFLICTS.md` §C)

```text
Project Truth → Program Intelligence → Task Context → Capability Broker
→ Specialist Agent Compiler → Contained Execution → Fast Feedback → Breaker
→ Independent Verification → Proof → Outcome → Eval → better next configuration
```

Universal agent compiler: the user specifies a GOAL, not a MODEL; Atlas
determines role, skills, context, models, tools, capabilities, runtime,
verifiers and budget. The unit of intelligence is capability composition,
not a permanent persona. Human + agent team compilation (humans as domain
owners, risk reviewers; agents as implementers, independent verifiers) is a
farther horizon; humans remain people, not "agent resources".

Autonomous project maintenance (stale docs, dependency risk, weak tests,
dead code, broken links, outdated decisions, missing evidence, performance
drift, advisories, knowledge gaps) follows `detect → investigate → prepare
candidate → verify → proof → policy / owner gate`, never `detect → mutate
main`. Executable project forks (architectures A/B/C in isolated
environments compared by correctness, tests, performance, security,
complexity, cost, maintainability) and research simulation (hypotheses
A/B/C under literature, data, simulation, criticism, counterexamples) share
the same multi-rollout, sandbox, proof and eval infrastructure — which is
why the two planes must share it.

The Agent OS is the bridge and the closed loop: Knowledge → Context → Agent
compile → Capability → Action → Evidence → Knowledge.

## 9. Missions, teams, organizations (STRATEGIC_HORIZON)

Missions evolve toward goal graphs; agent work stays lease/capability/proof
governed and mission availability never equals execution authority
(AT3-096 `mission.py` is declared-only today). Team Brain: humans + agents +
tools over one shared reality, with less re-explanation, less tribal
knowledge, less lost reasoning, less stale context, more reproducibility,
more traceable decisions. Knowledge federation across trust domains
(personal, university, employer, open source, research) may share selected
*derived* knowledge without collapsing authority boundaries, e.g. employer
code PRIVATE while a derived proof of skill is SELECTIVELY SHAREABLE —
dedicated privacy/policy work before any implementation. Organization Twin
(projects, products, services, teams, skills, decisions, infrastructure,
dependencies, research, risks, goals) stays behind developer-product
retention.

## 10. Local-first, provider-neutral, interface-neutral (LANDED principles)

Atlas must run across laptop, desktop, private server and (later) mobile
with selectable local/cloud/enterprise/hybrid models without changing truth
semantics. Cloud services enhance collaboration and compute; they are never
mandatory for canonical personal or project knowledge. Claude, Codex,
ChatGPT, Gemini, local models and future agents are replaceable
intelligence providers; CLI, IDE, Web, Obsidian, Mobile, MCP, ACP, A2A and
API are interfaces. `Interface != authority`. Project Twin, Program Twin,
Research Twin, Learning Twin, Personal Intelligence Twin and Organization
Twin compose around shared identity/evidence/time semantics; they never
become six competing truth stores.

## 11. Moat, portability, and the disappearance test

Atlas does not compete on foundation-model intelligence. Its durable
differentiation is user/project-specific reality + longitudinal history +
verifiable context + knowledge evolution + proof + procedural skills +
outcome history + provider neutrality + local-first ownership. Portability
is part of the moat: the user owns and can export their data, models are
replaceable, protocols are open, and Atlas becomes hard to replace only
because of accumulated value (useful lock-in, never hostile lock-in).

Moat test for any strategic feature: does it strengthen project/user
reality, longitudinal memory, provenance, context, proof, procedural
expertise, outcomes, or provider neutrality? If not, it is probably
commodity functionality better reached through a connector. Build
differentiated semantics (Truth, Context, Twin, Proof, Skills, capability
governance, knowledge evolution, outcome learning); integrate commodity
infrastructure through stable ports (models, sandboxes, telemetry, search
engines, language servers, MCP servers, databases, external research
indexes).

Disappearance test, per audience: would development become slower or less
trustworthy (developer)? would learning continuity degrade (student)? would
evidence/reasoning continuity degrade (researcher)? would institutional
memory degrade (team)? If not, the feature is architecture, not product.

## 12. Roadmap lanes and five-year trajectory (planning, not delivery promises)

Lanes: **F** shared foundation (F1 execution identity/proof · F2
object/program intelligence · F3 context · F4 capabilities · F5 execution ·
F6 verification · F7 eval/outcomes) · **D** development · **K** knowledge
control plane · **L** learning · **R** research · **I** innovation · **T**
team/organization · **E** ecosystem. Foundation dependencies outrank horizon
excitement.

Immediate wave order (accepted; corrected by ULT-00): ULT-01a (AT3-103) →
ULT-01b observation wiring + proof graph → ULT-02 program intelligence →
ULT-03 context compiler v2 → ULT-04 capability broker → ULT-05 skill
registry + agent genome → ULT-06 execution backends → ULT-07 verifier mesh →
ULT-08 impact/advanced testing → ULT-09 trajectory scaling → ULT-10
outcome/eval + model router → ULT-11 governed multi-hop. The Knowledge lane
may receive additive read/derived packages in parallel only where they do
not destabilize these dependencies.

| Window | Direction (hypothesis) |
|---|---|
| 2026–2027 | Best persistent project brain for AI developers + best verified development-agent substrate: context, program intelligence, proof, skills, capabilities, verification, IDE workflow, developer retention |
| 2027–2028 | Knowledge Control Plane expansion: universal objects, search, why engine, open questions, attention, failure memory, decision intelligence, advanced Time Machine |
| 2028–2029 | Atlas Learn / Research / Innovate, Personal Intelligence Twin, Skill Passport, literature graph, hypothesis/experiment systems — only after foundation maturity |
| 2029–2030 | Team Brain, knowledge federation, organization intelligence, shared skills/evals, enterprise policy |
| 2030–2031 | Universal intelligence layer, agent ecosystem, research network, proof-native publishing, simulation, lab integration |

These dates are directional hypotheses. No architecture guarantees market
capitalization, revenue, adoption or commercial success, and no such
guarantee belongs in Atlas truth. Optimize first for daily value, retention,
trust, speed, continuity and proof.

## 13. Metrics extension (define now; instrument only when surfaces exist)

Retain the Coder Alpha metrics. Development: accepted-task rate, first-pass
acceptance, proof coverage, verifier rejection, regression escape, time to
accepted task. Knowledge: time to useful knowledge, answer provenance
coverage, stale-context rate, open-question resolution, decision retrieval
success. Learning (future only): knowledge retention, demonstrated mastery,
misconception correction. Research (future only): claim provenance,
reproducibility, contradiction discovery. Never invent a metric from
telemetry that does not exist; never optimize a metric by weakening evidence
requirements.

## 14. Atlas Field Observatory (research radar; advisory only)

Atlas spans or intersects: AI coding agents · agent orchestration · context
engineering · program analysis · software verification · supply-chain
provenance · developer experience · knowledge management / PKM · knowledge
graphs · digital twins · information retrieval · local-first software ·
collaborative systems · adaptive learning / education technology ·
scientific discovery / research agents / self-driving laboratories ·
decision intelligence · innovation management · human-AI collaboration ·
privacy/security · identity/attestation · evaluation science ·
observability · MCP/ACP/A2A interoperability · enterprise knowledge
systems.

Compact watchlist: agent protocols · context engineering · coding-agent
evals · program intelligence · verification · local-first systems · adaptive
learning · research agents · scientific agents · digital twins · knowledge
graphs · provenance · privacy.

The observatory periodically asks: which external innovations materially
affect Atlas? which Atlas assumption became obsolete? which new technique
should enter an experiment gate? External novelty never changes production
architecture automatically. Every adoption records: problem · current Atlas
gap · candidate technique · measured benefit · compatibility · security ·
maintenance · experiment result.

## 15. Explicitly not authorized by this document

`STRATEGIC_HORIZON` until a future owner directive promotes them: Personal
Intelligence Twin · Learning Twin · Skill Passport · Research Network ·
Atlas Lab · Organization Twin · Agent Marketplace · Simulation Engine ·
Autonomous Maintenance Fleet · Proof-native publication network · knowledge
federation · selective-disclosure credentials.

## 16. The program principle

Ultimate Atlas is not "the AI that knows everything". It is the system that
knows what is relevant about the user's and project's reality, knows where
that knowledge came from, knows what remains unknown, gives the right
intelligence the right context and capability, and can prove the outcome.
Development is the wedge; Knowledge is the persistent substrate; Learning,
Research and Innovation are natural future planes; Agents are the execution
mechanism; Proof is the trust mechanism; Atlas is the intelligence
continuity layer connecting them. Build by convergence, not replacement.
