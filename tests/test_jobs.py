import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app import db, main, worker


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jobs.sqlite3")

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
