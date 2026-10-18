# TrafficFuse AI — Phase 1B Final Hotfix Report

Date: 2026-10-19 (Pacific/Auckland). Work was offline: synthetic fixtures,
in-process ASGI/MockTransport requests, mocked command executors and local
PowerShell parsing/stubs only.

```text
PHASE1B_HOTFIX_STATUS=PASS
PHASE1B_READINESS=PARTIAL
DOCKER_BUILD_STATUS=BUILD_NOT_VERIFIED
```

## Repository and protected state

- Branch: `main`.
- Actual HEAD, unchanged: `dd0b842bc68941bb09a6c4edbb7f597fdce8034e`.
- Initial working tree: clean. Final tree: 11 modified files and 5 new files,
  all unstaged, listed below. No prior user changes were present to preserve.
- `AGENTS.md` was read completely and never edited. SHA-256 before/after:
  `30fab17cc82e831a23edb15fa03bfb22821a0be24bbc24f2b94e094e287580be`.
- `pyproject.toml` and `uv.lock` are unchanged. Installed distribution:
  `agentic-intent-gate==0.2.0`; PEP 610 commit and requested revision both
  `7261fdd2f47d2b15fabe8faac527eeee37338eb1`. Import comes from this repository's
  `.venv/Lib/site-packages/agentic_intent_gate/runtime.py`. The locally available
  annotated `v0.2.0` tag peels to the same commit. No network lookup was made.
- The installed public API and pinned example, consumer handoff and adapter guide
  were inspected. No shared source was modified/copied; the five-second default
  and unregistered traffic-switch executor remain in place.
- No actual Docker, Kind or kubectl process was invoked. No real provider calls,
  image build/pull/load, cluster creation/deletion, manifest application, context
  change, real-cluster probe, commit, push, tag, release or deployment occurred.
  The protected foreign environments were not accessed.

## Findings and resolutions

| Review finding | Root cause | Resolution | Status |
| --- | --- | --- | --- |
| P1: incomplete ownership record | Runbook mentioned the v2 schema but omitted the required `receipt_schema` field and supplied no validated writer or state-directory preparation | Added `record-creation`, shared record validation, exact schema documentation, exclusive write and real-loader regression tests | PASS offline |
| P2: collector authenticity claim | Public Python dataclasses permit direct construction of facts and execution markers; marker presence was described as proof of execution | Corrected source/docs; regression explicitly constructs a marker that returns True, while independent collection and unconditional deletion refusal still hold | PASS offline |
| P2: missing healthy baseline | Only the initially faulty live Service had an executable probe workflow | Added a distinct healthy Job and scenario; both measured evidence documents must validate and appear in the audited report | PASS offline |
| Windows command compatibility | Bash flag strings, pipelines and shell-specific utilities did not define a safe Windows execution environment | Runbook now specifies PowerShell 7.4+, argument-array splatting, native exit checks and native byte redirection; parser, argv and byte-fidelity tests pass | PASS offline |

An additional compatibility defect directly affected the post-creation procedure:
the kubeconfig parser expected the logical cluster-entry name, while Kind writes
`kind-trafficfuse-lab`. It now accepts the exact Kind name and the earlier exact
logical-name form, reads the entry actually referenced by the pinned context,
and returns the same logical identity. Foreign cluster references remain denied.
This is verified with synthetic Kind-shaped kubeconfig data; live validation is
still pending.

## Exact changes

Created:

- `src/trafficfuse/lab/creation_record.py`: operator-run post-creation capture;
  scoped node observation, narrow Docker identity/Kind-label inspection,
  deterministic serialization, state directory creation, exclusive write,
  flush/fsync and reload through `load_ownership_receipt()`.
- `deploy/lab/probe-healthy-job.yaml`: healthy version-Service baseline.
- `tests/test_creation_record.py`: generated-record loader acceptance,
  determinism, missing fields, invalid times, inconsistent nodes/containers,
  authorization refusal, no overwrite, malformed responses, command failures
  and credential redaction.
- `tests/test_phase1b_scenarios.py`: execute both manifest-defined probe CLIs
  through offline transports, export/validate both documents, verify measured
  counts/latencies/hashes, exercise the PowerShell report and reject tampered or
  swapped evidence. Unexpected healthy HTTP 503 responses remain measured errors.
- `docs/PHASE1B-FINAL-HOTFIX-REPORT.md`: this report.

Modified:

- `src/trafficfuse/lab/lifecycle.py`: reusable creation-record validator; strict
  UTC timestamp, API/CA types and unique, lab-scoped node names with the expected
  control-plane; Kind kubeconfig-entry compatibility; accurate trust documentation;
  preflight descriptions include recording and both probes. Deletion stays disabled.
- `src/trafficfuse/lab/lifecycle_cli.py`: `record-creation` subcommand using the
  existing authorization-attestation guard and trusted command executor.
- `src/trafficfuse/lab/cluster_facts.py`: correct marker/trust descriptions;
  collection and verification control flow unchanged.
- `deploy/lab/probe-job.yaml`: preserve faulty live scenario; disable automatic
  retry and service-account token mounting, matching the healthy Job.
- `tests/test_trusted_ownership.py`: forged `CollectorExecution` regression;
  proves marker presence does not replace collection or enable deletion.
- `tests/test_lab_manifests.py`: include the healthy manifest in existing
  ownership, image, exposure and mutation-boundary checks.
- `tests/test_phase1b_runbook.py`: PowerShell parsing and stubbed argv execution,
  all 15 workflow steps, both exact Job lifecycles and schema documentation.
- `docs/PHASE1B-RUNBOOK.md`: complete ordered operator procedure with STOP gates,
  actual CLI interfaces and an executable offline evidence summary.
- `docs/PHASE1B-ENTRY-REVIEW.md`, `README.md`, `docs/PHASES.md`: correct active
  authenticity claims and link the current runbook/report; historical test counts
  remain identified as earlier results.

## Valid ownership-record procedure

After fresh written approval and successful creation of this exact new lab,
run step 4 of the runbook in PowerShell 7.4+:

```powershell
uv run python -m trafficfuse.lab.lifecycle_cli record-creation --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --receipt .runtime/state/cluster-trafficfuse-lab.json --authorized
if ($LASTEXITCODE -ne 0) { throw 'Creation record failed; STOP' }
```

This command was tested only with mocks here. The flag attests already granted
operator permission; it is not AIG Action Authorization, authenticated consent,
or proof of origin. Never run it to adopt an existing cluster.

The generated record includes `receipt_schema: "trafficfuse-ownership-record-v2"`
and all nine required record fields. Endpoint and CA fingerprint come from the
dedicated kubeconfig parser. Node identity and `created_at` come from the actual
scoped Node observation; `created_at` denotes its creation timestamp, not an
invented historical cluster time. Docker node name/cluster/role labels must agree.
Fixed `created_by: "trafficfuse"` and the intended namespace are workflow metadata.
`.runtime/state/` is created automatically, invalid inputs refuse before writing,
and existing receipts are never overwritten. An interrupted/failed write must
be reviewed; never automatically delete or replace the record to continue.

The receipt is **metadata, not independent ownership proof**. It does not grant
destruction authority. Full `verify --collect` still independently reads live
facts after Namespace bootstrap; the generator does not mint a `LiveFacts`
verification outcome or bypass that collector.

## Collector trust-model limitation

`CollectorExecution`, `LiveFacts`, `TrustedOwnership` and their traces are ordinary
Python data. A caller can forge a marker; `is_collector_executed()` and the legacy
`OwnershipVerification.trusted` field can then be True. The nonce is not a
signature. No cryptographic framework was added.

The security boundary remains trusted host control flow:
`verify_live_ownership()` accepts no caller facts and performs its own read-only
collection, record cross-check and Docker/namespace checks. The destruction path
also calls that pipeline itself and always refuses deletion. Neither diagnostic
marker presence nor content matching authorizes a security-critical operation.
Trusted host code, executors and process integrity are assumptions. Live tests
cannot turn these Python objects into cryptographic attestations.

## Healthy and faulty evidence workflow

Both Jobs use the existing probe and exporter unchanged. They measure individual
HTTP statuses, classifications and request durations. The application workload is
explicitly synthetic; its configured fault percentage is never an observed result.

| Scenario | Job | Target | Pod output | Host export |
| --- | --- | --- | --- | --- |
| `phase1b-healthy` | `trafficfuse-probe-healthy` | `trafficfuse-healthy` `/checkout` | `/evidence/probe-healthy.json` | `artifacts/phase1b/<session>-healthy/` |
| `phase1b-live` | `trafficfuse-probe` | `trafficfuse-live` `/checkout` | `/evidence/probe-live.json` | `artifacts/phase1b/<session>-live/` |

Each Job has 40 request attempts, concurrency 4, a five-second HTTP timeout,
no automatic retry, and no service-account token. Jobs are created explicitly
on demand and excluded from the kustomization. Initial live routing stays faulty.
Their application interactions are HTTP GETs; creating Jobs still requires
explicit infrastructure permission.

Runbook steps 11–14 create/wait both Jobs, capture their native log bytes to
separate paths, strictly export/validate both documents, then produce an offline
report from those exports. The report includes actual request/success/error counts,
observed error percentages, status-code distributions (including no-response cases),
latency measurements, artifact paths and SHA-256 values. Audits and scenario/URL
identities must match. No assumed success count, measured percentage or latency
is hardcoded. In-process test results are not presented as cluster evidence.

## Executed validation

All `uv` commands that might resolve dependencies used `UV_OFFLINE=1`.

| Check | Actual result |
| --- | --- |
| `uv lock --check` | PASS, 32 packages resolved from cache |
| `uv sync --locked` | PASS, 32 packages checked |
| `uv pip check` | PASS, all 32 installed packages compatible |
| Targeted ownership, forgery, scenario, runbook and manifest suite | **96 passed, 0 failed, 0 skipped** |
| `uv run pytest -q -o addopts='' --junitxml=.runtime/phase1b-final-hotfix/pytest.xml` | **376 passed, 1 skipped, 0 failed**, 21.53 seconds |
| Credential disclosure regressions | PASS, included above; fake credentials excluded from output |
| PowerShell AST, mocked argument expansion and native byte capture | PASS on PowerShell **7.6.7**; no infrastructure command executed |
| PowerShell dual-evidence report | PASS, including hash tampering and scenario substitution refusal |
| `git diff --check` | PASS |
| AGENTS/dependency/lock integrity | PASS, unchanged against starting state/HEAD |
| Installed AIG version/source/API/import/default budget | PASS; local annotated-tag peeling also matches |
| Local credential-pattern/private-artifact scan | PASS, no unexpected credential matches or private artifacts in source; explicit synthetic PEM fixture reviewed |

The original 327 passing cases and one platform skip are retained, with 49
additional cases. Existing runbook tests were adapted to the declared shell and
two scenarios. The first targeted run exposed a test prefix collision:
`trafficfuse-probe` also matched `trafficfuse-probe-healthy`. It was corrected
to check exact Job names and both lifecycle orders before the passing runs above.
No product failure was hidden or test safety assertion removed.

The existing Windows symlink test remains skipped for platform privilege reasons.
There is one dependency warning: Starlette deprecates its use of httpx in
TestClient. Dependencies were preserved as requested.

Private local validation artifacts are under `.runtime/phase1b-final-hotfix/`
(`pytest.xml`, `offline-audit.json`). The secret scan used bounded patterns for
private-key blocks, GitHub/API/AWS credentials and credential-bearing URLs, plus
private-path exclusion checks. It is not an exhaustive secret-detection guarantee;
no standalone secret scanner was installed. No credential contents were printed.

## Remaining blockers and exact future permission scope

Phase 1B readiness remains **PARTIAL**. Real image build/build checks, image
identity, Docker/Kind runtime compatibility, exact new-cluster creation, generated
live record, Namespace bootstrap, independent live ownership verification,
workload readiness, initial selector, both in-cluster HTTP probes and their
validated deployment report are **NOT VERIFIED**. The hotfix does not claim these
operations succeeded.

Before execution, obtain fresh, task-specific written approval naming:

1. Local image build with pinned inputs and required network pulls.
2. Creation of a **new disposable `trafficfuse-lab`** with the exact dedicated
   kubeconfig/context, scoped identity reads, local creation record and image load.
3. Applying only the named Namespace first, then independent live verification,
   then the named healthy/faulty Deployments and three Services.
4. Creating both named probe Jobs and their HTTP GETs, exact fresh run IDs,
   read-only log capture and local evidence/report persistence.
5. Preserving the cluster, resources and private artifacts for human inspection.

Any ambiguous mutation stops for review without automatic retry. Deletion is
disabled and excluded: future cleanup requires a separately reviewed workflow
and separate explicit permission. No traffic switching, AI diagnosis, runtime
Plan Approval/Action Authorization, production/foreign-cluster access, commit,
push, release or deployment permission is inferred from this offline task.
