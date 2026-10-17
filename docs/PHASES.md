# TrafficFuse AI — Implementation Phases

Each phase ends at an explicit **PASS / PARTIAL / BLOCKED** gate. The next phase starts only
after human authorization (AGENTS.md). No phase is started automatically.

## Phase 0 — Foundation & Dependency Verification — **PASS (pending sign-off)**

Goal: reproducible consumer foundation on top of the pinned Agentic Intent Gate v0.2.0.

- Repository skeleton, `AGENTS.md` (upstream consumer rules + TrafficFuse section).
- Python 3.13 + uv project; pinned shared runtime from immutable Git commit; `uv.lock`.
- FastAPI health endpoint; offline smoke tests; `.env.example`; `.gitignore`.
- Documentation set (product brief, architecture, competition, phases, report).

**PASS requires:** AGENTS.md verified against upstream; installed distribution == 0.2.0 from
the pinned commit (not a source checkout); reproducible lockfile; `uv sync --locked` succeeds;
FastAPI health smoke test passes; offline tests pass; no secrets tracked; no commits/pushes
without authorization; docs complete.

## Phase 1 — Kubernetes Failure Lab

Goal: a real isolated **Kind** environment with a healthy and a faulty service version and
reproducible HTTP 500 failure evidence; evidence collection service.

- Kind cluster (disposable, allowlisted, created/destroyed per task on explicit approval).
- Two deployments of a demo service (healthy + faulty), real HTTP traffic generator.
- Evidence collection: HTTP probe results, deployment/service state snapshots.
- Baseline tests proving the faulty version measurably degrades.

**PASS:** faulty vs healthy services differ measurably; evidence endpoints return real data;
cluster is created and destroyed deterministically; nothing left running afterwards.
**PARTIAL:** environment works but partial evidence. **BLOCKED:** report the exact blocker
(e.g., container runtime unavailable).

## Phase 2 — AI Diagnosis

Goal: evidence-grounded incident reasoning and bounded Plan generation.

- Consumer `ModelProvider` (Ollama phi4-mini) with a TrafficFuse-specific prompt.
- Structured incident explanation with traceable evidence references.
- Bounded Plan proposal for `reroute_service_traffic` (exact target, allowlisted args).
- Plan validation performed by Agentic Intent Gate; synthetic training/test data labeled.

**PASS:** explanation cites collected evidence; Plan passes AIG validation; wrong-target and
malformed Plans are rejected; tests run offline with synthetic evidence.

## Phase 3 — Governed Execution

Goal: the full governed action chain against the isolated Kind environment.

- Trusted `reroute_service_traffic` ToolExecutor registered with AIG (single SIDE_EFFECT tool).
- Human Plan Approval through the product's own admission path (not the bundled demo UI).
- Independent Authorization; durable claim; consumer receipt store.
- Unknown-outcome, duplicate, concurrency, restart and wrong-identity tests.

**PASS:** end-to-end chain executes one real bounded traffic switch in Kind with durable
audit evidence; no retry on ambiguity; no production access by construction.

## Phase 4 — Competition-Grade Product Experience

Goal: a demo-able product surface.

- React + TypeScript dashboard: incident timeline, before/after traffic health, action
  history, failure states that fail closed.
- Independent post-action verification presented with the incident record.

**PASS:** full demo path visible in the UI against the live Kind lab.

## Phase 5 — Demonstration & Submission

Goal: official submission.

- End-to-end validation and rehearsal; demo video; slide presentation; repo polish.
- Cover image, descriptions/tags, application URL; official submission after re-verifying
  the deadline and guidelines (docs/COMPETITION.md).

**PASS:** every item in the submission readiness checklist is complete and verified.
