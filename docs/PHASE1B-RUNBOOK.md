# TrafficFuse AI — Phase 1B Operator Runbook

**PHASE1B_READINESS=PARTIAL — DOCKER_BUILD_STATUS=BUILD_NOT_VERIFIED.**
This sequence is prepared and checked offline. Docker build, Kind creation,
deployment and in-cluster HTTP evidence have **not** been verified. Do not run
this document automatically. Written, task-specific approval is required first.
See [the final hotfix report](PHASE1B-FINAL-HOTFIX-REPORT.md).

## Shell and exact identities

Use **PowerShell 7.4 or newer**, launched with `pwsh -NoProfile` from the
repository root. Windows PowerShell 5.1, Bash and WSL are not the shell for
these examples. Version 7.4+ preserves native stdout bytes with `>`; do not
replace log capture with a PowerShell text pipeline, `Out-File` or `Tee-Object`.
Run stages individually, inspect their output and stop on any failure.

| Identity | Exact value |
| --- | --- |
| Kind cluster | `trafficfuse-lab` (single control-plane) |
| Context | `kind-trafficfuse-lab` |
| Namespace | `trafficfuse-system` |
| Dedicated kubeconfig | `.runtime/kubeconfig/trafficfuse-lab.yaml` |
| Creation record | `.runtime/state/cluster-trafficfuse-lab.json` |
| Image | `trafficfuse-lab-service:phase1a` |
| Live Service | `trafficfuse-live`, initially `app=trafficfuse,version=faulty` |
| Healthy Service | `trafficfuse-healthy`, `app=trafficfuse,version=healthy` |
| Healthy Job / scenario | `trafficfuse-probe-healthy` / `phase1b-healthy` |
| Faulty live Job / scenario | `trafficfuse-probe` / `phase1b-live` |

`incident-lab`, `pcp-dev`, `playground-crd`, and `vmware-k8s` are protected.
Never select or access their contexts. Do not change the ambient context.
Every kubectl call uses the argument array below; `@KC` splats four separate
arguments. Never store all flags in one string.

```powershell
if ($PSVersionTable.PSVersion -lt [version]'7.4') { throw 'PowerShell 7.4+ required' }
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
function Assert-NativeSuccess {
    if ($LASTEXITCODE -ne 0) { throw "Native command failed: exit $LASTEXITCODE; STOP" }
}
$KC = @('--kubeconfig', '.runtime/kubeconfig/trafficfuse-lab.yaml', '--context', 'kind-trafficfuse-lab')
$SessionId = 'phase1b-001'  # operator-approved, fresh ID; never reuse a directory
$HealthyRunId = "$SessionId-healthy"
$LiveRunId = "$SessionId-live"
$CaptureRoot = "artifacts/phase1b/captures-$SessionId"
```

## 1. Read-only preflight and authorization check

```powershell
git status --short
Assert-NativeSuccess
git rev-parse HEAD
Assert-NativeSuccess
git rev-parse origin/main
Assert-NativeSuccess
Get-FileHash AGENTS.md -Algorithm SHA256
uv lock --check
Assert-NativeSuccess
uv sync --locked
Assert-NativeSuccess
uv pip check
Assert-NativeSuccess
uv run pytest -q
Assert-NativeSuccess
uv run python -m trafficfuse.lab.lifecycle_cli preflight --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
Assert-NativeSuccess
```

Record HEAD and the understood working tree. Review any divergence from
`origin/main`; do not silently reset. AGENTS.md SHA-256 must remain
`30fab17cc82e831a23edb15fa03bfb22821a0be24bbc24f2b94e094e287580be`.
AIG must remain installed at 0.2.0, pinned to
`7261fdd2f47d2b15fabe8faac527eeee37338eb1`, default five-second budget.
The lifecycle `create`/`destroy` commands still refuse; the printed preflight
plan is for review only.

Written approval must name this new disposable local cluster, dedicated path,
image build including network pulls, image loading, Namespace/Deployment/Service
creation, **both** probe Jobs and their HTTP GETs, the run IDs, read-only identity
collection and local artifacts. It does not grant future traffic switching,
AI diagnosis, Plan Approval, Action Authorization, or cleanup permission.

After authorization, inspect only local tooling and cluster names:

```powershell
docker version --format '{{.Server.Version}}'
Assert-NativeSuccess
kind get clusters
Assert-NativeSuccess
if (Test-Path .runtime/kubeconfig/trafficfuse-lab.yaml) { throw 'Existing kubeconfig; STOP, no overwrite/adoption' }
if (Test-Path .runtime/state/cluster-trafficfuse-lab.json) { throw 'Existing record; STOP, no overwrite/adoption' }
if (Test-Path $CaptureRoot) { throw 'Capture path already exists; STOP' }
if (Test-Path "artifacts/phase1b/$HealthyRunId") { throw 'Healthy export already exists; STOP' }
if (Test-Path "artifacts/phase1b/$LiveRunId") { throw 'Live export already exists; STOP' }
New-Item -ItemType Directory -Force .runtime/kubeconfig | Out-Null
```

**STOP** if `trafficfuse-lab` already exists, tooling is unavailable, any check
fails, inputs drift, or written approval is missing. Never adopt an existing
cluster. `kind get clusters` lists names only; it does not access foreign APIs.

## 2. Docker image build

```powershell
docker build --pull -f deploy/lab/Dockerfile -t trafficfuse-lab-service:phase1a .
Assert-NativeSuccess
docker build --check -f deploy/lab/Dockerfile .
Assert-NativeSuccess
```

The base digest and dependencies are pinned; AIG is excluded from the lab image.
Record actual build output and image identity. **STOP** on failure; report
`DOCKER_BUILD_STATUS=FAILED`. An ambiguous mutation requires human review before
any repeat. Do not alter pins to get a build through.

## 3. Cluster creation

```powershell
kind create cluster --name trafficfuse-lab --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
Assert-NativeSuccess
```

Kind writes only the dedicated kubeconfig. Its context and cluster entry are
`kind-trafficfuse-lab`; the parser returns the logical cluster `trafficfuse-lab`.
The earlier logical-name cluster entry is also accepted only for this exact lab.
**STOP** if creation fails or its outcome is ambiguous. Do not retry or adopt.

## 4. Capture actual identity and generate the creation record

```powershell
uv run python -m trafficfuse.lab.lifecycle_cli record-creation --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --receipt .runtime/state/cluster-trafficfuse-lab.json --authorized
Assert-NativeSuccess
```

`--authorized` attests the operator's already granted creation-workflow approval;
it cannot establish permission by itself. Use this only immediately after the
approved successful creation in step 3. It does not establish whether an existing
cluster was created by TrafficFuse; the operator must not use it for adoption.
The command validates the dedicated kubeconfig locally, reads nodes with exact
kubeconfig/context flags, then inspects only the expected Docker node's name and
Kind cluster/role labels. It requires exactly `trafficfuse-lab-control-plane` and
its actual `metadata.creationTimestamp`. It creates `.runtime/state/` before an
exclusive write, flushes the record and reloads it through the real
`load_ownership_receipt()` validator. Existing records are never overwritten.

Every required field is generated; **do not hand-fill identity values**:

| Field | Value / source |
| --- | --- |
| `receipt_schema` | `"trafficfuse-ownership-record-v2"` |
| `cluster` | `"trafficfuse-lab"` |
| `context` | `"kind-trafficfuse-lab"` |
| `namespace` | `"trafficfuse-system"` (intended bootstrap namespace) |
| `api_server` | Actual local HTTPS endpoint from the dedicated file |
| `ca_fingerprint_sha256` | SHA-256 of decoded CA data from that file |
| `kind_node_names` | Actual scoped node observation, cross-checked with Docker |
| `created_at` | Observed control-plane Node creation timestamp, UTC ISO-8601 |
| `created_by` | `"trafficfuse"`, operator-attested workflow metadata |
| `kubeconfig` | `".runtime/kubeconfig/trafficfuse-lab.yaml"` |

This is **recorded metadata**, not independent proof that TrafficFuse created
the cluster. Matching names, labels, a timestamp and `created_by` do not prove
historical ownership. Step 7 must independently recollect and cross-check live
facts after bootstrap. The generator does not bypass that collector or authorize
destruction. No raw kubeconfig credentials are printed or archived.

**STOP** on missing/inconsistent identity, invalid schema/time, existing receipt
or any collection failure. Never edit the record to match unexpected facts.

## 5. Load the image into the new cluster

```powershell
kind load docker-image trafficfuse-lab-service:phase1a --name trafficfuse-lab
Assert-NativeSuccess
```

**STOP** on load failure. Pods use `imagePullPolicy: Never`; creation must precede
loading, and loading must precede workloads.

## 6. Namespace bootstrap

```powershell
kubectl @KC apply -f deploy/lab/namespace.yaml
Assert-NativeSuccess
kubectl @KC get namespace trafficfuse-system -o json
Assert-NativeSuccess
```

Apply only this manifest. The Namespace must carry
`trafficfuse.io/owner=trafficfuse-lab` and
`app.kubernetes.io/managed-by=trafficfuse-lab`. No workloads exist yet.
**STOP** if bootstrap fails or labels disagree; do not hand-edit labels.

## 7. Live ownership verification

```powershell
uv run python -m trafficfuse.lab.lifecycle_cli verify --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --receipt .runtime/state/cluster-trafficfuse-lab.json --collect
Assert-NativeSuccess
```

The trusted host path performs its own read-only collection: scoped node and
namespace reads plus Docker node inspection. It checks endpoint, CA fingerprint,
exact node identities, namespace ownership labels and Docker identities against
the record. Output contains minimal identity, checks and the command trace.
`--facts` is inspection-only and is not used here.

The collector and creation recorder use an exact command-shape gate: scoped
node JSON reads, this Namespace's JSON read, and two fixed Docker node identity
projections. Configuration subcommands, extra options and duplicate scope flags
are rejected. Identity parsing stays local. The operator's readiness, selector
and log commands elsewhere in this runbook are separate explicitly scoped
operations; they are not inputs to the collector executor. See the
[security-gate report](PHASE1B-SECURITY-GATE-REPORT.md).

`CollectorExecution` is a **forgeable Python diagnostic marker**. Its nonce is
not a signature; `is_collector_executed()` and the legacy `trusted` output field
only indicate marker presence. Neither proves collection or grants authority.
The boundary is controlled host execution of `verify_live_ownership()`, which
accepts no facts parameter and invokes collection itself. Trusted host code and
executors are assumed; this is not malicious-Python isolation. Deletion remains
disabled even when all checks pass.

**STOP** on any refusal or missing check. Only a successful step 7 permits the
already authorized deployment stage; a Python marker alone never permits it.

## 8. Application deployment

```powershell
kubectl @KC -n trafficfuse-system apply -f deploy/lab/healthy-deployment.yaml
Assert-NativeSuccess
kubectl @KC -n trafficfuse-system apply -f deploy/lab/faulty-deployment.yaml
Assert-NativeSuccess
kubectl @KC -n trafficfuse-system apply -f deploy/lab/service-versions.yaml
Assert-NativeSuccess
kubectl @KC -n trafficfuse-system apply -f deploy/lab/service-live.yaml
Assert-NativeSuccess
```

**STOP** on failure or ambiguous outcome. No full kustomization apply is used.

## 9. Wait for healthy/faulty readiness

```powershell
kubectl @KC -n trafficfuse-system rollout status deployment/trafficfuse-healthy --timeout=180s
Assert-NativeSuccess
kubectl @KC -n trafficfuse-system rollout status deployment/trafficfuse-faulty --timeout=180s
Assert-NativeSuccess
kubectl @KC -n trafficfuse-system get pods -o wide
Assert-NativeSuccess
```

Record Pod names and restarts. **STOP** if either deployment is unready.
Readiness is not successful `/checkout` business-endpoint evidence.

## 10. Validate the live Service selector

```powershell
$LiveServiceJson = kubectl @KC -n trafficfuse-system get service trafficfuse-live -o json
Assert-NativeSuccess
$LiveSelector = ($LiveServiceJson | ConvertFrom-Json).spec.selector
if ($LiveSelector.app -ne 'trafficfuse' -or $LiveSelector.version -ne 'faulty' -or @($LiveSelector.PSObject.Properties).Count -ne 2) { throw 'Unexpected live selector; STOP' }
```

**STOP** if routing differs. Keep the initial faulty selector unchanged; traffic
switching belongs to a separately reviewed future phase.

## 11. Probe execution — healthy HTTP baseline

```powershell
kubectl @KC -n trafficfuse-system create -f deploy/lab/probe-healthy-job.yaml
Assert-NativeSuccess
kubectl @KC -n trafficfuse-system wait --for=condition=complete job/trafficfuse-probe-healthy --timeout=300s
Assert-NativeSuccess
```

The Job requests `/checkout` from `trafficfuse-healthy`, scenario
`phase1b-healthy`, label `service:trafficfuse-healthy`, writing
`/evidence/probe-healthy.json` plus stdout evidence markers. It performs 40 HTTP
GET attempts with concurrency 4 and a five-second request timeout. Status codes
and latency are measured per attempt. **STOP** unless the Job completed.

## 12. Probe execution — faulty live-Service scenario

```powershell
kubectl @KC -n trafficfuse-system create -f deploy/lab/probe-job.yaml
Assert-NativeSuccess
kubectl @KC -n trafficfuse-system wait --for=condition=complete job/trafficfuse-probe --timeout=300s
Assert-NativeSuccess
```

This Job requests `/checkout` from `trafficfuse-live`, scenario `phase1b-live`,
label `service:trafficfuse-live`, writing `/evidence/probe-live.json` plus stdout
markers. Request count/concurrency/timeout match the baseline. Both Jobs disable
automatic retry and service-account token mounting. They only perform HTTP GETs
and write their evidence; they do not mutate application infrastructure. Job
creation itself is an explicitly authorized lab-resource mutation. `create`
refuses an existing Job; do not reuse old completed evidence or delete Jobs.

**STOP** on failure/incompletion. Inspect the exact Job with scoped read-only
commands under the same approval; no automatic retry or forced success.

## 13. Evidence export and strict validation — both documents

```powershell
New-Item -ItemType Directory $CaptureRoot | Out-Null
kubectl @KC -n trafficfuse-system logs job/trafficfuse-probe-healthy > "$CaptureRoot/healthy.log"
Assert-NativeSuccess
kubectl @KC -n trafficfuse-system logs job/trafficfuse-probe > "$CaptureRoot/live.log"
Assert-NativeSuccess
uv run python -m trafficfuse.lab.evidence_cli --logs "$CaptureRoot/healthy.log" --run-id $HealthyRunId
Assert-NativeSuccess
uv run python -m trafficfuse.lab.evidence_cli --logs "$CaptureRoot/live.log" --run-id $LiveRunId
Assert-NativeSuccess
```

The exporter strictly validates each marked document and preserves raw capture
bytes before parsing. Distinct directories `artifacts/phase1b/<session>-healthy/`
and `artifacts/phase1b/<session>-live/` each contain `probe-evidence.json`,
`raw-probe-logs.txt`, and `evidence-audit.json` with byte counts, SHA-256 values,
UTC time and final status. Existing directories cannot be overwritten. Exit 3
means capture format failure; exit 4 means strict validation/export failure.
Preserve partial logs and failure audits for review.

Configured 50% failure is a synthetic workload policy input, **not an observed
result**. Do not require 20 successes/20 errors or substitute configured values
for measurements. Real timeouts, transport errors and unexpected statuses must
remain in the evidence. **STOP** unless both exports succeed and validate.

## 14. Produce the Phase 1B evidence report

This offline summary consumes only the two validated exports. It checks their
identities and audit hashes, and derives status distributions from request
records. It never fabricates outcomes or latency.

```powershell
# BEGIN EVIDENCE SUMMARY
$Summaries = foreach ($Scenario in @('healthy', 'live')) {
    $RunId = if ($Scenario -eq 'healthy') { $HealthyRunId } else { $LiveRunId }
    $Service = if ($Scenario -eq 'healthy') { 'trafficfuse-healthy' } else { 'trafficfuse-live' }
    $Directory = "artifacts/phase1b/$RunId"
    $EvidencePath = "$Directory/probe-evidence.json"
    $RawPath = "$Directory/raw-probe-logs.txt"
    $AuditPath = "$Directory/evidence-audit.json"
    $Document = Get-Content -Raw $EvidencePath | ConvertFrom-Json
    $Audit = Get-Content -Raw $AuditPath | ConvertFrom-Json
    $EvidenceHash = (Get-FileHash $EvidencePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $RawHash = (Get-FileHash $RawPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Audit.status -ne 'ok' -or $Audit.evidence.sha256 -ne $EvidenceHash -or $Audit.raw_logs.sha256 -ne $RawHash) { throw 'Evidence audit mismatch; STOP' }
    if ($Document.scenario_id -ne "phase1b-$Scenario" -or $Document.target_label -ne "service:$Service" -or $Document.target_url -ne "http://$Service.trafficfuse-system.svc.cluster.local/checkout") { throw 'Scenario identity mismatch; STOP' }
    $Distribution = @{}
    $Document.records | Group-Object { if ($null -eq $_.status_code) { 'no_http_response' } else { [string]$_.status_code } } | ForEach-Object { $Distribution[$_.Name] = $_.Count }
    [ordered]@{
        scenario_id = $Document.scenario_id
        target_url = $Document.target_url
        total_requests = $Document.total_requests
        success_count = $Document.success_count
        error_count = $Document.error_count
        observed_error_percent = $Document.observed_error_percent
        status_code_distribution = $Distribution
        latency_ms = $Document.latency_ms
        synthetic_workload = $Document.synthetic_workload
        evidence_path = $EvidencePath
        evidence_sha256 = $EvidenceHash
        raw_logs_path = $RawPath
        raw_logs_sha256 = $RawHash
        audit_path = $AuditPath
    }
}
$ReportPath = "$CaptureRoot/phase1b-evidence-report.json"
if (Test-Path $ReportPath) { throw 'Report exists; STOP, no overwrite' }
$Summaries | ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8NoBOM $ReportPath
# END EVIDENCE SUMMARY
Get-Content -Raw $ReportPath
Get-FileHash $ReportPath -Algorithm SHA256
```

Attach the actual HEAD/working tree, local Docker/Kind versions, build output,
image digest/tag, node/namespace/Pod identities, selector, ownership verification
output, Job completion status and both evidence audits to the operator's report.
All workload data is a synthetic lab experiment; in-process test evidence is
never represented as in-cluster evidence. **STOP** on any report/hash mismatch.

## 15. Preservation of resources for inspection

Preserve `trafficfuse-lab`, the Namespace, both Deployments, all three Services,
both completed Jobs, dedicated kubeconfig, creation record, captures and exports.
Keep private files under gitignored `.runtime/` and `artifacts/`; do not publish
credentials or private state. No automatic cleanup follows this experiment.
Cluster deletion remains disabled in code and outside this runbook. Any future
cleanup requires a separately reviewed workflow and **separate explicit human
authorization**; neither an ownership record nor a collector marker grants it.

**STOP** — end of Phase 1B. Report observed results and remaining cleanup needs
to the human operator; preserve everything for inspection.
