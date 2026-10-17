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

## 3. Fault injection model (deterministic, exact)

`FaultPolicy` implements an exact Bresenham distribution over the request index `k`
(0-based): a request fails iff `floor((k+1)·P/100) > floor(k·P/100)`.

| Configured rate | Realized failures per 100 requests |
| --- | --- |
| 0% | 0 (never) |
| 1% | 1 |
| 30% | 30 |
| 50% | 50 |
| 75% | 75 |
| 99% | 99 |
| 100% | 100 (always) |

- Exactly `P` failures per complete 100-request sequence (`2P`/`3P` across 200/300), evenly
  spread, never clustered at the boundaries. (Replaces the Phase 1A interval rotation, which
  was exact only for rates dividing 100 and capped at ~50% for higher rates — review finding
  P2; see docs/PHASE1A1-HARDENING-REPORT.md.)
- No randomness anywhere: the same `(rate, request_number)` pair always produces the same
  outcome; tests assert exact status sequences over the full matrix.
- The probe stamps every request with `X-TrafficFuse-Request-Index`. With the index, the
  decision is a pure function of the probe's sequence number, so the exact distribution holds
  **globally across multiple faulty Pods** behind the Service. Without it, each Pod falls
  back to its own counter: deterministic per Pod, with a combined total bounded to within
  ±1 request per replica. The header proves client-side exactness; it does not prove
  distributed request ordering.
- The configured percentage is a **policy input**, never a measurement.
- `/ready` returns 200 in faulty mode on purpose: the Pod must remain a valid Service
  endpoint so the failure is user-visible, not infrastructure-level.

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

**Durable representation (Phase 1A.1):** the CLI always prints the complete document to
stdout between `TRAFFICFUSE_EVIDENCE_BEGIN` / `TRAFFICFUSE_EVIDENCE_END` markers (progress to
stderr only), so `kubectl logs` yields verifiable evidence even if the Pod's emptyDir is lost.
Documents are strictly validated before emission (schema, identity, counts, outcome
classification, sequence completeness 1..total, latency, observed-rate consistency, synthetic
declaration, timestamps). Phase 1B collection: `kubectl … logs job/trafficfuse-probe` →
`python -m trafficfuse.lab.evidence_cli` → `artifacts/phase1b/<run-id>/` with raw logs
preserved byte-for-byte (docs/PHASE1A1-HARDENING-REPORT.md §1).

**Timestamp strictness (Phase 1A.2):** `started_at_utc` / `completed_at_utc` must be
ISO-8601 **with an explicit UTC offset** — `Z`/`z` or `+00:00` (any other explicit offset is
normalized to UTC). A naive timestamp (no offset), a malformed value or a non-string is
refused via the controlled `TimestampValidationError` (a `ValueError` subclass), and
`completed_at_utc` may never precede `started_at_utc` after normalization.

**Byte preservation (Phase 1A.2):** collection accepts **raw bytes**
(`collect_evidence_from_log_bytes`); the strict UTF-8 decode exists only as a private parse
copy, so nothing is transcoded before it hits disk. The raw capture is written first
(atomically, temp file + `os.replace`), is bounded by `MAX_RAW_LOG_BYTES`, and is preserved
**even when extraction or validation fails**; each run directory carries `evidence-audit.json`
(exact byte length, SHA-256, strict UTC timestamp, failure stage). Non-UTF-8 or oversized
captures are refused before parsing.

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
  closed (`Settings.validate`, lifecycle guards). A namespace is never a context.
- **Dedicated kubeconfig:** `.runtime/kubeconfig/trafficfuse-lab.yaml`
  (git-ignored). The global `~/.kube/config` and the ambient context are never
  read or trusted. **Phase 1A.2:** the path is not merely shape-checked — it is
  derived from the module's own repository root and must resolve to exactly that
  canonical path, so a foreign repository with an identical directory layout is
  refused (shape is not ownership). Symlink escapes and traversal are refused;
  every kubectl invocation carries the dedicated kubeconfig and exact context
  explicitly (`lifecycle.kubectl_prefix()`).
- **Ownership metadata:** every manifest carries
  `trafficfuse.io/owner=trafficfuse-lab` and
  `app.kubernetes.io/managed-by=trafficfuse-lab`.
- **Creation record (schema `trafficfuse-ownership-record-v2`):** Phase 1B
  tooling writes `.runtime/state/cluster-trafficfuse-lab.json` containing the
  cluster/context/namespace, the API server, the CA certificate SHA-256
  fingerprint, the exact kind node names, creation timestamp (strict
  timezone-aware UTC), creator and kubeconfig reference. The record is
  **metadata, not cryptographic proof**.
- **Ownership verification (Phase 1A.1):** destruction requires a complete
  creation record *and* live ownership evidence that matches it exactly — API
  server, CA fingerprint, node identities and ownership labels — plus explicit
  authorization. The kubeconfig document itself is verified offline (exact
  context→cluster binding, local HTTPS API server only, CA fingerprint anchor,
  no foreign contexts, no embedded static credentials). A matching cluster
  *name* alone is never deletion authority.
- **Trusted live-facts collection (Phase 1A.2):** the only facts that can
  authorize a destructive operation are **collector-sourced**
  (`trafficfuse.lab.cluster_facts`): allowlisted, read-only commands (`config
  view`, `get nodes`, `get namespace`, `docker inspect`), each carrying the
  dedicated kubeconfig and exact context, each verified read-only *before*
  execution, failing closed on any deviation. Caller-supplied JSON facts are
  checked when explicitly requested (`lifecycle_cli verify --facts`) but are
  marked **untrusted** and never sufficient proof for destruction.
- **Known foreign clusters** (`incident-lab`, `pcp-dev`, `playground-crd`,
  `vmware-k8s`) are refused by name, as is any non-standard target.

## 8. Configuration assumptions

- Ports come from `Settings` (validated 1–65535; reference machine has
  8000/8080/8081/8443 free — **5000 is occupied** and never reserved).
- `.env` semantics unchanged: repository `.env` auto-loaded, process environment
  always wins, credentials never in source.
- The lab image `trafficfuse-lab-service:phase1a` is built from
  `deploy/lab/Dockerfile` (digest-pinned Python 3.13 base, dependency pins
  mirroring `uv.lock`, non-root, dual entrypoints for the service and the probe
  CLI). Manifests reference it with `imagePullPolicy: Never`; kind loads it
  into the node (`kind load docker-image …`) in Phase 1B. The image installs
  **no** Agentic Intent Gate — the lab package does not import it (enforced by
  test).

## 9. Future Phase 3 traffic-switch compatibility

- The single stable Service name `trafficfuse-live` is exactly the resource the
  trusted `reroute_service_traffic` tool will patch in Phase 3 (selector
  `version=faulty` → `version=healthy`).
- Phase 1A deliberately ships **no** bypass route or independent mutation
  endpoint; the switch will exist only as a registered SIDE_EFFECT tool behind
  Plan Approval and independent Authorization.
- Probe evidence for healthy/faulty/live targets feeds the independent
  post-action verification designed in Phase 3.
