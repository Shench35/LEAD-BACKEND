from typing import Any

import pytest

from app import db, search
from app.config import Settings


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "lead.sqlite3")
    db.initialize_database()


class StubResponse:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class StubClient:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict[str, str], timeout: float) -> StubResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return StubResponse(
            {
                "organic_results": [
                    {
                        "title": f"Opportunity {len(self.calls)}",
                        "link": f"https://jobs.example.ng/{len(self.calls)}",
                        "snippet": "Industrial training placement",
                    }
                ]
            }
        )


def test_query_builder_creates_two_expected_strings_and_cleans_control_chars():
    assert search.build_queries("software\nengineering", "Lagos\tIsland") == [
        ("siwes", "software engineering SIWES placement Lagos Island"),
        ("internship", "software engineering internship industrial training Lagos Island 2026"),
    ]
    assert search.build_queries("Data\x00 Science", "REMOTE") == [
        ("siwes", "Data Science SIWES placement remote Nigeria"),
        ("internship", "Data Science internship industrial training remote Nigeria 2026"),
    ]


def test_same_live_input_uses_cache_for_both_queries(isolated_database):
    settings = Settings(lead_access_key="test", lead_mode="live", serpapi_key="test-key")
    client = StubClient()

    first = search.search_leads("software", "Lagos", settings=settings, client=client)
    second = search.search_leads("software", "Lagos", settings=settings, client=client)

    assert len(first) == 2
    assert second == first
    assert len(client.calls) == 2
    assert all(call["timeout"] == 10.0 for call in client.calls)
    assert all(call["params"]["engine"] == "google" for call in client.calls)
    assert all(call["params"]["gl"] == "ng" and call["params"]["hl"] == "en" for call in client.calls)


def test_counter_blocks_at_limit_and_cache_hits_do_not_increment(isolated_database):
    settings = Settings(
        lead_access_key="test",
        lead_mode="live",
        serpapi_key="test-key",
        monthly_search_limit=2,
    )
    client = StubClient()

    search.search_leads("software", "Lagos", settings=settings, client=client)
    assert db.monthly_search_count() == 2
    search.search_leads("software", "Lagos", settings=settings, client=client)
    assert db.monthly_search_count() == 2

    with pytest.raises(search.SearchLimitReached):
        search.search_leads("data science", "Lagos", settings=settings, client=client)
    assert db.monthly_search_count() == 2
    assert len(client.calls) == 2


def test_fixture_mode_never_constructs_http_client(isolated_database, monkeypatch):
    def forbidden_client():
        raise AssertionError("Fixture mode must not create a network client")

    monkeypatch.setattr(search.httpx, "Client", forbidden_client)
    results = search.search_leads(
        "data analytics", "Lagos", settings=Settings(lead_access_key="test")
    )

    assert len(results) >= 3
    assert "Data" in results[0]["title"]


def test_electronics_role_selects_networking_fixture(isolated_database):
    results = search.search_leads(
        "electronics", "Lagos", settings=Settings(lead_access_key="test")
    )

    assert results[0]["title"] == "Network Operations SIWES Student"
    assert any("Electronics Engineering Internship" in result["title"] for result in results)
