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

# The embed page doesn't report a playlist's true total, so truncation has
# to be inferred from how many tracks it handed back. A raw page anywhere
# near the observed ~100-track ceiling is treated as "might be truncated"
# rather than requiring an exact match -- Spotify doesn't always fill the
# page completely (e.g. region-unavailable tracks can be dropped from the
# response), so a large playlist can land just under the cap while still
# being incomplete. Better to warn/attempt an extension unnecessarily on a
# rare edge case than to silently under-report a big playlist as "done".
SPOTIFY_EMBED_TRUNCATION_THRESHOLD = 90

# Optional user login (Authorization Code + PKCE -- no client secret needed).
# This is the *only* way to read personalized/algorithmic playlists
# (Discover Weekly, a Daily Mix, Release Radar, ...) or a user's own private
# playlists, since those have no public, logged-out identity at all -- not
# even Spotify's own official API can return them without the owning
# account's login. Everything else (public, editorial, user-created
# playlists) keeps working with zero login via the embed page above; this
# is only used as a fallback when that reports a playlist it can't resolve.
#
# Uses the same SPOTIFY_CLIENT_ID configured above. The redirect URI below
# must exactly match what's registered in the Spotify app's dashboard
# (the README has both use the same http://127.0.0.1:8080 value).
SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8080"
SPOTIFY_REDIRECT_PORT = 8080
SPOTIFY_AUTH_SCOPE = "playlist-read-private playlist-read-collaborative"

# Where the login's refresh token is cached locally so the user only has to
# log in once. Contains sensitive data -- never commit this file (it's in
# .gitignore already).
SPOTIFY_TOKEN_CACHE_PATH = PROJECT_ROOT / ".spotify_token_cache.json"

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
