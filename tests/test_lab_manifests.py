"""Offline Kubernetes manifest validation (no cluster contact).

Every assertion is derived from the YAML documents themselves; no kubectl,
kind, or cluster API call is performed (Phase 1A brief, section 9).
"""

from pathlib import Path

import yaml

LAB_DIR = Path(__file__).resolve().parents[1] / "deploy" / "lab"

BASELINE_FILES = [
    "namespace.yaml",
    "healthy-deployment.yaml",
    "faulty-deployment.yaml",
    "service-live.yaml",
    "service-versions.yaml",
]
ALL_FILES = BASELINE_FILES + ["probe-job.yaml"]

EXPECTED_NAMESPACE = "trafficfuse-system"
EXPECTED_CLUSTER = "trafficfuse-lab"
EXPECTED_IMAGE_TAG = "trafficfuse-lab-service:phase1a"


def _load(name: str) -> list[dict]:
    documents = yaml.safe_load_all((LAB_DIR / name).read_text(encoding="utf-8"))
    return [document for document in documents if document]


def _all_documents() -> list[tuple[str, dict]]:
    return [(name, document) for name in ALL_FILES for document in _load(name)]


def _containers(document: dict) -> list[dict]:
    spec = document["spec"]["template"]["spec"]
    return spec["containers"]


def test_manifest_files_exist() -> None:
    for name in ALL_FILES + ["kustomization.yaml"]:
        assert (LAB_DIR / name).is_file(), f"missing manifest {name}"


def test_every_document_parses_as_yaml() -> None:
    for _, document in _all_documents():
        assert isinstance(document, dict)
        assert document["kind"]


def test_namespace_identity() -> None:
    (namespace,) = _load("namespace.yaml")
    assert namespace["kind"] == "Namespace"
    assert namespace["metadata"]["name"] == EXPECTED_NAMESPACE


def test_all_resources_carry_ownership_metadata() -> None:
    for name, document in _all_documents():
        labels = document["metadata"].get("labels", {})
        assert labels.get("trafficfuse.io/owner") == EXPECTED_CLUSTER, name
        assert labels.get("app.kubernetes.io/managed-by") == EXPECTED_CLUSTER, name
        if document["kind"] != "Namespace":
            assert document["metadata"]["namespace"] == EXPECTED_NAMESPACE, name


def test_deployments_selectors_match_templates() -> None:
    for name, version, expected_name in (
        ("healthy-deployment.yaml", "healthy", "trafficfuse-healthy"),
        ("faulty-deployment.yaml", "faulty", "trafficfuse-faulty"),
    ):
        (deployment,) = _load(name)
        assert deployment["kind"] == "Deployment"
        assert deployment["metadata"]["name"] == expected_name
        selector = deployment["spec"]["selector"]["matchLabels"]
        assert selector == {"app": "trafficfuse", "version": version}
        template_labels = deployment["spec"]["template"]["metadata"]["labels"]
        for key, value in selector.items():
            assert template_labels[key] == value
        assert template_labels["trafficfuse.io/owner"] == EXPECTED_CLUSTER


def test_deployments_have_sane_resources_and_probes() -> None:
    for name in ("healthy-deployment.yaml", "faulty-deployment.yaml"):
        (deployment,) = _load(name)
        for container in _containers(deployment):
            assert container["image"] == EXPECTED_IMAGE_TAG
            assert not container["image"].endswith(":latest")
            resources = container["resources"]
            assert resources["requests"]["cpu"] and resources["requests"]["memory"]
            assert resources["limits"]["cpu"] and resources["limits"]["memory"]
            probe = container["readinessProbe"]
            assert probe["httpGet"]["path"] == "/ready"
            assert container["env"][0]["name"] == "TRAFFICFUSE_LAB_MODE"


def test_faulty_deployment_configures_deterministic_fault_rate() -> None:
    (deployment,) = _load("faulty-deployment.yaml")
    env = {entry["name"]: entry["value"] for entry in _containers(deployment)[0]["env"]}
    assert env["TRAFFICFUSE_LAB_MODE"] == "faulty"
    assert env["TRAFFICFUSE_LAB_FAULT_RATE_PERCENT"] == "50"


def test_healthy_deployment_has_no_fault_rate() -> None:
    (deployment,) = _load("healthy-deployment.yaml")
    env = {entry["name"]: entry["value"] for entry in _containers(deployment)[0]["env"]}
    assert env["TRAFFICFUSE_LAB_MODE"] == "healthy"
    assert "TRAFFICFUSE_LAB_FAULT_RATE_PERCENT" not in env


def test_live_service_routes_to_faulty_initially() -> None:
    (service,) = _load("service-live.yaml")
    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "trafficfuse-live"
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"] == {"app": "trafficfuse", "version": "faulty"}
    (port,) = service["spec"]["ports"]
    assert port["port"] == 80
    assert port["targetPort"] == "http"


def test_version_services_exist_and_stay_internal() -> None:
    services = {document["metadata"]["name"]: document for document in _load("service-versions.yaml")}
    assert set(services) == {"trafficfuse-healthy", "trafficfuse-faulty"}
    assert services["trafficfuse-healthy"]["spec"]["selector"] == {
        "app": "trafficfuse",
        "version": "healthy",
    }
    assert services["trafficfuse-faulty"]["spec"]["selector"] == {
        "app": "trafficfuse",
        "version": "faulty",
    }
    for service in services.values():
        assert service["spec"]["type"] == "ClusterIP"


def test_no_public_exposure_anywhere() -> None:
    for name, document in _all_documents():
        if document["kind"] != "Service":
            continue
        spec = document["spec"]
        assert spec["type"] in ("ClusterIP",), name
        for port in spec.get("ports", []):
            assert "nodePort" not in port, name


def test_probe_job_is_scoped_and_isolated() -> None:
    (job,) = _load("probe-job.yaml")
    assert job["kind"] == "Job"
    assert job["metadata"]["namespace"] == EXPECTED_NAMESPACE
    pod_spec = job["spec"]["template"]["spec"]
    assert pod_spec["restartPolicy"] == "Never"
    (container,) = pod_spec["containers"]
    assert container["command"] == ["python", "-m", "trafficfuse.lab.probe_cli"]
    args = container["args"]
    url_index = args.index("--url") + 1
    assert args[url_index] == "http://trafficfuse-live.trafficfuse-system.svc.cluster.local"
    scenario_index = args.index("--scenario-id") + 1
    assert args[scenario_index] == "phase1b-live"
    output_index = args.index("--output") + 1
    assert args[output_index] == "/evidence/probe-live.json"
    mounts = {mount["name"] for mount in container["volumeMounts"]}
    assert mounts == {"evidence"}


def test_kustomization_excludes_probe_job() -> None:
    (kustomization,) = _load("kustomization.yaml")
    resources = kustomization["resources"]
    assert set(resources) == set(BASELINE_FILES)
    assert "probe-job.yaml" not in resources


def test_no_mutation_tool_or_switch_endpoint_in_manifests() -> None:
    # Comments may mention the future Phase 3 tool; the applied configuration
    # must not implement or register any mutation path.
    for name in ALL_FILES:
        text = (LAB_DIR / name).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#")
        )
        assert "reroute_service_traffic" not in code, name
        assert "kind: Role" not in code, name
        assert "kind: ClusterRole" not in code, name
