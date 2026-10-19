# TrafficFuse AI — Award Sprint Report

Date: 2026-10-19 (Pacific/Auckland). Times marked `Z` are UTC.
Scope: turn the offline infrastructure into the complete governed AI SRE
product, verified on the approved disposable Kind lab.

```text
SPRINT_STATUS=PASS            (all P0/P1 engineering items implemented and verified live; see §9 for scope)
REAL_KIND_STATUS=VERIFIED
AI_DIAGNOSIS_STATUS=REAL_MODEL_VERIFIED
AIG_GOVERNANCE_STATUS=LIVE_VERIFIED
TRAFFIC_RECOVERY_STATUS=VERIFIED
DASHBOARD_STATUS=WORKING
SUBMISSION_READINESS=PARTIAL  (video, slide file, hosted URL, commit/push and the portal submission need the owner)
```

## 1. Repository state

- Branch `main`, HEAD `3643fda6e2b292f5e22475c63f53843ce13178f5` (unchanged). **No commit,
  push, tag or release was made.** All work is in the working tree for review
  (`git status` lists the modified lab/API/test/doc files and the new `src/` modules,
  tests, `dashboard/`, `demo/` and `docs/` files).
- `AGENTS.md` SHA-256 before/after: `30fab17cc82e831a23edb15fa03bfb22821a0be24bbc24f2b94e094e287580be`.
- `pyproject.toml` and `uv.lock` unchanged; `uv lock --check`, `uv sync --locked`,
  `uv pip check` pass. AIG 0.2.0 @ `7261fdd2…`, default 5 s budget, imported from `.venv`.
  No shared-runtime source was copied, modified, monkeypatched or bypassed; the product
  uses only `agentic_intent_gate.runtime` public symbols and the public `Runtime` fields
  returned by `open_runtime`.

## 2. What was built

| Area | Implementation |
| --- | --- |
| Routing evidence | Lab service reports serving `version` and Downward-API `pod`; probe records `served_version`/`served_pod` per request; validator accepts only bounded identifiers |
| Evidence model | `domain/evidence.py`: items `EV-n` with source label (`LIVE_KIND`, `OFFLINE_SIMULATION`, `RECORDED_REPLAY`, `TEST_FIXTURE`), observation time, SHA-256; bundle digest re-verified on load |
| Live adapter | `infrastructure/kube.py` exact-shape kubectl gateway (5 reads, probe-Job create, governed patch); `live_cluster.py` probe Job lifecycle with strict byte-preserving export |
| AI diagnosis | `domain/diagnosis.py` prompt + closed output contract + grounding validator; `infrastructure/ollama.py` loopback-only client with structured output |
| Governance | `infrastructure/governance.py` composes `ToolBinding`/`ToolCapability`/`ExecutableRegistry`/`CapabilityVerifier`/`BoundCapabilityAuthorization`/`open_runtime` per private profile; `StagedProposalProvider` as the consumer `ModelProvider` |
| Trusted tool | `infrastructure/reroute.py`: `reroute_service_traffic` v1, closed 8-field schema, pure semantic rule, consumer receipt store with dispatch fence, `RerouteExecutor` (identity, evidence anchor, freshness, live preconditions, one guarded patch, independent re-read, durable receipt) |
| Recovery proof | `domain/recovery.py`: verdict only from re-measured traffic, routing and selector (4 checks) |
| Orchestration/API | `application/incidents.py`, `store.py`, FastAPI routes, mutation boundary middleware (loopback-only, client header against CSRF, replay read-only), trusted-Host allowlist, operator CLI |
| Dashboard | React 18 + TS + Vite (no UI/chart libs): hero metric, KPI tiles, 8-step governance chain, request strips, distributions, AI diagnosis, exact Plan with model-vs-evidence fields, three separate gates, evidence, routing, ledger, timeline, red-team refusals; light/dark, keyboard tooltips, table views |
| Simulation & replay | Labelled offline lab for development; read-only replay of the recorded live incident |

## 3. Real external actions performed (complete list)

All Kubernetes operations used `--kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
--context kind-trafficfuse-lab`; nothing touched any other cluster/context. The global
kubeconfig was not modified (last write 2026-10-12).

| # | Action | Result |
| --- | --- | --- |
| 1 | `docker build --pull` lab image (network: base digest check + 15 pinned wheels) | `sha256:fe0763a2…` (25 s), non-root, amd64; `docker build --check` clean |
| 2 | `kind create cluster --name trafficfuse-lab --kubeconfig <dedicated>` | Created; control plane `2026-10-19T02:00:28Z`; API `https://127.0.0.1:62192`; CA fp `c66e18ba…` |
| 3 | `lifecycle_cli record-creation --authorized` | v2 creation record written and reloaded (first live run of the recorder) |
| 4 | `kind load docker-image` (×2) | Second load after the probe stream fix: `sha256:45847d4c…` |
| 5 | Namespace apply → `lifecycle_cli verify --collect` | 5/5 ownership checks passed (endpoint, CA, nodes, owned nodes, labels) |
| 6 | Apply 2 Deployments + 3 Services; rollout status | 3 Pods ready, 0 restarts; live selector `version=faulty` |
| 7 | 9 probe Jobs (never deleted) | `trafficfuse-probe`, `trafficfuse-probe-healthy` (session 001), `tf-probe-{baseline,live}` ×3 sessions, `tf-probe-verify-36a47ec6`; all succeeded |
| 8 | Server-side dry runs of the governed patch (positive + stale rv) | Positive accepted; stale refused by the API server; Service unchanged (rv 635) |
| 9 | **The governed selector switch** (once, through AIG) | `faulty → healthy`, rv 635 → 25444 |
| 10 | Lab reset `kubectl apply -f deploy/lab/service-live.yaml` | Selector back to `faulty`, rv 25799 (demo-ready) |
| 11 | Local Ollama inference (`phi4-mini`, loopback) | 1 timing probe + 5 diagnoses; no external model provider |
| 12 | `npm install` (public npm registry) | 67 packages for exact pinned versions; install scripts blocked by npm 12 policy |
| 13 | Web read of the official competition page | Deadline not published |

Not performed: commits/pushes, hosted deployment, public submission, cluster
deletion, any mutation outside `trafficfuse-system`, any automatic retry.

## 4. Live Kind evidence (real HTTP, synthetic workload)

Session `phase1b-001` (runbook verbatim) **found a defect**: `kubectl logs` merges
stdout/stderr, and the stderr line `probe complete: …` landed inside the stdout JSON
document. Strict extraction failed closed; raw bytes preserved
(`artifacts/phase1b/phase1b-001-healthy/raw-probe-logs.txt`, SHA-256 `96411b2e…`).
Fix: the probe CLI writes one ordered stdout stream with the document last; regression
test added. No extraction rule was loosened.

Session `phase1b-002` (fixed image), 2026-10-19T02:08Z:

| Probe | Target | Result | Served by | p95 | Evidence SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `tf-probe-baseline-5d01c4d6` | `trafficfuse-healthy` | 40/40 OK, 0.0% | `healthy` ×40 (`…-lsthn`) | 66.7 ms | `d81ab4df…` |
| `tf-probe-live-9761f254` | `trafficfuse-live` | 20/40 failed, **50.0%** (200:20, 500:20) | `faulty` ×40 (`…-lc954`) | 81.0 ms | `27159d10…` |

Target identity: Service `trafficfuse-system/trafficfuse-live`, uid
`209a93f5-52d0-479b-b891-97c107899ff6`, rv 635, endpoints = the two faulty Pods.
Evidence exports with raw logs and audits live under `artifacts/` (git-ignored).

## 5. AI diagnosis — real model

Two live-evidence diagnoses by `phi4-mini` (digest `78fad5d182a7…`, local CPU):
31.9 s (655/178 tokens) and 23.4 s (658/179), plus three simulation-evidence runs
(API flow and two UI smoke runs, 20–34 s). All
passed grounding (cited only existing `EV-n`, cited measured probes, no extra fields)
and proposed `reroute_service_traffic` `faulty → healthy` on `trafficfuse-live`.
Observed weakness, shown honestly in the UI: the model claims "latency" degradation
although only the error rate differs materially. Fixtures used in tests are labelled
`TEST_FIXTURE (not a model)` and are never presented as model output.

## 6. Governed live remediation — `INC-20261019-f17711`

| Step | Evidence |
| --- | --- |
| Detection | Bundle `b9377e50c280…`; live 50.0% errors (`tf-probe-live-854b779b`, faulty ×40, p95 89.4 ms); baseline 0.0% (`tf-probe-baseline-88159b34`) |
| Plan | AIG Run `458d59ba-1310-4a67-b975-c207fe945377` → `AwaitingApproval` v4; fingerprint `125d87035272a39a815ac27ca03db44615002b6696dac4ad19764f84f4247d8e` |
| Human approval | Decided by the owner in chat with the exact Plan shown; submitted as approver `Incident commander` → Run `Approved` v5 at 08:48:02Z. Service still rv 635 / `faulty` afterwards. |
| Admission + authorization | Action `70f38862-7ebc-4fc3-aa88-3e19335539b0`; Authorization `eadd5d89-9194-4f0e-a769-ba314cd35a80` (subject `trafficfuse-operator`, policy `trafficfuse-policy-v1`) via the CLI capability; nothing dispatched |
| Ownership | Trusted collector re-verified `trafficfuse-lab` before dispatch |
| Durable claim + execution | Claim attempt `6620ab1e-f313-42c0-b1b7-8b95761d3027`; one JSON patch; dispatched 08:48:23.786Z → receipt 08:48:24.674Z (0.89 s) |
| Receipt | `36e933bc-a674-4c9d-b15b-1f5cc24214f9`, state `SUCCEEDED`, rv 635 → 25444, selector `{"app":"trafficfuse","version":"healthy"}`; Action `Succeeded` v4 |
| Independent verification | `tf-probe-verify-36a47ec6`: **0/40 failed (0.0%)**, served by `healthy` ×40, p95 73.3 ms, evidence `d56a9158aa0d…`; verdict **RECOVERED**, 4/4 checks |
| Timing | Approval → proven recovery **33 s**; detection → recovery 2 h 7 m (the approval question waited) |
| Unsafe proposals (live gate) | 4 red-team fixtures → `PlanRejected`/`PLAN_VALIDATION_ERROR` (Runs `e0cdb10f`, `b2e99886`, `0540c84e`, `2ebe64c0`); Service unchanged |

Recorded for replay: `demo/replay/recorded-incident.json` (82 KB; scanned: no capability
token, no key material, no unlabelled 64-hex values).

## 7. Tests executed

| Suite | Result |
| --- | --- |
| Full offline suite `uv run pytest -q` | **554 passed, 1 skipped, 0 failed** (42 s; skip = Windows symlink privilege, pre-existing) |
| New: `test_governance_runtime.py` | 20 — full chain on the real AIG runtime, approval/authorization separation, unsafe Plans (9 variants), two-step, stale rv, unknown/unjustified/stale evidence, approval not reusable |
| New: `test_reroute_executor.py` | 23 — wrong binding/scope/revision/inputs, store identity mismatch, payload conflict, receipt replay, fenced keys, ambiguous dispatch (no retry), unconfirmed effect, pre-dispatch expiry, cross-environment evidence, **4-way concurrent execute → 1 dispatch**, budget overrun → `Unknown`, **restart after durable claim → `Unknown`, zero effects** |
| New: `test_incident_api.py` | 18 — HTTP end-to-end recovery, stale fingerprint, missing/wrong capability, authorize-before-approval refused by the gate, ungrounded ids, authority fields, unsafe model proposal refused by the gate, model unavailable, red-team ×4, replay read-only (before body validation), loopback-only, CSRF client-header guard, trusted-Host allowlist (DNS rebinding), tamper-evident per-request records |
| Additive lab tests | 7 — routing identity capture/validation, Downward-API manifests, merged-stream regression |
| Adapted existing tests | `test_evidence_export` (single-stream contract), `test_lab_manifests` (env entries with `valueFrom`), `test_lab_ownership::test_missing_dedicated_kubeconfig_refused` made hermetic (the real kubeconfig now legitimately exists) — no assertion removed |
| Dashboard | `tsc --noEmit` strict clean; `vite build` 184 kB JS / 58 kB gzip |
| UI smoke (headless Chrome, real buttons, simulation backend, real phi4-mini) | **UI_SMOKE=PASS**, 8 steps, 30 s — run twice, the second time after the CSRF/Host hardening |
| Live server guards | Forged simple POST without the client header → 403; foreign `Host` header → 400 |
| Replay server | GET 200; every POST 403 `REPLAY_READ_ONLY` |

## 8. Safety properties demonstrated

- Approval and authorization each dispatched nothing (Service read unchanged after both).
- Unsafe proposals refused by the gate's own validator, in tests and live.
- Exactly one dispatch under concurrency; ambiguous outcomes terminal `Unknown`;
  restart recovery never replays an effect.
- Stale preconditions refused before dispatch; the API server re-checks them atomically.
- Model output never selected credentials, scopes, bindings, budgets or grants.
- Synthetic workload, simulation and replay are labelled on every surface.
- The privileged local API resists cross-site request forgery (mutations need a custom
  header, which forces a never-granted CORS preflight) and DNS rebinding (Host allowlist);
  mutations are refused before routing for non-loopback clients and in replay mode.

## 9. Status scope and remaining items

`SPRINT_STATUS=PASS` covers the permitted engineering sprint: every P0 item (real
evidence, real diagnosis, AIG integration, trusted executor, independent
verification, working UI) and P1 item (real refusals, audit timeline, design,
evidence references, narrative) is implemented and verified live. It does **not**
mean the competition submission is complete:

1. Record the demo video ([DEMO-SCRIPT.md](DEMO-SCRIPT.md)); build the slide file from [PITCH.md](PITCH.md).
2. Review and commit/push the working tree (requires the owner's authorization).
3. Decide on an application URL: a read-only replay deployment is the safe option; no
   privileged operation may be exposed publicly. Hosting needs separate authorization.
4. Confirm the submission deadline in the authenticated portal and submit.

Next authorizations needed: commit + push; any hosted deployment; the portal
submission. Cluster cleanup remains excluded until separately reviewed.
