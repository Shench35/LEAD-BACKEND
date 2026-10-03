import asyncio
import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from app import db, main, security, worker
from app.config import Settings


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite3")
    security.reset_rate_limits()

    async def no_warmup():
        return None

    async def fake_rank(role, location, results, **kwargs):
        return True, [
            {**result, "fit_score": 4, "reason": "Relevant training result", "tip": "Review details"}
            for result in results
        ]

    monkeypatch.setattr(main, "warm_ollama", no_warmup)
    monkeypatch.setattr(worker.ranker, "rank_results", fake_rank)
    worker.jobs.clear()
    with TestClient(main.app) as client:
        yield client
    worker.jobs.clear()
    security.reset_rate_limits()


@pytest.mark.parametrize(
    ("role", "expected_title"),
    [
        ("software engineering", "Software"),
        ("data science", "Data"),
        ("networking", "Network"),
    ],
)
def test_fixture_job_runs_to_done_for_three_profiles(api_client, role, expected_title):
    response = api_client.post(
        "/jobs",
        headers={"X-Lead-Key": "test-access-key"},
        json={"role": role, "location": "Lagos", "level": "300 level", "skills": "Python"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = api_client.get(f"/jobs/{job_id}", headers={"X-Lead-Key": "test-access-key"})
        assert status.status_code == 200
        if status.json()["status"] in {"done", "failed"}:
            break
        time.sleep(0.01)

    payload = status.json()
    assert payload["status"] == "done"
    assert payload["ranked"] is True
    assert payload["results"]
    assert expected_title.casefold() in payload["results"][0]["title"].casefold()


def test_input_is_cleaned_and_invalid_empty_text_is_rejected(api_client):
    response = api_client.post(
        "/jobs",
        headers={"X-Lead-Key": "test-access-key"},
        json={"role": "  software\nengineering  ", "location": " Lagos\x00 ", "skills": "  "},
    )
    assert response.status_code == 422

    cleaned = api_client.post(
        "/jobs",
        headers={"X-Lead-Key": "test-access-key"},
        json={"role": "  software\nengineering  ", "location": " Lagos\x00 "},
    )
    job = worker.jobs[cleaned.json()["job_id"]]
    assert job.request.role == "software engineering"
    assert job.request.location == "Lagos"


def test_worker_processes_jobs_one_at_a_time(api_client, monkeypatch):
    running = 0
    highest_parallel = 0

    async def slow_rank(role, location, results, **kwargs):
        nonlocal running, highest_parallel
        running += 1
        highest_parallel = max(highest_parallel, running)
        await asyncio.sleep(0.03)
        running -= 1
        return True, results

    monkeypatch.setattr(worker.ranker, "rank_results", slow_rank)
    job_ids = []
    for index in range(3):
        response = api_client.post(
            "/jobs",
            headers={"X-Lead-Key": "test-access-key"},
            json={"role": f"software engineering {index}", "location": "Lagos"},
        )
        assert response.status_code == 202
        job_ids.append(response.json()["job_id"])

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        statuses = [
            api_client.get(f"/jobs/{job_id}", headers={"X-Lead-Key": "test-access-key"}).json()["status"]
            for job_id in job_ids
        ]
        if all(status == "done" for status in statuses):
            break
        time.sleep(0.01)

    assert statuses == ["done", "done", "done"]
    assert highest_parallel == 1


def test_email_limit_is_hashed_and_limited_without_stopping_results(api_client, monkeypatch):
    settings = Settings(
        lead_access_key="test-access-key",
        email_enabled=True,
        email_hash_salt="test-salt",
        per_email_per_day=1,
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    attempts = []

    async def fake_send(email, role, location, results, send_settings):
        attempts.append(email)
        return True

    monkeypatch.setattr(worker.mailer, "send_email", fake_send)

    statuses = []
    for _ in range(2):
        response = api_client.post(
            "/jobs",
            headers={"X-Lead-Key": "test-access-key"},
            json={"role": "software", "location": "Lagos", "email": "Student@Gmail.com"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = api_client.get(
                f"/jobs/{job_id}", headers={"X-Lead-Key": "test-access-key"}
            ).json()
            if result["status"] == "done" and result["email_status"] != "none":
                break
            time.sleep(0.01)
        assert result["status"] == "done"
        assert result["results"]
        statuses.append(result["email_status"])
        assert worker.jobs[job_id].request.email is None

    assert statuses == ["sent", "skipped"]
    assert attempts == ["Student@gmail.com"]
    email_hash = hashlib.sha256(b"test-saltstudent@gmail.com").hexdigest()
    connection = db.connect()
    try:
        rows = connection.execute("SELECT email_hash FROM email_sends").fetchall()
    finally:
        connection.close()
    assert [row["email_hash"] for row in rows] == [email_hash]
    assert "Student@gmail.com" not in db.DB_PATH.read_bytes().decode("latin-1")


def test_email_send_failure_keeps_job_done(api_client, monkeypatch):
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: Settings(
            lead_access_key="test-access-key",
            email_enabled=True,
            email_hash_salt="test-salt",
        ),
    )

    async def fail_send(*args, **kwargs):
        return False

    monkeypatch.setattr(worker.mailer, "send_email", fail_send)
    response = api_client.post(
        "/jobs",
        headers={"X-Lead-Key": "test-access-key"},
        json={"role": "software", "location": "Lagos", "email": "student@gmail.com"},
    )
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = api_client.get(
            f"/jobs/{job_id}", headers={"X-Lead-Key": "test-access-key"}
        ).json()
        if result["status"] == "done" and result["email_status"] == "failed":
            break
        time.sleep(0.01)
    assert result["status"] == "done"
    assert result["results"]
    assert result["email_status"] == "failed"


def test_ip_job_limit_returns_rate_limit_code(api_client, monkeypatch):
    settings = Settings(lead_access_key="test-access-key", per_ip_jobs_per_hour=2)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    statuses = []
    for _ in range(3):
        response = api_client.post(
            "/jobs",
            headers={"X-Lead-Key": "test-access-key"},
            json={"role": "software", "location": "Lagos"},
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            assert response.json()["code"] == "rate_limit"
    assert statuses == [202, 202, 429]
