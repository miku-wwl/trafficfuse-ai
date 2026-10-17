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
│   └── config/               # environment-based settings, no secrets in source
├── tests/                    # offline smoke tests
└── docs/
    ├── PRODUCT-BRIEF.md
    ├── ARCHITECTURE.md
    ├── COMPETITION.md
    ├── PHASES.md
    └── PHASE0-REPORT.md
```

## Documentation

- [docs/PRODUCT-BRIEF.md](docs/PRODUCT-BRIEF.md) — users, problem, differentiation, success criteria
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, trust boundaries, execution design
- [docs/COMPETITION.md](docs/COMPETITION.md) — verified official requirements, unknowns, disclosures
- [docs/PHASES.md](docs/PHASES.md) — roadmap and acceptance gates
- [docs/PHASE0-REPORT.md](docs/PHASE0-REPORT.md) — executed verification evidence

## License

TrafficFuse AI is released under the **MIT License** — see [LICENSE](LICENSE).
External dependencies retain their own licenses; in particular the shared runtime
**Agentic Intent Gate** (<https://github.com/miku-wwl/agentic-intent-gate>) is a
separate MIT-licensed project consumed as a pinned external dependency.

## Scope boundaries

- This is a hackathon prototype, not production software.
- Production Kubernetes clusters are never accessed or mutated.
- No commit, push, release, or deployment happens without explicit authorization.
