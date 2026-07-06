"""
Central configuration for Downloadify.

Loads optional settings from a `.env` file (see `.env.example`) and exposes
constants used across the GUI, web and core modules so nothing is hard-coded
in more than one place.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load a .env file if present (safe no-op if it doesn't exist).
load_dotenv()

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default root folder where "<playlist_name>/<artist - track>.mp3" trees go.
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "downloadify_downloads"

# --------------------------------------------------------------------------
# Spotify
# --------------------------------------------------------------------------

# Downloadify reads playlists from Spotify's public embed page by default --
# no login, no API key, no setup of any kind. Its one limitation is that it
# only exposes the first ~100 tracks of a playlist.
#
# These two are OPTIONAL. If set (get a free pair at
# https://developer.spotify.com/dashboard), Downloadify uses them purely to
# fetch the *remaining* tracks of playlists over the 100-track cap via the
# official, fully-paginated Spotify Web API. Each user of this app is meant
# to set their own -- see the README before hard-coding/sharing a single key,
# since Spotify rate-limits per credential and a shared key won't scale.
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

SPOTIFY_OAUTH_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

SPOTIFY_EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
SPOTIFY_EMBED_TRACK_LIMIT = 100

# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------

YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

# A realistic desktop-browser User-Agent makes both Spotify's and YouTube's
# public pages far less likely to serve a stripped-down / bot-detection page.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT_SECONDS = 15

# --------------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------------

AUDIO_FORMAT = "mp3"
AUDIO_QUALITY_KBPS = "192"

# Optional explicit folder containing ffmpeg/ffprobe. Only needed if yt-dlp
# reports "ffprobe and ffmpeg not found" despite ffmpeg being installed --
# most commonly right after installing it, before restarting the terminal/
# VS Code so it picks up the updated PATH. See README for details.
FFMPEG_LOCATION = os.getenv("FFMPEG_LOCATION", "").strip()

# Cap on how many tracks are downloaded at once. yt-dlp + ffmpeg are CPU/IO
# heavy, and hammering YouTube with too many concurrent requests increases
# the odds of getting throttled, so a small number is deliberately used.
MAX_CONCURRENT_DOWNLOADS = 3
