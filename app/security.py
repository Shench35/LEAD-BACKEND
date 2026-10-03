import hmac
import time
from collections import deque

from fastapi import Header, HTTPException, Request

from app.config import get_settings

_ip_job_times: dict[str, deque[float]] = {}
_IP_WINDOW_SECONDS = 60 * 60


async def require_lead_key(x_lead_key: str | None = Header(default=None)) -> None:
    expected = get_settings().lead_access_key
    if x_lead_key is None or not hmac.compare_digest(x_lead_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing access key")


def client_ip(request: Request, *, trust_forwarded_for: bool) -> str:
    if trust_forwarded_for:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded.strip():
            return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client is not None else "unknown"


def allow_ip_job(ip: str, limit: int, *, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    recent = _ip_job_times.setdefault(ip, deque())
    while recent and current - recent[0] >= _IP_WINDOW_SECONDS:
        recent.popleft()
    if len(recent) >= limit:
        return False
    recent.append(current)
    return True


def reset_rate_limits() -> None:
    _ip_job_times.clear()
