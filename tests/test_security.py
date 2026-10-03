from fastapi.testclient import TestClient
import pytest

from types import SimpleNamespace

from app import main, security

PAYLOAD = {"role": "software engineering", "location": "Lagos"}


@pytest.fixture
def client(monkeypatch):
    security.reset_rate_limits()
    async def no_warmup():
        return None

    monkeypatch.setattr(main, "warm_ollama", no_warmup)
    with TestClient(main.app) as test_client:
        yield test_client
    security.reset_rate_limits()


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


def test_health_is_public(client, monkeypatch):
    class HealthResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "gemma3:4b"}]}

    class HealthClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return HealthResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: HealthClient())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["model_ready"] is True


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


def test_forwarded_ip_is_used_only_when_trusted():
    request = SimpleNamespace(
        headers={"X-Forwarded-For": "203.0.113.8, 10.0.0.1"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert security.client_ip(request, trust_forwarded_for=False) == "127.0.0.1"
    assert security.client_ip(request, trust_forwarded_for=True) == "203.0.113.8"


def test_ip_job_limit_expires_after_one_hour():
    assert security.allow_ip_job("203.0.113.10", 1, now=100) is True
    assert security.allow_ip_job("203.0.113.10", 1, now=200) is False
    assert security.allow_ip_job("203.0.113.10", 1, now=3700) is True
