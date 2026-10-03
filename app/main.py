import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app import db, worker
from app.db import initialize_database
from app.models import JobRequest
from app.security import allow_ip_job, client_ip, require_lead_key

settings = get_settings()
_warmup_task: asyncio.Task[None] | None = None


async def warm_ollama() -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "stream": False,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "options": {"num_predict": 1},
                },
            )
    except httpx.HTTPError:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _warmup_task
    initialize_database()
    worker.start_worker()
    _warmup_task = asyncio.create_task(warm_ollama(), name="ollama-warmup")
    try:
        yield
    finally:
        if _warmup_task is not None:
            _warmup_task.cancel()
            try:
                await _warmup_task
            except asyncio.CancelledError:
                pass
            _warmup_task = None
        await worker.stop_worker()


app = FastAPI(title="Lead API", lifespan=lifespan)
jobs_api = APIRouter(dependencies=[Depends(require_lead_key)])
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Lead-Key", "ngrok-skip-browser-warning"],
)


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
        "searches_left": max(0, settings.monthly_search_limit - db.monthly_search_count()),
        "email_enabled": settings.email_enabled,
    }


@jobs_api.post("/jobs", status_code=202)
async def create_job(request: JobRequest, http_request: Request) -> Response:
    active_settings = get_settings()
    ip = client_ip(http_request, trust_forwarded_for=active_settings.trust_forwarded_for)
    if not allow_ip_job(ip, active_settings.per_ip_jobs_per_hour):
        return JSONResponse(
            status_code=429,
            content={"code": "rate_limit", "message": "Too many searches. Please try again later."},
        )
    try:
        job = worker.enqueue(request)
    except OverflowError:
        return JSONResponse(
            status_code=503,
            content={"code": "busy", "message": "The job queue is full. Please try again shortly."},
        )
    except RuntimeError:
        raise HTTPException(status_code=503, detail="The job worker is starting. Please retry.")
    return JSONResponse(status_code=202, content={"job_id": job.job_id})


@jobs_api.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, object]:
    worker.purge_expired_jobs()
    job = worker.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    result: dict[str, object] = {
        "status": job.status,
        "queue_position": worker.queue_position(job_id) if job.status == "queued" else 0,
        "ranked": job.ranked,
        "email_status": job.email_status,
        "error": job.error,
    }
    if job.status == "done":
        result["results"] = job.results or []
    return result


app.include_router(jobs_api)
