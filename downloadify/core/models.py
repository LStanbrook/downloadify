"""Plain data structures shared across the core, GUI and web layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Track:
    """A single song pulled from a Spotify playlist."""

    name: str
    artists: list[str]
    # Used to filter out clearly-wrong YouTube matches (live versions, full
    # concerts, unrelated videos); None if Spotify didn't report a duration.
    duration_ms: int | None = None

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists) if self.artists else "Unknown Artist"

    @property
    def search_query(self) -> str:
        """Query string used for the YouTube search, e.g. 'Artist - Track'."""
        return f"{self.artist_str} - {self.name}"

    @property
    def display_name(self) -> str:
        return f"{self.artist_str} - {self.name}"


@dataclass
class PlaylistInfo:
    """A resolved Spotify playlist: its display name and ordered tracks."""

    playlist_id: str
    name: str
    tracks: list[Track] = field(default_factory=list)
    # True only when the track list came from the zero-config embed-page
    # fallback and hit its ~100-track cap, so some tracks may be missing.
    possibly_truncated: bool = False
    # How many raw entries the embed page actually returned before any were
    # filtered out (e.g. unavailable/local tracks with no title). Used as
    # the resume offset when extending past the cap -- `len(tracks)` isn't
    # safe to use for that once filtering can make it diverge from Spotify's
    # own notion of how many entries were already consumed.
    embed_raw_count: int = 0


@dataclass
class PlaylistSummary:
    """One entry in the logged-in user's own playlist library (see `SpotifyClient.list_my_playlists`)."""

    playlist_id: str
    name: str
    track_count: int
    owner: str = ""

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/playlist/{self.playlist_id}"


class TrackStatus(str, Enum):
    PENDING = "pending"
    SEARCHING = "searching"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"


@dataclass
class TrackResult:
    """Outcome of processing a single track, used for the final summary."""

    track: Track
    status: TrackStatus
    file_path: str | None = None
    error: str | None = None


@dataclass
class PlaylistDownloadSummary:
    playlist_name: str
    output_folder: str
    results: list[TrackResult] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.status == TrackStatus.DONE)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TrackStatus.FAILED)

    @property
    def total(self) -> int:
        return len(self.results)
