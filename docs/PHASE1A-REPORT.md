# TrafficFuse AI — Phase 1A Report (Offline Failure Lab Implementation)

**Date:** 2026-10-17 · **Scope:** Phase 1A only — offline implementation, no cluster operations ·
**Status: PASS** · `PHASE1B_READINESS=PARTIAL` (see §8)

## 1. Actual Git state

| Item | Value |
| --- | --- |
| Branch | `main` |
| HEAD | `627a63b103bd87e2be7cc64223379fac55cb2b6c` (unchanged; equals `origin/main`) |
| Working tree | 6 modified + 18 new files, **all uncommitted** (no commit/push performed) |
| `AGENTS.md` | sha1 `03870389d009b3d2` — identical to `origin/main`; byte-for-byte unchanged |
| AIG pin | `agentic-intent-gate==0.2.0` @ `7261fdd2f47d2b15fabe8faac527eeee37338eb1` — unchanged (verified via isolated interpreter and lockfile) |
| Foreign clusters | `incident-lab`, `pcp-dev`, `playground-crd` — **untouched** (no kind/kubectl command executed) |
| Kubernetes context | unchanged (ambient context is foreign and was never relied upon) |

## 2. Files created / modified

**Created (18):** `src/trafficfuse/lab/{__init__,fault,service,probe,probe_cli,lifecycle,lifecycle_cli}.py`;
`deploy/lab/{namespace,healthy-deployment,faulty-deployment,service-live,service-versions,probe-job,kustomization}.yaml`;
`tests/{test_lab_service,test_lab_probe,test_lab_manifests,test_lab_lifecycle}.py`;
`docs/PHASE1A-DESIGN.md`, `docs/PHASE1A-REPORT.md`.

**Modified (6):** `pyproject.toml` (httpx moved to runtime deps for the probe; `pyyaml` added to dev
for offline manifest validation), `uv.lock`, `.gitignore` (added `.runtime/`), `.env.example`
(standardized Phase 1 identity keys), `src/trafficfuse/config/settings.py` (standardized cluster
`trafficfuse-lab`; added context/namespace/kubeconfig/lab_port; fail-closed validation), `tests/test_config_settings.py`.

**Untouched:** `AGENTS.md`, the shared runtime repository, Git history, all Kubernetes contexts.

## 3. Commands executed and actual results

```text
uv lock                 # Resolved 32 packages (added pyyaml 6.0.3; httpx moved to runtime)
uv lock --check         # lockfile up to date
uv sync                 # Installed 2 packages (pyyaml), project reinstalled
uv sync --locked        # Checked 32 packages — reproducible
uv pip check            # All installed packages are compatible
uv run pytest           # 83 passed, 1 warning in 2.58s   (Phase 0.1 baseline: 14)
uv run python -m compileall -q src tests   # syntax OK
```

Test breakdown (all offline): FastAPI health + runtime pin + origin (5+2+5), settings (13),
lab service/fault (10), probe (8), manifests (13), lifecycle guards (13), config/API regression
(14 carried over). Failures found during the round and fixed at root cause: FastAPI response-model
error from a union return annotation (annotations removed), `MockTransport` bypasses connection-layer
timeouts (test converted to timeout-*classification*), two over-narrow structural assertions
(comment-vs-code scan; `KUBECONFIG` substring vs ambient-context API scan).

**Lifecycle CLI guard behavior (executed):**

```text
preflight --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
  → prints non-mutating plan (mutating=false), exit 0
create --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
  → REFUSED: cluster creation requires explicit, task-specific user authorization; exit 2
preflight --kubeconfig ~/.kube/config
  → REFUSED: refusing the global kubeconfig; exit 2
destroy --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --receipt … --authorized
  → REFUSED: no ownership receipt; refusing destructive operations without verifiable ownership; exit 2
```

## 4. Offline probe evidence (real HTTP responses, in-process ASGI transport)

Scenario `phase1a-offline-faulty` against the faulty workload (target rate 50%), 20 requests,
concurrency 4:

```json
{
  "schema": "trafficfuse-probe-evidence-v1",
  "scenario_id": "phase1a-offline-faulty",
  "target_label": "version:faulty",
  "total_requests": 20,
  "success_count": 10,
  "error_count": 10,
  "observed_error_percent": 50.0,
  "latency_ms": {"min": 0.782, "p50": 1.067, "p95": 1.53, "max": 3.241, "mean": 1.185},
  "evidence_source": "real_http_responses",
  "synthetic_workload": true,
  "workload_declaration": "…configured target failure percentage is a policy input, never a measured outcome."
}
```

Sample records: `#1 success/200`, `#2 http_error/500`, `#3 success/200` — deterministic rotation,
measured from real responses. The configured 50% and the measured 50.0% are deliberately separate
fields.

## 5. Kubernetes manifest validation results

13 offline YAML tests pass without contacting any cluster (PyYAML parsing only):

- namespace identity and ownership labels on every document;
- Deployment selectors exactly match Pod template labels (`app=trafficfuse`,
  `version=healthy|faulty`);
- `trafficfuse-live` Service: ClusterIP, `version=faulty` initial selector, port 80 → `http`;
- version-specific internal Services (`trafficfuse-healthy`, `trafficfuse-faulty`);
- no NodePort/LoadBalancer anywhere; images never `:latest`; requests/limits on every container;
  readiness probe on `/ready`;
- probe Job isolated (restartPolicy Never, evidence to an `emptyDir`, in-cluster DNS target);
- `kustomization.yaml` excludes the probe Job so probes run only on demand;
- no mutation/switch path in applied configuration (RBAC kinds and tool references absent from
  non-comment content).

## 6. Safety verification

| Requirement | Evidence |
| --- | --- |
| No cluster created/modified/deleted | no `kind`/`kubectl` mutating command executed this phase; foreign clusters untouched |
| No context change | ambient context never read; guards require the exact future context |
| Dedicated kubeconfig enforced | global kubeconfig refused (CLI + unit tests) |
| No shared-runtime duplication | no Plan/Approval/Authorization/Ledger code in the lab package; pin unchanged |
| No 15-second budget | default 5 s only; no runtime composition created |
| Synthetic labeling | workload declaration in every evidence payload; `/checkout` responses labelled |
| No secrets / no runtime DBs tracked | scan clean; `.runtime/` git-ignored; 24-file diff whitespace-clean |

## 7. Remaining limitations

- Real cluster behavior (scheduling, DNS, ClusterIP routing, probe Job execution) is unverified —
  Phase 1B by design.
- The lab image `trafficfuse-lab-service:phase1a` is not built yet (Phase 1B build/load step).
- Fault rotation approximates the configured rate and never overshoots (>50% stabilizes ~50%).
- Timeout *enforcement* is httpx's connection-layer responsibility; the probe's timeout
  classification is what the offline tests cover.
- One cosmetic warning remains (starlette `TestClient`/`httpx2` deprecation notice).
- All Phase 0.1 changes remain uncommitted together with Phase 1A — awaiting authorization.

## 8. Phase 1B readiness — PARTIAL

Ready: code, manifests, guards, and tests are complete and green; machine capacity verified
(16 cores / 31.3 GB RAM / 30.9 GB disk; ports 8000/8080/8081/8443 free; Docker 29.8.0, kind
v0.33.0, kubectl v1.36.1).

Not yet satisfied (require user approval):

1. **Cluster creation authorization** for `trafficfuse-lab` (explicit, name-pinned).
2. **Image build/load** into kind (`trafficfuse-lab-service:phase1a`).
3. Confirmation that no other process binds the chosen probe ports.

**Proposed Phase 1B commands (only after approval):**

```bash
kind create cluster --name trafficfuse-lab --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --context kind-trafficfuse-lab create namespace trafficfuse-system
# build + load the lab image (docker/podman build → kind load docker-image)
kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --context kind-trafficfuse-lab apply -k deploy/lab
# write .runtime/state/cluster-trafficfuse-lab.json (ownership receipt), then run probe scenarios
```

**Exact resources Phase 1B would create** (all in `trafficfuse-system`, all TrafficFuse-owned):
Namespace `trafficfuse-system`; Deployments `trafficfuse-healthy` (1 Pod), `trafficfuse-faulty`
(2 Pods); Services `trafficfuse-live`, `trafficfuse-healthy`, `trafficfuse-faulty` (ClusterIP);
on-demand Job `trafficfuse-probe`; the kind cluster `trafficfuse-lab` itself. Deletion is
restricted to the receipt-verified cluster.

---

PHASE1A_FINAL_STATUS=PASS
PHASE1B_READINESS=PARTIAL
