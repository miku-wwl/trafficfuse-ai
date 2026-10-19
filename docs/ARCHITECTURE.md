# TrafficFuse AI — Architecture

## Component overview

```text
                      ┌────────────────────────────────────────────┐
                      │          Dashboard (dashboard/)            │
                      │        React + TypeScript + Vite           │
                      └───────────────────▲────────────────────────┘
                                          │ HTTP (untrusted input)
                      ┌───────────────────┴────────────────────────┐
                      │        src/trafficfuse/api (FastAPI)       │
                      │   incident API · mutation boundary · CLI   │
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

## Single-tool execution workflow (implemented, verified live 2026-10-19)

```text
probe Jobs (live + healthy baseline) → evidence bundle (EV-n, SHA-256)
   → local model diagnosis → grounding validator → Plan composed
     (model: goal/target/service/namespace/versions; host: cluster, UID,
      resourceVersion, evidence digest)
   → StagedProposalProvider → AIG validates + persists Run (AwaitingApproval)
   → human approves the exact Plan (fingerprint-bound)
   → capability holder: Action admission + independent Authorization
   → trusted ownership re-verification → durable Action claim
   → RerouteExecutor (5 s budget after claim): identity + inputs, evidence anchor
     (source, preconditions, justification, ≤30 min freshness), live pre-read,
     durable dispatch fence, ONE JSON patch with test ops, independent re-read,
     durable receipt
   → TrafficFuse re-probes the same Service → RECOVERED / NOT_RECOVERED / INCONCLUSIVE
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

## Implementation state (2026-10-19)

| Layer | Modules |
| --- | --- |
| Domain | `domain/evidence.py` (labelled, hashed evidence), `domain/diagnosis.py` (prompt, output contract, grounding, Plan composition), `domain/recovery.py` (measured verdict) |
| Infrastructure | `kube.py` (exact-shape kubectl gateway, probe template, `SelectorPatch`), `live_cluster.py`, `simulation.py` (labelled offline lab), `reroute.py` (tool contract, receipt store, executor, live port), `governance.py` (AIG composition per profile), `ollama.py`, `runtime.py` (pins) |
| Application | `evidence_collection.py` (bundle + degradation policy + anchors), `incidents.py` (orchestration over the AIG public API, red-team scenarios), `store.py` (incidents + append-only events) |
| API / UI | `api/app.py` (routes, loopback/replay mutation boundary), `api/composition.py` (live / simulation / replay), `cli.py`, `dashboard/` |

### Governance profiles

`.runtime/trafficfuse/<mode>/aig/` holds one private profile per mode:
`identity.json` (retained `ToolBinding`: `reroute_service_traffic` / `1` /
`trafficfuse-live` / adapter `kind-kubectl-reroute` or `offline-sim-reroute` /
`reroute-v1` / fresh UUIDv4 scope), `capability` (operator secret), `gate.sqlite3`
(AIG ledger) and `receipts.sqlite3` (consumer effect store). The live profile is
provisioned explicitly by the operator; the simulation profile is created on first
start because it cannot reach any infrastructure.

### Trust boundaries (as implemented)

- Model output and HTTP input are untrusted. The grounding validator rejects
  unknown fields, invented evidence ids and unsupported actions; the gate's tool
  schema and semantic rule decide every Plan argument.
- Mutating HTTP routes are refused before routing for non-loopback clients, for
  requests without the `X-TrafficFuse-Client` header (CSRF: the custom header forces a
  CORS preflight that is never granted) and in replay mode. A trusted-Host allowlist
  (`TRAFFICFUSE_ALLOWED_HOSTS`) defeats DNS rebinding. The execution capability arrives
  per request and is never returned or stored by the server or the dashboard.
- The governed patch exists only in the executor's live port; the API can read and
  create probe Jobs but has no path to the Service mutation.
- Live operations require the dedicated kubeconfig; ownership is re-verified by the
  trusted collector before each execution.
