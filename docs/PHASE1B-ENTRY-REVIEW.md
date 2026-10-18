# TrafficFuse AI — Phase 1B Entry Review: Safe Deployment Gate

> **Final hotfix correction (2026-10-19):** the execution marker is forgeable by
> ordinary Python construction; it is not authentication. The corrected receipt
> generator, dual probe workflow and PowerShell 7.4+ runbook are documented in
> [PHASE1B-FINAL-HOTFIX-REPORT.md](PHASE1B-FINAL-HOTFIX-REPORT.md) and
> [PHASE1B-RUNBOOK.md](PHASE1B-RUNBOOK.md). Results below are the historical entry
> review baseline. Phase 1B readiness remains **PARTIAL**: real build/deployment
> and in-cluster probes are unverified.

**Date:** 2026-10-18 · **Method:** offline implementation, tests and static validation only —
no cluster was created, modified or deleted; no Kubernetes mutation was executed; no ambient
kubeconfig or context was changed; no Docker image was built, pulled or loaded; no commit, push,
tag or release was made.

---

## 0. Mandatory repository inspection (before any change)

| Check | Result |
| --- | --- |
| `AGENTS.md` | read in full, **not modified** — sha1 `03870389d009b3d2c9eca19181a7eb68e26053fe` (identical before and after) |
| HEAD | `a43809ef06f667632966be5091be99029668b7df` (Phase 1A.2 push) |
| HEAD vs `origin/main` | identical — remote metadata fetched and compared before any edit |
| Working tree | clean at start; all uncommitted user changes: none (nothing to preserve) |
| Phase 1A.2 code/tests | `lifecycle.py`, `cluster_facts.py`, `lifecycle_cli.py`, `evidence*.py` and their tests re-read |
| `docs/PHASE1B-RUNBOOK.md`, `docs/PHASE1A2-FINAL-PREFLIGHT-REPORT.md` | read in full |
| Installed AIG | `agentic-intent-gate==0.2.0`, PEP 610 `direct_url.json` commit **`7261fdd2f47d2b15fabe8faac527eeee37338eb1`**, `requested_revision` identical; `pyproject.toml` and `uv.lock` pins unchanged |
| Foreign clusters | `incident-lab`, `pcp-dev`, `playground-crd`, `vmware-k8s` untouched — read-only `docker ps` verification before and after |
| Ambient context | `kind-playground-crd` (foreign) — never read, switched or relied upon |

The previously reported HEAD (`a43809e`) was re-verified against the remote rather than assumed.

---

## 1. Four findings and root causes

### Finding P1 — Namespace bootstrap and ownership verification were circularly ordered

**Root cause.** `docs/PHASE1B-RUNBOOK.md` invoked the trusted collector
(`lifecycle_cli verify --collect`) *before* applying any namespace manifest. The collector's
required evidence includes `kubectl get namespace trafficfuse-system -o json`, which cannot
succeed on a freshly created Kind cluster — the sequence failed by construction. The preflight
plan had the same defect in miniature (namespace creation listed as a bare `create namespace`
step after `apply -k deploy/lab`).

### Finding P1 — Direct cluster deletion sat inside the authorized Phase 1B workflow

**Root cause.** The runbook ended with `kind delete cluster --name trafficfuse-lab`, gated only
by a *preceding* verification log. A verification that ran earlier does not enforce ownership
checks on a later command, and deletion is a different class of operation from the
create-and-observe scope Phase 1B authorizes.

### Finding P2 — Collector provenance was a spoofable string

**Root cause.** `ClusterFacts.source = "collector"` (Phase 1A.2) was an ordinary dataclass
field: any caller could construct content-matching facts and set the string, and
`plan_cluster_destroy` consulted exactly that string. A caller-controlled string is not a
provenance guarantee.

### Finding P2 — Credential disclosure through the raw kubeconfig view command

**Root cause.** The collector and the runbook used `kubectl config view --raw --minify -o json`
to read cluster identity. That command prints the complete kubeconfig — including
`client-key-data` — to the terminal. (`kubectl config view` reads the *local file*, not the
cluster, so it also provided no live evidence beyond what the trusted local parser already
supplies.)

---

## 2. Code changes

### `src/trafficfuse/lab/cluster_facts.py` (rewritten)

1. **Controlled collection (P2-a provenance).** Normal collection attaches a
   `CollectorExecution` diagnostic marker (nonce + command trace). Both it and
   `LiveFacts` can be constructed directly. `is_collector_executed()` checks marker
   presence only, and a forged marker returns True. The `source` string and the
   `COLLECTOR_SOURCE`/`CALLER_SOURCE` constants are removed, but this does not provide
   cryptographic authenticity. Security depends on the controlled collection path.
2. **`verify_live_ownership()` is the single trusted entry point** returning a
   `TrustedOwnership` outcome. It performs kubeconfig-document verification, creation-record
   verification, **the collection itself**, and the evidence cross-check inside one controlled
   operation — it has **no facts parameter**, so there is nothing a caller can pass in to be
   trusted.
3. **Identity from the trusted local parser (P2-b credentials).** The API server and CA
   fingerprint are read from the dedicated kubeconfig file via
   `lifecycle.load_kubeconfig_document` / `verify_kubeconfig_document`. The raw-view command is
   gone; live connectivity is proven by the scoped `get nodes` / `get namespace` commands and
   the Docker node-identity cross-check.
4. **Redaction.** New `redact_for_message()` strips PEM blocks and long base64 runs from
   command stderr before it can reach any operator-facing message or exception. A regression
   test proves credential-shaped material never survives into a failure message.

### `src/trafficfuse/lab/lifecycle.py`

1. `ClusterFacts` loses `source` (no caller-settable provenance); `verify_ownership_evidence()`
   computes the legacy diagnostic `trusted` field from marker presence (deferred
   import of `is_collector_executed` breaks the module cycle). This is not authority.
2. **`plan_cluster_destroy()` no longer accepts facts at all.** Its signature is
   `(*, kubeconfig_path, receipt_path, authorized, executor=None)`; it performs the trusted
   collection itself (deferred import of `verify_live_ownership`) and accepts **no**
   caller-supplied JSON, `ClusterFacts`, `LiveFacts` or provenance string. Guard order:
   dedicated kubeconfig path → creation record → exact context → explicit authorization →
   collector execution (missing executor = refusal) → ownership verification → deletion-scope
   refusal. The removed `kubeconfig_document` parameter is subsumed by the collector's own
   document verification.
3. `build_preflight_plan()` steps match the corrected sequence: `kind create cluster` →
   `kind load docker-image` → apply `deploy/lab/namespace.yaml` only → verify namespace
   metadata → trusted-collector ownership verification → apply the four workload/service
   manifests explicitly → rollout wait → probe/export → preservation (deletion out of scope).

### `src/trafficfuse/lab/lifecycle_cli.py`

* `verify --collect` prints `TrustedOwnership.to_dict()` — the four identity fields, per-check
  verification results, non-sensitive live facts and the exact read-only command trace. No
  credential-bearing key exists in the payload.
* `destroy` replaces `--facts` with `--collect`: without a collector execution it refuses with
  "no collector execution was performed"; with one it reaches the deletion-scope refusal.
  Caller JSON can no longer be attached to the destructive path in any form.

---

## 3. Documentation changes

* **`docs/PHASE1B-RUNBOOK.md` (rewritten).** Complete Stages A–F with per-stage labels
  (read-only preflight / Docker image build / cluster creation / namespace bootstrap / live
  ownership verification / application deployment / probe execution / evidence export /
  preservation of resources for inspection) and a **STOP** condition after every critical
  stage. Namespace bootstrap (`deploy/lab/namespace.yaml` only — the kustomization is never
  bulk-applied) precedes the namespace-dependent collector read; image load follows cluster
  creation and precedes workload readiness; the probe Job is created → waited → logged →
  exported in order. Cluster deletion is **removed** from the authorized sequence; the
  preservation section documents resources and reports cleanup requirements pending separate
  explicit human authorization. No raw-kubeconfig-view command appears anywhere; identity
  extraction uses the trusted local parser (endpoint + CA fingerprint only).
* **`docs/PHASE1B-ENTRY-REVIEW.md`** — this report.
* `README.md`, `docs/PHASES.md` — updated to the entry-review state.
* `docs/PHASE1A2-FINAL-PREFLIGHT-REPORT.md` — dated correction banner (its finding P1-c
  provenance-string resolution is superseded by the execution-receipt model; historical text
  preserved).

---

## 4. Test evidence

| File | Tests | Notes |
| --- | --- | --- |
| tests/test_trusted_ownership.py | 14 | **new** — P2-a provenance negative tests + P2-b credential-redaction security tests (synthetic kubeconfig with fake credentials) |
| tests/test_phase1b_runbook.py | 11 | **new** — runbook ordering, no deletion, no raw-view, kubeconfig/context pinning, explicit manifests, STOP conditions |
| tests/test_cluster_facts.py | 27 | **rewritten** (was 21) — execution receipt, trusted entry point, local identity parser, never a raw view, executor-based destroy gates |
| tests/test_lab_lifecycle.py | 32 | **updated** — executor-based destroy chain, corrected preflight-plan ordering assertions |
| tests/test_lab_ownership.py | 43 (1 skipped) | **updated** — plan-cluster-destroy uses its own collection; skip unchanged (Windows symlink privilege) |
| tests/test_lifecycle_cli.py | 5 | **updated** (+1) — destroy `--collect` with mocked executor; removed `--facts` flag regression |
| tests/test_fault_matrix.py | 82 | unchanged, green |
| tests/test_evidence_export.py | 22 | unchanged, green |
| tests/test_evidence_preservation.py | 30 | unchanged, green |
| tests/test_lab_manifests.py | 22 | unchanged, green |
| tests/test_lab_service.py | 13 | unchanged, green |
| tests/test_lab_probe.py | 7 | unchanged, green |
| tests/test_config_settings.py | 13 | unchanged, green |
| tests/test_runtime_dependency.py | 5 | unchanged — AIG pin verified |
| tests/test_fastapi_app.py | 2 | unchanged, green |

**Total: 328 collected → 327 passed, 1 skipped** (Phase 1A.2 baseline: 295 passed, 1 skipped;
+32 tests, no baseline test removed). Test corrections replaced assertions that encoded the
superseded trust model (caller-settable provenance; fact parameters on the destructive gate);
every replacement strengthens the original intent.

### Security negative-test results (spec-mandated, all mocked, no cluster access)

| Negative test | Result |
| --- | --- |
| Caller sets `source="collector"` manually | **refused** — `ClusterFacts` has no such field (`TypeError`) |
| Caller supplies a forged `ClusterFacts` object | **refused** — the destructive gate has no facts parameter (`TypeError`) |
| Caller supplies a matching but unverified ownership receipt | **refused** — "no collector execution was performed" |
| Collector execution is skipped | **refused** — same gate, fail-closed |
| Live collection fails | **refused** — "live facts collection failed" |
| Docker and Kubernetes identities disagree | **refused** — "docker node identities do not match" |
| Valid mocked collector returns verified facts | **verified + trusted** — `TrustedOwnership` with execution receipt and command trace |
| Unauthorized deletion remains refused | **refused** — authorization gate before any collection |
| Sensitive fields absent from stdout/stderr (fake credentials) | **confirmed** — payload limited to 4 identity fields + verification + live facts + command trace |
| Errors do not expose secrets | **confirmed** — mismatched-record message carries no kubeconfig content; failing command stderr is `[REDACTED]`-stripped |
| Evidence reports exclude credential material | **confirmed** — no credential-bearing key exists in the audit schema |
| API Server and CA fingerprint remain verifiable | **confirmed** — printed and asserted in every redaction test |

---

## 5. Corrected Phase 1B sequence (summary)

**Stage A — read-only preflight:** authorization verification → Docker/Kind availability →
cluster absent → dedicated kubeconfig path → no foreign target → image build inputs.
**Stage B — image build + cluster creation (future authorization only):** build →
`kind create cluster --name trafficfuse-lab --kubeconfig …` → record creation-time identity →
independent inspection → refuse on mismatch. **Stage C — minimal namespace bootstrap:** load
image → apply only `deploy/lab/namespace.yaml` → verify namespace ownership metadata.
**Stage D — trusted ownership verification:** `lifecycle_cli verify --collect` (kubeconfig
document, creation record, collector-executed facts: API server, CA fingerprint, Docker/kind
node identities, namespace identity) → stop on any failure. **Stage E — application
deployment:** apply the four workload/service manifests explicitly → wait ready → confirm
`trafficfuse-live` selects `version=faulty`. **Stage F — evidence collection:** probe Job →
wait complete → raw logs → validate → export with SHA-256 audit → report → **preserve**.

---

## 6. Trusted ownership verification — limitations (explicit)

1. `CollectorExecution` is **not cryptographic authenticity**. Ordinary Python code can
   construct matching `LiveFacts` and a marker; the helper and diagnostic `trusted` field
   then return True. Those results do not prove execution or authorize action. The actual
   boundary is `verify_live_ownership()` performing independent collection inside trusted
   host control flow, with no caller-facts parameter. Deletion remains disabled regardless.
2. Live Kind integration remains **unverified**. It will not make these objects unforgeable;
   trusted host code, executors and process integrity remain assumptions. All verification
   here uses synthetic inputs and mocked executors.
3. The final security-gate hotfix replaces broad verb classification with four exact command
   shapes. Extending collection requires a deliberate reviewed shape change and regression
   tests; see [PHASE1B-SECURITY-GATE-REPORT.md](PHASE1B-SECURITY-GATE-REPORT.md).
4. The Windows symlink-escape negative test remains skipped (platform privilege); the real
   traversal branch and a mocked-resolution test cover the logic.
5. The creation record remains metadata. Independent live cross-checking is required, but
   neither labels nor creation metadata prove historical ownership on their own. The final
   runbook records actual post-creation identity and requires independent collection later.

---

## 7. Validation battery

| Check | Result |
| --- | --- |
| `uv lock --check` | clean (32 packages) |
| `uv sync --locked` | clean (32 packages) |
| `uv pip check` | all installed packages compatible |
| `uv run pytest` (offline) | **327 passed, 1 skipped** |
| `git diff --check` | clean |
| Secret scan (new/changed files) | clean — no credentials, tokens, private keys, kubeconfigs or `.env` files added |
| `AGENTS.md` sha1 | `03870389d009b3d2c9eca19181a7eb68e26053fe` — unchanged |
| AIG pin | `0.2.0` @ `7261fdd2f47d2b15fabe8faac527eeee37338eb1`, `requested_revision` identical |
| Foreign clusters | `incident-lab`, `pcp-dev`, `playground-crd`, `vmware-k8s` untouched (read-only before/after) |
| Ambient context | `kind-playground-crd` — unchanged, never used |
| Cluster/Docker mutations | none — no image built, no container launched, no kind/kubectl mutation |
| Commit / push | **none** — working tree left for human review |

`DOCKER_BUILD_STATUS=BUILD_NOT_VERIFIED` — unchanged: no build ran in this phase (by rule).

---

## 8. Remaining deployment blockers

1. **Explicit human authorization** naming cluster creation + locally mutating image build +
   run ids (deletion is *not* part of it).
2. **Real image build** with network (`docker build --pull …`) — the only way to move
   `DOCKER_BUILD_STATUS` to `VERIFIED`.
3. After Phase 1B execution: evidence validation on real probes and the Phase 1B report.

---

## 9. Acceptance criteria assessment

1. `AGENTS.md` unchanged — **yes** (sha verified before/after).
2. AIG v0.2.0 pin unchanged — **yes** (pyproject, uv.lock, installed distribution).
3. Namespace bootstrap before namespace-dependent ownership verification — **yes** (runbook
   Stage C < Stage D; enforced by `tests/test_phase1b_runbook.py`).
4. All deployment commands ordered correctly — **yes** (static ordering tests).
5. No unguarded cluster deletion in the runbook — **yes** (removed; regression test forbids
   the command).
6. Caller-controlled provenance cannot grant destructive authority — **yes** (no provenance
   string exists; the gate collects itself).
7. Trusted collector verification path tested — **yes** (27 + 14 tests, mocked executors).
8. Credential-bearing kubeconfig content never printed or archived — **yes** (local parser +
   redaction tests).
9. Existing tests remain green — **yes** (327 passed, 1 skipped; baseline preserved).
10. New tests pass — **yes** (+32).
11. No foreign cluster contacted or modified — **yes**.
12. No Kind cluster created or deleted — **yes**.
13. No Docker image build or pull — **yes**.
14. No Kubernetes mutation — **yes**.
15. No commit or push — **yes**.
16. Operator runbook ready for explicit human authorization — **yes**.

---

**PHASE1B_ENTRY_STATUS=PASS**
**PHASE1B_READINESS=PARTIAL** — code, guards, tests and the executable runbook are ready;
blocked only on (a) explicit human authorization and (b) the real image build (network).
**DOCKER_BUILD_STATUS=BUILD_NOT_VERIFIED**
