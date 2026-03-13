"""Basic health check test."""

from fastapi.testclient import TestClient

from src.api.app import app

client = TestClient(app)


def test_health():
    response = client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_command_stub():
    response = client.post(
        "/v1/jarvis/command",
        json={"command": "What's my schedule today?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "acknowledged"
