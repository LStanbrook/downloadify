"""
FastAPI backend for Downloadify's web mode.

Exposes a tiny JSON API the static frontend (see `static/`) talks to:

- GET  /api/config         whether this deployment is running in public mode
- POST /api/jobs           start a playlist download, returns a job id
- GET  /api/jobs/{id}      current status snapshot (for polling/refresh)
- GET  /api/jobs/{id}/events   live log/progress stream via Server-Sent Events
- POST /api/jobs/{id}/cancel   ask a running job to stop
- GET  /api/jobs/{id}/download  zip a finished job's output and download it

The Spotify login endpoints (/api/spotify/*) are only registered when
PUBLIC_DEPLOYMENT is off -- see the module docstring note below the routes.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from downloadify import config
from downloadify.core.utils import sanitize_filename
from downloadify.web.job_manager import TERMINAL_STATUSES, JobStatus, job_manager

app = FastAPI(title="Downloadify", description="Spotify playlist -> MP3 downloader")


class CreateJobRequest(BaseModel):
    playlist_url: str = Field(..., description="Public Spotify playlist URL")
    output_dir: str = Field(default=str(config.DEFAULT_DOWNLOAD_DIR))


class CreateJobResponse(BaseModel):
    job_id: str


@app.get("/api/config")
async def get_client_config() -> dict:
    return {"public_deployment": config.PUBLIC_DEPLOYMENT}


@app.post("/api/jobs", response_model=CreateJobResponse)
async def create_job(payload: CreateJobRequest) -> CreateJobResponse:
    if not payload.playlist_url.strip():
        raise HTTPException(status_code=400, detail="playlist_url is required")

    if config.PUBLIC_DEPLOYMENT and job_manager.running_count() >= config.PUBLIC_MAX_CONCURRENT_JOBS:
        raise HTTPException(
            status_code=429,
            detail="This server is busy with other downloads right now -- try again in a minute.",
        )

    job = job_manager.create_job(payload.playlist_url.strip(), payload.output_dir.strip())
    if config.PUBLIC_DEPLOYMENT:
        # Ignore any client-supplied path in public mode -- always write to a
        # server-generated per-job temp folder. This is what closes the
        # path-traversal hole: an arbitrary filesystem path from an
        # untrusted client is never used to decide where files get written.
        job.output_dir = str(config.PUBLIC_JOBS_DIR / job.id)

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


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str) -> FileResponse:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id")
    if job.status not in (JobStatus.DONE, JobStatus.CANCELLED) or not job.summary:
        raise HTTPException(status_code=400, detail="Job isn't finished yet")
    if job.summary.succeeded == 0:
        raise HTTPException(status_code=400, detail="No tracks downloaded successfully")

    zip_path = await job_manager.get_or_build_zip(job)
    filename = f"{sanitize_filename(job.summary.playlist_name)}.zip"
    return FileResponse(zip_path, media_type="application/zip", filename=filename)


# The Spotify login flow is single-user by design (one cached token file on
# disk) and would route a stranger's own Spotify session through
# infrastructure the operator controls -- a meaningfully bigger trust ask
# than a login that never leaves the visitor's own machine. It's kept for
# local/desktop use (running from source, or the packaged .exe) and simply
# not registered at all on a public deployment.
if not config.PUBLIC_DEPLOYMENT:
    from downloadify.core import spotify_auth
    from downloadify.core.spotify_client import SpotifyClient, SpotifyPlaylistError

    @app.get("/api/spotify/status")
    async def spotify_status() -> dict:
        return {"logged_in": spotify_auth.is_logged_in()}

    @app.post("/api/spotify/login")
    async def spotify_login() -> dict:
        """
        Runs the one-time browser-based Spotify login. This is only needed for
        personalized playlists (Discover Weekly, a Daily Mix, ...) or a user's
        own private playlists -- regular public playlists never need it.

        Blocks the request until the user finishes logging in (or it times out),
        since it needs to wait for Spotify's redirect either way.
        """
        if not config.SPOTIFY_CLIENT_ID:
            raise HTTPException(
                status_code=400,
                detail="Set SPOTIFY_CLIENT_ID in your .env first -- see the README.",
            )
        try:
            await asyncio.to_thread(spotify_auth.login_interactive)
        except spotify_auth.SpotifyLoginError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/spotify/logout")
    async def spotify_logout() -> dict:
        spotify_auth.logout()
        return {"ok": True}

    @app.get("/api/spotify/my-playlists")
    async def spotify_my_playlists() -> dict:
        """
        Lists the logged-in user's own playlist library -- lets a playlist that
        doesn't resolve reliably by pasting its link be picked directly instead.
        """
        try:
            playlists = await asyncio.to_thread(SpotifyClient().list_my_playlists)
        except SpotifyPlaylistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "playlists": [
                {
                    "id": p.playlist_id,
                    "name": p.name,
                    "track_count": p.track_count,
                    "owner": p.owner,
                    "url": p.url,
                }
                for p in playlists
            ]
        }


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
