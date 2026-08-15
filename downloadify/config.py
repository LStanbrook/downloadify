"""
Central configuration for Downloadify.

Loads optional settings from a `.env` file (see `.env.example`) and exposes
constants used across the GUI, web and core modules so nothing is hard-coded
in more than one place.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    # Running as a packaged executable (PyInstaller). `__file__` would
    # resolve inside the one-file build's temporary extraction directory,
    # which is wiped when the app closes -- downloads and the login token
    # cache need to live next to the actual .exe instead, so they persist.
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load a .env file if present (safe no-op if it doesn't exist). Pointed
# explicitly at PROJECT_ROOT when packaged, since the frozen exe's working
# directory isn't guaranteed to be where the user placed `.env`.
load_dotenv(PROJECT_ROOT / ".env" if getattr(sys, "frozen", False) else None)

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
# since Spotify rate-limits per credential and a shared key won't scale, and
# (as of Spotify's February 2026 policy change) Development Mode caps
# user-authorizing logins at just 5 accounts per Client ID -- a baked-in
# shared key was tried for the packaged exe and deliberately reverted once
# that made the login feature effectively a 5-person allowlist rather than
# something that works for anyone who downloads the app. See the README's
# "Publishing this app" section for the full reasoning.
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
# YouTube's own source audio tops out well below this (typically ~128-160kbps
# Opus/AAC), so this doesn't add fidelity that wasn't there to begin with --
# but encoding at 320 rather than a lower target avoids throwing away any
# more of it than YouTube's own compression already did.
AUDIO_QUALITY_KBPS = "320"

# Optional path to a Netscape-format cookies.txt, passed straight to yt-dlp.
# Needed on hosts where YouTube's bot-check ("Sign in to confirm you're not
# a bot") blocks requests outright regardless of player client -- disproportionately
# hits datacenter/VPS IPs. Deliberately meant to hold an anonymous (logged-out)
# session's cookies, not a real account's -- never commit this file or point
# it at cookies from a real login.
YOUTUBE_COOKIES_FILE = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()

# Optional explicit folder containing ffmpeg/ffprobe. Only needed if yt-dlp
# reports "ffprobe and ffmpeg not found" despite ffmpeg being installed --
# most commonly right after installing it, before restarting the terminal/
# VS Code so it picks up the updated PATH. See README for details.
FFMPEG_LOCATION = os.getenv("FFMPEG_LOCATION", "").strip()

# Cap on how many tracks are downloaded at once. yt-dlp + ffmpeg are CPU/IO
# heavy, and hammering YouTube with too many concurrent requests increases
# the odds of getting throttled, so a small number is deliberately used.
MAX_CONCURRENT_DOWNLOADS = 3

# --------------------------------------------------------------------------
# Public web deployment
# --------------------------------------------------------------------------

# When true, the web app runs in "public mode" -- one server shared by every
# visitor rather than a single local user. Several things that are fine for
# a lone local user become unsafe or meaningless at that point, so this flag
# gates them off: a client-supplied output path (path traversal), the
# Spotify login (would route strangers' own Spotify sessions through
# infrastructure the operator controls), and unbounded concurrent jobs
# (resource exhaustion) are all disabled, and finished downloads are zipped
# and streamed back to the browser instead of saved to a folder on the
# server. Set via the PUBLIC_DEPLOYMENT env var on the hosted deployment
# only -- never set for local/desktop use.
PUBLIC_DEPLOYMENT = os.getenv("PUBLIC_DEPLOYMENT", "").strip().lower() in ("1", "true", "yes")

# Root folder for per-job temp directories, used only in public mode.
PUBLIC_JOBS_DIR = Path(
    os.getenv("PUBLIC_JOBS_DIR", "").strip() or str(Path(tempfile.gettempdir()) / "downloadify_jobs")
)

# How many playlist downloads may run at once, server-wide, in public mode.
PUBLIC_MAX_CONCURRENT_JOBS = int(os.getenv("PUBLIC_MAX_CONCURRENT_JOBS", "3"))

# How long a finished job's files stay on disk before being deleted, in
# public mode -- bounds disk usage on a shared host without racing a visitor
# who's mid-download.
PUBLIC_JOB_TTL_SECONDS = int(os.getenv("PUBLIC_JOB_TTL_SECONDS", str(60 * 60)))
