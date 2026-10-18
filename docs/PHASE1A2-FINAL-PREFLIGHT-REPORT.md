# TrafficFuse AI — Phase 1A.2: Final Preflight Corrections Report

> **Correction note (2026-10-18, Phase 1B entry review):** the provenance model described in
> finding P1-c item 3 below (facts carrying a `source="collector"` string consulted by
> `plan_cluster_destroy`) was **superseded**: a caller-settable string is not a provenance
> guarantee. Trust is now established by execution — see
> [docs/PHASE1B-ENTRY-REVIEW.md](docs/PHASE1B-ENTRY-REVIEW.md). The text below is the
> historical Phase 1A.2 record and is preserved unchanged.

**Date:** 2026-10-18 · **Method:** offline implementation, tests and static validation only —
no cluster was created, modified or deleted; no Kubernetes mutation was executed; no ambient
kubeconfig or context was changed; no commit, push, tag or release was made; no Docker image
was built, pulled or loaded; no container was launched.

**Repository verification before any change** (mandated step 1):

| Check | Result |
| --- | --- |
| `git branch` / HEAD | `main` @ `a200a3bafcd0e50d99e7dd3460502d8f2bc52b65` (Phase 1A.1) |
| HEAD vs `origin/main` | identical (`a200a3b…`, fetched and compared) |
| Working tree | Phase 1A.1 committed and pushed clean; all Phase 1A.2 changes tracked in `git status` |
| `AGENTS.md` | read in full, **not modified** — sha1 `03870389d009b3d2c9eca19181a7eb68e26053fe` (identical before and after) |
| Installed AIG | `agentic-intent-gate==0.2.0`, PEP 610 `direct_url.json` commit **`7261fdd2f47d2b15fabe8faac527eeee37338eb1`**, `requested_revision` identical; imported from `.venv` site-packages, not a source checkout; `pyproject.toml` and `uv.lock` pins unchanged |
| Foreign clusters | `incident-lab`, `pcp-dev`, `playground-crd`, `vmware-k8s` (and their containers) untouched — verified before and after by read-only `docker ps`; no TrafficFuse resource involved |
| Ambient context | `kind-playground-crd` (foreign) — never read, switched or relied upon (re-verified after the phase) |

---

## 1. Review findings and resolutions

### Finding P1-a — Kubeconfig path was shape-checked, not ownership-checked

A foreign repository with an identical directory layout (a
`.runtime/kubeconfig/trafficfuse-lab.yaml` file somewhere else on disk) could pass the
Phase 1A.1 checks: correct file name, correct parent directory, correct kind of file. A
matching *shape* is not ownership.

**Resolution** (`src/trafficfuse/lab/lifecycle.py`): the accepted path is now derived from the
module's own repository root (`canonical_kubeconfig_path()` = `PROJECT_ROOT` resolved from
`__file__`, joined with the dedicated relative path) and a candidate must resolve to **exactly
that path**. Any other path — including a foreign repository with an identical layout, a
symlink escape, or a traversal — is refused with an explicit "not the TrafficFuse
repository-owned path … refusing any path outside the verified repository root" message.
`is_dedicated_kubeconfig()` reuses the same single check.

**Tests:** foreign-repository-with-identical-layout refusal; mocked symlink escape via path
resolution (explicitly labeled mocked, because real symlink materialization is not permitted
on this workstation — the non-privileged traversal branch is covered by a real test);
CLI and preflight entry points enforcing the canonical path
(`tests/test_lab_ownership.py`, 43 collected, up from 38 + 1 skipped).

### Finding P1-b — Phase 1B runbook ordering defect (documentation only)

The Phase 1A.1 §7 sketch and the preflight §8 text listed `kind load docker-image …` **before**
`kind create cluster`. A kind image is loaded into the nodes of an existing cluster; the
earlier order was impossible. Two further defects: the log-collection line used
`--context kind-trafficfuse-system` (a **namespace** used as a context — the context is
`kind-trafficfuse-lab`), and ownership verification relied on a caller-supplied
`cluster-facts.json` (see P1-c).

**Resolution (docs only, no code executed):** new **`docs/PHASE1B-RUNBOOK.md`** — the
authoritative, corrected operator sequence: repository/state verification → approval gate →
preconditions (cluster absent) → dedicated kubeconfig prep → image build and static check →
offline preflight → `kind create cluster` with the dedicated kubeconfig → **image load after
creation** → creation record + trusted collector verification → `apply -k deploy/lab` →
rollout wait → confirm `trafficfuse-live` selects `version=faulty` → probe Job → wait →
byte-preserving logs export → validate evidence → measurements/report → **environment
preserved** (deletion only on separate written authorization, with ownership re-verification
immediately before destruction). Every step carries the exact identities (cluster
`trafficfuse-lab`, context `kind-trafficfuse-lab`, namespace `trafficfuse-system`, node
`trafficfuse-lab-control-plane`, kubeconfig `.runtime/kubeconfig/trafficfuse-lab.yaml`) and
the foreign-cluster protections (`incident-lab`, `pcp-dev`, `playground-crd`, non-disposable
`vmware-k8s`; ambient context never used). The Phase 1A.1 report §7 carries a dated
correction note (its historical text is preserved); `docs/PHASE1-PREFLIGHT.md` §8,
`docs/PHASE1A-DESIGN.md`, `docs/PHASES.md` and `README.md` were updated to point at it.

### Finding P1-c — Ownership verification trusted caller-supplied facts

The `verify` subcommand accepted `--facts cluster-facts.json` and treated the values as
ownership evidence. A hand-written JSON file is not evidence of anything: it could name the
right cluster while the live environment is entirely different.

**Resolution** (new module `src/trafficfuse/lab/cluster_facts.py`, strengthened
`lifecycle.py`):

1. **Trusted read-only collector** — `collect_live_facts()` gathers API server, CA
   fingerprint, node names, namespace ownership labels and Docker node identities through an
   allowlisted, read-only command set (`kubectl get/describe/config view/version/
   cluster-info/api-resources/api-versions`; `docker inspect/version/ps`). Every command is
   checked **before** execution (a mutating verb is refused even if an executor would run
   it), carries the dedicated kubeconfig and the exact context — never the ambient
   selection — and any failure, missing field or inconsistent observation fails closed.
2. **Testable seam** — commands run through an injectable executor (`subprocess_executor`
   by default); the entire suite uses a mocked executor (no cluster, Docker daemon or
   subprocess contacted).
3. **Trust separation** — facts now carry provenance (`COLLECTOR_SOURCE` / `CALLER_SOURCE`).
   `verify_ownership_evidence` records `trusted`; `plan_cluster_destroy` **refuses
   caller-sourced facts outright** ("caller-supplied facts are never sole proof of
   ownership"). `lifecycle_cli verify --collect` runs the trusted pipeline; `--facts` remains
   for inspection and prints an untrusted warning.
4. **Verb-scanner correction found by the new tests** — the read-only checker first scanned
   the first non-flag token as the verb, which was the *value* of `--kubeconfig` and refused
   every real collector command. The scanner now skips value-taking flags (`--kubeconfig`,
   `--context`, `-o`, `--format`, `--type`, …), with a dedicated regression test.

**Tests:** `tests/test_cluster_facts.py` (21 tests): happy path; every command scoped and
read-only; mutation verbs refused before execution; unknown binaries refused; flag values
never mistaken for the verb; wrong API server / wrong CA / unexpected nodes / missing or
conflicting Docker identity / missing namespace ownership / failing `config view` /
inconsistent post-creation record / foreign context / foreign kubeconfig path all refused
fail-closed; caller facts never authorize destruction; collector facts reach the (still
refusing) Phase 1A gate; unauthorized destroy refused; caller-facts verification marked
untrusted.

### Finding P2-a — Timestamps accepted naive values and could raise on comparison

`_require_iso_timestamp` used bare `datetime.fromisoformat`, which accepts naive datetimes
(`"2026-10-18T10:00:00"` — no offset), and the ordering comparison could raise an unhandled
`TypeError` on a naive-vs-aware comparison.

**Resolution** (`src/trafficfuse/lab/evidence.py`): `parse_utc_timestamp()` — the single
strict parser. It requires a string, valid ISO-8601, and an **explicit UTC offset**; accepts
`Z`/`z` and `+00:00` (and normalizes any other explicit offset to UTC); refuses naive,
malformed, empty and non-string values via the controlled `TimestampValidationError` (a
`ValueError` subclass, so existing callers keep working). Document validation uses the parsed,
normalized values for the `completed_at_utc >= started_at_utc` check — no raw-string
comparison remains, and a naive value never reaches the comparison.

### Finding P2-b — Raw logs were decoded before preservation

`collect_evidence_from_logs` took a `str`, and the CLI read files with
`errors="replace"` — the original bytes were lossily transcoded before anything was stored.

**Resolution** (`src/trafficfuse/lab/evidence_export.py`, `evidence_cli.py`):

* **raw bytes first** — `collect_evidence_from_log_bytes()` is the primary API; the strict
  UTF-8 decode exists only as a private parse copy, so nothing is transcoded before it hits
  disk (non-UTF-8 captures are refused, raw bytes still preserved);
* **preserved on failure** — the raw capture is written *before* extraction/validation, so a
  failed run still leaves an auditable capture plus an `evidence-audit.json` recording the
  failing stage, error type and message (the CLI's stderr says where);
* **bounded** — `MAX_RAW_LOG_BYTES` (16 MiB) refuses a runaway capture before any write;
* **atomic** — every file is written via temporary file + `os.fsync` + `os.replace`; no
  half-written artifact, no `.tmp` leftovers;
* **audit metadata** — per run: exact byte length, SHA-256, strict timezone-aware UTC
  collection timestamp, final status (`ok` / `failed`), and the evidence document's own
  length and SHA-256 on success;
* the CLI reads **bytes** (stdin buffer / file bytes); the evidence is exported from the
  exact owned Job (`kubectl … -n trafficfuse-system logs job/trafficfuse-probe`).

**Tests:** `tests/test_evidence_preservation.py` (30 tests): timestamp forms, normalization,
naive/malformed/non-string refusals, controlled error type, document-level timestamp
reporting, Zulu acceptance, reversed refusal across offsets, equal/offset-shifted acceptance,
mixed naive/aware never raising `TypeError`; exact byte preservation; preservation on
extraction failure, validation failure and non-UTF-8 capture; oversize refusal before any
write; audit content and strict audit timestamp; no temporary leftovers; existing-run refusal;
CRLF byte fidelity through the file wrapper; CLI byte preservation from file and stdin with a
failure hint pointing at the preserved path.

---

## 2. Test evidence

| File | Tests | Notes |
| --- | --- | --- |
| tests/test_evidence_preservation.py | 30 | **new** — P2 strict timestamps + byte-for-byte preservation |
| tests/test_cluster_facts.py | 21 | **new** — P1-c trusted collector + trust separation |
| tests/test_lab_ownership.py | 43 (1 skipped) | **extended** (+4 net) — canonical-path enforcement, foreign-layout refusal, mocked symlink escape, CLI/preflight enforcement |
| tests/test_lab_lifecycle.py | 32 | **strengthened** — collector-sourced mismatched facts now refused at the verification gate |
| tests/test_lifecycle_cli.py | 4 | **updated** — synthetic repository-root fixture, dedicated-path kubeconfigs |
| tests/test_evidence_export.py | 22 | unchanged count — the str API is now a wrapper over the bytes API; all green |
| tests/test_fault_matrix.py | 82 | unchanged, green |
| tests/test_lab_manifests.py | 22 | unchanged, green |
| tests/test_lab_service.py | 13 | unchanged, green |
| tests/test_lab_probe.py | 7 | unchanged, green |
| tests/test_config_settings.py | 13 | unchanged, green |
| tests/test_runtime_dependency.py | 5 | unchanged — AIG pin verified |
| tests/test_fastapi_app.py | 2 | unchanged, green |

**Total: 296 collected → 295 passed, 1 skipped** (Phase 1A.1 baseline: 240 passed, 1 skipped;
the skip is unchanged — symlink materialization is not permitted on this workstation, and the
traversal branch is covered by a dedicated non-symlink test plus an explicitly labeled mocked
resolution test).

Test corrections were limited to assertions that encoded the pre-correction trust model
(caller facts reaching the verification gate; flag-value verb scanning; docker commands
expected to carry kubeconfig flags). Every replacement strengthens the original intent; no
test was weakened, skipped or deleted to obtain a pass.

## 3. Validation battery

| Check | Result |
| --- | --- |
| `uv lock --check` | clean (32 packages) |
| `uv sync --locked` | clean (32 packages) |
| `uv pip check` | all installed packages compatible |
| `uv run pytest` (offline) | **295 passed, 1 skipped** |
| `git diff --check` | clean (CRLF advisory warnings are the repository's existing Windows behavior) |
| Secret scan (new/changed files) | clean — no credentials, tokens, private keys, kubeconfigs or `.env` files added |
| `AGENTS.md` sha1 | `03870389d009b3d2c9eca19181a7eb68e26053fe` — unchanged |
| AIG pin | `0.2.0` @ `7261fdd2f47d2b15fabe8faac527eeee37338eb1`, `requested_revision` identical; `pyproject.toml`/`uv.lock` unchanged |
| Foreign clusters | `incident-lab`, `pcp-dev`, `playground-crd`, `vmware-k8s` untouched (read-only verification before/after) |
| Ambient context | `kind-playground-crd` — unchanged, never used |
| Cluster/Docker mutations | none — no image built, no container launched, no `kind`/`kubectl` mutation |
| Commit / push | **none** — working tree left for human review |

`DOCKER_BUILD_STATUS=BUILD_NOT_VERIFIED` — unchanged from Phase 1A.1: the image *definition*
is ready and `docker build --check` passes, but no build ran in this phase (by rule: no image
was built, pulled or loaded). Phase 1B performs the real build (runbook step 5).

## 4. Remaining limitations

1. **BUILD_NOT_VERIFIED** image — the definition, digest pin, non-root user and `uv.lock`
   parity are verified offline; the real build happens in Phase 1B with network.
2. Symlink-escape negative test uses a mocked resolution (platform privilege); the real
   traversal branch is covered without symlinks.
3. Multi-Pod global exactness without the request-index header is bounded (±1 per replica);
   the probe always sends the header.
4. The creation record remains metadata; ownership proof is the live-facts cross-check via
   the trusted collector, which first becomes meaningful when the cluster exists (Phase 1B).
5. The collector's read-only allowlist covers the verbs this project needs; any future verb
   requires extending `_READ_ONLY_KUBECTL_VERBS` / `_READ_ONLY_DOCKER_VERBS` deliberately.
6. Phase 1B itself has **not** begun; the runbook is documentation only.

## 5. Acceptance criteria assessment

1. Exact repository-owned kubeconfig path enforced; foreign repository with identical layout
   refused — **yes** (P1-a, tests).
2. Phase 1B runbook ordering corrected (image load after creation; context vs namespace;
   collector verification) — **yes** (P1-b, `docs/PHASE1B-RUNBOOK.md`; docs only).
3. Trusted read-only live-facts collector with testable seams; caller JSON never sole proof
   of ownership — **yes** (P1-c, `cluster_facts.py`, 21 tests).
4. Timestamps strictly timezone-aware and UTC-normalized; naive/malformed/reversed refused;
   controlled error type — **yes** (P2-a).
5. Raw logs preserved byte-for-byte, separate from the parse copy; bounded, atomic, audited,
   preserved on failure — **yes** (P2-b, 30 tests).
6. No caller-supplied facts authorize destruction — **yes** (refused by type, tested).
7. Regression suite green against the Phase 1A.1 baseline — **yes** (295 passed, 1 skipped).
8. `AGENTS.md` and the pinned runtime unchanged — **yes** (sha + pins verified).
9. No foreign cluster, ambient context or Docker state touched — **yes**.
10. No commit or push; no Phase 1B execution — **yes**.

---

**PHASE1A2_FINAL_STATUS=PASS**
**PHASE1B_READINESS=PARTIAL** — all code, guards, tests, manifests, evidence tooling and the
corrected operator runbook are ready; blocked only on (a) explicit cluster-creation
authorization and (b) the real image build (offline verification impossible).
**DOCKER_BUILD_STATUS=BUILD_NOT_VERIFIED**
