"""
Tracks background download jobs for the web app.

Each POST /api/jobs call spins up one `Job`, running the shared
`DownloadPipeline` as an `asyncio.Task`. Pipeline callbacks append small JSON
events to the job's `events` list, which the SSE endpoint in `server.py`
tails and streams straight to the browser so the terminal-style log view
updates live.

Events are stored in one ordered, append-only list rather than handed off
through an `asyncio.Queue`, specifically so that a client connecting after
the job has already produced some events (the normal case: the browser opens
the event stream *after* the POST response comes back) sees each event
exactly once -- replaying a list by index can't duplicate or drop entries
the way "replay a snapshot, then also drain whatever's left in a queue" can.
It also means multiple tabs/reconnects can each track their own read
position independently.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from downloadify import config
from downloadify.core.models import PlaylistDownloadSummary, TrackResult
from downloadify.core.pipeline import DownloadPipeline


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass
class Job:
    id: str
    playlist_url: str
    output_dir: str
    status: JobStatus = JobStatus.PENDING
    events: list[dict[str, Any]] = field(default_factory=list)
    progress_done: int = 0
    progress_total: int = 0
    error: str | None = None
    summary: PlaylistDownloadSummary | None = None
    new_event: asyncio.Event = field(default_factory=asyncio.Event)
    zip_path: Path | None = field(default=None, repr=False)
    _pipeline: DownloadPipeline | None = field(default=None, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _zip_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def cancel(self) -> None:
        if self._pipeline:
            self._pipeline.cancel()

    @property
    def logs(self) -> list[dict[str, Any]]:
        return [
            {"message": e["message"], "verbose": e["verbose"]}
            for e in self.events
            if e.get("type") == "log"
        ]


class JobManager:
    """In-memory registry of jobs, keyed by job id."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create_job(self, playlist_url: str, output_dir: str) -> Job:
        job = Job(id=str(uuid.uuid4()), playlist_url=playlist_url, output_dir=output_dir)
        self._jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def running_count(self) -> int:
        """How many jobs are currently downloading -- used to cap concurrent jobs in public mode."""
        return sum(1 for job in self._jobs.values() if job.status == JobStatus.RUNNING)

    async def get_or_build_zip(self, job: Job) -> Path:
        """
        Zip a finished job's output folder on first request, caching the
        result so repeat/parallel requests for the same job reuse it rather
        than re-zipping (or racing to build it concurrently).
        """
        async with job._zip_lock:
            if job.zip_path and job.zip_path.exists():
                return job.zip_path
            if not job.summary:
                raise ValueError("Job has no output to zip yet")
            playlist_folder = Path(job.summary.output_folder)
            archive_base = playlist_folder.parent / "download"
            built = await asyncio.to_thread(
                shutil.make_archive,
                str(archive_base),
                "zip",
                str(playlist_folder.parent),
                playlist_folder.name,
            )
            job.zip_path = Path(built)
            return job.zip_path

    def start_job(self, job: Job) -> None:
        pipeline = DownloadPipeline(
            on_log=lambda msg, verbose: self._push(
                job, {"type": "log", "message": msg, "verbose": verbose}
            ),
            on_progress=lambda done, total: self._push(
                job, {"type": "progress", "done": done, "total": total}
            ),
            on_track_done=lambda result: self._push(
                job, {"type": "track", **_track_result_to_dict(result)}
            ),
        )
        job._pipeline = pipeline
        job.status = JobStatus.RUNNING
        job._task = asyncio.create_task(self._run(job, pipeline))

    async def _run(self, job: Job, pipeline: DownloadPipeline) -> None:
        try:
            summary = await pipeline.run(job.playlist_url, Path(job.output_dir))
            job.summary = summary
            # Push the terminal event *before* flipping the status so a
            # concurrently-tailing SSE stream can never observe "job is
            # done" without the corresponding event already being visible.
            self._push(
                job,
                {
                    "type": "cancelled" if pipeline.was_cancelled else "done",
                    "playlist_name": summary.playlist_name,
                    "output_folder": summary.output_folder,
                    "succeeded": summary.succeeded,
                    "failed": summary.failed,
                    "total": summary.total,
                },
            )
            job.status = JobStatus.CANCELLED if pipeline.was_cancelled else JobStatus.DONE
        except Exception as exc:  # noqa: BLE001 - report any failure to the client
            job.error = str(exc)
            self._push(job, {"type": "error", "message": str(exc)})
            job.status = JobStatus.FAILED
        finally:
            if config.PUBLIC_DEPLOYMENT:
                asyncio.create_task(self._cleanup_after_delay(job))

    async def _cleanup_after_delay(self, job: Job) -> None:
        """
        Deletes a public-mode job's temp folder (source files + any built
        zip) after PUBLIC_JOB_TTL_SECONDS, bounding disk usage on a shared
        host. The delay gives the visitor a real window to click "Download
        ZIP" before their files disappear.
        """
        await asyncio.sleep(config.PUBLIC_JOB_TTL_SECONDS)
        job_dir = config.PUBLIC_JOBS_DIR / job.id
        await asyncio.to_thread(shutil.rmtree, job_dir, True)

    def _push(self, job: Job, event: dict[str, Any]) -> None:
        if event.get("type") == "progress":
            job.progress_done = event["done"]
            job.progress_total = event["total"]
        job.events.append(event)
        job.new_event.set()


def _track_result_to_dict(result: TrackResult) -> dict[str, Any]:
    return {
        "track": result.track.display_name,
        "status": result.status.value,
        "error": result.error,
    }


# A single, process-wide manager is all that's needed for this local-use app.
job_manager = JobManager()
