import os

import pytest
from fastapi.testclient import TestClient

os.environ["LEAD_ACCESS_KEY"] = "test-secret"

from app.main import app


client = TestClient(app)


def test_missing_key_is_unauthorized():
    response = client.get("/protected")
    assert response.status_code == 401


def test_wrong_key_is_unauthorized():
    response = client.get("/protected", headers={"X-Lead-Key": "wrong"})
    assert response.status_code == 401


def test_correct_key_is_allowed():
    response = client.get("/protected", headers={"X-Lead-Key": "test-secret"})
    assert response.status_code == 200


def test_cors_preflight_allows_api_headers():
    response = client.options(
        "/protected",
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
