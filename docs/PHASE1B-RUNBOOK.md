# TrafficFuse AI — Phase 1B Operator Runbook (Safe Deployment Gate)

**Status:** documentation only — **no command in this runbook has been executed.**
This is the corrected, safety-scoped Phase 1B sequence (Stages A–F). Phase 1B
authorization covers **cluster creation and scoped deployment only**. The
environment is **preserved** at the end: cleanup is outside this scope and
requires a separate explicit human authorization with its own guard chain.
Nothing here is permission to mutate; every mutating stage names its
prerequisites, and every critical stage ends with a **STOP** condition.

This runbook supersedes the Phase 1A.2 sequence, which had a **circular
ordering defect**: ownership verification ran before the namespace existed,
so the collector's namespace read could never succeed on a fresh cluster.
The corrected order bootstraps the namespace first (Stage C) and verifies
ownership afterwards (Stage D). Image loading now also happens **after**
cluster creation (a kind image loads into the nodes of an existing cluster),
and cluster identity is extracted through the **trusted local parser** — no
command that dumps the whole kubeconfig (with client key material) is used
anywhere.

---

## 0. Exact identities (pin these; nothing else is a valid target)

| Identity | Exact value |
| --- | --- |
| Cluster | `trafficfuse-lab` |
| Context | `kind-trafficfuse-lab` (**never** `kind-trafficfuse-system` — that string is a namespace, not a context) |
| Namespace | `trafficfuse-system` |
| Kind node | `trafficfuse-lab-control-plane` (single node) |
| Dedicated kubeconfig | `.runtime/kubeconfig/trafficfuse-lab.yaml` (repository-owned path) |
| Creation record | `.runtime/state/cluster-trafficfuse-lab.json` |
| Image | `trafficfuse-lab-service:phase1a` (built from `deploy/lab/Dockerfile`) |
| Live Service | `trafficfuse-live` (ClusterIP :80, selector `version=faulty`) |
| Version Services | `trafficfuse-healthy`, `trafficfuse-faulty` (ClusterIP :80) |
| Deployments | `trafficfuse-healthy`, `trafficfuse-faulty` (container port 8080) |
| Probe Job | `trafficfuse-probe` (applied on demand; not in the kustomization) |
| Artifacts root | `artifacts/phase1b/<run-id>/` (git-ignored) |

**Foreign-cluster protections.** `incident-lab`, `pcp-dev`, `playground-crd` and
`vmware-k8s` belong to other projects and are never valid targets; `vmware-k8s`
is non-disposable and must never be read from or written to. The ambient
`kubectl` context at Phase 1A time is the foreign `kind-playground-crd` — it is
never selected, switched or relied upon; the ambient context is never used at
any step. Every `kubectl` invocation below carries **both** flags via one
variable:

```bash
KC="--kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --context kind-trafficfuse-lab"
```

(namespace via `-n trafficfuse-system`). If any command is ever run without
these flags, stop and re-issue it with them.

---

## Stage A — Read-only preflight (always first; no mutation)

### A1. Repository and state verification

```bash
git status --short                       # must be clean or understood
git rev-parse HEAD                       # record the exact commit in the report
git rev-parse origin/main                # HEAD == origin/main before starting
sha1sum AGENTS.md                        # unchanged (Phase 1A.2: 03870389d009b3d2…)
uv run python -m pytest -q               # full suite green (Phase 1B entry baseline: 327 passed, 1 skipped)
uv lock --check && uv sync --locked      # dependency pins unchanged (AIG 0.2.0 @ 7261fdd2…)
```

Stop and report if `AGENTS.md` differs, the suite is red, or HEAD has drifted
from `origin/main`. These are gates, not chores.

### A2. Approval gate (mutating stages need this first)

Written user approval naming, explicitly: cluster `trafficfuse-lab`, its
**creation**, the locally mutating image build with network access, and the run
id(s). Creation and scoped deployment only — **deletion is not part of this
authorization**. No implicit consent; no approval carried over from a previous
phase; no automatic retry of any mutating step whose outcome is ambiguous.

### A3. Preconditions

```bash
docker version --format '{{.Server.Version}}'   # daemon reachable
kind get clusters                                # trafficfuse-lab must NOT be listed
```

If a cluster named `trafficfuse-lab` already exists, **stop** — do not adopt,
reuse or overwrite it; report the finding. (kind's default API-server port is
randomized, so no port reservation is needed; ports `8000/8080/8081/8443` remain
the allowed app bind set, `5000` is never used.)

### A4. Kubeconfig preparation and image build inputs

```bash
mkdir -p .runtime/kubeconfig
# The file is created by `kind create cluster --kubeconfig …` in Stage B.
# Never pass or edit the global kubeconfig; never point kubectl at it.
```

The dedicated path is validated by `lifecycle.validate_kubeconfig_path()` —
exact resolved repository-owned path; the global kubeconfig and any path
outside the verified repository root are refused (including a foreign
repository with an identical directory layout). Verify the image build inputs
are present and pinned:

```bash
uv run python -m pytest tests/test_lab_manifests.py -q   # image pin parity with uv.lock
```

### A5. Offline preflight (still no mutation)

```bash
uv run python -m trafficfuse.lab.lifecycle_cli preflight --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
```

Review the non-mutating plan (corrected Stage B–F ordering). `create` is
unlocked only by the Stage A2 approval; it still refuses in code — the plan is
for human review, not blind execution.

> **STOP** — do not proceed without the written Stage A2 approval naming
> creation (not deletion), the image build, and the run ids.

---

## Stage B — Docker image build (locally mutating; approval required)

```bash
docker build --pull -f deploy/lab/Dockerfile -t trafficfuse-lab-service:phase1a .
docker build --check -f deploy/lab/Dockerfile .        # no warnings
```

The base image is digest-pinned and all dependency pins mirror `uv.lock`
(verified by `tests/test_lab_manifests.py`). The Agentic Intent Gate is **not**
installed in the image. If the build fails, Phase 1B stops here with
`DOCKER_BUILD_STATUS=FAILED`; the image is never built from an unpinned base or
with extra packages to "make it work". A locally mutating step with an
ambiguous outcome (interrupted build) is re-run only after human review —
never automatically.

> **STOP** — build success is recorded verbatim; a failed build ends the phase.

---

## Stage B — Cluster creation (future authorization only)

```bash
kind create cluster --name trafficfuse-lab --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
```

This single command creates the cluster **and** installs the dedicated
kubeconfig (its `current-context` is `kind-trafficfuse-lab`; the API server is
a local `127.0.0.1` endpoint). The ambient kubeconfig is untouched.

### Record initial creation-time identity

Independently inspect the newly created cluster with explicitly scoped,
read-only commands:

```bash
kubectl $KC get nodes -o json
```

Extract the non-sensitive identity fields from the dedicated kubeconfig with
the **trusted local parser** (prints only the endpoint and the CA fingerprint —
never the raw file, never client key material):

```bash
uv run python -c "from trafficfuse.lab.lifecycle import canonical_kubeconfig_path, load_kubeconfig_document, verify_kubeconfig_document; i = verify_kubeconfig_document(load_kubeconfig_document(canonical_kubeconfig_path())); print(i.context, i.cluster); print(i.api_server); print(i.ca_fingerprint_sha256)"
```

Then write the creation record `.runtime/state/cluster-trafficfuse-lab.json`
(schema `trafficfuse-ownership-record-v2`) with exactly these fields:
`cluster: "trafficfuse-lab"`, `context: "kind-trafficfuse-lab"`, `namespace:
"trafficfuse-system"`, `api_server` (the local endpoint printed above),
`ca_fingerprint_sha256` (the fingerprint printed above), `kind_node_names`
(the exact node names, e.g. `["trafficfuse-lab-control-plane"]`), `created_at`
(strict timezone-aware UTC ISO-8601, e.g. `2026-…Z` or `…+00:00`),
`created_by: "trafficfuse"`, `kubeconfig:
".runtime/kubeconfig/trafficfuse-lab.yaml"`. The record is **metadata**, not
proof of ownership; Stage D supplies the proof.

**Refuse continuation on mismatched evidence:** if the independently inspected
node names or identity fields do not match what was recorded, stop — do not
edit the record to match; report the finding.

> **STOP** — the cluster exists and is recorded; nothing else has been created.

---

## Stage C — Minimal namespace bootstrap (explicitly selected manifest only)

```bash
kind load docker-image trafficfuse-lab-service:phase1a --name trafficfuse-lab
kubectl $KC apply -f deploy/lab/namespace.yaml
```

**Ordering rules:** the image loads **after** cluster creation and **before**
any workload readies (Pods run with `imagePullPolicy: Never`). The namespace is
bootstrapped with **only** `deploy/lab/namespace.yaml` — the whole
`deploy/lab` kustomization is deliberately **not** applied here, because it
also creates the Deployments and Services, which must not exist before
ownership verification (Stage D). Only this explicitly authorized bootstrap
step runs before full ownership verification.

Verify the namespace ownership metadata (read-only):

```bash
kubectl $KC get namespace trafficfuse-system -o json
```

The namespace must carry `trafficfuse.io/owner=trafficfuse-lab` and
`app.kubernetes.io/managed-by=trafficfuse-lab`. If the labels are absent or
wrong, stop — do not hand-edit the namespace; report the finding.

> **STOP** — `trafficfuse-system` exists with verified ownership labels; the
> namespace-dependent verification can now run (no circular dependency).

---

## Stage D — Live ownership verification (trusted collector; read-only)

```bash
uv run python -m trafficfuse.lab.lifecycle_cli verify --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --receipt .runtime/state/cluster-trafficfuse-lab.json --collect
```

This is the single trusted verification operation: it verifies the dedicated
kubeconfig document with the trusted local parser (exact context/cluster
binding, local HTTPS API host only, CA-fingerprint anchor, no foreign
contexts, no embedded credentials), loads and cross-checks the creation
record, and then **performs the collection itself** through the read-only
collector — `kubectl get nodes`, `kubectl get namespace trafficfuse-system`
and `docker inspect` per recorded node, every command allowlisted and scoped
to the dedicated kubeconfig and exact context. It compares actual facts with
the creation-time records: API server, CA fingerprint, node identities,
namespace identity and Docker node identity.

The printed output is redacted to the minimal identity information: expected
context, cluster, API endpoint, CA fingerprint, per-check verification
results, non-sensitive live facts and the exact executed command trace. No
credential material is printed or archived; a failing command's output is
stripped of credential-shaped material before it can reach a message.

Caller-supplied JSON (`--facts`) exists only for inspection, is marked
untrusted, and is never sufficient proof for any destructive operation.
Trust is established by **execution**, not by any provenance string.

Any refusal exits 2 with the failing check. **Stop immediately** — do not
apply workloads against an unverified cluster, and do not retry mutations
whose outcome is ambiguous.

> **STOP** — proceed to application deployment only on a verified, trusted
> ownership outcome.

---

## Stage E — Application deployment (explicitly selected manifests)

```bash
kubectl $KC -n trafficfuse-system apply -f deploy/lab/healthy-deployment.yaml
kubectl $KC -n trafficfuse-system apply -f deploy/lab/faulty-deployment.yaml
kubectl $KC -n trafficfuse-system apply -f deploy/lab/service-versions.yaml
kubectl $KC -n trafficfuse-system apply -f deploy/lab/service-live.yaml
```

The namespace manifest is intentionally **not** repeated here (Stage C already
applied and verified it), and the probe Job is applied only in Stage F. Each
resource is selected explicitly — no whole-kustomization apply.

Wait for readiness:

```bash
kubectl $KC -n trafficfuse-system rollout status deployment/trafficfuse-healthy
kubectl $KC -n trafficfuse-system rollout status deployment/trafficfuse-faulty
kubectl $KC -n trafficfuse-system get pods -o wide
```

All Pods must be `Running` with `READY 1/1` before any probe. Record the exact
Pod names and restart counts in the run report.

Confirm the live Service initially routes to the faulty version:

```bash
kubectl $KC -n trafficfuse-system get service trafficfuse-live -o jsonpath='{.spec.selector}'
```

Expected: `{"app":"trafficfuse","version":"faulty"}`. If it does not select
`version=faulty`, stop — the degraded scenario depends on this selector, and
changing it is a Phase 3 mutation that Phase 1B must not perform.

> **STOP** — workloads ready and routing confirmed; before any probe.

---

## Stage F — Probe execution (evidence collection)

```bash
kubectl $KC -n trafficfuse-system apply -f deploy/lab/probe-job.yaml
kubectl $KC -n trafficfuse-system wait --for=condition=complete job/trafficfuse-probe --timeout=300s
kubectl $KC -n trafficfuse-system logs job/trafficfuse-probe
```

The Job runs `trafficfuse.lab.probe_cli` against
`http://trafficfuse-live.trafficfuse-system.svc.cluster.local/checkout` with
`--scenario-id phase1b-live --target-label service:trafficfuse-live --requests 40
--concurrency 4 --timeout 5`, writing `/evidence/probe-live.json` in its emptyDir
(ephemeral) **and** printing the complete evidence document to stdout between
`TRAFFICFUSE_EVIDENCE_BEGIN` / `TRAFFICFUSE_EVIDENCE_END` markers. The marker
block is the durable evidence channel. A failed Job exits non-zero — inspect
`kubectl $KC -n trafficfuse-system describe job/trafficfuse-probe` and the Pod
logs before any retry; never edit the Job to force success.

> **STOP** — the Job must be complete before evidence export; an incomplete or
> failed Job ends the evidence attempt with a report, not a retry loop.

---

## Stage F — Evidence export and validation (byte-preserving)

```bash
kubectl $KC -n trafficfuse-system logs job/trafficfuse-probe 2>/dev/null | uv run python -m trafficfuse.lab.evidence_cli --logs - --run-id phase1b-live-001
```

The exporter preserves the raw capture **byte-for-byte** before parsing,
rejects non-UTF-8 or oversized captures (bounded), writes atomically, and
stores under `artifacts/phase1b/phase1b-live-001/`: `probe-evidence.json`,
`raw-probe-logs.txt` and `evidence-audit.json` (exact byte length, SHA-256,
strict UTC timestamp, status). Raw logs are preserved **even when extraction
or validation fails** — the audit file records the failing stage and stderr
points at the preserved path. Exit codes: 0 success, 3 structural format
problem, 4 strict validation failure. An existing run directory is never
overwritten — use a new run id.

Expected evidence properties for a 40-request run at the configured 50% fault
rate: `total_requests=40`, `success_count=20`, `error_count=20`,
`observed_error_percent=50.0`, sequences 1..40 each exactly once, every record a
classified outcome, latency percentiles present, `synthetic_workload` declared,
and timezone-aware UTC timestamps with `completed_at_utc >= started_at_utc`. The
configured fault percentage itself never appears in the evidence.

Then produce the Phase 1B report: measured error rate and latency for the
faulty live Service versus the healthy version Service on real probes, the
exact fault distribution (Bresenham exactness), the environment facts
(cluster, context, node, image digest/tag, commit), and the artifact paths with
their audit SHA-256 values. Synthetic or estimated numbers are never presented
as measurements (AGENTS.md).

> **STOP** — evidence validated (or failure documented with preserved raw
> logs); report written before preservation.

---

## Preservation of resources for inspection (no deletion in Phase 1B)

Phase 1B ends with the cluster **running and preserved**:

- the `trafficfuse-lab` cluster, the `trafficfuse-system` namespace, both
  Deployments, all three Services and the completed probe Job stay in place
  for inspection;
- all evidence artifacts under `artifacts/phase1b/<run-id>/` stay in place
  (git-ignored, operator-local), including raw logs and audit files;
- the created resources (names, images, node, endpoints, selectors) are
  documented in the Phase 1B report;
- cleanup requirements are reported to the human operator.

**No automatic cleanup follows the experiment.** Removing the cluster is
outside the Phase 1B authorization scope and is **not** part of this runbook:
cleanup requires a separate explicit human authorization, and the guarded
`lifecycle` destruction path (dedicated kubeconfig, creation record, trusted
live collection, ownership verification — with the collector performing the
collection itself) remains the only supported route. Never issue ad-hoc
removal commands against the cluster, its containers or its namespace.

> **STOP** — end of the authorized Phase 1B sequence: resources preserved,
> evidence archived, cleanup awaiting separate authorization.

---

## Corrections applied to the earlier Phase 1A.2 sequence

| # | Earlier text | Correction |
| --- | --- | --- |
| 1 | Ownership verification (`verify --collect`) ran before the namespace existed | Namespace bootstrap (Stage C, `deploy/lab/namespace.yaml` only) precedes the namespace-dependent collector read (Stage D); no circular dependency with cluster creation |
| 2 | `kind load docker-image …` effectively tied to pre-creation ordering | Image load is Stage C: after `kind create cluster`, before workload readiness (Pods use `imagePullPolicy: Never`) |
| 3 | `kubectl … --context kind-trafficfuse-system` used a namespace as a context | Context is `kind-trafficfuse-lab`; namespace via `-n trafficfuse-system` |
| 4 | Raw kubeconfig dump command used to read identity | Identity extraction via the trusted local parser (endpoint + CA fingerprint only); no credential-bearing output, redacted failure messages |
| 5 | Runbook ended with a direct cluster-removal command | Deletion removed from the Phase 1B scope; preservation + separate cleanup authorization (guarded lifecycle path only) |
| 6 | The whole-kustomization apply created Deployments/Services during bootstrap | Explicitly selected manifests per stage; namespace manifest alone in Stage C |
| 7 | Caller-supplied facts JSON as ownership evidence | Trusted collector performs collection inside the verification operation; caller JSON is inspection-only and marked untrusted (no provenance string exists to spoof) |
