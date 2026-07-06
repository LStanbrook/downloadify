"""
FastAPI backend for Downloadify's web mode.

Exposes a tiny JSON API the static frontend (see `static/`) talks to:

- POST /api/jobs           start a playlist download, returns a job id
- GET  /api/jobs/{id}      current status snapshot (for polling/refresh)
- GET  /api/jobs/{id}/events   live log/progress stream via Server-Sent Events
- POST /api/jobs/{id}/cancel   ask a running job to stop
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from downloadify import config
from downloadify.web.job_manager import TERMINAL_STATUSES, job_manager

app = FastAPI(title="Downloadify", description="Spotify playlist -> MP3 downloader")


class CreateJobRequest(BaseModel):
    playlist_url: str = Field(..., description="Public Spotify playlist URL")
    output_dir: str = Field(default=str(config.DEFAULT_DOWNLOAD_DIR))


class CreateJobResponse(BaseModel):
    job_id: str


@app.post("/api/jobs", response_model=CreateJobResponse)
async def create_job(payload: CreateJobRequest) -> CreateJobResponse:
    if not payload.playlist_url.strip():
        raise HTTPException(status_code=400, detail="playlist_url is required")

    job = job_manager.create_job(payload.playlist_url.strip(), payload.output_dir.strip())
    job_manager.start_job(job)
    return CreateJobResponse(job_id=job.id)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return {
        "id": job.id,
        "status": job.status.value,
        "logs": job.logs,
        "progress_done": job.progress_done,
        "progress_total": job.progress_total,
        "error": job.error,
    }


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id")
    job.cancel()
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id")

    async def event_stream():
        # Tail `job.events` by index rather than draining a queue, so a
        # client connecting after the job already produced some events
        # (the normal case) sees each one exactly once -- no duplicates,
        # nothing missed.
        next_index = 0
        while True:
            while next_index < len(job.events):
                yield _sse_event(job.events[next_index])
                next_index += 1

            job.new_event.clear()
            if next_index < len(job.events):
                continue  # something arrived between draining and clearing
            if job.status in TERMINAL_STATUSES:
                yield _sse_event({"type": "stream_end"})
                return
            await job.new_event.wait()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# Serve the frontend (index.html, style.css, app.js) as static files, with
# index.html at the site root.
app.mount(
    "/",
    StaticFiles(directory=str(config.PROJECT_ROOT / "downloadify" / "web" / "static"), html=True),
    name="static",
)
