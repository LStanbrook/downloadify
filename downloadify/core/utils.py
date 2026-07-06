"""Small stateless helpers: filename sanitizing and Spotify URL parsing."""

from __future__ import annotations

import re

# Characters that are illegal (or awkward) in Windows/macOS/Linux filenames.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Accepts:
#   https://open.spotify.com/playlist/<id>
#   https://open.spotify.com/playlist/<id>?si=...
#   https://open.spotify.com/intl-en/playlist/<id>
#   spotify:playlist:<id>
_PLAYLIST_URL_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?playlist/|spotify:playlist:)"
    r"([a-zA-Z0-9]+)"
)


def sanitize_filename(name: str, max_length: int = 150) -> str:
    """Make `name` safe to use as a file or folder name on any OS."""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("", name)
    cleaned = cleaned.strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_length]


def extract_playlist_id(playlist_url: str) -> str:
    """Pull the raw playlist ID out of any accepted Spotify playlist link."""
    match = _PLAYLIST_URL_RE.search(playlist_url.strip())
    if not match:
        raise ValueError(
            "That doesn't look like a public Spotify playlist link. "
            "Expected something like "
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        )
    return match.group(1)
