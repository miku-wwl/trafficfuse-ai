# TrafficFuse AI — Phase 1B Security Gate Report

Date: 2026-10-19 (Pacific/Auckland). Implementation and validation were offline.

```text
PHASE1B_SECURITY_GATE=PASS
PHASE1B_READINESS=PARTIAL
DOCKER_BUILD_STATUS=BUILD_NOT_VERIFIED
```

## Repository and protected state

Actual branch: `main`. HEAD and local `origin/main` both resolve to
`26c6d1694fef098342f2cc430be72335c342cd43`; local ahead/behind counts are `0/0`.
The working tree was clean before this task. No remote fetch or infrastructure
inspection was needed. Final working tree: five modified files and two new
files, all unstaged. No commit or push occurred.

`AGENTS.md` was read completely and remains unchanged. Its before/after SHA-256:
`30fab17cc82e831a23edb15fa03bfb22821a0be24bbc24f2b94e094e287580be`.
`pyproject.toml` and `uv.lock` are unchanged. The installed AIG distribution is
`agentic-intent-gate==0.2.0`; both PEP 610 source commit and requested revision
are `7261fdd2f47d2b15fabe8faac527eeee37338eb1`. The public API is imported from
this repository's `.venv/Lib/site-packages/agentic_intent_gate/runtime.py`, with
the five-second default intact. No shared Runtime source or mechanisms changed.

## Reproduction before the fix

The new regression was added and run **before changing production code**:

```text
uv run pytest -q -o addopts= tests/test_read_only_commands.py::test_reported_config_set_context_is_refused
```

It called the actual validator with the exact reviewer input:

```python
assert_read_only_command([
    "kubectl", "--kubeconfig", "/tmp/foo", "--context",
    "kind-trafficfuse-lab", "config", "set-context", "danger",
    "--cluster", "evil",
])
```

Observed result: **1 failed**, exit **1**:

```text
Failed: DID NOT RAISE <class 'trafficfuse.lab.lifecycle.SafetyViolation'>
```

The test only validated arguments; it never ran kubectl. The exact failure
capture remains operator-local at
`.runtime/phase1b-security-gate/original-reproduction.log`.

**Root cause:** `_command_verb()` classified the first positional verb and the
allowlist included `config`. That authorized an entire command family containing
mutations. It also accepted broad `get` forms, arbitrary kubeconfig paths and
context flags without validating their complete command structure.

## Corrected validation

The generic verb scanner and verb sets are removed. A small explicit matcher,
`_validated_read_only_command()`, checks the entire argument sequence and returns
an immutable snapshot only on acceptance. Both `assert_read_only_command()` and
`subprocess_executor()` use that same matcher. The executor dispatches the
validated snapshot with `shell=False`, captured byte output and the existing
ten-second command timeout. Rejected arguments are not echoed into errors.

Only these four forms are allowed, in the exact generated argument order:

| Form | Required structure |
| --- | --- |
| Node facts | `kubectl --kubeconfig <dedicated-path> --context kind-trafficfuse-lab get nodes -o json` |
| Namespace ownership | `kubectl --kubeconfig <dedicated-path> --context kind-trafficfuse-lab get namespace trafficfuse-system -o json` |
| Docker node name | `docker inspect --format <fixed-name-template> --type container <owned-kind-node-name>` |
| Docker creation identity | `docker inspect --format <fixed-kind-identity-template> --type container <owned-kind-node-name>` |

The dedicated path must resolve through the existing validator to this exact
repository's `.runtime/kubeconfig/trafficfuse-lab.yaml`. Absolute spelling or the
dedicated relative spelling from the repository root are supported; a foreign
repository with an identical directory layout is rejected. Missing, duplicate,
conflicting, reordered or alternate flag spellings are not accepted command
forms. Fixed positions prevent flag values from being scanned as verbs.

The name template is exactly `{{.Name}}`. The second template is exactly the
existing JSON projection of container name and the two Kind labels
`io.x-k8s.kind.cluster` / `io.x-k8s.kind.role`; it is now shared as a constant
between the validator and recorder. Arbitrary Go templates, full inspection,
environment inspection and remote-Docker flags are denied. Node names must be
TrafficFuse Kind `control-plane` or `worker` names, optionally numbered from 2,
and at most 63 characters. Existing recorded-worker inspection remains supported;
this name check grants no ownership or action authority.

Every kubectl configuration subcommand is rejected, including read variants:
none is needed because kubeconfig identity inspection already uses the trusted
local parser. Broad verb permissions were not added to accommodate operator
commands outside the collector.

Rejected examples and tested categories include:

- `config set-context`, `use-context`, `delete-context`, `set-cluster`,
  `set-credentials`, `delete-cluster`, `delete-user`, `rename-context`, `set`,
  `unset`, `view --raw`, and unknown configuration subcommands.
- Arbitrary `get secrets`, `get pods`, another Namespace, extra positional
  arguments, output formats other than JSON, watch, impersonation, server/token
  overrides and TLS bypass options.
- Missing/wrong kubeconfig or context, repeated/conflicting scoping flags,
  missing values, combined flag strings and values that resemble verbs.
- Shell wrappers, chaining tokens, alternate executable paths, empty/non-string
  argument lists, NUL/newline tokens and unsupported top-level commands.
- Docker mutations, `ps`/`version`, full inspection, credential-bearing
  projections, unrelated containers, extra targets and remote context/host flags.

After the fix, the exact reproduced command returns:

```text
CORRECTED_REPRODUCTION=REFUSED
SafetyViolation: read-only collector refused unsupported command shape
```

The corrected capture is at
`.runtime/phase1b-security-gate/corrected-rejection.log`. Negative cases call both
the actual validator and actual executor; subprocess dispatch is mocked to fail
the test if reached. Positive cases mock dispatch and verify exact arguments,
byte results, timeout and `shell=False`. No real Docker/kubectl command ran.

## Changed files

Modified:

- `src/trafficfuse/lab/cluster_facts.py`: exact four-form matcher, path/context
  validation, safe node-name checks, shared fixed Docker projections, explicit
  non-shell dispatch of the validated snapshot; removed generic verb parsing.
- `src/trafficfuse/lab/creation_record.py`: reuse the exact existing Docker
  identity template constant. Command behavior and receipt schema are unchanged.
- `tests/test_cluster_facts.py`: retain the flag-value regression using the
  real canonical path. Remove assertions on the deleted private scanner and
  require malformed/verb-shaped scope values to be rejected.
- `docs/PHASE1B-RUNBOOK.md`: clarify the collector gate's scope and link this report.
- `docs/PHASE1B-ENTRY-REVIEW.md`: replace the obsolete instruction to extend broad
  verb sets with the requirement to review exact command shapes.

Added:

- `tests/test_read_only_commands.py`: original reproduction and 109 additional
  negative/positive cases, all offline.
- `docs/PHASE1B-SECURITY-GATE-REPORT.md`: this report.

The prior flag-value test allowed `/repo/.runtime/...` even though it was not
the actual dedicated path. Correcting that fixture is required by this security
fix; no existing test case was removed. All 376 prior passing cases remain green.

## Executed verification

Dependency resolution/synchronization used `UV_OFFLINE=1` and cached packages.

| Check | Actual result |
| --- | --- |
| Original reproduction against unfixed code | **1 failed**, exit 1, expected `DID NOT RAISE` |
| Exact reproduction after fix | **REFUSED with SafetyViolation** |
| Targeted command, collector, creation-record and trusted-ownership suite | **189 passed, 0 failed, 0 skipped**, 2.43 seconds |
| `uv lock --check` | PASS, 32 packages resolved |
| `uv sync --locked` | PASS, 32 packages checked |
| `uv pip check` | PASS, all installed packages compatible |
| `uv run pytest -q -o addopts='' --junitxml=.runtime/phase1b-security-gate/pytest.xml` | **486 passed, 1 skipped, 0 failed**, 19.93 seconds |
| Ownership/receipt/API identity/CA fingerprint regressions | PASS, included in targeted/full suite |
| Healthy/faulty scenarios, evidence integrity and fault policy | PASS, full suite |
| Runbook order, PowerShell syntax/argument/byte-capture checks | PASS, full suite |
| `git diff --check` | PASS |
| AGENTS/dependency/lock integrity and installed AIG API/origin/default budget | PASS |
| Local credential-pattern and private-artifact scan | PASS, no unexpected matches; explicit synthetic PEM fixture reviewed |

There are **110 added cases** above the `376 passed, 1 skipped` baseline. The
existing `test_symlink_escape_refused` is still skipped because symlink creation
is not permitted on this Windows platform. The existing Starlette/TestClient
httpx deprecation warning remains; dependencies were preserved.

Private captures, targeted/full JUnit results and the final offline audit are
stored under `.runtime/phase1b-security-gate/`, which remains gitignored. The
secret scan uses bounded credential/private-key/credential-URL patterns and
private-path exclusion checks; it is not an exhaustive secret-detection guarantee.

## Runbook review and remaining limits

The 15-step PowerShell 7.4+ sequence remains intact: preflight and explicit
permission, image build, new cluster, observed creation record, image load,
Namespace bootstrap, independent live verification, workloads, readiness and
initial faulty selector, healthy baseline, faulty live probe, separate validated
exports, audited report, preservation. No configuration mutation is required.
The Namespace read governed by the gate and all commands actually emitted by
the collector/recorder use approved shapes. Manual readiness, selector, Job and
log commands remain separately scoped operator operations; they are not passed
to `subprocess_executor()` and do not expand its allowlist.

This is a command filter, not authentication or infrastructure authorization.
Trusted host code, executable resolution and operator-controlled environment
remain assumptions; this does not isolate malicious Python or authenticate a
`CollectorExecution` marker. The marker stays diagnostic. Independent
`verify_live_ownership()` collection is retained, and lifecycle creation/deletion
remain disabled. No approval, authorization, Runtime gateway/ledger, traffic
switching, AI diagnosis or dashboard was implemented.

**Phase 1B readiness remains PARTIAL.** Real Docker image build/pull, daemon/Kind
compatibility, cluster creation, live ownership collection, deployment readiness
and both in-cluster HTTP evidence runs are **NOT VERIFIED**. No cluster was
contacted, created, changed or deleted. No image was built/pulled/loaded. The
protected `incident-lab`, `pcp-dev`, `playground-crd` and `vmware-k8s` environments
and ambient Kubernetes context were not accessed or changed.

Future execution needs fresh, task-specific approval naming the pinned local
image build/pulls/load; creation of a new disposable `trafficfuse-lab` with the
exact dedicated kubeconfig/context; scoped node/Docker identity reads and local
record; Namespace bootstrap and independent ownership verification; the two
Deployments and three Services; both named probe Jobs and HTTP GETs; fresh run
IDs, local evidence/report persistence; and preservation for inspection.
Ambiguous mutation outcomes stop for review. Cleanup is excluded and requires
separate review and permission; deletion stays disabled. Commit/push also require
separate explicit authorization. This task ends at human review.
