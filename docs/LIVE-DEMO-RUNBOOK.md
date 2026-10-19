# TrafficFuse AI — Live Demo Runbook

How to run, demonstrate and reset TrafficFuse against the approved disposable
`trafficfuse-lab` Kind cluster. Use **PowerShell 7.4+** from the repository root.
Every Kubernetes command uses the dedicated kubeconfig and exact context; the
ambient context is never used. Foreign clusters (`incident-lab`, `pcp-dev`,
`playground-crd`, `vmware-k8s`) are never touched.

> The workload is a **synthetic lab service**. Every number in the UI is measured
> from real HTTP responses inside the real Kind cluster, and labelled `LIVE KIND`.
> Do not describe it as production traffic.

## 0. Prerequisites (already satisfied on the reference workstation, 2026-10-19)

| Requirement | Check |
| --- | --- |
| Docker Desktop running, `trafficfuse-lab` present | `kind get clusters` lists `trafficfuse-lab` |
| Dedicated kubeconfig | `.runtime/kubeconfig/trafficfuse-lab.yaml` exists (git-ignored) |
| Lab image loaded, workloads ready | step 2 below |
| Ollama serving `phi4-mini` on loopback | `ollama list` shows `phi4-mini` |
| Live governance profile provisioned once | `.runtime/trafficfuse/live/aig/identity.json` exists |
| Dashboard built | `dashboard/dist/index.html` exists (`cd dashboard; npm ci; npm run build`) |

The live profile was provisioned with
`uv run python -m trafficfuse.cli provision --profile live` (fresh UUIDv4 scope,
private capability, receipt store). Never re-provision over it; a new profile
needs a new empty directory.

```powershell
$KC = @('--kubeconfig', '.runtime/kubeconfig/trafficfuse-lab.yaml', '--context', 'kind-trafficfuse-lab')
```

## 1. Start the product (live mode)

```powershell
$env:TRAFFICFUSE_MODE = 'live'
uv run uvicorn trafficfuse.api.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The header must show **LIVE KIND**, the cluster
`trafficfuse-lab · kind-trafficfuse-lab · ns trafficfuse-system`, `AIG 0.2.0 @7261fdd · 5s budget`
and `phi4-mini local model ready`. Do not use `--reload` for live demos, and keep the
server on loopback (mutating routes refuse non-loopback clients anyway).

## 2. Pre-flight (read-only)

```powershell
kubectl @KC -n trafficfuse-system get pods
kubectl @KC -n trafficfuse-system get service trafficfuse-live -o jsonpath='{.metadata.resourceVersion} {.spec.selector}'
```

Expect three `Running` Pods (2 faulty, 1 healthy) and selector
`{"app":"trafficfuse","version":"faulty"}`. If the selector is `healthy`, a previous
take already remediated it: reset first (section 4).

## 3. The demo flow (dashboard)

1. **Probe live Service now** — two probe Jobs (live Service + healthy baseline),
   ~10 s. Expect ~50% errors on `trafficfuse-live`, 40/40 requests served by
   `faulty`, and a 0% baseline served by `healthy`.
2. **Diagnose with AI** — local `phi4-mini` on CPU, typically 20–40 s. The panel
   shows `Real model output`, cited evidence ids, confidence, stated uncertainty and
   the verbatim raw output. The gate then validates the composed Plan.
3. **Approve** — enter a role name (e.g. `Incident commander`; avoid personal names in
   recordings), tick the fingerprint acknowledgement, click **Approve exact Plan**.
   Nothing is dispatched.
4. **Authorize** — the execution capability is a separate operator secret. Either:
   - copy it to the clipboard without displaying it, then paste it into the
     password field and click **Authorize one Action**:
     ```powershell
     Get-Content .runtime/trafficfuse/live/aig/capability | Set-Clipboard
     ```
   - or, as a separate operator in a second terminal (never prints the secret):
     ```powershell
     uv run python -m trafficfuse.cli authorize INC-... --profile live
     ```
   Nothing is dispatched.
5. **Execute governed switch** (or `uv run python -m trafficfuse.cli execute INC-... --profile live`).
   The trusted collector re-verifies cluster ownership, the gate durably claims the
   Action, and the executor dispatches exactly one guarded JSON patch. Verification then
   starts automatically: a new probe Job of the same live Service. Expect
   `RECOVERED: 50% → 0%`, 40/40 served by `healthy`, 4/4 checks passed.
6. **Unsafe proposals** — click any red-team button. Each submits a labelled fixture
   Plan through the same gate validation and is refused (`PlanRejected`), with the
   gate's issue codes and a readable explanation.

## 4. Reset between takes (approved lab operation)

```powershell
kubectl @KC -n trafficfuse-system apply -f deploy/lab/service-live.yaml
kubectl @KC -n trafficfuse-system get service trafficfuse-live -o jsonpath='{.metadata.resourceVersion} {.spec.selector}'
```

This re-applies the reviewed manifest (selector back to `version=faulty`). It is an
operator lab action, intentionally **not** exposed through the product API. Earlier
incidents remain in the dashboard as history; their Plans can never run again
(their Actions are terminal and their preconditions are stale).

## 5. Other modes

| Mode | Command | What it is |
| --- | --- | --- |
| Simulation | `$env:TRAFFICFUSE_MODE='simulation'` | In-process lab services behind a simulated Service. No Kubernetes. Real gate, real probes of in-process apps, real receipts. Labelled **Offline simulation** everywhere. |
| Replay | `$env:TRAFFICFUSE_MODE='replay'` | Read-only display of `demo/replay/recorded-incident.json` (the live incident recorded on 2026-10-19). All mutating routes return 403. The only mode suitable for a public URL; add the public hostname to `TRAFFICFUSE_ALLOWED_HOSTS` (default: loopback names only). |

Re-export a recorded live incident (live server running):
`uv run python -m trafficfuse.cli export-replay INC-... --out demo/replay/recorded-incident.json`.

## 6. Verification aids

```powershell
uv run pytest -q                                   # 554 offline tests
node dashboard/scripts/ui-smoke.mjs --url http://127.0.0.1:8001/ `
     --capability .runtime/trafficfuse/simulation/aig/capability --out ui-smoke.png
```

The UI smoke test drives the real buttons in headless Chrome against a
**simulation** server on port 8001 (start one with `TRAFFICFUSE_MODE=simulation`
and `--port 8001`).

## 7. If something goes wrong

| Symptom | Meaning | Action |
| --- | --- | --- |
| `MODEL_UNAVAILABLE` | Ollama not reachable | Start Ollama; click **Retry diagnosis**. No diagnosis is ever fabricated. |
| `DIAGNOSIS_REJECTED` | Model output failed grounding/shape validation | Retry; the rejection reason is shown. |
| `PLAN_REJECTED` | The gate refused the proposal | Expected for unsafe proposals; read the explanations. |
| Action `Failed` | Confirmed no effect (e.g. stale `resourceVersion`, stale evidence) | Probe again for fresh evidence; approve a new Plan. |
| Action `Unknown` | Ambiguous outcome after dispatch | **Never retry.** Inspect read-only (`get service`), review the receipt store, decide manually. |
| Probe Job failed | Lab problem; Job preserved | Inspect with `kubectl @KC -n trafficfuse-system logs job/<name>`. Do not delete. |

Evidence older than 30 minutes cannot justify an intervention: the executor
refuses it before dispatch. Probe again and approve the new Plan.

## 8. Cleanup

Nothing is cleaned up automatically. The cluster, Jobs, kubeconfig, creation
record, gate ledgers, receipt stores and artifacts are preserved for inspection.
Deleting `trafficfuse-lab` requires a separately reviewed workflow and separate
explicit authorization.
