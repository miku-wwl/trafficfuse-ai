# TrafficFuse AI

**Evidence-Governed AI Release Circuit Breaker** — a consumer application for the
**TechEx Amsterdam Hackathon 2026**.

> **Status — Phase 0: PASS (local verification complete).** Repository foundation, the pinned
> Agentic Intent Gate v0.2.0 dependency, lockfile, offline smoke tests and the FastAPI health
> foundation are in place and verified. No AI diagnosis, Kubernetes execution, or dashboard
> exists yet — see [docs/PHASES.md](docs/PHASES.md) for the roadmap and
> [docs/PHASE0-REPORT.md](docs/PHASE0-REPORT.md) for verification evidence.
>
> **Phase 0.1 — review fixes applied:** MIT `LICENSE`, automatic local `.env` loading with
> process-environment precedence, and portable dependency-origin tests. See
> [docs/PHASE0.1-REVIEW-FIXES.md](docs/PHASE0.1-REVIEW-FIXES.md).
>
> **Phase 1A — offline failure lab implemented:** deterministic healthy/faulty workload, real-HTTP
> probe with structured evidence, Kubernetes manifests for the future `trafficfuse-lab` cluster, and
> safety-guarded (offline) lifecycle tooling — 83 offline tests green. No cluster has been created
> or touched. See [docs/PHASE1A-REPORT.md](docs/PHASE1A-REPORT.md) and
> [docs/PHASE1A-DESIGN.md](docs/PHASE1A-DESIGN.md).
>
> **Phase 1A.1 — deployment readiness hardening: PASS.** Reproducible lab image definition
> (`deploy/lab/Dockerfile`, pinned to `uv.lock`, non-root; offline build reported
> BUILD_NOT_VERIFIED — no wheels staged, no network used), durable probe-evidence export
> (stdout `TRAFFICFUSE_EVIDENCE_*` markers, strict validation, `artifacts/phase1b/<run-id>/`
> collection), stronger Kubernetes ownership guards (kubeconfig document verification with a
> CA-fingerprint anchor, v2 creation record, live-facts ownership verification, mandatory
> negative tests), and an **exact** deterministic fault-rate algorithm (configured P% → exactly
> P failures per 100 requests). 240 offline tests green; `AGENTS.md` and the pinned runtime
> untouched. See [docs/PHASE1A1-HARDENING-REPORT.md](docs/PHASE1A1-HARDENING-REPORT.md).
>
> **Phase 1A.2 — final preflight corrections: PASS.** Exact repository-owned kubeconfig path
> enforcement (a foreign repository with an identical layout is refused), a **trusted
> read-only live-facts collector** whose commands are allowlisted, explicitly scoped and
> fail-closed — caller-supplied facts are never sole proof of ownership, strict
> timezone-aware (UTC) evidence timestamps, and byte-for-byte raw-log preservation (bounded,
> atomic writes, audit metadata, raw logs preserved on parse failure). The Phase 1B operator
> runbook ordering is corrected (the image loads **after** cluster creation; the context is
> `kind-trafficfuse-lab`, never the namespace). 295 offline tests green, 1 skipped;
> `AGENTS.md` and the pinned runtime untouched. See
> [docs/PHASE1A2-FINAL-PREFLIGHT-REPORT.md](docs/PHASE1A2-FINAL-PREFLIGHT-REPORT.md) and
> [docs/PHASE1B-RUNBOOK.md](docs/PHASE1B-RUNBOOK.md).
>
> **Phase 1B entry review — safe deployment gate: PASS.** Four findings corrected before any
> execution: (1) the namespace/ownership-verification circular ordering — namespace bootstrap
> now precedes the namespace-dependent collector read (Stages C→D); (2) direct cluster deletion
> removed from the authorized Phase 1B sequence (preserve + separate cleanup authorization);
> (3) spoofed collector provenance eliminated — trust is established by **execution**: facts
> carry a private collector execution receipt minted only inside the collector, the destructive
> gate performs the collection itself and accepts no caller facts, and the `source` string is
> gone; (4) credential disclosure removed — kubeconfig identity comes from the trusted local
> parser (endpoint + CA fingerprint only), outputs are redacted, and no raw kubeconfig view
> command appears in the workflow. 327 offline tests green, 1 skipped. See
> [docs/PHASE1B-ENTRY-REVIEW.md](docs/PHASE1B-ENTRY-REVIEW.md).

## What TrafficFuse AI is

A new deployment starts failing: HTTP 500 rates spike, the blast radius is unclear, and a
human is pressured into authorizing a risky manual traffic change. TrafficFuse AI turns that
moment into a governed, evidence-backed decision:

1. Real evidence is collected (HTTP probes, deployment metadata, Kubernetes state).
2. An AI model reasons over the evidence and proposes a bounded remediation Plan.
3. **Agentic Intent Gate** validates and persists the Plan.
4. A human approves the *exact* Plan.
5. A **separate** authorization permits exactly one Action.
6. A trusted executor performs one controlled Kubernetes traffic-switch.
7. TrafficFuse independently verifies service health and presents before/after evidence.

A recorded Action success is never mistaken for proof of service recovery — the application
verifies health independently.

## Safety model (summary)

- Only disposable, explicitly approved local **Kind** environments may ever be mutated;
  production Kubernetes clusters are strictly out of scope.
- Human **Plan Approval** and **Action Authorization** are distinct gates; approval alone
  never dispatches an effect.
- The AI proposes Plans; it never selects credentials, execution identities, scopes,
  budgets, or grant decisions.
- Ambiguous post-dispatch outcomes are terminal `Unknown` — no automatic retry, no replay.
- Synthetic or simulated metrics are always labeled and never presented as real production
  evidence.
- The shared runtime is **reused as a pinned installed dependency**, never copied, forked,
  monkeypatched, or re-implemented.

The binding rules live in [AGENTS.md](AGENTS.md) (upstream runtime rules plus a
TrafficFuse-specific section) and govern all development.

## Architecture at a glance

| Concern | Owner |
| --- | --- |
| Evidence collection, HTTP probes, Kubernetes inspection | TrafficFuse AI |
| Deployment/service health analysis, incident reasoning, consumer `ModelProvider` | TrafficFuse AI |
| Trusted Kubernetes executor (`reroute_service_traffic`), target allowlists, preconditions | TrafficFuse AI |
| Independent post-action verification, incident UI and reporting | TrafficFuse AI |
| Plan validation, persistent Runs, exact Plan-Approval binding, Action admission | Agentic Intent Gate |
| Independent Authorization, durable Action claim, Action Ledger, idempotency, fail-closed outcomes | Agentic Intent Gate |

Full detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

Requires Python 3.13 and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone git@github.com:miku-wwl/trafficfuse-ai.git
cd trafficfuse-ai
cp .env.example .env        # optional; .env is git-ignored. It is loaded automatically,
                           # and explicit process environment variables always win.
uv sync                     # resolves uv.lock, installs the pinned runtime + dev tools
uv run pytest               # offline smoke tests: pinned dependency, public API, health endpoint
uv run uvicorn trafficfuse.api.app:app --reload
curl http://127.0.0.1:8000/health
```

## Failure lab (offline — Phase 1A / 1A.1)

The synthetic workload, real-HTTP probe, and guarded lifecycle planner run without any cluster:

```bash
uv run python -m trafficfuse.lab.service                                   # healthy workload on :8080
TRAFFICFUSE_LAB_MODE=faulty TRAFFICFUSE_LAB_FAULT_RATE_PERCENT=75 \
    uv run python -m trafficfuse.lab.service                               # exactly 75% deterministic 500s
uv run python -m trafficfuse.lab.lifecycle_cli preflight \
    --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml                  # non-mutating Phase 1B plan
uv run python -m trafficfuse.lab.lifecycle_cli verify \
    --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \
    --receipt .runtime/state/cluster-trafficfuse-lab.json --collect  # trusted read-only ownership check
# --facts <file> is an untrusted, inspection-only alternative: never sufficient
# proof for a destructive operation.
```

The lab image is defined by `deploy/lab/Dockerfile` (build context: repository root):

```bash
docker build --pull -f deploy/lab/Dockerfile -t trafficfuse-lab-service:phase1a .   # Phase 1B (network required)
```

Cluster execution (`trafficfuse-lab`), image build/load, and the future
`reroute_service_traffic` tool are **not** executed in Phase 1A/1A.1/1A.2 and require
explicit authorization (docs/PHASE1-PREFLIGHT.md, docs/PHASE1A-REPORT.md,
docs/PHASE1A1-HARDENING-REPORT.md). The corrected operator sequence for Phase 1B
(cluster creation → image load → record + trusted verification → workloads → probe →
byte-preserving evidence export → preserved environment) is specified in
[docs/PHASE1B-RUNBOOK.md](docs/PHASE1B-RUNBOOK.md).

## Repository layout

```text
trafficfuse-ai/
├── AGENTS.md                 # binding agent rules (upstream + TrafficFuse-specific section)
├── README.md
├── pyproject.toml            # dependencies incl. the pinned shared runtime (immutable commit)
├── uv.lock                   # reproducible lockfile (verified by uv sync --locked)
├── .env.example              # environment template — no secrets
├── .gitignore                # runtime data, kubeconfigs, credentials, Python/Node artifacts
├── src/trafficfuse/
│   ├── api/                  # FastAPI application (health endpoint in Phase 0)
│   ├── application/          # orchestration services — Phase 2/3, intentionally empty
│   ├── domain/               # domain model — Phase 1, intentionally empty
│   ├── infrastructure/       # external integrations: pinned runtime surface (Phase 0)
│   ├── config/               # environment-based settings, no secrets in source
│   └── lab/                  # Phase 1A: synthetic workload, fault policy, probe, lifecycle guards
├── deploy/lab/               # Kubernetes manifests + Dockerfile for the future trafficfuse-lab cluster (Phase 1B)
├── tests/                    # offline tests (dependency, API, lab service, fault matrix, probe, evidence, manifests, guards)
└── docs/
    ├── PRODUCT-BRIEF.md
    ├── ARCHITECTURE.md
    ├── COMPETITION.md
    ├── PHASES.md
    ├── PHASE0-REPORT.md
    ├── PHASE0.1-REVIEW-FIXES.md
    ├── PHASE1A-DESIGN.md
    ├── PHASE1A-REPORT.md
    ├── PHASE1A1-HARDENING-REPORT.md
    ├── PHASE1A2-FINAL-PREFLIGHT-REPORT.md
    ├── PHASE1B-ENTRY-REVIEW.md
    ├── PHASE1B-RUNBOOK.md
    └── PHASE1-PREFLIGHT.md
```

## Documentation

- [docs/PRODUCT-BRIEF.md](docs/PRODUCT-BRIEF.md) — users, problem, differentiation, success criteria
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, trust boundaries, execution design
- [docs/COMPETITION.md](docs/COMPETITION.md) — verified official requirements, unknowns, disclosures
- [docs/PHASES.md](docs/PHASES.md) — roadmap and acceptance gates
- [docs/PHASE0-REPORT.md](docs/PHASE0-REPORT.md) — executed verification evidence
- [docs/PHASE0.1-REVIEW-FIXES.md](docs/PHASE0.1-REVIEW-FIXES.md) — review fixes and git reconciliation
- [docs/PHASE1-PREFLIGHT.md](docs/PHASE1-PREFLIGHT.md) — read-only environment findings, cluster design
- [docs/PHASE1A-DESIGN.md](docs/PHASE1A-DESIGN.md) — failure-lab design and topology
- [docs/PHASE1A-REPORT.md](docs/PHASE1A-REPORT.md) — Phase 1A implementation evidence
- [docs/PHASE1A1-HARDENING-REPORT.md](docs/PHASE1A1-HARDENING-REPORT.md) — Phase 1A.1 review-fix
  evidence: image, evidence export, ownership guards, fault-rate matrix, Phase 1B workflow
- [docs/PHASE1A2-FINAL-PREFLIGHT-REPORT.md](docs/PHASE1A2-FINAL-PREFLIGHT-REPORT.md) — Phase 1A.2
  final preflight corrections: exact kubeconfig ownership, trusted live-facts collector,
  strict UTC timestamps, byte-preserving raw logs
- [docs/PHASE1B-ENTRY-REVIEW.md](docs/PHASE1B-ENTRY-REVIEW.md) — Phase 1B entry review: safe
  deployment gate (ordering, no deletion, execution-based trust, credential redaction)
- [docs/PHASE1B-RUNBOOK.md](docs/PHASE1B-RUNBOOK.md) — corrected Phase 1B operator runbook
  (Stages A–F, STOP conditions, preservation; no deletion)
  (documentation only; every mutating step requires explicit approval)

## License

TrafficFuse AI is released under the **MIT License** — see [LICENSE](LICENSE).
External dependencies retain their own licenses; in particular the shared runtime
**Agentic Intent Gate** (<https://github.com/miku-wwl/agentic-intent-gate>) is a
separate MIT-licensed project consumed as a pinned external dependency.

## Scope boundaries

- This is a hackathon prototype, not production software.
- Production Kubernetes clusters are never accessed or mutated.
- No commit, push, release, or deployment happens without explicit authorization.
