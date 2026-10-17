"""Offline Kubernetes manifest validation (no cluster contact).

Every assertion is derived from the YAML documents themselves; no kubectl,
kind, or cluster API call is performed (Phase 1A brief, section 9).
"""

import tomllib
from pathlib import Path

import yaml

LAB_DIR = Path(__file__).resolve().parents[1] / "deploy" / "lab"
PROJECT_ROOT = LAB_DIR.parents[1]

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


# --- reproducible image definition (Phase 1A.1, finding P1) ---------------------

DOCKERFILE = LAB_DIR / "Dockerfile"
REQUIREMENTS = LAB_DIR / "requirements-lab.txt"
DOCKERIGNORE = PROJECT_ROOT / ".dockerignore"


def test_dockerfile_and_dockerignore_exist() -> None:
    assert DOCKERFILE.is_file()
    assert DOCKERIGNORE.is_file()
    assert REQUIREMENTS.is_file()


def test_dockerfile_pins_base_image_by_digest() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG PYTHON_IMAGE=" in text
    assert "FROM ${PYTHON_IMAGE}" in text
    # No moving tags: the default base reference is digest-pinned.
    assert "python@sha256:" in text


def test_dockerfile_runs_non_root_on_8080() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "useradd" in text
    assert "USER trafficfuse" in text
    assert "EXPOSE 8080" in text
    # Single shared image: both startup commands are documented/executable.
    assert "python -m trafficfuse.lab.service" in text
    assert "python -m trafficfuse.lab.probe_cli" in text


def test_dockerfile_embeds_no_credentials_or_shared_runtime() -> None:
    # Comments may explain *why* something is excluded; only executed content
    # is scanned. (Inline trailing comments are not valid Dockerfile syntax.)
    code = "\n".join(
        line
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    ).lower()
    for forbidden in (
        "git+https://",
        "password",
        "secret",
        "api_key",
        "token",
        "agentic-intent-gate",
        "agentic_intent_gate",
    ):
        assert forbidden not in code, forbidden


def test_image_requirements_match_uv_lock_exactly() -> None:
    # The image pins must mirror the authoritative lockfile (uv.lock), and the
    # shared runtime must not leak into the lightweight lab image.
    pins: dict[str, str] = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, version = line.partition("==")
        assert version, f"unpinned requirement: {line}"
        pins[name.lower()] = version
    lock = tomllib.loads((PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {
        package["name"].lower(): package["version"] for package in lock["package"]
    }
    assert pins, "no pinned requirements found"
    for name, version in pins.items():
        assert locked.get(name) == version, f"{name}: image {version} != lock {locked.get(name)}"
    assert "agentic-intent-gate" not in pins


def test_lab_package_does_not_import_shared_runtime() -> None:
    # Proof the lightweight image needs no Agentic Intent Gate installation:
    # no lab module imports it.
    for source in (PROJECT_ROOT / "src" / "trafficfuse" / "lab").glob("*.py"):
        assert "agentic_intent_gate" not in source.read_text(encoding="utf-8"), source.name


def test_manifests_use_never_pull_policy_for_kind_loaded_image() -> None:
    # The image is loaded into the kind node; it must never be pulled.
    for name in ("healthy-deployment.yaml", "faulty-deployment.yaml", "probe-job.yaml"):
        for container in _containers(_load(name)[0]):
            assert container["image"] == EXPECTED_IMAGE_TAG
            assert container["imagePullPolicy"] == "Never", name


def test_dockerignore_excludes_local_state() -> None:
    entries = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    for required in (".git", ".venv", ".runtime", "tests", ".workbuddy-ai", ".env"):
        assert required in entries, required


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
