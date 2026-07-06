"""
Fetches track lists from *public* Spotify playlists without any user login,
OAuth flow, or API key required by default.

The default (and only required) method scrapes Spotify's own public *embed*
page (``open.spotify.com/embed/playlist/{id}``), which ships the track list
as plain JSON inside the page's `__NEXT_DATA__` blob -- the same data
Spotify's own embeddable player renders from. It's just a normal webpage
load, so it works identically for any number of people running this app,
with no shared credential and no login. Its one limitation: it only exposes
the first ~100 tracks.

For playlists larger than that, an OPTIONAL enhancement is available: if the
user has set their own free `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`
(Client Credentials OAuth grant -- still no login, just an app-level token),
Downloadify fetches the *remaining* tracks via the official, fully-paginated
Spotify Web API. This is opt-in and per-user by design: earlier testing
during development showed that even a valid, legitimately-obtained anonymous
token can get `api.spotify.com` itself rate-limited for many hours, so that
endpoint is deliberately not something this app depends on by default, and
never something it shares a single embedded credential for across users.
"""

from __future__ import annotations

import json
import re

import requests

from downloadify import config
from downloadify.core.models import PlaylistInfo, Track
from downloadify.core.utils import extract_playlist_id

_TRACKS_PAGE_FIELDS = "next,items(track(name,artists(name),duration_ms))"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)


class SpotifyPlaylistError(RuntimeError):
    """Raised when the playlist itself can't be read (private/deleted/etc.)."""


class SpotifyClient:
    """Stateless-ish helper for turning a playlist URL into a `PlaylistInfo`."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(config.DEFAULT_HTTP_HEADERS)

    # -- Public API ----------------------------------------------------

    def fetch_playlist(self, playlist_url: str) -> PlaylistInfo:
        """Resolve a public playlist URL into a `PlaylistInfo` with all tracks."""
        playlist_id = extract_playlist_id(playlist_url)
        playlist = self._fetch_via_embed_page(playlist_id)

        if playlist.possibly_truncated and config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET:
            try:
                self._extend_with_official_api(playlist)
            except requests.RequestException:
                # Keep whatever the embed page already gave us rather than
                # failing the whole playlist over an optional enhancement.
                pass

        if not playlist.tracks:
            raise SpotifyPlaylistError(
                "That playlist appears to be empty, private, or unavailable. "
                "Downloadify only works with *public* Spotify playlists."
            )
        return playlist

    # -- Default, zero-config method: public embed page --------------------

    def _fetch_via_embed_page(self, playlist_id: str) -> PlaylistInfo:
        url = config.SPOTIFY_EMBED_URL.format(playlist_id=playlist_id)
        resp = self._session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        if resp.status_code == 404:
            raise SpotifyPlaylistError(
                "Playlist not found. Double-check the link is public and correct."
            )
        resp.raise_for_status()

        match = _NEXT_DATA_RE.search(resp.text)
        if not match:
            raise SpotifyPlaylistError("Could not read playlist data from Spotify.")

        try:
            next_data = json.loads(match.group(1))
            page_props = next_data["props"]["pageProps"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SpotifyPlaylistError(
                "Could not parse playlist data from Spotify (its page layout "
                "may have changed)."
            ) from exc

        if page_props.get("status") == 404:
            # Spotify returns this "soft 404" (HTTP 200, but the page's own
            # data says not-found) for links it won't resolve without a
            # login -- notably personalized/algorithmic playlists like
            # Discover Weekly, Daily Mix or Release Radar, which have no
            # fixed public identity and differ per account. A genuinely
            # public, user-created playlist wouldn't hit this at all.
            raise SpotifyPlaylistError(
                "Spotify says this playlist doesn't exist for a logged-out "
                "visitor. If this is a personalized playlist (Discover "
                "Weekly, a Daily Mix, Release Radar, etc.), it's tied to "
                "your account and Downloadify can't access it without "
                "logging in, which it deliberately never does. Public, "
                "user-created or editorial playlists work fine."
            )

        try:
            entity = page_props["state"]["data"]["entity"]
        except (KeyError, TypeError) as exc:
            raise SpotifyPlaylistError(
                "Could not parse playlist data from Spotify (its page layout "
                "may have changed)."
            ) from exc

        if entity.get("type") != "playlist":
            raise SpotifyPlaylistError("That link doesn't point to a playlist.")

        name = entity.get("name") or entity.get("title") or f"Playlist {playlist_id}"
        raw_tracks = entity.get("trackList") or []
        tracks = [
            Track(
                name=item["title"],
                artists=[a.strip() for a in item.get("subtitle", "").split(",") if a.strip()],
                duration_ms=item.get("duration"),
            )
            for item in raw_tracks
            if item.get("title")
        ]

        return PlaylistInfo(
            playlist_id=playlist_id,
            name=name,
            tracks=tracks,
            possibly_truncated=len(tracks) >= config.SPOTIFY_EMBED_TRACK_LIMIT,
        )

    # -- Optional enhancement: fill in tracks past the embed page's cap --

    def _get_client_credentials_token(self) -> str | None:
        try:
            resp = self._session.post(
                config.SPOTIFY_OAUTH_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET),
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json().get("access_token")
        except (requests.RequestException, ValueError):
            return None

    def _extend_with_official_api(self, playlist: PlaylistInfo) -> None:
        token = self._get_client_credentials_token()
        if not token:
            return  # Bad/missing credentials -- silently keep the embed-page result.

        url = f"{config.SPOTIFY_API_BASE}/playlists/{playlist.playlist_id}/tracks"
        params: dict | None = {
            "fields": _TRACKS_PAGE_FIELDS,
            "offset": len(playlist.tracks),
            "limit": 100,
        }
        while url:
            resp = self._session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            page = resp.json()
            self._append_items(page.get("items", []), playlist.tracks)
            url = page.get("next")
            params = None  # `next` is already a fully-formed URL with its own query string.

        playlist.possibly_truncated = False

    @staticmethod
    def _append_items(items: list[dict], out: list[Track]) -> None:
        for item in items:
            track = (item or {}).get("track")
            if not track or not track.get("name"):
                # Local files / removed tracks show up as null entries.
                continue
            artists = [a["name"] for a in track.get("artists", []) if a.get("name")]
            out.append(
                Track(
                    name=track["name"],
                    artists=artists,
                    duration_ms=track.get("duration_ms"),
                )
            )
