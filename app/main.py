import httpx
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import initialize_database
from app.security import require_lead_key

settings = get_settings()
app = FastAPI(title="Lead API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Lead-Key", "ngrok-skip-browser-warning"],
)


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/health")
async def health() -> dict[str, object]:
    model_ready = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{settings.ollama_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            model_ready = any(
                model.get("name") == settings.ollama_model
                or model.get("name", "").startswith(f"{settings.ollama_model}:")
                for model in models
            )
    except (httpx.HTTPError, ValueError):
        pass
    return {
        "status": "ok",
        "mode": settings.lead_mode,
        "model_ready": model_ready,
        "searches_left": settings.monthly_search_limit,
        "email_enabled": settings.email_enabled,
    }


@app.get("/protected", dependencies=[Depends(require_lead_key)])
async def protected_check() -> dict[str, str]:
    """Temporary scaffold route used to verify access-key protection."""
    return {"status": "ok"}
