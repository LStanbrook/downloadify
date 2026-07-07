"""Background QThread that runs the async download pipeline off the UI thread."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from downloadify.core import spotify_auth
from downloadify.core.models import PlaylistDownloadSummary, PlaylistSummary, TrackResult
from downloadify.core.pipeline import DownloadPipeline
from downloadify.core.spotify_client import SpotifyClient


class DownloadWorker(QThread):
    """
    Runs `DownloadPipeline.run()` on a separate thread so the Qt event loop
    (and therefore the UI) never blocks while network/yt-dlp work happens.

    PyQt signals are thread-safe to emit from a worker thread and are
    automatically delivered on the main thread, so callbacks below just
    emit signals rather than touching any widgets directly.
    """

    log_message = pyqtSignal(str, bool)  # message, verbose
    progress_updated = pyqtSignal(int, int)
    track_finished = pyqtSignal(object)  # TrackResult
    finished_ok = pyqtSignal(object)  # PlaylistDownloadSummary
    cancelled = pyqtSignal(object)  # PlaylistDownloadSummary
    failed = pyqtSignal(str)

    def __init__(self, playlist_url: str, output_dir: Path) -> None:
        super().__init__()
        self._playlist_url = playlist_url
        self._output_dir = output_dir
        self._pipeline = DownloadPipeline(
            on_log=self.log_message.emit,
            on_progress=self.progress_updated.emit,
            on_track_done=self.track_finished.emit,
        )

    def cancel(self) -> None:
        self._pipeline.cancel()

    def run(self) -> None:  # noqa: D102 - QThread override
        try:
            summary: PlaylistDownloadSummary = asyncio.run(
                self._pipeline.run(self._playlist_url, self._output_dir)
            )
            if self._pipeline.was_cancelled:
                self.cancelled.emit(summary)
            else:
                self.finished_ok.emit(summary)
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            self.failed.emit(str(exc))


class SpotifyLoginWorker(QThread):
    """
    Runs the blocking browser-based Spotify login (waits for the user to
    finish logging in and Spotify to redirect back) off the UI thread.
    """

    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    def run(self) -> None:  # noqa: D102 - QThread override
        try:
            spotify_auth.login_interactive()
            self.succeeded.emit()
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            self.failed.emit(str(exc))


class PlaylistListWorker(QThread):
    """Lists the logged-in user's own Spotify playlists off the UI thread."""

    succeeded = pyqtSignal(list)  # list[PlaylistSummary]
    failed = pyqtSignal(str)

    def run(self) -> None:  # noqa: D102 - QThread override
        try:
            playlists: list[PlaylistSummary] = SpotifyClient().list_my_playlists()
            self.succeeded.emit(playlists)
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            self.failed.emit(str(exc))
