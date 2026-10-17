# TrafficFuse AI — Architecture

## Component overview

```text
                      ┌────────────────────────────────────────────┐
                      │               (future) Dashboard           │
                      │        React + TypeScript (Phase 4)         │
                      └───────────────────▲────────────────────────┘
                                          │ HTTP (untrusted input)
                      ┌───────────────────┴────────────────────────┐
                      │        src/trafficfuse/api (FastAPI)       │
                      │        health (Phase 0) → incident APIs    │
                      └───────────────────▲────────────────────────┘
                                          │
        ┌─────────────────────────────────┼──────────────────────────────────┐
        │                                 │                                  │
┌───────┴────────┐              ┌─────────┴──────────┐            ┌───────────┴─────────┐
│   application  │              │  infrastructure    │            │       config        │
│ (orchestration │              │  - runtime surface │            │  env settings only  │
│  Phase 2/3)    │              │    (AIG v0.2.0)    │            │  (no secrets in     │
│                │              │  - K8s executor    │            │   source control)   │
│                │              │    (Phase 3)       │            └─────────────────────┘
│                │              │  - evidence probes │
│                │              │    (Phase 1/2)     │
└───────┬────────┘              └─────────┬──────────┘
        │                                │ trusted host code only
        ▼                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Agentic Intent Gate v0.2.0 (pinned)               │
│  Plan validation · persistent Runs · exact Plan-Approval binding      │
│  Action admission · independent Authorization · durable Action claim  │
│  Action Ledger · idempotency · fail-closed / Unknown outcomes         │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼ one trusted SIDE_EFFECT tool (Phase 3)
   reroute_service_traffic → disposable local Kind cluster only
```

## Ownership boundary

| Capability | Owner |
| --- | --- |
| Incident evidence collection, HTTP probes | TrafficFuse AI |
| Deployment / service health analysis | TrafficFuse AI |
| Model-specific incident reasoning, consumer `ModelProvider` | TrafficFuse AI |
| Trusted Kubernetes ToolExecutor (`reroute_service_traffic`) | TrafficFuse AI |
| Exact target allowlists and Kubernetes preconditions | TrafficFuse AI |
| Consumer-owned durable effect receipts | TrafficFuse AI |
| Independent post-action verification | TrafficFuse AI |
| Product UI and incident reporting | TrafficFuse AI |
| Plan validation, persistent Runs, exact Plan-Approval binding | Agentic Intent Gate |
| Action admission, independent Authorization | Agentic Intent Gate |
| Durable Action claim, execution control, Action Ledger, audit events | Agentic Intent Gate |
| Idempotent command handling, CAS, fail-closed recovery, Unknown protection | Agentic Intent Gate |

**Rule:** the right-hand column is never re-implemented, monkeypatched, forked, or bypassed.
TrafficFuse consumes the pinned installed distribution `agentic-intent-gate==0.2.0`
(commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1`) through the public module
`agentic_intent_gate.runtime` only.

## Trust boundaries

- **Model output** (Ollama `phi4-mini` via a consumer `ModelProvider`, Phase 2): untrusted
  input to the Plan schema. It cannot select credentials, executors, binding revisions,
  scopes, execution budgets, or grant decisions.
- **HTTP input** (API/Dashboard): untrusted. It cannot register code or trusted identities.
- **Trusted host code** (`src/trafficfuse/infrastructure/`): the only place trusted identity
  configuration lives; reviewed by a human.
- **Future Kubernetes mutations**: limited to explicitly approved, disposable, allowlisted
  local Kind resources. Production clusters are never reachable by the product.
- **Bundled demo tooling** of the shared runtime (`service-demo` prompt, browser admission
  UI) is demo-specific and is not reused; TrafficFuse provides its own provider and admission path.

## Planned single-tool execution workflow (Phase 3)

```text
evidence → Provider proposes Plan → AIG validates + persists Run
   → human approves the exact Plan → Action admission
   → independent Authorization → durable Action claim
   → reroute_service_traffic executor dispatch (5s default budget starts after claim)
   → durable receipt → TrafficFuse independent health verification
```

- One explicitly registered trusted `SIDE_EFFECT` tool per Runtime composition:
  `reroute_service_traffic` (one controlled Kubernetes Service traffic-switch).
- **No generic multi-agent orchestration, MCP execution, or multi-tool support is assumed.**

## Execution budgets

- Default trusted adapter execution budget: **5 seconds** (used from the start).
- The extended 15-second profile requires the upstream-mandated fresh reviewed base
  `ToolBinding` transformed with `execution_budget_binding`, a fresh UUIDv4 scope, separate
  ledger/receipt identity, fresh Run/Approval/Authorization, and host-side opt-in. It is
  **documented but not activated**; it may never be selected by model output, tool arguments,
  browser parameters, or API clients.
- Any caller-supplied deadline can only shorten, never extend, the budget.

## Evidence flows

- **Before/after evidence**: HTTP probe error rates and Kubernetes deployment/service state
  captured before and after the action, stored with the incident record.
- **Synthetic data** (if ever used in tests or demos) is explicitly labeled and never
  presented as real production evidence.

## Failure and Unknown-outcome behavior

- Ambiguous post-dispatch outcomes are terminal `Unknown`: no automatic retry, no
  reconciliation, no duplicate dispatch, no recovery replay.
- Proven pre-dispatch failures (input rejection, identity denial) are confirmed failures
  with no effect.
- **AIG Action success evidence ≠ application-level service recovery.** Recovery is proven
  only by TrafficFuse's independent verification.

## Phase 0 implementation state

Implemented: FastAPI health endpoint; pinned runtime integration surface
(`src/trafficfuse/infrastructure/runtime.py`: pins, version/commit verification, public API
import, budget introspection); environment-based settings.

Intentionally empty (`__init__.py` documentation only): `application/`, `domain/` — their
contents belong to later phases. No placeholder logic pretends AI diagnosis or Kubernetes
execution exists.
