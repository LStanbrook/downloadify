"""
Orchestrates the full flow: resolve a Spotify playlist, then for every
track search YouTube and download the audio as MP3.

This is the one piece both the GUI and the web app call into, so all of the
"business logic" and its error handling lives here exactly once.

Network + subprocess calls (requests, yt-dlp) are all blocking, so each is
pushed onto a worker thread via `asyncio.to_thread`. A semaphore caps how
many tracks are processed at once, which lets multiple downloads run
concurrently without overwhelming YouTube or the disk.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path

from downloadify import config
from downloadify.core.downloader import DownloadError, download_audio_as_mp3
from downloadify.core.models import (
    PlaylistDownloadSummary,
    Track,
    TrackResult,
    TrackStatus,
)
from downloadify.core.spotify_client import SpotifyClient, SpotifyPlaylistError
from downloadify.core.utils import sanitize_filename
from downloadify.core.youtube_search import YouTubeSearchError, find_best_video_url

# `verbose=False` messages are the handful of milestones worth always
# showing (playlist resolved, a note, the final summary); `verbose=True` is
# per-track chatter and yt-dlp's own internal logging, shown only when the
# caller has opted into full logs.
LogCallback = Callable[[str, bool], None]
ProgressCallback = Callable[[int, int], None]
TrackCallback = Callable[[TrackResult], None]

# A 403/network hiccup on a single format request is common and usually
# transient (YouTube throttling, a stale extraction), so a failed download
# gets one retry before being marked as failed outright.
_DOWNLOAD_MAX_ATTEMPTS = 2
_DOWNLOAD_RETRY_DELAY_SECONDS = 2


class PipelineCancelled(Exception):
    """Raised internally when a cancellation is requested mid-run."""


def _assign_unique_filenames(tracks: list[Track]) -> dict[int, str]:
    """
    Map each track (keyed by `id()`, valid only for the lifetime of this
    `tracks` list) to a filesystem-safe filename base that's unique within
    the playlist. Playlists can legitimately contain the same track twice,
    or two different tracks whose "Artist - Title" happens to match -- left
    alone, both would resolve to the same output path and the second
    download would silently overwrite the first on disk.
    """
    seen_counts: dict[str, int] = {}
    filenames: dict[int, str] = {}
    for track in tracks:
        base = sanitize_filename(track.display_name)
        occurrence = seen_counts.get(base, 0) + 1
        seen_counts[base] = occurrence
        filenames[id(track)] = base if occurrence == 1 else f"{base} ({occurrence})"
    return filenames


class DownloadPipeline:
    """Runs the Spotify -> YouTube -> yt-dlp flow for an entire playlist."""

    def __init__(
        self,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
        on_track_done: TrackCallback | None = None,
        max_concurrent: int = config.MAX_CONCURRENT_DOWNLOADS,
    ) -> None:
        self._on_log = on_log or (lambda _msg, _verbose: None)
        self._on_progress = on_progress or (lambda _done, _total: None)
        self._on_track_done = on_track_done or (lambda _result: None)
        self._max_concurrent = max_concurrent
        self._spotify = SpotifyClient()
        self._cancel_event = asyncio.Event()
        self._completed = 0
        self._lock = asyncio.Lock()

    def cancel(self) -> None:
        """Request that the current/next `run()` stop as soon as possible."""
        self._cancel_event.set()

    @property
    def was_cancelled(self) -> bool:
        """True if `cancel()` was called during the most recent `run()`."""
        return self._cancel_event.is_set()

    def _log(self, message: str, verbose: bool = False) -> None:
        self._on_log(message, verbose)

    async def run(
        self, playlist_url: str, output_root: Path
    ) -> PlaylistDownloadSummary:
        """Download every track of `playlist_url` into `output_root`."""
        self._cancel_event.clear()
        self._completed = 0

        self._log(f"Resolving playlist: {playlist_url}")
        try:
            playlist = await asyncio.to_thread(
                self._spotify.fetch_playlist, playlist_url
            )
        except (SpotifyPlaylistError, ValueError) as exc:
            self._log(f"ERROR: {exc}")
            raise

        self._log(f"Found playlist \"{playlist.name}\" with {len(playlist.tracks)} tracks.")
        if playlist.possibly_truncated:
            self._log(
                f"Note: Spotify's no-login embed page only exposes the first "
                f"{config.SPOTIFY_EMBED_TRACK_LIMIT} tracks of a playlist, and "
                "some tracks here may be missing as a result. Set your own free "
                "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET in .env to fetch the "
                "rest -- see the README."
            )

        playlist_folder = Path(output_root) / sanitize_filename(playlist.name)
        if playlist_folder.exists():
            self._log(
                f"\"{playlist_folder}\" already exists -- replacing it with a "
                "fresh copy of the current playlist."
            )
            await asyncio.to_thread(shutil.rmtree, playlist_folder)
        playlist_folder.mkdir(parents=True, exist_ok=True)

        total = len(playlist.tracks)
        self._on_progress(0, total)

        # Two different tracks (or the same track listed twice) can share
        # the same "Artist - Title" name -- assigned up front, by playlist
        # order, so a later duplicate gets " (2)" appended rather than
        # silently overwriting the earlier track's file on disk.
        filenames_by_track = _assign_unique_filenames(playlist.tracks)

        semaphore = asyncio.Semaphore(self._max_concurrent)
        tasks = [
            asyncio.create_task(
                self._process_track(
                    track, filenames_by_track[id(track)], playlist_folder, semaphore, total
                )
            )
            for track in playlist.tracks
        ]
        results: list[TrackResult] = await asyncio.gather(*tasks)

        summary = PlaylistDownloadSummary(
            playlist_name=playlist.name,
            output_folder=str(playlist_folder),
            results=results,
        )
        self._log(
            f"Done. {summary.succeeded}/{summary.total} tracks downloaded "
            f"successfully to \"{summary.output_folder}\"."
        )
        # Tracks skipped only because of a cancellation aren't "failures" in
        # the sense worth calling out here -- the done/cancelled counts
        # already cover those; this list is specifically the tracks that
        # were actually attempted and didn't work out.
        failures = [r for r in results if r.status != TrackStatus.DONE and r.error != "Cancelled"]
        if failures:
            self._log(f"Failed ({len(failures)}):")
            for result in failures:
                self._log(f"  - {result.track.display_name} -- {result.error}")
        return summary

    async def _process_track(
        self,
        track: Track,
        filename_base: str,
        playlist_folder: Path,
        semaphore: asyncio.Semaphore,
        total: int,
    ) -> TrackResult:
        async with semaphore:
            if self._cancel_event.is_set():
                result = TrackResult(
                    track=track, status=TrackStatus.FAILED, error="Cancelled"
                )
                self._on_track_done(result)
                return result

            try:
                self._log(f"Searching YouTube for: {track.search_query}", verbose=True)
                video_url = await asyncio.to_thread(
                    find_best_video_url, track.artist_str, track.name, track.duration_ms
                )
                if not video_url:
                    raise YouTubeSearchError(
                        "No confident match found on YouTube -- skipped rather "
                        "than risk downloading the wrong song."
                    )

                self._log(f"Downloading \"{track.display_name}\" from {video_url}", verbose=True)
                file_path = await self._download_with_retry(
                    video_url, playlist_folder, filename_base
                )
                self._log(f"Saved: {file_path}", verbose=True)
                result = TrackResult(
                    track=track,
                    status=TrackStatus.DONE,
                    file_path=str(file_path),
                )
            except (YouTubeSearchError, DownloadError) as exc:
                self._log(f"FAILED \"{track.display_name}\": {exc}", verbose=True)
                result = TrackResult(
                    track=track, status=TrackStatus.FAILED, error=str(exc)
                )
            except Exception as exc:  # noqa: BLE001 - surface any other failure
                self._log(
                    f"FAILED \"{track.display_name}\": unexpected error: {exc}",
                    verbose=True,
                )
                result = TrackResult(
                    track=track, status=TrackStatus.FAILED, error=str(exc)
                )

        async with self._lock:
            self._completed += 1
            self._on_progress(self._completed, total)
        self._on_track_done(result)
        return result

    async def _download_with_retry(
        self, video_url: str, playlist_folder: Path, filename_base: str
    ) -> Path:
        """Download with one retry, since a lone 403/network hiccup is often transient."""
        for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                return await asyncio.to_thread(
                    download_audio_as_mp3,
                    video_url,
                    playlist_folder,
                    filename_base,
                    lambda msg: self._log(msg, verbose=True),
                )
            except DownloadError as exc:
                if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                    raise
                self._log(
                    f"Download attempt {attempt} failed for \"{filename_base}\" "
                    f"({exc}) -- retrying...",
                    verbose=True,
                )
                await asyncio.sleep(_DOWNLOAD_RETRY_DELAY_SECONDS)
        raise AssertionError("unreachable")  # loop always returns or raises
