# TrafficFuse AI — Phase 1A.1: Deployment Readiness Hardening Report

**Date:** 2026-10-18 · **Method:** offline implementation and static/offline validation only —
no cluster was created, modified or deleted; no Kubernetes mutation was executed; no ambient
kubeconfig or context was changed; no commit, push, tag or release was made; no Docker image
was pulled, no container launched, no cache pruned.

**Repository verification before any change** (mandated step 1):

| Check | Result |
| --- | --- |
| `git branch` / HEAD | `main` @ `b72fe208aee46d67f86a2a68231b22970463ca9e` (Phase 1A) |
| HEAD vs `origin/main` | identical (`b72fe208…`, fetched and compared) |
| Working tree | clean before this phase (0 dirty entries) |
| `AGENTS.md` | read in full, **not modified** — sha1 `03870389d009b3d2c9eca19181a7eb68e26053fe` (identical before and after) |
| Installed AIG | `agentic-intent-gate==0.2.0`, PEP 610 `direct_url.json` commit **`7261fdd2f47d2b15fabe8faac527eeee37338eb1`**, `requested_revision` identical; imported from `.venv` site-packages, not a source checkout; `pyproject.toml` and `uv.lock` pins unchanged |
| Foreign clusters | `incident-lab`, `pcp-dev`, `playground-crd` — untouched throughout (verified before and after) |
| Ambient context | `kind-playground-crd` (foreign) — never read, switched or relied upon |

---

## 1. Review findings and root causes

### Finding P1-a — Missing reproducible Docker image build definition

The manifests referenced `trafficfuse-lab-service:phase1a`, but no build definition existed, so
the image could not be reproduced.

**Root cause:** Phase 1A deliberately stopped at manifest authoring; the image contract was
assumed rather than implemented.

**Resolution:** `deploy/lab/Dockerfile` + `deploy/lab/requirements-lab.txt` + root
`.dockerignore`. One shared image runs both roles: the FastAPI workload
(`python -m trafficfuse.lab.service`, healthy/faulty modes) and the in-cluster probe
(`python -m trafficfuse.lab.probe_cli`). Base image pinned **by digest** (verified locally:
python 3.13.7, linux/amd64), all 15 dependency pins copied from the authoritative `uv.lock`
(cross-checked by test `test_image_requirements_match_uv_lock_exactly`), non-root user
(uid 10001), `EXPOSE 8080`, no credentials. The Agentic Intent Gate framework is **not**
installed in the image — a test proves no lab module imports it. Manifests now use
`imagePullPolicy: Never` (kind-loaded image), and `docker build --check` passes with no
warnings.

**Docker validation status: `BUILD_NOT_VERIFIED`.** An offline build was attempted exactly as
permitted (`--pull=false --network=none`, TrafficFuse-owned tag
`trafficfuse-lab-service:phase1a`). The digest-pinned base image resolved from the local
store (no pull); `pip install` then failed because no wheels are available offline and the
`--network=none` build cannot reach PyPI:

```
ERROR: Could not find a version that satisfies the requirement annotated-types==0.8.0
       (from versions: none)
Dockerfile:41 RUN pip install --no-cache-dir --requirement /tmp/requirements-lab.txt
```

No partial image was created (`docker images` shows no `trafficfuse-*` tag). Per the brief, the
build is reported as unverified rather than silently using network access; Phase 1B performs
the real build and load (see §7).

### Finding P1-b — Probe evidence stored in ephemeral Pod storage

The probe Job wrote `/evidence/probe-live.json` to an emptyDir; deleting the Pod lost the
evidence and no reliable extraction path existed.

**Root cause:** the CLI treated the file as the only output channel.

**Resolution:** the probe CLI now always prints the complete evidence document to **stdout**
between unambiguous `TRAFFICFUSE_EVIDENCE_BEGIN` / `TRAFFICFUSE_EVIDENCE_END` markers; all
progress goes to stderr; the marker block is the only stdout payload. Evidence is **strictly
validated** before it is written or printed (`trafficfuse.lab.evidence`): schema version,
scenario id, source/target identity, request count, success/error counts, per-record outcome
classification and status codes, sequence completeness (1..total exactly once), latency,
observed error-rate consistency, synthetic-workload declaration, and real observation
timestamps. The configured fault percentage never appears in evidence.

**Phase 1B collection process** (offline components implemented; cluster commands documented
only, not executed): `kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml --context
kind-trafficfuse-lab -n trafficfuse-system logs job/trafficfuse-probe` →
`python -m trafficfuse.lab.evidence_cli --logs <captured> --run-id <id>` →
`collect_evidence_from_logs()` stores `artifacts/phase1b/<run-id>/probe-evidence.json` plus the
**raw logs preserved byte-for-byte** (`raw-probe-logs.txt`, now git-ignored). Missing,
truncated, malformed, incomplete, duplicated or ambiguous evidence are hard errors (exit 3/4);
an existing run directory is never overwritten.

### Finding P1-c — Ownership receipt was metadata only

The JSON receipt did not prove ownership; a matching cluster name was the weakest link.

**Root cause:** the receipt check verified only three identity fields, and path handling had no
symlink/traversal resistance.

**Resolution** (`src/trafficfuse/lab/lifecycle.py`, strengthened, fail-closed):

1. **Exact normalized kubeconfig path** — resolved absolute path; global kubeconfig refused;
   file name, `.runtime/kubeconfig` containment and *post-resolution* containment enforced
   (symlink escape and traversal refused).
2. **Exact context** `kind-trafficfuse-lab`; the ambient context is never read (structural test:
   no `os.environ`, no kubectl context introspection, no subprocess at all).
3. **Kubeconfig document verification** (offline YAML parse): exact context→cluster binding,
   local HTTPS API server only (`127.0.0.1`/`localhost`/`::1`), CA certificate required and
   reduced to a **SHA-256 fingerprint** identity anchor, foreign contexts in the dedicated file
   refused, embedded static credentials refused.
4. **Creation record** (`trafficfuse-ownership-record-v2`) with full schema: cluster, context,
   namespace, api server, CA fingerprint, exact kind node names, creation timestamp,
   TrafficFuse creator, kubeconfig reference. **The record is explicitly documented as
   metadata, not cryptographic proof** — ownership is established by cross-checking *live*
   facts against it.
5. **Ownership evidence verification** (`verify_ownership_evidence`): API server, CA
   fingerprint, node identities and ownership labels must all match the record; any mismatch
   refuses. `plan_cluster_destroy` now requires *verified* evidence in addition to
   authorization — authorized-but-unverified destroy is refused.
6. **Foreign clusters** refused by name; a matching name alone is never deletion authority.
7. `kubectl_prefix()` guarantees every future invocation carries `--kubeconfig` and `--context`.
8. The lifecycle CLI gained an offline `verify` subcommand (kubeconfig + record + facts →
   per-check JSON verdict, exit 2 on refusal). Create/destroy remain fail-closed until Phase 1B
   authorization.

### Finding P2 — FaultPolicy did not support arbitrary percentages

| Configured rate | Previous observed | Root cause |
| --- | --- | --- |
| 25% | 25% | interval `round(100/25)=4` happened to be exact |
| 30% | 33% | interval 3 → 1/3 |
| 45% | 50% | interval 2 |
| 50% | 50% | interval 2 |
| 75% | 50% | `max(2, …)` cap |
| 90% | 50% | `max(2, …)` cap |

**Resolution:** replaced the interval rotation with an exact Bresenham distribution
(`fault.py`): for the 0-based index `k`, fail iff `floor((k+1)·P/100) > floor(k·P/100)`.
Properties: 0% never fails; 100% always fails; **exactly P failures per complete 100-request
sequence** (and `2P`, `3P` across cycles); failures evenly spread; pure function — reproducible
across instances, safe under concurrency; invalid rates/request numbers rejected. The probe
stamps every request with `X-TrafficFuse-Request-Index`, so the exact distribution holds
**globally across multiple faulty Pods** (each Pod evaluates the same pure function of the same
index). Without the header, per-Pod counters remain deterministic per Pod; the combined total
can deviate from the global exact count by at most one request per replica (bounded rounding),
which is covered by a dedicated limitation test — this is documented, not glossed over.

---

## 2. Deterministic fault-rate validation table

Full 100-request sequences, verified by `tests/test_fault_matrix.py` (82 tests) and by real
probe runs against the faulty app (`test_probe_measures_exact_configured_rate_over_100_requests`):

| Configured P | Failures / 100 (policy) | Real probe observed error % | 200-request cycles | 300-request cycles |
| --- | --- | --- | --- | --- |
| 0% | 0 | 0.0 | 0 | 0 |
| 1% | 1 | 1.0 | 2 | 3 |
| 10% | 10 | 10.0 | 20 | 30 |
| 25% | 25 | 25.0 | 50 | 75 |
| 30% | 30 | 30.0 | 60 | 90 |
| 45% | 45 | 45.0 | 90 | 135 |
| 50% | 50 | 50.0 | 100 | 150 |
| 75% | 75 | 75.0 | 150 | 225 |
| 90% | 90 | 90.0 | 180 | 270 |
| 99% | 99 | 99.0 | 198 | 297 |
| 100% | 100 | 100.0 | 200 | 300 |

Also verified: even distribution (no boundary clustering), reproducibility across instances and
replays, concurrency (8 threads × 500 requests identical to serial), invalid inputs rejected
(`-1`, `101`, `True`, `50.0`, `"50"`, `None`, `0`, oversized indices), missing/invalid request
headers fall back deterministically, two-replica header-driven exactness, and the documented
one-per-replica counter bound without the header.

## 3. Test evidence

`uv lock --check` clean (32 packages) · `uv sync --locked` clean · `uv pip check` — all
compatible · **`uv run pytest`: 240 passed, 1 skipped** (1 skipped: symlink materialization is
not permitted on this workstation; the traversal branch is covered by a dedicated
non-symlink test) · `git diff --check` clean · secret scan: no secret-like content (only
security-check code and guard docstrings) · `AGENTS.md` sha unchanged.

| File | Tests | Notes |
| --- | --- | --- |
| tests/test_fault_matrix.py | 82 | **new** — P2 matrix |
| tests/test_lab_ownership.py | 38 + 1 skipped | **new** — P1-c mandatory negative tests |
| tests/test_evidence_export.py | 22 | **new** — P1-b export/validation |
| tests/test_lab_manifests.py | 22 | **extended** (+8 image checks: digest pin, non-root, lockfile parity, no AIG import, Never pull policy, .dockerignore) |
| tests/test_lab_lifecycle.py | 32 | **strengthened** (full-schema record fixture; authorized-destroy now requires verified evidence) |
| tests/test_lab_service.py | 13 | **corrected** — the two tests asserting the defective interval/overshoot semantics were replaced with exactness assertions; header-driven determinism added |
| tests/test_lifecycle_cli.py | 4 | **new** — offline `verify` subcommand |
| tests/test_lab_probe.py | 7 | unchanged, green (probe now stamps the request index) |
| tests/test_config_settings.py | 13 | unchanged, green |
| tests/test_runtime_dependency.py | 5 | unchanged — AIG pin verified |
| tests/test_fastapi_app.py | 2 | unchanged, green |

Corrections to existing tests were limited to assertions that encoded the very defects under
review (interval rotation, name-only deletion authority, ambient-context substring check — now
a stronger structural check). Every existing test file remains green.

## 4. Docker image readiness

- Definition: `deploy/lab/Dockerfile` (digest-pinned base, pinned 15-package requirements
  mirroring `uv.lock`, non-root, dual entrypoints) + `.dockerignore`.
- Static validation: `docker build --check` — **complete, no warnings**.
- Offline build (`--pull=false --network=none`): base image resolved locally; dependency
  install impossible offline (no wheels staged) → **BUILD_NOT_VERIFIED**, no image created.
- Readiness consequence: image *definition* ready; Phase 1B must run the real build with
  network (documented in §7) before cluster deployment.

## 5. Evidence export validation results

22 offline tests cover: valid complete JSON stdout; file/stdout equivalence (same run); logs
with surrounding progress text; malformed JSON; truncated documents; empty/markerless logs;
duplicate documents; end-marker without begin; missing required fields; wrong schema; wrong
totals; wrong record counts; duplicate/missing sequences; inconsistent observed rate; bad
outcome/status/timestamps; missing latency/declaration; output write failure (exit 3);
validation failure (exit 4); raw-log preservation; run-directory overwrite refusal; unsafe
run-id (traversal) refusal.

## 6. Ownership guard negative-test results

Every mandated refusal verified against synthetic fixtures (no cluster contacted): missing
dedicated kubeconfig · unexpected kubeconfig path · symlink escape (skipped on this platform —
privilege) and path-traversal escape (covered) · wrong context · wrong API server (remote host,
non-TLS) · wrong current-context in the dedicated file · foreign context in the dedicated file
· missing CA data · embedded static credentials · wrong CA identity · missing creation record ·
invalid or conflicting record (9 parametrized variants) · pre-existing unowned cluster ·
missing ownership labels · wrong node identities · foreign cluster names (5 parametrized) ·
global-kubeconfig fallback · destroy without verified ownership (authorized, no facts, and
mismatched facts).

## 7. Phase 1B readiness and exact proposed workflow

`PHASE1B_READINESS=PARTIAL` — all code, guards, tests and manifests are ready; blocked only on
(a) explicit cluster-creation authorization and (b) the real image build (offline verification
was impossible). Proposed, mutating commands (**not executed**; each requires human approval):

```bash
# 1. Build and load the lab image (network required; pins from uv.lock)
docker build --pull -f deploy/lab/Dockerfile -t trafficfuse-lab-service:phase1a .
kind load docker-image trafficfuse-lab-service:phase1a --name trafficfuse-lab

# 2. Create the disposable cluster with the dedicated kubeconfig (exact context)
kind create cluster --name trafficfuse-lab \
    --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml
kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \
    --context kind-trafficfuse-lab create namespace trafficfuse-system

# 3. Apply the baseline stack (probe Job excluded from the kustomization)
kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \
    --context kind-trafficfuse-lab apply -k deploy/lab

# 4. Write the creation record (api server, CA fingerprint, node names, labels) to
#    .runtime/state/cluster-trafficfuse-lab.json, then verify ownership offline:
python -m trafficfuse.lab.lifecycle_cli verify \
    --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \
    --receipt .runtime/state/cluster-trafficfuse-lab.json --facts cluster-facts.json

# 5. Run scenarios (probe Job applied on demand), then collect evidence from logs
kubectl --kubeconfig .runtime/kubeconfig/trafficfuse-lab.yaml \
    --context kind-trafficfuse-system logs job/trafficfuse-probe 2>/dev/null | \
    python -m trafficfuse.lab.evidence_cli --logs - --run-id phase1b-live-001

# 6. Cleanup (name-pinned, ownership-verified, separately authorized)
kind delete cluster --name trafficfuse-lab
```

Facts for step 4 are produced with read-only, context-pinned commands (e.g. `kubectl … get
namespace trafficfuse-system -o json`, `kubectl … config view --raw`, `kubectl … get nodes`).
The exact API-server host port, CA fingerprint and node names come from the created cluster.

## 8. Remaining limitations

1. **BUILD_NOT_VERIFIED** image (offline constraint) — definition and static checks pass.
2. Symlink-escape negative test skipped on this workstation (no privilege); the traversal
   branch is covered by an equivalent non-symlink test.
3. Without the probe request-index header, multi-Pod global exactness is bounded (±1 request
   per replica) rather than exact — documented and tested; the probe always sends the header.
4. The header sequence proves client-side exactness; it does not prove distributed request
   ordering (stated in `fault.py` and the design doc).
5. The creation record is metadata; ownership proof is the live-facts cross-check, which first
   becomes meaningful when the cluster exists (Phase 1B).
6. Base image pinned to the full python 3.13.7 digest (larger than slim) because that is the
   locally verifiable image; switching to slim requires re-pinning the digest.

## 9. Acceptance criteria assessment

1. AGENTS.md unchanged — **yes** (sha verified).
2. Pinned AIG dependency unchanged — **yes** (version, commit, requested revision, lockfile).
3. Reproducible Docker image definition exists — **yes**.
4. Offline Docker build — **not possible offline** (no wheels); reported BUILD_NOT_VERIFIED,
   readiness PARTIAL (explicitly permitted outcome).
5. Probe evidence reliably represented on stdout and validated — **yes** (markers + strict
   validation).
6. Evidence export failure cases tested — **yes** (22 tests).
7. Ownership/kubeconfig guards strengthened — **yes** (§1-c, §6).
8. No existing cluster modified — **yes** (verified before/after).
9. Fault injection passes the deterministic matrix — **yes** (§2).
10. Existing tests remain green — **yes** (240 passed; invalid assertions corrected with
    strengthened replacements).
11. New regression tests pass — **yes**.
12. No real cluster creation or destruction — **yes**.
13. No unauthorized commit or push — **yes** (working tree left uncommitted for review).
14. Phase 1B permissions and steps documented — **yes** (§7).

---

**PHASE1A1_FINAL_STATUS=PASS** (with image build reported BUILD_NOT_VERIFIED as permitted;
readiness PARTIAL)
**PHASE1B_READINESS=PARTIAL**
