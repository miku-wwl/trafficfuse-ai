# TrafficFuse AI — Phase 1 Preflight (Read-Only)

**Date:** 2026-10-17 · **Method:** read-only inspection only — no cluster was created,
deleted, or modified; no Kubernetes mutation was executed; no Docker configuration
changed; no images pulled.

## 1. Actual environment findings

### Docker

| Item | Finding |
| --- | --- |
| Client / server | `29.8.0` / `29.8.0` — daemon reachable |
| Current context | `desktop-linux` (Docker Desktop) |
| Containers | 9 total, **2 running**, both foreign to TrafficFuse: `incidentgraph-agent-phase1-falkordb-1` (another project's FalkorDB) and `playground-crd-control-plane` (`kindest/node:v1.37.0` — a node of another project's kind cluster) |

### Kind

| Item | Finding |
| --- | --- |
| Version | `v0.33.0` (`go1.26.7`, windows/amd64) |
| Existing clusters | **`incident-lab`, `pcp-dev`, `playground-crd`** — all pre-existing, owned by other projects |

### kubectl

| Item | Finding |
| --- | --- |
| Client | `v1.36.1` (Kustomize v5.8.1) |
| **Current context** | **`kind-playground-crd` — a cluster TrafficFuse does NOT own** |
| Contexts | many: `k3d-job-demo`, `k3d-trace-demo`, `kind-argo-canary-local`, `kind-incident-lab`, `kind-pcp-management-local`, `kind-pcp-target-b-local`, `kind-pcp-target-local`, `kind-playground-crd` (current), and **`vmware-k8s` (namespace `k8s-failure-lab`) — a non-kind, treat-as-production/non-disposable cluster** |
| Stale context entries | several listed contexts (`kind-argo-canary-local`, `kind-pcp-*`) no longer appear in `kind get clusters` |

### Resources

| Item | Finding |
| --- | --- |
| CPU | 16 logical cores (Docker sees 16) |
| RAM | 31.3 GB host; Docker allocation 16.4 GB — sufficient for one small kind cluster |
| Disk | 30.9 GB free on `C:` |
| Ports | `8000`, `8080`, `8081`, `8443` free; **`5000` is occupied** (PID 4, system) — must not be used |
| Tooling | Python `3.13.14`, `uv 0.11.16`, git `2.55.0` |

## 2. Risks and safety boundaries

1. **Foreign clusters and foreign ambient context.** The machine hosts three
   other kind clusters and many foreign contexts; `kubectl` currently points at
   `kind-playground-crd`. Every Phase 1 command must pass an explicit
   `--context kind-trafficfuse-lab` and must never rely on the ambient context.
2. **`vmware-k8s` is non-disposable.** No Phase 1 tooling may reference,
   target, read from, or write to it. It is recorded here as a known hazard only.
3. **Destructive commands must be name-pinned.** Any cleanup is restricted to
   resources inside `kind trafficfuse-lab`; cluster deletion accepts only the
   exact name `trafficfuse-lab` behind an explicit guard.
4. **No mutation occurred in this preflight** — `kubectl get nodes` was
   deliberately *not* run because the ambient context is foreign.

## 3. Proposed disposable test cluster (for later approval)

| Property | Value |
| --- | --- |
| Cluster name | `trafficfuse-lab` (unique, TrafficFuse-owned; context `kind-trafficfuse-lab`) |
| Topology | single-node kind cluster (default `kindest/node`), local registry not required |
| Namespace | `trafficfuse-system` — standardized in Phase 1A, **superseding** the earlier `trafficfuse-demo`/`trafficfuse-evidence` proposal; the `default` namespace is never used |
| Workloads | `trafficfuse-healthy` (HTTP 200) and `trafficfuse-faulty` (deterministic, exact configured 500 rate — Phase 1A.1 matrix) from the shared image `trafficfuse-lab-service:phase1a` |
| Failure injection | env-driven fault switch (`TRAFFICFUSE_LAB_FAULT_RATE_PERCENT`) — exact deterministic rate, no randomness across runs |
| Probes | consumer-owned HTTP prober (Python) against the live/version Services, recording status codes and latency per version, with `X-TrafficFuse-Request-Index` for exact cross-Pod distribution |
| Measurements | error rate and latency (before/after), persisted as real incident evidence; synthetic data never presented as real |
| Cleanup | `kind delete cluster --name trafficfuse-lab` only, guarded by exact-name checks **and verified ownership evidence**; helpers for observed-only inspection |

## 4. Required permissions (explicit user approval needed)

1. Create and (on completion) delete the kind cluster `trafficfuse-lab`.
2. Consume local Docker resources for the kind node.
3. Bind local ports from the free set (`8000`, `8080`, `8081`, `8443`; never `5000`).
4. Use `kubectl` exclusively with `--context kind-trafficfuse-lab`.

## 5. Exact conditions before cluster creation

- Written user approval naming cluster `trafficfuse-lab` (no implicit consent).
- Docker daemon reachable and at least one free port from the allowed set.
- The creation/cleanup scripts reviewed (name-pinned deletion guard in place).
- No other context is active during execution; `vmware-k8s` must never be touched.

## 6. Phase 1 implementation tasks and acceptance criteria

**Tasks**

1. Kind cluster definition + guarded create/destroy scripts for `trafficfuse-lab`.
2. Demo service image with healthy/faulty modes and deterministic fault switch.
3. `trafficfuse-demo` and `trafficfuse-evidence` namespaces, deployments, services.
4. HTTP prober collecting real status/latency evidence per service version.
5. Evidence storage (SQLite) + reproducible failure reproduction.
6. Tests proving the faulty version measurably degrades (real HTTP evidence).

**Acceptance gates**

- **PASS:** faulty vs. healthy services measurably differ on real probes; cluster
  create/destroy is deterministic; only `trafficfuse-lab` resources are used and
  cleaned; every kubectl call is context-pinned; nothing left running afterwards.
- **PARTIAL:** lab works but evidence collection or fault determinism is incomplete.
- **BLOCKED:** Docker/kind unavailable at execution time — record the exact blocker.

## 7. Readiness

`PHASE1_READINESS=PARTIAL` — the machine has every required tool running
(Docker 29.8.0 daemon up, kind v0.33.0, kubectl v1.36.1) with ample resources
and usable ports. It is **not READY** only because Phase 1 requires an explicit
cluster-creation approval and the ambient Kubernetes context is foreign to this
project; both are conditions for authorization, not environment gaps.

## 8. Phase 1B workflow (documentation only — not executed)

After Phase 1A.1 hardening (docs/PHASE1A1-HARDENING-REPORT.md) and the Phase 1A.2
final preflight corrections (docs/PHASE1A2-FINAL-PREFLIGHT-REPORT.md), Phase 1B
requires the same explicit authorizations above **plus** a real image build
(offline build was impossible: no staged wheels).

The authoritative operator sequence is **docs/PHASE1B-RUNBOOK.md** (superseding
the sketch previously in this section and in the hardening report §7, which had a
step-ordering defect: the image was listed to load *before* cluster creation — a
kind image can only load into the nodes of an existing cluster). The corrected
order: repository/approval/precondition gates → kubeconfig prep → image build and
static check → offline preflight → `kind create cluster` with the dedicated
kubeconfig → **image load (after creation)** → creation record + trusted
collector verification (`lifecycle_cli verify --collect`; caller JSON is
untrusted) → `apply -k deploy/lab` → rollout wait → confirm `trafficfuse-live`
selects `version=faulty` → probe Job → byte-preserving evidence export →
measurements/report → environment preserved. Exact identities: cluster
`trafficfuse-lab`, context `kind-trafficfuse-lab`, namespace `trafficfuse-system`,
node `trafficfuse-lab-control-plane`, kubeconfig
`.runtime/kubeconfig/trafficfuse-lab.yaml`.

Every kubectl invocation carries `--kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
--context kind-trafficfuse-lab` explicitly (the namespace is passed as
`-n trafficfuse-system`, never as a context); the ambient context — the foreign
`kind-playground-crd` — is never read, switched or relied upon, and the foreign
clusters `incident-lab`, `pcp-dev`, `playground-crd` and the non-disposable
`vmware-k8s` are never valid targets.
