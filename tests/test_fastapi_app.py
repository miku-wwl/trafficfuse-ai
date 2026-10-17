"""FastAPI foundation smoke test: the app imports and /health responds."""

from fastapi.testclient import TestClient

from trafficfuse import __version__
from trafficfuse.api.app import app


def test_app_imports() -> None:
    assert app is not None
    assert app.title == "TrafficFuse AI"
    assert app.version == __version__


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["runtime"]["distribution"] == "agentic-intent-gate"
