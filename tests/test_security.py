from fastapi.testclient import TestClient
import pytest

from app import main

PAYLOAD = {"role": "software engineering", "location": "Lagos"}


@pytest.fixture
def client(monkeypatch):
    async def no_warmup():
        return None

    monkeypatch.setattr(main, "warm_ollama", no_warmup)
    with TestClient(main.app) as test_client:
        yield test_client


def test_missing_key_is_unauthorized(client):
    response = client.post("/jobs", json=PAYLOAD)
    assert response.status_code == 401


def test_wrong_key_is_unauthorized(client):
    response = client.post("/jobs", json=PAYLOAD, headers={"X-Lead-Key": "wrong"})
    assert response.status_code == 401


def test_correct_key_is_allowed(client):
    response = client.post(
        "/jobs", json=PAYLOAD, headers={"X-Lead-Key": "test-access-key"}
    )
    assert response.status_code == 202


def test_cors_preflight_allows_api_headers(client):
    response = client.options(
        "/jobs",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Lead-Key, ngrok-skip-browser-warning",
        },
    )

    assert response.status_code == 200
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "x-lead-key" in allowed_headers
    assert "ngrok-skip-browser-warning" in allowed_headers
