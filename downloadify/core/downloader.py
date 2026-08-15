"""Thin wrapper around yt-dlp for downloading a single video as an MP3."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yt_dlp

from downloadify import config


class DownloadError(RuntimeError):
    """Raised when yt-dlp fails to download or convert a track."""


class _YdlLogger:
    """Routes yt-dlp's internal logging into our own log callback."""

    def __init__(self, log_callback: Callable[[str], None] | None) -> None:
        self._log = log_callback or (lambda _msg: None)

    def debug(self, msg: str) -> None:
        # yt-dlp sends a lot of noisy [debug] lines; skip those.
        if msg.startswith("[debug] "):
            return
        self._log(msg)

    def info(self, msg: str) -> None:
        self._log(msg)

    def warning(self, msg: str) -> None:
        self._log(f"WARNING: {msg}")

    def error(self, msg: str) -> None:
        self._log(f"ERROR: {msg}")


def download_audio_as_mp3(
    video_url: str,
    output_dir: Path,
    filename_base: str,
    log_callback: Callable[[str], None] | None = None,
) -> Path:
    """
    Download `video_url`'s audio, converted to MP3, into `output_dir` as
    `<filename_base>.mp3`. Returns the resulting file path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(output_dir / f"{filename_base}.%(ext)s")
    final_path = output_dir / f"{filename_base}.{config.AUDIO_FORMAT}"

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _YdlLogger(log_callback),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": config.AUDIO_FORMAT,
                "preferredquality": config.AUDIO_QUALITY_KBPS,
            }
        ],
        # YouTube's bot-check ("Sign in to confirm you're not a bot") is
        # tied to the web client's stricter token requirements and
        # disproportionately hits requests from datacenter/VPS IPs like a
        # hosted deployment's. The android/ios clients use a different auth
        # flow that isn't subject to the same check, so trying those first
        # avoids it in most cases without needing cookies at all; "web" stays
        # as a last-resort fallback for anything those two can't resolve.
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
    }
    if config.FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = config.FFMPEG_LOCATION
    if config.YOUTUBE_COOKIES_FILE:
        ydl_opts["cookiefile"] = config.YOUTUBE_COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(str(exc)) from exc

    if not final_path.exists():
        raise DownloadError(
            f"yt-dlp finished but no output file was found at {final_path}. "
            "Is ffmpeg installed and on your PATH?"
        )

    return final_path
