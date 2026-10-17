# TrafficFuse AI — Phase 1A Design (Offline Failure Lab)

**Scope:** everything the Kubernetes failure lab needs, except cluster execution.
Phase 1A implements and tests the components offline; creating or touching a real
cluster is Phase 1B and requires explicit authorization (docs/PHASE1-PREFLIGHT.md).

## 1. Architecture

```text
                  trafficfuse.lab (consumer-owned, offline-ready)
                  ┌──────────────────────────────────────────────┐
                  │ service.py   synthetic workload (2 modes)     │
                  │ fault.py     deterministic fault injection     │
                  │ probe.py     real-HTTP measurement + evidence  │
                  │ probe_cli.py in-cluster entrypoint (python -m) │
                  │ lifecycle.py safety guards (offline planning)  │
                  └──────────────────────────────────────────────┘
                                     │ manifests only (no execution)
                                     ▼
                  deploy/lab/*.yaml   (applied in Phase 1B only)
```

The lab is a **read-only measurement capability** plus an inert workload. It does
not register, call, or bypass any shared-runtime mechanism: no Plan, Approval,
Authorization, Action or Ledger is involved in Phase 1A, and none may be
re-implemented here (AGENTS.md).

## 2. Kubernetes resource topology (future Phase 1B cluster `trafficfuse-lab`)

```text
                    ┌────────────────────────────────────────────┐
                    │ namespace: trafficfuse-system              │
                    │                                            │
                    │  trafficfuse-live (Service, ClusterIP)      │
                    │    selector: version=faulty  (initial)      │
                    │        │                                   │
                    │        ├──► Pods: trafficfuse-faulty (2)    │
                    │        │      /checkout ~50% HTTP 500       │
                    │        │      /ready 200 (valid endpoint)   │
                    │        │                                   │
                    │  trafficfuse-faulty / trafficfuse-healthy    │
                    │  (version-specific ClusterIP endpoints)     │
                    │        └──► Pods: trafficfuse-healthy (1)    │
                    │               /checkout 200 always          │
                    │                                            │
                    │  Job: trafficfuse-probe (on demand)          │
                    │    → probe_cli → /evidence/*.json (emptyDir) │
                    └────────────────────────────────────────────┘
```

- `trafficfuse-live` is the single stable client-facing Service name; its
  selector initially points at `faulty` to create the reproducible degraded
  scenario. The healthy version is deployed alongside it, ready for remediation.
- Phase 3 moves the selector to `version=healthy` **only** through the trusted
  `reroute_service_traffic` executor approved by the shared runtime. Phase 1A
  contains no mutation path (verified by tests: no RBAC kinds, no tool command,
  no switch endpoint).
- Pod labels: `app: trafficfuse`, `version: healthy|faulty`, plus ownership
  markers (`trafficfuse.io/owner`, `app.kubernetes.io/managed-by`).

## 3. Fault injection model (deterministic)

`FaultPolicy` fails every N-th request, N derived from the configured target:

| Configured rate | Interval | Approx. realized rate |
| --- | --- | --- |
| 0% | never | 0% |
| 30% | every 3rd | ~33% |
| 50% | every 2nd | 50% |
| 100% | every request | 100% |
| > 50% (e.g. 75%) | every 2nd | ~50% (never overshoots) |

- No randomness anywhere: the same request numbers always produce the same
  outcome; tests assert exact status sequences.
- The configured percentage is a **policy input**, never a measurement.
- `/ready` returns 200 in faulty mode on purpose: the Pod must remain a valid
  Service endpoint so the failure is user-visible, not infrastructure-level.

## 4. Probe methodology

- Consumer-owned, read-only HTTP `GET` prober with bounded concurrency
  (`asyncio.Semaphore`), per-request timeout, and explicit outcome
  classification: `success`, `http_error`, `timeout`, `transport_error`,
  `malformed` (200 with a non-JSON body).
- Success is defined as `200 ≤ status < 400` **and** a JSON-decodable body.
- Observed error rate and latency statistics (`min/p50/p95/max/mean`) are
  computed from real responses only.
- The same component runs offline (ASGI transport in tests) and in-cluster
  (`python -m trafficfuse.lab.probe_cli` in the probe Job).
- No Prometheus/Grafana: direct HTTP measurement is the actual requirement.

## 5. Evidence schema (`trafficfuse-probe-evidence-v1`)

```json
{
  "schema": "trafficfuse-probe-evidence-v1",
  "scenario_id": "string",
  "target_label": "version:faulty | service:trafficfuse-live",
  "target_url": "string",
  "started_at_utc": "ISO-8601",
  "completed_at_utc": "ISO-8601",
  "total_requests": 20,
  "success_count": 10,
  "error_count": 10,
  "observed_error_percent": 50.0,
  "latency_ms": {"min": 0.78, "p50": 1.07, "p95": 1.53, "max": 3.24, "mean": 1.19},
  "evidence_source": "real_http_responses",
  "synthetic_workload": true,
  "workload_declaration": "…configured percentage is never a measured outcome…",
  "records": [{"sequence": 1, "outcome": "success", "status_code": 200, "duration_ms": 3.24, "detail": ""}]
}
```

Raw per-request records are preserved for auditability; a configured target
percentage never appears as a measured field.

## 6. Resource sizing (development workstation)

| Resource | Requests | Limits |
| --- | --- | --- |
| lab service container | 50m CPU / 64Mi | 500m CPU / 128Mi |
| probe container | 50m CPU / 64Mi | 500m CPU / 128Mi |

Three Pods (1 healthy + 2 faulty) plus one short-lived probe Job — comfortably
inside the verified machine capacity (16 cores, 31.3 GB RAM; docs/PHASE1-PREFLIGHT.md).

## 7. Identity and ownership design

- **Standardized identity:** cluster `trafficfuse-lab`, context
  `kind-trafficfuse-lab`, namespace `trafficfuse-system`. Any other value fails
  closed (`Settings.validate`, lifecycle guards).
- **Dedicated kubeconfig:** `.runtime/kubeconfig/trafficfuse-lab.yaml`
  (git-ignored). The global `~/.kube/config` and the ambient context are never
  read or trusted; `kind`/`kubectl` must always receive explicit paths.
- **Ownership metadata:** every manifest carries
  `trafficfuse.io/owner=trafficfuse-lab` and
  `app.kubernetes.io/managed-by=trafficfuse-lab`.
- **Ownership receipt:** Phase 1B tooling writes
  `.runtime/state/cluster-trafficfuse-lab.json`; destruction requires that
  receipt (verified cluster/context/namespace) — a matching cluster *name*
  alone is never deletion authority.
- **Known foreign clusters** (`incident-lab`, `pcp-dev`, `playground-crd`,
  `vmware-k8s`) are refused by name, as is any non-standard target.

## 8. Configuration assumptions

- Ports come from `Settings` (validated 1–65535; reference machine has
  8000/8080/8081/8443 free — **5000 is occupied** and never reserved).
- `.env` semantics unchanged: repository `.env` auto-loaded, process environment
  always wins, credentials never in source.
- The lab image `trafficfuse-lab-service:phase1a` is built in Phase 1B from
  `trafficfuse.lab.service`; manifests reference it with `IfNotPresent`.

## 9. Future Phase 3 traffic-switch compatibility

- The single stable Service name `trafficfuse-live` is exactly the resource the
  trusted `reroute_service_traffic` tool will patch in Phase 3 (selector
  `version=faulty` → `version=healthy`).
- Phase 1A deliberately ships **no** bypass route or independent mutation
  endpoint; the switch will exist only as a registered SIDE_EFFECT tool behind
  Plan Approval and independent Authorization.
- Probe evidence for healthy/faulty/live targets feeds the independent
  post-action verification designed in Phase 3.
