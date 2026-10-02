import hmac

from fastapi import Header, HTTPException

from app.config import get_settings


async def require_lead_key(x_lead_key: str | None = Header(default=None)) -> None:
    expected = get_settings().lead_access_key
    if x_lead_key is None or not hmac.compare_digest(x_lead_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing access key")
