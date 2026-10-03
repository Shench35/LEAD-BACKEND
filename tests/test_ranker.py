import asyncio
import json

import pytest

from app.config import Settings
from app.ranker import InvalidRanking, rank_results, validate_ranking


def test_validation_rejects_unknown_and_duplicate_ids_and_strips_urls():
    with pytest.raises(InvalidRanking):
        validate_ranking('{"ranked":[{"id":9,"fit_score":4,"reason":"ok","tip":"apply"}]}', {1})
    with pytest.raises(InvalidRanking):
        validate_ranking(
            '{"ranked":[{"id":1,"fit_score":4,"reason":"ok","tip":"a"},'
            '{"id":1,"fit_score":3,"reason":"ok","tip":"b"}]}',
            {1},
        )

    ranked = validate_ranking(
        '{"ranked":[{"id":1,"fit_score":8,"reason":"Apply at https://jobs.example.ng/now",'
        '"tip":"www.example.com/role"}]}',
        {1},
    )
    assert ranked[0].fit_score == 5
    assert "http" not in ranked[0].reason
    assert ranked[0].tip == ""


class StubResponse:
    def __init__(self, content: str):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self.content}}


class StubOllama:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    async def post(self, url, *, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return StubResponse(self.contents.pop(0))


def test_two_bad_model_outputs_fall_back_to_unranked_results():
    results = [{"id": 1, "title": "Placement", "link": "https://real.example/", "snippet": "SIWES"}]
    client = StubOllama(["not json", '{"ranked":[{"id":7,"fit_score":4,"reason":"x","tip":"y"}]}'])

    ranked, output = asyncio.run(rank_results(
        "software", "Lagos", results,
        settings=Settings(lead_access_key="test"), client=client,
    ))

    assert ranked is False
    assert output == results
    assert len(client.calls) == 2
    assert client.calls[0]["timeout"] == 180.0
    assert "stricter" not in client.calls[0]["json"]["messages"][0]["content"].lower()
    assert "valid JSON only" in client.calls[1]["json"]["messages"][0]["content"]


def test_merge_uses_only_search_links_and_model_receives_no_links():
    results = [
        {"id": 1, "title": "Current placement", "link": "https://search.example/real", "snippet": "Apply for SIWES"},
        {"id": 2, "title": "Expired page", "link": "https://search.example/old", "snippet": "Archived"},
        {"id": 3, "title": "Internship", "link": "https://search.example/intern", "snippet": "Students"},
        {"id": 4, "title": "Training role", "link": "https://search.example/train", "snippet": "Industrial training"},
    ]
    content = json.dumps(
        {"ranked": [
            {"id": 1, "fit_score": 5, "reason": "Open placement", "tip": "Prepare a CV", "link": "https://invented.example"},
            {"id": 2, "fit_score": 1, "reason": "Expired", "tip": "Skip"},
            {"id": 3, "fit_score": 4, "reason": "Internship", "tip": "Apply"},
            {"id": 4, "fit_score": 3, "reason": "Training", "tip": "Ask"},
        ]}
    )
    client = StubOllama([content])

    ranked, output = asyncio.run(rank_results(
        "software", "Lagos", results,
        level="300 level", skills="Python",
        settings=Settings(lead_access_key="test"), client=client,
    ))

    assert ranked is True
    assert [item["id"] for item in output] == [1, 3, 4]
    assert {item["link"] for item in output}.issubset({item["link"] for item in results})
    model_input = client.calls[0]["json"]["messages"][1]["content"]
    assert "link" not in model_input
    assert "Python" in model_input
