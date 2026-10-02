import pytest
import asyncio
from fastapi import HTTPException

from app.config import get_settings
from app.security import require_lead_key


@pytest.fixture(autouse=True)
def configured_key(monkeypatch):
    monkeypatch.setenv("LEAD_ACCESS_KEY", "test-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("key", [None, "wrong"])
def test_missing_or_wrong_key_is_unauthorized(key):
    with pytest.raises(HTTPException) as error:
        asyncio.run(require_lead_key(key))
    assert error.value.status_code == 401


def test_correct_key_is_allowed():
    assert asyncio.run(require_lead_key("test-secret")) is None
