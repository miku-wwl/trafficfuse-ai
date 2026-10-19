# TrafficFuse AI — Claude Code Handover Report

Date: 2026-10-19 (Pacific/Auckland; UTC times are given where they matter).
Author: Claude Code (Opus 5.5), taking over from the earlier Codex-driven phases.
Companion documents: [AWARD-SPRINT-REPORT.md](AWARD-SPRINT-REPORT.md) (what was built
and verified) and [SUBMISSION-READINESS.md](SUBMISSION-READINESS.md).

## 1. Starting state (verified, not assumed)

| Item | Observed at session start |
| --- | --- |
| Branch / HEAD | `main` @ `3643fda6e2b292f5e22475c63f53843ce13178f5` (= `origin/main`), clean tree |
| `AGENTS.md` | Loaded and read in full; SHA-256 `30fab17cc82e831a23edb15fa03bfb22821a0be24bbc24f2b94e094e287580be` (unchanged at the end) |
| Shared runtime | `agentic-intent-gate==0.2.0`, PEP 610 commit and requested revision `7261fdd2f47d2b15fabe8faac527eeee37338eb1`, imported from this repository's `.venv` site-packages, default execution budget 5 s. The local checkout's annotated tag `v0.2.0` peels to the same commit. |
| Runtime references read | Installed `agentic_intent_gate/runtime.py` and the ports/composition it uses; `examples/consumer/app.py`, `docs/ADAPTER_GUIDE.md` at `v0.2.0`; `docs/V0.2.0-CONSUMER-HANDOFF.md` |
| Offline suite | 486 passed, 1 skipped (reproduced: 492 after my first additive tests) |
| Infrastructure | **No `trafficfuse-lab` cluster** (only foreign `incident-lab`, `pcp-dev`, `playground-crd`); no lab image built; no dedicated kubeconfig or creation record |
| Phase 2/3 code | **None.** `application/` and `domain/` contained only docstrings; no AIG composition, executor, model provider, incident API or UI |
| Tooling | Docker 29.8.0 (running), kind v0.33.0, kubectl v1.36.1, PowerShell 7.6.7, uv 0.11.16, Node 26.5.0 / npm 12.0.1, Ollama 0.35.1 serving `phi4-mini` (3.8B Q4_K_M, digest `78fad5d1…`, CPU-only) |
| Competition deadline | Official page (fetched 2026-10-19): online build 16–19 Oct 2026, on-site 19–20 Oct; submission deadline **"To be announced" — not published**. Must be checked in the authenticated lablab.ai portal. |

## 2. Assessment

The earlier phases produced strong safety plumbing (ownership guards, exact
kubeconfig enforcement, a strict evidence exporter) but no product: no
diagnosis, no governed execution, no UI, and nothing had run against a real
cluster. The highest-value move was to stop hardening and build the product on
top of the existing guards, verifying each piece live as soon as the user
approved infrastructure.

## 3. Authorizations obtained (explicit, task-specific)

1. **Infrastructure Stage A + B** (answered via the session's question tool):
   image build with network pulls, creation of the new disposable
   `trafficfuse-lab`, creation record, image load, Namespace/Deployments/Services,
   probe Jobs, read-only inspection; then additional uniquely-named probe Jobs, the
   single AIG-governed selector switch **only after in-product Approval +
   Authorization**, and lab resets between demo takes. Deletion excluded.
2. **Live Plan approval:** the user reviewed the exact live Plan (fingerprint
   `125d8703…`) in chat and chose "Approve — Claude submits it". Claude then
   submitted approval, authorization (CLI capability) and execution as three separate
   calls. The approval is recorded with approver role `Incident commander` and the
   reason "Approved by operator in chat after reviewing exact Plan and EV-1..EV-6".
   This is genuine human consent relayed by the agent, not an authenticated
   dashboard identity.

Not authorized and not done: commits, pushes, tags, releases, hosted deployment,
public submission, cluster deletion, any foreign cluster or context.

## 4. Key decisions (and why)

| Decision | Reason |
| --- | --- |
| Executor talks to Kubernetes through an **exact-shape kubectl gateway** (not a client library, no kubeconfig parsing of keys) | Keeps credentials inside kubectl; matches the existing command-shape security model; easy to audit. |
| JSON patch with server-side `test` ops on UID, resourceVersion and full selector | The API server refuses stale/unexpected targets atomically, closing the read→patch race. Verified with server-side dry runs before the first real patch. |
| Diagnosis runs **before** the AIG Run; a `StagedProposalProvider` hands AIG the model-derived Plan text | CPU inference (20–40 s) can exceed the gate's 30 s planning deadline; the gate still validates the exact model-derived text. |
| TrafficFuse validates **grounding**, AIG validates **authority** | Unsafe model choices (namespace, target, direction) are passed through verbatim, so their refusal comes from the gate itself — a real refusal, not a filtered one. |
| Separate profiles per mode (live/simulation): own scope, adapter id, gate, capability, receipt store | Simulation receipts can never authorize or be confused with live effects; the executor also refuses evidence from another environment. |
| Default 5 s budget, no 15 s profile | Measured kubectl calls ≈0.42–0.57 s; dispatch-to-receipt took 0.89 s live. |
| Probe CLI writes a **single ordered stdout stream** | Live finding: `kubectl logs` merges stdout/stderr and interleaved a progress line into the JSON document. Strict extraction failed closed (correctly) and preserved the raw bytes. |
| Evidence freshness window (30 min) | Live finding: approval arrived ~2 h after detection; preconditions still held, but stale evidence should not justify an intervention. |
| React + TypeScript + Vite with **no UI/chart library**; pinned exact stable versions | Small, auditable dependency surface; charts follow the dataviz palette validated with the skill's validator. |
| Mutation boundary middleware: loopback-only, `X-TrafficFuse-Client` header, replay read-only; trusted-Host allowlist | Self-review found that a hostile web page could forge a *simple* cross-site POST to the local API (e.g. trigger probe Jobs) and that DNS rebinding could read incident data. The custom header forces a CORS preflight the server never grants; the Host allowlist defeats rebinding. |

## 5. Map of the new code

```text
src/trafficfuse/
  domain/evidence.py          provenance-labelled, hashed evidence items/bundles
  domain/diagnosis.py         prompt, output contract, grounding validation, Plan composition
  domain/recovery.py          measured before/after recovery verdict
  infrastructure/kube.py      exact-shape kubectl gateway, probe Job template, SelectorPatch
  infrastructure/live_cluster.py  live reads + probe Job lifecycle (create→poll→logs→export)
  infrastructure/simulation.py    labelled offline lab (in-memory Service, in-process apps)
  infrastructure/reroute.py   tool schema, semantic rule, receipt store, RerouteExecutor
  infrastructure/governance.py    AIG composition per profile, StagedProposalProvider
  infrastructure/ollama.py    loopback-only Ollama client (structured output)
  application/evidence_collection.py  bundle collection, degradation policy, anchors
  application/incidents.py    orchestration over the AIG public API, red-team scenarios
  application/store.py        incident store + append-only audit events
  api/app.py, api/composition.py  FastAPI routes, mode composition, mutation boundary
  config/product.py           TRAFFICFUSE_MODE / Ollama settings (fail closed)
  cli.py                      provision, authorize, execute, export-replay
dashboard/                    React + TS command center (+ scripts/ui-smoke.mjs)
demo/replay/recorded-incident.json  recorded live incident for read-only replay
```

Modified existing files: lab service/probe/evidence (served-version routing
evidence, single-stream output), the two Deployments (Downward-API pod name),
`api/app.py`, `.env.example`, and four test files (adapted, not weakened — see
the sprint report).

## 6. How to resume

- Run the product: [LIVE-DEMO-RUNBOOK.md](LIVE-DEMO-RUNBOOK.md). The lab was left
  **demo-ready** (selector `version=faulty`, rv 25799) and a live-mode server was left
  running on `http://127.0.0.1:8000` from the Claude Code session.
- Verify: `uv run pytest -q` (554 passed, 1 skipped at handover), dashboard
  `npm run build`, UI smoke test (runbook §6).
- Record the video: [DEMO-SCRIPT.md](DEMO-SCRIPT.md). Submission copy: [PITCH.md](PITCH.md).
- Before committing: review the diff; nothing has been committed. `dashboard/node_modules`,
  `dashboard/dist`, `.runtime/` and `artifacts/` are git-ignored.

## 7. Open risks

1. Submission deadline unknown (likely imminent) — check the portal now.
2. CPU inference latency (20–40 s) — fine live, cut in the video.
3. The approver identity is not authenticated (local name + AIG `LOCAL_USER`);
   the capability token is a local secret, not SSO.
4. Docker Desktop restarts keep the kind container, but if `trafficfuse-lab`
   disappears, live mode refuses to start (dedicated kubeconfig/ownership checks);
   recreate only with fresh authorization.
