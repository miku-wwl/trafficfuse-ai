"""Trusted startup composition per mode (host code only; no HTTP input).

* ``live``: requires the dedicated kubeconfig and an explicitly provisioned
  live governance profile (``python -m trafficfuse.cli provision --profile
  live``); wires the kubectl gateway, the live mutation port and the trusted
  ownership collector as a pre-dispatch check.
* ``simulation``: the offline lab; its private governance profile is
  provisioned automatically on first start because it cannot touch any
  infrastructure.
* ``replay``: no governance runtime at all; recorded evidence, read-only.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from trafficfuse.application.incidents import IncidentService
from trafficfuse.application.store import IncidentStore
from trafficfuse.config.product import ProductSettings
from trafficfuse.domain.evidence import SOURCE_DESCRIPTIONS, SOURCE_LIVE, SOURCE_REPLAY, SOURCE_SIMULATION
from trafficfuse.infrastructure import runtime as aig_runtime
from trafficfuse.infrastructure.governance import IDENTITY_FILE, open_governance, provision_profile
from trafficfuse.infrastructure.ollama import OllamaDiagnosisClient
from trafficfuse.lab.lifecycle import SafetyViolation


def _aig_meta() -> dict[str, Any]:
    return {
        "distribution": aig_runtime.RUNTIME_DISTRIBUTION,
        "version": aig_runtime.installed_version(),
        "commit": aig_runtime.resolved_git_commit(),
        "public_module": aig_runtime.RUNTIME_PUBLIC_MODULE,
        "execution_budget_seconds": aig_runtime.default_execution_budget_seconds(),
    }


class _ReplayStore:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def refusals(self) -> list[dict[str, Any]]:
        return list(self._data.get("refusals", []))

    def events(self, incident_id: str | None = None) -> list[dict[str, Any]]:
        events = self._data.get("audit", {}).get("events", [])
        return [e for e in events if incident_id is None or e.get("incident_id") == incident_id]


class ReplayService:
    """Read-only recorded evidence. Labels are preserved; nothing is re-measured."""

    gov = None

    def __init__(self, path: Path) -> None:
        self.data = json.loads(Path(path).read_text("utf-8")) if Path(path).is_file() else {}
        self.store = _ReplayStore(self.data)

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "incident_id": i["incident_id"],
                "created_at": i["created_at"],
                "stage": i["stage"],
                "source": SOURCE_REPLAY,
                "recorded_source": i["source"],
                "live_error_percent": i["assessment"]["live_error_percent"],
                "verdict": (i.get("verification") or {}).get("verdict"),
            }
            for i in self.data.get("incidents", [])
        ]

    def view(self, incident_id: str) -> dict[str, Any]:
        from trafficfuse.application.incidents import OperationRefused

        for incident in self.data.get("incidents", []):
            if incident["incident_id"] == incident_id:
                return incident | {"replay": True, "recorded_source": incident["source"]}
        raise OperationRefused("INCIDENT_NOT_FOUND", "no such recorded incident", 404)


@asynccontextmanager
async def open_product(settings: ProductSettings | None = None) -> AsyncIterator[tuple[Any, dict[str, Any]]]:
    settings = settings or ProductSettings.from_env()
    base_meta: dict[str, Any] = {"mode": settings.mode, "aig": _aig_meta()}
    if settings.mode == "replay":
        service = ReplayService(settings.replay_file)
        yield service, base_meta | {
            "source": SOURCE_REPLAY,
            "source_description": SOURCE_DESCRIPTIONS[SOURCE_REPLAY],
            "recorded": service.data.get("meta", {}),
            "read_only": True,
        }
        return

    store = IncidentStore(settings.store_path())
    lookup = IncidentService.evidence_lookup_for(store)
    profile_dir = settings.profile_directory()
    ownership_check = None
    if settings.mode == "live":
        from trafficfuse.infrastructure.live_cluster import LiveKindCluster, ensure_dedicated_kubeconfig
        from trafficfuse.infrastructure.reroute import KubectlServiceMutation
        from trafficfuse.lab.cluster_facts import verify_live_ownership
        from trafficfuse.lab.lifecycle import PROJECT_ROOT, RECEIPT_DIRECTORY, RECEIPT_FILENAME

        ensure_dedicated_kubeconfig()
        if not (profile_dir / IDENTITY_FILE).is_file():
            raise SafetyViolation(
                "live governance profile is not provisioned; run "
                "`uv run python -m trafficfuse.cli provision --profile live` first"
            )
        cluster = LiveKindCluster(settings.artifacts_root / "live")
        mutation = KubectlServiceMutation()

        def ownership_check() -> dict[str, Any]:
            return verify_live_ownership(
                kubeconfig_path=PROJECT_ROOT / ".runtime" / "kubeconfig" / "trafficfuse-lab.yaml",
                record_path=PROJECT_ROOT / RECEIPT_DIRECTORY / RECEIPT_FILENAME,
            ).to_dict()

        source = SOURCE_LIVE
    else:
        from trafficfuse.infrastructure.simulation import SimulatedLab

        if not (profile_dir / IDENTITY_FILE).is_file():
            provision_profile(profile_dir, "simulation")
        cluster = mutation = SimulatedLab(SOURCE_SIMULATION, artifacts_root=settings.artifacts_root / "simulation")
        source = SOURCE_SIMULATION

    model = OllamaDiagnosisClient(settings.ollama_url, settings.ollama_model)
    with open_governance(profile_dir, settings.mode, mutation=mutation, evidence_lookup=lookup) as gov:
        service = IncidentService(
            mode=settings.mode,
            cluster=cluster,
            governance=gov,
            store=store,
            model=model,
            ownership_check=ownership_check,
        )
        yield service, base_meta | {
            "source": source,
            "source_description": SOURCE_DESCRIPTIONS[source],
            "cluster": cluster.identity(),
            "governance": dict(gov.identity()),
            "model": {
                "provider": "ollama (local, loopback)",
                "name": settings.ollama_model,
                "available": await model.available(),
            },
            "synthetic_workload": True,
            "read_only": False,
        }
