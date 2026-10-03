import json
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app import db
from app.config import Settings, get_settings

SERPAPI_URL = "https://serpapi.com/search.json"
FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
_search_lock = threading.Lock()
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class SearchLimitReached(Exception):
    """Raised when the monthly SerpApi allowance cannot cover a search."""


class SearchFailed(Exception):
    """Raised when the remote search provider fails."""


def clean_query_text(value: str) -> str:
    return " ".join(_CONTROL_CHARS.sub(" ", value).split())


def build_queries(role: str, location: str) -> list[tuple[str, str]]:
    clean_role = clean_query_text(role)
    clean_location = clean_query_text(location)
    location_text = "remote Nigeria" if clean_location.casefold() == "remote" else clean_location
    return [
        ("siwes", f"{clean_role} SIWES placement {location_text}"),
        ("internship", f"{clean_role} internship industrial training {location_text} 2026"),
    ]


def _cache_key(role: str, location: str, template_id: str) -> str:
    return f"{clean_query_text(role).lower()}|{clean_query_text(location).lower()}|{template_id}"


def _fixture_for_role(role: str) -> dict[str, Any]:
    normalized = role.casefold()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    if any(word in normalized for word in ("data", "machine learning", "analytics")) or "ai" in tokens:
        name = "data_ai.json"
    elif any(word in normalized for word in ("network", "electronics", "telecom")):
        name = "networking_electronics.json"
    else:
        name = "software.json"
    return _read_fixture(FIXTURE_DIR / name)


def _read_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalized_link(link: str) -> str:
    parsed = urlsplit(link.strip())
    host = parsed.netloc.casefold()
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))


def _merge_results(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for response in responses:
        for item in response.get("organic_results", []):
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            link = item.get("link")
            if not isinstance(title, str) or not isinstance(link, str) or not link.strip():
                continue
            normalized = _normalized_link(link)
            if normalized in seen:
                continue
            seen.add(normalized)
            snippet = item.get("snippet", "")
            results.append(
                {
                    "title": title,
                    "link": link,
                    "snippet": snippet[:300] if isinstance(snippet, str) else "",
                }
            )
            if len(results) == 8:
                break
        if len(results) == 8:
            break
    return [{"id": index, **item} for index, item in enumerate(results, start=1)]


def _fetch_live(query: str, settings: Settings, client: httpx.Client) -> dict[str, Any]:
    if not settings.serpapi_key:
        raise SearchFailed("Live search is not configured with a SerpApi key.")
    try:
        response = client.get(
            SERPAPI_URL,
            params={
                "engine": "google",
                "gl": "ng",
                "hl": "en",
                "q": query,
                "api_key": settings.serpapi_key,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected response shape")
        return payload
    except (httpx.HTTPError, ValueError) as error:
        raise SearchFailed("Search provider is unavailable. Please try again later.") from error


def search_leads(
    role: str,
    location: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    active_settings = settings or get_settings()
    queries = build_queries(role, location)
    responses_by_template: dict[str, dict[str, Any]] = {}

    with _search_lock:
        pending: list[tuple[str, str]] = []
        for template_id, query in queries:
            cached = db.get_cached_search(_cache_key(role, location, template_id))
            if cached is None:
                pending.append((template_id, query))
            else:
                responses_by_template[template_id] = {"organic_results": cached}

        fixture = active_settings.lead_mode == "fixture"
        if not fixture and db.monthly_search_count() + len(pending) > active_settings.monthly_search_limit:
            raise SearchLimitReached("Monthly search limit reached. Try again next month.")

        owns_client = client is None and not fixture and bool(pending)
        http_client = client or (httpx.Client() if owns_client else None)
        try:
            for template_id, query in pending:
                cache_key = _cache_key(role, location, template_id)
                if fixture:
                    raw_response = _fixture_for_role(role)
                else:
                    db.increment_monthly_searches()
                    assert http_client is not None
                    raw_response = _fetch_live(query, active_settings, http_client)
                organic_results = raw_response.get("organic_results", [])
                if not isinstance(organic_results, list):
                    organic_results = []
                db.set_cached_search(cache_key, organic_results)
                responses_by_template[template_id] = {"organic_results": organic_results}
        finally:
            if owns_client and http_client is not None:
                http_client.close()

    return _merge_results([responses_by_template[template_id] for template_id, _ in queries])
