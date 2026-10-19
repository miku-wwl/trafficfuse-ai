# TrafficFuse AI — Implementation Phases

Each phase ends at an explicit **PASS / PARTIAL / BLOCKED** gate. The next phase starts only
after human authorization (AGENTS.md). No phase is started automatically.

> **Award sprint (2026-10-19): Phases 1B–4 completed and verified on the live Kind lab;
> Phase 5 PARTIAL.** One integrated sprint replaced further micro-phases. Evidence:
> [AWARD-SPRINT-REPORT.md](AWARD-SPRINT-REPORT.md); handover: [CLAUDE-HANDOVER-REPORT.md](CLAUDE-HANDOVER-REPORT.md).

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

**Phase 1A (offline implementation): PASS** — synthetic workload with deterministic fault
injection, real-HTTP probe with structured evidence, `deploy/lab` manifests for the future
cluster, safety-guarded lifecycle tooling (dedicated kubeconfig, exact context, ownership
receipt), standardized identity (`trafficfuse-lab` / `kind-trafficfuse-lab` /
`trafficfuse-system`); 83 offline tests green; no cluster created or touched.
See docs/PHASE1A-DESIGN.md and docs/PHASE1A-REPORT.md.

**Phase 1A.1 (deployment readiness hardening): PASS** — resolves the four review findings:
reproducible lab image definition (`deploy/lab/Dockerfile`, `.dockerignore`, pinned
requirements, `imagePullPolicy: Never`; offline build reported BUILD_NOT_VERIFIED as permitted),
durable probe-evidence export (stdout markers, strict validation, `evidence_cli`,
`artifacts/phase1b/<run-id>/` collection with raw-log preservation), strengthened ownership
guards (path normalization/symlink/traversal resistance, kubeconfig document verification with
CA-fingerprint anchor, v2 creation record, live-facts ownership verification, mandatory
negative tests), and an exact deterministic fault-rate algorithm (Bresenham distribution with
probe-supplied request indices). 240 offline tests green; AGENTS.md and the pinned runtime
untouched; no cluster operations. See docs/PHASE1A1-HARDENING-REPORT.md.

**Phase 1A.2 (final preflight corrections): PASS** — resolves the five review findings:
exact repository-owned kubeconfig path enforcement (the path is derived from the module's own
repository root; a foreign repository with an identical layout is refused), corrected Phase 1B
runbook ordering (documentation only: image load **after** cluster creation, context
`kind-trafficfuse-lab` vs namespace `trafficfuse-system`, caller-facts replaced by the trusted
collector), a trusted read-only live-facts collector (allowlisted, explicitly scoped,
fail-closed; caller-supplied facts never sole proof of ownership), strict timezone-aware UTC
evidence timestamps (naive/malformed/reversed refused via a controlled error type), and
byte-for-byte raw-log preservation (bounded, atomic writes, audit metadata, raw preserved on
parse failure). 295 offline tests green, 1 skipped; AGENTS.md and the pinned runtime
untouched; no cluster operations. See docs/PHASE1A2-FINAL-PREFLIGHT-REPORT.md and
docs/PHASE1B-RUNBOOK.md.

**Phase 1B entry review (safe deployment gate): PASS** — four findings corrected before any
execution: (1) namespace bootstrap now precedes the namespace-dependent ownership-collector
read (Stages C→D of the runbook; the old sequence was circular); (2) direct cluster deletion
removed from the authorized Phase 1B workflow (preservation + separate cleanup authorization;
regression-tested); (3) the destructive gate performs independent collection and accepts no
caller facts; the `source` string is removed. Collector markers remain forgeable Python
objects and do not provide authentication or authority (final hotfix correction); (4)
credential disclosure removed — kubeconfig identity via the trusted local parser (endpoint + CA
fingerprint only), redacted outputs, no raw kubeconfig view command. 327 offline tests green,
1 skipped; see docs/PHASE1B-ENTRY-REVIEW.md.

**Phase 1B (real cluster execution): PASS (executed 2026-10-19 with explicit approval).** The
image built (`sha256:fe0763a2…`), `trafficfuse-lab` was created with the dedicated kubeconfig,
the creation record and live ownership verification passed, workloads became ready, and both
probe scenarios ran. The first session exposed a real defect — `kubectl logs` merges stdout and
stderr, interleaving a progress line into the evidence document; strict extraction failed closed
and preserved the raw bytes. The probe now writes one ordered stdout stream, and the second
session produced validated evidence (baseline 0%, live 50.0% errors, 40/40 served by `faulty`).
Historical text of the pre-execution gate follows. Remaining at that time:
explicit human authorization (creation + scoped deployment only; deletion excluded), the real
image build with network, and running the probe scenarios against the live Services. The
corrected operator sequence (PowerShell 7.4+, 15 ordered steps, STOP conditions,
preservation) is specified in docs/PHASE1B-RUNBOOK.md. The final offline hotfix
and remaining deployment blockers are recorded in docs/PHASE1B-FINAL-HOTFIX-REPORT.md.

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

## Phase 2 — AI Diagnosis — **PASS (real model verified on live evidence)**

Goal: evidence-grounded incident reasoning and bounded Plan generation.

- Consumer `ModelProvider` (Ollama phi4-mini) with a TrafficFuse-specific prompt.
- Structured incident explanation with traceable evidence references.
- Bounded Plan proposal for `reroute_service_traffic` (exact target, allowlisted args).
- Plan validation performed by Agentic Intent Gate; synthetic training/test data labeled.

**PASS:** explanation cites collected evidence; Plan passes AIG validation; wrong-target and
malformed Plans are rejected; tests run offline with synthetic evidence.

## Phase 3 — Governed Execution — **PASS (one governed switch executed live, recovery verified)**

Goal: the full governed action chain against the isolated Kind environment.

- Trusted `reroute_service_traffic` ToolExecutor registered with AIG (single SIDE_EFFECT tool).
- Human Plan Approval through the product's own admission path (not the bundled demo UI).
- Independent Authorization; durable claim; consumer receipt store.
- Unknown-outcome, duplicate, concurrency, restart and wrong-identity tests.

**PASS:** end-to-end chain executes one real bounded traffic switch in Kind with durable
audit evidence; no retry on ambiguity; no production access by construction.

## Phase 4 — Competition-Grade Product Experience — **PASS (dashboard working; browser smoke test PASS)**

Goal: a demo-able product surface.

- React + TypeScript dashboard: incident timeline, before/after traffic health, action
  history, failure states that fail closed.
- Independent post-action verification presented with the incident record.

**PASS:** full demo path visible in the UI against the live Kind lab.

## Phase 5 — Demonstration & Submission — **PARTIAL (materials prepared; video, slides file, commit/push and submission pending the owner)**

Goal: official submission.

- End-to-end validation and rehearsal; demo video; slide presentation; repo polish.
- Cover image, descriptions/tags, application URL; official submission after re-verifying
  the deadline and guidelines (docs/COMPETITION.md).

**PASS:** every item in the submission readiness checklist is complete and verified.
