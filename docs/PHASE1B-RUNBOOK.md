# TrafficFuse AI — Phase 1B Operator Runbook (Corrected Ordering)

**Status:** documentation only — **no command in this runbook has been executed.**
Phase 1B has not begun; nothing here is permission to mutate. Every mutating step
requires the explicit written approvals listed in step 2, and the environment is
preserved at the end (no deletion without separate authorization).

This runbook supersedes the workflow sketch in
`docs/PHASE1A1-HARDENING-REPORT.md` §7 and `docs/PHASE1-PREFLIGHT.md` §8, both of
which contained a **step-ordering defect**: they listed `kind load docker-image`
*before* `kind create cluster`. A kind image is loaded into the nodes of an
existing cluster; loading before creation is impossible. The corrected order
below loads the image **after** cluster creation (step 8). Two further
corrections are incorporated: the ownership-verification step now uses the
**trusted collector** (`lifecycle_cli verify --collect`), not caller-supplied
JSON, and every `kubectl` invocation names the context exactly
(`--context kind-trafficfuse-lab`) with the namespace passed separately
(`-n trafficfuse-system`) — a namespace is never a context.

---

## 0. Exact identities (pin these; nothing else is a valid target)

| Identity | Exact value |
| --- | --- |
| Cluster | `trafficfuse-lab` |
| Context | `kind-trafficfuse-lab` (**never** `kind-trafficfuse-system` — that string is not a context) |
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
`vmware-k8s` are owned by other projects and are never valid targets. `vmware-k8s`
is non-disposable and must never be read from or written to. The ambient
`kubectl` context at Phase 1A time is the foreign `kind-playground-crd` — it is
never selected, switched or relied upon; the ambient context is never used at any
step. Every `kubectl` invocation below carries **both**:

```
--kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --context kind-trafficfuse-lab
```

(namespace via `-n trafficfuse-system`). If any command is ever run without these
flags, stop and re-issue it with them.

---

## 1. Repository and state verification (read-only, always first)

```bash
git status --short                       # must be clean or understood
git rev-parse HEAD                       # record the exact commit in the report
git rev-parse origin/main                # HEAD == origin/main before starting
sha1sum AGENTS.md                        # expected: 03870389d009b3d2…, unchanged
uv run python -m pytest -q               # full suite green (baseline: 295 passed, 1 skipped)
uv lock --check && uv sync --locked      # dependency pins unchanged
```

Stop and report if `AGENTS.md` differs, the suite is red, or HEAD has drifted from
`origin/main`. Do not "fix" these inside Phase 1B; they are gates, not chores.

## 2. Approval gate (mutating steps need this first)

Written user approval naming, explicitly: cluster `trafficfuse-lab`, its creation
**and** its later deletion, the image build with network access, and the run id(s).
No implicit consent; no approval carried over from a previous phase.

## 3. Preconditions (read-only)

```bash
docker version --format '{{.Server.Version}}'   # daemon reachable
kind get clusters                                # trafficfuse-lab must NOT be listed
kubectl config get-contexts -o name | grep -x kind-trafficfuse-lab || echo "absent (expected)"
```

If a cluster named `trafficfuse-lab` (or a context `kind-trafficfuse-lab`) already
exists, **stop** — do not adopt, reuse or overwrite it; report the finding.
(kind's default API-server port is randomized, so no port reservation is needed;
ports `8000/8080/8081/8443` remain the allowed app bind set, `5000` is never used.)

## 4. Kubeconfig preparation (dedicated path only)

```bash
mkdir -p .runtime/kubeconfig
# The file is created by `kind create cluster --kubeconfig …` in step 7.
# Never pass or edit ~/.kube/config; never point kubectl at the ambient file.
```

The dedicated path is validated by `lifecycle.validate_kubeconfig_path()`
(exact resolved repository-owned path; global kubeconfigs and any path outside
the verified repository root are refused — including a foreign repository with
an identical directory layout).

## 5. Image build and static validation (network required)

```bash
docker build --pull -f deploy/lab/Dockerfile -t trafficfuse-lab-service:phase1a .
docker build --check -f deploy/lab/Dockerfile .        # no warnings
```

The base image is digest-pinned and all 15 dependency pins mirror `uv.lock`. The
Agentic Intent Gate is **not** installed in the image. If this build fails,
Phase 1B stops here with `DOCKER_BUILD_STATUS=FAILED`; the image is never built
from an unpinned base or with extra packages to "make it work".

## 6. Offline preflight (still no mutation)

```bash
uv run python -m trafficfuse.lab.lifecycle_cli preflight \
    --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
```

Review the non-mutating plan. `create` is only *unlocked* by the step-2 approval;
it still refuses in code until the Phase 1B authorization path is enabled — the
plan is for human review, not for blind execution.

## 7. Cluster creation (first mutating step — approval required)

```bash
kind create cluster --name trafficfuse-lab \
    --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
```

This single command creates the cluster **and** installs the dedicated kubeconfig
(its `current-context` is `kind-trafficfuse-lab`; the API server is a local
`127.0.0.1` endpoint). The ambient kubeconfig is untouched.

## 8. Load the image into the cluster nodes (after creation — corrected order)

```bash
kind load docker-image trafficfuse-lab-service:phase1a --name trafficfuse-lab
```

**Ordering rule:** image load comes *after* `kind create cluster` and *before*
applying the workloads — loading into a cluster that does not exist yet is
impossible, and applying workloads before the image is on the nodes would leave
Pods in `ImagePullBackOff` (`imagePullPolicy: Never`).

## 9. Creation record and trusted identity verification

Collect the cluster's real identity with **read-only, context-pinned** commands:

```bash
KC="--kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --context kind-trafficfuse-lab"

kubectl $KC config view --raw --minify -o json     # server + certificate-authority-data
kubectl $KC get nodes -o json                      # exact node names
```

Then write the creation record `.runtime/state/cluster-trafficfuse-lab.json`
(`trafficfuse-ownership-record-v2`) with exactly these fields — `cluster:
"trafficfuse-lab"`, `context: "kind-trafficfuse-lab"`, `namespace:
"trafficfuse-system"`, `api_server` (the local server URL from the command above),
`ca_fingerprint_sha256` (SHA-256 of the decoded CA bytes), `kind_node_names` (exact
node names, e.g. `["trafficfuse-lab-control-plane"]`), `created_at` (strict
timezone-aware UTC ISO-8601, e.g. `2026-…Z` or `…+00:00`), `created_by:
"trafficfuse"`, `kubeconfig: ".runtime/kubeconfig/trafficfuse-lab.yaml"`. The
record is **metadata**; it is not itself proof of ownership.

Verify with the **trusted collector** (allowlisted read-only commands: `config
view`, `get nodes`, `get namespace`, `docker inspect` — all explicitly scoped to
the dedicated kubeconfig and exact context):

```bash
uv run python -m trafficfuse.lab.lifecycle_cli verify \
    --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \
    --receipt .runtime/state/cluster-trafficfuse-lab.json \
    --collect
```

Verification cross-checks: kubeconfig document identity (context binding, local
API host, CA fingerprint anchor, no foreign contexts, no embedded credentials),
the creation record, collector-sourced live facts (API server, CA fingerprint,
node names, `trafficfuse.io/owner` + `app.kubernetes.io/managed-by` namespace
labels) and Docker node identity. Caller-supplied JSON (`--facts`) may be used
for inspection but is marked untrusted and is **never** sufficient proof for any
destructive operation. A refusal exits 2 with the failing check — stop and report.

## 10. Apply the baseline stack

```bash
kubectl $KC apply -k deploy/lab
```

This creates `trafficfuse-system` (with ownership labels), both Deployments and
all three Services. The probe Job is **not** part of the kustomization and is
applied only in step 13.

## 11. Wait for readiness

```bash
kubectl $KC -n trafficfuse-system rollout status deployment/trafficfuse-healthy
kubectl $KC -n trafficfuse-system rollout status deployment/trafficfuse-faulty
kubectl $KC -n trafficfuse-system get pods -o wide
```

All Pods must be `Running` with `READY 1/1` before any probe. Record the exact
Pod names and restart counts in the run report.

## 12. Confirm live routing points at the faulty version

```bash
kubectl $KC -n trafficfuse-system get service trafficfuse-live -o jsonpath='{.spec.selector}'
# expected: {"app":"trafficfuse","version":"faulty"}
```

If `trafficfuse-live` does not select `version=faulty`, stop — the degraded
scenario depends on this selector, and changing it is a Phase 3 mutation that
Phase 1B must not perform.

## 13. Run the probe scenario (evidence collection)

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
logs before retrying; never edit the Job to force success.

## 14. Export and validate the evidence (byte-preserving)

```bash
kubectl $KC -n trafficfuse-system logs job/trafficfuse-probe 2>/dev/null | \
    uv run python -m trafficfuse.lab.evidence_cli --logs - --run-id phase1b-live-001
```

The collector preserves the raw capture **byte-for-byte** before parsing, rejects
non-UTF-8 or oversized captures (bounded), writes atomically, and stores under
`artifacts/phase1b/phase1b-live-001/`: `probe-evidence.json`,
`raw-probe-logs.txt` and `evidence-audit.json` (exact byte length, SHA-256,
strict UTC timestamp, status). Raw logs are preserved **even when extraction or
validation fails** — the audit file records the failing stage and the stderr
message points at the preserved path. Exit codes: 0 success, 3 structural format
problem, 4 strict validation failure. An existing run directory is never
overwritten — use a new run id.

Expected evidence properties for a 40-request run at the configured 50% fault
rate: `total_requests=40`, `success_count=20`, `error_count=20`,
`observed_error_percent=50.0`, sequences 1..40 each exactly once, every record a
classified outcome, latency percentiles present, `synthetic_workload` declared,
and timezone-aware UTC timestamps with `completed_at_utc >= started_at_utc`. The
configured fault percentage itself never appears in the evidence.

## 15. Measurements and report

From the validated document (and repeat runs with new run ids for the healthy
endpoint scenario), produce the Phase 1B report: measured error rate and latency
for the faulty live Service versus the healthy version Service on real probes,
the exact fault distribution (Bresenham exactness: exactly P failures per
complete 100-request sequence, request-index stamped for cross-Pod exactness),
the environment facts (cluster, context, node, image digest/tag, commit), and
the artifact paths with their audit SHA-256 values. Synthetic or estimated
numbers are never presented as measurements (AGENTS.md).

## 16. Preserve the environment

Phase 1B ends with the cluster **running and preserved** unless deletion was
separately approved. If (and only if) deletion is later authorized in writing:

```bash
# Ownership re-verification immediately before destruction, with the trusted
# collector; the ambient context is never used:
uv run python -m trafficfuse.lab.lifecycle_cli verify \
    --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \
    --receipt .runtime/state/cluster-trafficfuse-lab.json --collect

kind delete cluster --name trafficfuse-lab     # exact name only, never a wildcard
```

The guard chain refuses foreign names (`incident-lab`, `pcp-dev`, `playground-crd`,
`vmware-k8s`), caller-supplied facts, unverified ownership and missing
authorization; deletion is enabled only through the authorized Phase 1B path.
After deletion, record the outcome in the Phase 1B report and leave no
TrafficFuse resources behind.

---

## Corrections applied to the earlier workflow sketch

| # | Earlier text (Phase 1A.1 §7 / preflight §8) | Correction |
| --- | --- | --- |
| 1 | `kind load docker-image …` listed **before** `kind create cluster` | Load **after** creation (step 8): a kind image loads into the nodes of an existing cluster; the earlier order was impossible |
| 2 | `kubectl … --context kind-trafficfuse-system logs …` | The context is `kind-trafficfuse-lab`; `trafficfuse-system` is the namespace (`-n trafficfuse-system`) — a namespace is never a context |
| 3 | `lifecycle_cli verify … --facts cluster-facts.json` | Caller JSON is untrusted and never sufficient proof; verify with `--collect` (trusted read-only collector). `--facts` is inspection-only and prints an untrusted warning |
| 4 | Separate `kubectl … create namespace` step | `apply -k deploy/lab` includes `namespace.yaml` (with ownership labels); one apply path, no duplicate-creation race |
| 5 | "Write the creation record … then verify" without content | Step 9 specifies the exact `trafficfuse-ownership-record-v2` field set and the strict timezone-aware UTC `created_at` format |
