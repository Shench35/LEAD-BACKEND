import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app import ranker, search
from app.models import JobRequest

JOB_TTL_SECONDS = 24 * 60 * 60
MAX_WAITING_JOBS = 10


@dataclass
class Job:
    job_id: str
    request: JobRequest
    created_at: float = field(default_factory=time.time)
    status: str = "queued"
    ranked: bool = False
    results: list[dict[str, Any]] | None = None
    email_status: str = "none"
    error: str | None = None


jobs: dict[str, Job] = {}
queue: asyncio.Queue[str] | None = None
worker_task: asyncio.Task[None] | None = None
active_job_id: str | None = None


def purge_expired_jobs(now: float | None = None) -> None:
    current = now or time.time()
    expired = [job_id for job_id, job in jobs.items() if current - job.created_at >= JOB_TTL_SECONDS]
    for job_id in expired:
        jobs.pop(job_id, None)


def start_worker() -> None:
    global queue, worker_task
    queue = asyncio.Queue()
    worker_task = asyncio.create_task(_worker_loop(), name="lead-job-worker")


async def stop_worker() -> None:
    global queue, worker_task, active_job_id
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    worker_task = None
    queue = None
    active_job_id = None


def enqueue(request: JobRequest) -> Job:
    if queue is None:
        raise RuntimeError("Job worker is not running")
    purge_expired_jobs()
    if queue.qsize() >= MAX_WAITING_JOBS:
        raise OverflowError("The job queue is full. Please try again shortly.")
    job = Job(job_id=str(uuid.uuid4()), request=request)
    jobs[job.job_id] = job
    queue.put_nowait(job.job_id)
    return job


def queue_position(job_id: str) -> int:
    if queue is None:
        return 0
    for position, queued_id in enumerate(queue._queue):
        if queued_id == job_id:
            return position
    return 0


async def _worker_loop() -> None:
    global active_job_id
    assert queue is not None
    while True:
        job_id = await queue.get()
        active_job_id = job_id
        job = jobs.get(job_id)
        if job is not None:
            try:
                await _process_job(job)
            except Exception:
                job.status = "failed"
                job.error = "The search could not be completed. Please try again."
        active_job_id = None
        queue.task_done()


async def _process_job(job: Job) -> None:
    job.status = "searching"
    try:
        results = await asyncio.to_thread(
            search.search_leads, job.request.role, job.request.location
        )
    except search.SearchLimitReached:
        job.status = "failed"
        job.error = "Monthly search limit reached. Try again next month."
        return
    except search.SearchFailed as error:
        job.status = "failed"
        job.error = str(error)
        return

    job.status = "ranking"
    try:
        job.ranked, job.results = await ranker.rank_results(
            job.request.role,
            job.request.location,
            results,
            level=job.request.level,
            skills=job.request.skills,
        )
    except Exception:
        job.ranked = False
        job.results = results
    job.status = "done"
