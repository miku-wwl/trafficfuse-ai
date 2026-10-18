"""Command gate security regressions; no real subprocess or infrastructure use."""

from types import SimpleNamespace

import pytest

from trafficfuse.lab import cluster_facts, lifecycle
from trafficfuse.lab.cluster_facts import (
    DOCKER_KIND_IDENTITY_FORMAT,
    DOCKER_NODE_NAME_FORMAT,
    assert_read_only_command,
    subprocess_executor,
)
from trafficfuse.lab.lifecycle import SafetyViolation


@pytest.fixture
def gate_root(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def scoped(*args):
    return [
        "kubectl", "--kubeconfig", str(lifecycle.canonical_kubeconfig_path()),
        "--context", lifecycle.EXPECTED_CONTEXT, *args,
    ]


def nodes():
    return scoped("get", "nodes", "-o", "json")


def inspect_node(template=DOCKER_NODE_NAME_FORMAT, node="trafficfuse-lab-control-plane"):
    return ["docker", "inspect", "--format", template, "--type", "container", node]


def assert_denied_before_subprocess(argv, monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("a rejected command reached subprocess.run")
    monkeypatch.setattr(cluster_facts.subprocess, "run", forbidden)
    with pytest.raises(SafetyViolation):
        assert_read_only_command(argv)
    with pytest.raises(SafetyViolation):
        subprocess_executor(argv)
    assert calls == []


def test_reported_config_set_context_is_refused():
    # Exact reviewer reproduction. The validator alone must refuse; this test
    # never invokes kubectl, even when the old validator incorrectly accepts it.
    with pytest.raises(SafetyViolation):
        assert_read_only_command([
            "kubectl", "--kubeconfig", "/tmp/foo", "--context",
            "kind-trafficfuse-lab", "config", "set-context", "danger",
            "--cluster", "evil",
        ])


@pytest.mark.parametrize("subcommand,args", [
    ("set-context", ["danger", "--cluster", "evil"]),
    ("use-context", ["danger"]),
    ("delete-context", ["danger"]),
    ("set-cluster", ["evil", "--server", "https://example.invalid"]),
    ("set-credentials", ["evil", "--token", "synthetic-value"]),
    ("delete-cluster", ["evil"]),
    ("delete-user", ["evil"]),
    ("rename-context", ["old", "new"]),
    ("unset", ["current-context"]),
    ("set", ["current-context", "danger"]),
    ("view", ["--raw"]),
    ("view", []),
    ("current-context", []),
    ("unknown", []),
])
def test_all_config_commands_denied_even_with_correct_scope(gate_root, monkeypatch, subcommand, args):
    assert_denied_before_subprocess(scoped("config", subcommand, *args), monkeypatch)


@pytest.mark.parametrize("verb", ["apply", "delete", "patch", "edit", "exec", "create", "run", "scale", "describe", "version", "cluster-info", "api-resources", "api-versions", "unknown"])
def test_no_broad_top_level_verb_permission(gate_root, monkeypatch, verb):
    assert_denied_before_subprocess(scoped(verb, "nodes", "-o", "json"), monkeypatch)


@pytest.mark.parametrize("suffix", [
    ["get", "secrets", "-o", "json"],
    ["get", "pods", "-o", "json"],
    ["get", "namespace", "default", "-o", "json"],
    ["get", "namespaces", "-o", "json"],
    ["get", "nodes", "extra", "-o", "json"],
    ["get", "nodes", "-o", "yaml"],
    ["get", "--raw", "/api/v1/secrets"],
    ["get", "nodes", "-o", "json", "--watch"],
    ["get", "nodes", "-o", "json", "--as", "admin"],
    ["get", "nodes", "-o", "json", "--server", "https://example.invalid"],
    ["get", "nodes", "-o", "json", "--token", "synthetic-value"],
    ["get", "nodes", "-o", "json", "--insecure-skip-tls-verify"],
])
def test_get_is_limited_to_two_exact_resource_reads(gate_root, monkeypatch, suffix):
    assert_denied_before_subprocess(scoped(*suffix), monkeypatch)


@pytest.mark.parametrize("variant", [
    "missing-kubeconfig", "missing-context", "foreign-kubeconfig", "foreign-context",
    "duplicate-kubeconfig", "duplicate-context", "conflicting-context",
    "conflicting-kubeconfig", "missing-kubeconfig-value", "missing-context-value",
    "reordered-scope", "equals-scope", "combined-flag-string", "value-looks-like-verb",
])
def test_invalid_or_duplicate_scope_flags(gate_root, monkeypatch, variant):
    command = nodes()
    if variant == "missing-kubeconfig":
        del command[1:3]
    elif variant == "missing-context":
        del command[3:5]
    elif variant == "foreign-kubeconfig":
        command[2] = str(gate_root / "foreign/.runtime/kubeconfig/trafficfuse-lab.yaml")
    elif variant == "foreign-context":
        command[4] = "kind-incident-lab"
    elif variant == "duplicate-kubeconfig":
        command += command[1:3]
    elif variant == "duplicate-context":
        command += command[3:5]
    elif variant == "conflicting-context":
        command += ["--context", "vmware-k8s"]
    elif variant == "conflicting-kubeconfig":
        command += ["--kubeconfig", "/tmp/foo"]
    elif variant == "missing-kubeconfig-value":
        del command[2]
    elif variant == "missing-context-value":
        del command[4]
    elif variant == "reordered-scope":
        command[1:5] = command[3:5] + command[1:3]
    elif variant == "equals-scope":
        command[1:3] = ["--kubeconfig=" + command[2]]
    elif variant == "combined-flag-string":
        command[1:5] = [" ".join(command[1:5])]
    elif variant == "value-looks-like-verb":
        command[2] = "get"
    assert_denied_before_subprocess(command, monkeypatch)


@pytest.mark.parametrize("bad", [
    None, [], "kubectl get nodes", b"kubectl get nodes", {"kubectl": "get"},
    ["kubectl"], ["kubectl", None], ["kubectl", 1],
    ["kubectl", "--kubeconfig", ""], ["kubectl", "--kubeconfig", "bad\x00path"],
    ["kubectl", "get\nnodes"], ["kubectl", "get\rnodes"],
])
def test_malformed_argument_lists_fail_closed(monkeypatch, bad):
    assert_denied_before_subprocess(bad, monkeypatch)


@pytest.mark.parametrize("wrapper", [
    ["sh", "-c"], ["bash", "-c"], ["cmd.exe", "/c"],
    ["powershell", "-Command"], ["pwsh", "-Command"], ["env", "KUBECONFIG=/tmp/foo"],
])
def test_shell_wrappers_are_not_allowed(gate_root, monkeypatch, wrapper):
    assert_denied_before_subprocess([*wrapper, *nodes()], monkeypatch)


@pytest.mark.parametrize("operator", [";", "&&", "||", "|", "&", "\n"])
def test_chained_command_tokens_are_not_allowed(gate_root, monkeypatch, operator):
    assert_denied_before_subprocess([*nodes(), operator, "kubectl", "delete", "all"], monkeypatch)


@pytest.mark.parametrize("binary", ["./kubectl", "/tmp/kubectl", "C:/untrusted/kubectl.exe", "./docker", "C:/untrusted/docker.exe"])
def test_alternate_binary_paths_are_not_trusted(gate_root, monkeypatch, binary):
    command = nodes() if "kubectl" in binary else inspect_node()
    command[0] = binary
    assert_denied_before_subprocess(command, monkeypatch)


@pytest.mark.parametrize("command", [
    ["docker", "ps"], ["docker", "version"], ["docker", "rm", "trafficfuse-lab-control-plane"],
    ["docker", "exec", "trafficfuse-lab-control-plane", "sh"],
    ["docker", "inspect", "trafficfuse-lab-control-plane"],
    ["docker", "container", "inspect", "trafficfuse-lab-control-plane"],
    inspect_node("{{json .}}"), inspect_node("{{json .Config.Env}}"),
    inspect_node(node="incident-lab-control-plane"), inspect_node(node="vmware-k8s"),
    inspect_node(node="trafficfuse-lab-control-plane;echo"), inspect_node(node="--help"),
    [*inspect_node(), "other-container"],
    ["docker", "--host", "tcp://example.invalid:2375", *inspect_node()[1:]],
    ["docker", "--context", "foreign", *inspect_node()[1:]],
    ["docker", "inspect", "--format", DOCKER_NODE_NAME_FORMAT, "--type", "volume", "trafficfuse-lab-control-plane"],
])
def test_docker_is_limited_to_safe_local_node_projections(monkeypatch, command):
    assert_denied_before_subprocess(command, monkeypatch)


@pytest.mark.parametrize("kind", ["nodes", "namespace", "docker-name", "docker-identity", "docker-worker"])
@pytest.mark.parametrize("relative", [False, True])
def test_approved_commands_reach_executor_as_arguments_without_shell(gate_root, monkeypatch, kind, relative):
    if kind == "nodes":
        command = nodes()
    elif kind == "namespace":
        command = scoped("get", "namespace", lifecycle.NAMESPACE, "-o", "json")
    else:
        template = DOCKER_KIND_IDENTITY_FORMAT if kind == "docker-identity" else DOCKER_NODE_NAME_FORMAT
        command = inspect_node(template, "trafficfuse-lab-worker2" if kind == "docker-worker" else "trafficfuse-lab-control-plane")
    if relative and command[0] == "kubectl":
        command[2] = lifecycle.DEDICATED_KUBECONFIG
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"synthetic result", stderr=b"")
    monkeypatch.setattr(cluster_facts.subprocess, "run", fake_run)
    assert_read_only_command(command)
    result = subprocess_executor(command)
    assert calls == [(command, {"capture_output": True, "timeout": 10.0, "shell": False})]
    assert result.argv == tuple(command)
    assert result.stdout == b"synthetic result"
