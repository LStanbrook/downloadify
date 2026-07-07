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

A third case exists that neither of the above can ever solve: personalized/
algorithmic playlists (Discover Weekly, a Daily Mix, Release Radar, ...) and
a user's own private playlists have no public, logged-out identity at all --
Spotify only serves them to the owning account, official API included. The
embed page reports these with a "soft 404" (see `_PersonalizedPlaylistError`
below), which is the one case where `spotify_auth`'s optional user login is
used as a fallback.
"""

from __future__ import annotations

import json
import re

import requests

from downloadify import config
from downloadify.core import spotify_auth
from downloadify.core.models import PlaylistInfo, PlaylistSummary, Track
from downloadify.core.utils import extract_playlist_id

# Spotify renamed the playlist object's track-list field from `tracks` to
# `items` sometime after this app's initial development (confirmed live:
# the old `tracks` field/`.../tracks` sub-endpoint now 404s/403s, replaced
# by an identically-shaped `items`/`.../items`). Each entry's own `track`
# field was renamed to `item` the same way. Discovered by testing against a
# real logged-in account -- kept here since the embed-page default method is
# unaffected (different data source entirely) and this is easy to miss.
_PLAYLIST_FIELDS = "name,items.next,items.items(item(name,artists(name),duration_ms))"
_TRACKS_PAGE_FIELDS = "next,items(item(name,artists(name),duration_ms))"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)


class SpotifyPlaylistError(RuntimeError):
    """Raised when the playlist itself can't be read (private/deleted/etc.)."""


class _PersonalizedPlaylistError(Exception):
    """
    Internal signal only -- raised when the embed page's soft-404 pattern
    indicates a playlist with no public identity (personalized/algorithmic,
    or private), caught by `fetch_playlist` to try the user-login fallback
    before giving up.
    """


class SpotifyClient:
    """Stateless-ish helper for turning a playlist URL into a `PlaylistInfo`."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(config.DEFAULT_HTTP_HEADERS)

    # -- Public API ----------------------------------------------------

    def fetch_playlist(self, playlist_url: str) -> PlaylistInfo:
        """Resolve a public playlist URL into a `PlaylistInfo` with all tracks."""
        playlist_id = extract_playlist_id(playlist_url)
        try:
            playlist = self._fetch_via_embed_page(playlist_id)
        except _PersonalizedPlaylistError:
            playlist = self._fetch_via_user_token(playlist_id)

        can_extend = spotify_auth.is_logged_in() or (
            config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET
        )
        if playlist.possibly_truncated and can_extend:
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

    def list_my_playlists(self) -> list[PlaylistSummary]:
        """
        List the logged-in user's own playlist library -- everything Spotify
        shows them in their library sidebar, which for many accounts
        includes their personalized playlists (Discover Weekly, Daily Mix,
        Release Radar) alongside their own created/followed ones.

        This exists specifically so a playlist that can't be resolved by
        pasting its link (e.g. Spotify doesn't expose Blend playlists via
        the Web API even to a logged-in participant) can still be found if
        it shows up here -- and if it *doesn't* show up here either, that's
        a reliable sign Spotify simply won't hand it over via the API to
        anyone, not something Downloadify can work around.
        """
        token = spotify_auth.get_valid_access_token()
        if not token:
            raise SpotifyPlaylistError("Log in with Spotify first to browse your playlists.")

        headers = {"Authorization": f"Bearer {token}"}
        summaries: list[PlaylistSummary] = []
        url = f"{config.SPOTIFY_API_BASE}/me/playlists"
        params: dict | None = {"limit": 50}
        while url:
            resp = self._session.get(url, params=params, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            payload = resp.json()
            for item in payload.get("items", []):
                if not item or not item.get("id"):
                    continue
                owner = (item.get("owner") or {}).get("display_name") or ""
                summaries.append(
                    PlaylistSummary(
                        playlist_id=item["id"],
                        name=item.get("name") or "Untitled",
                        track_count=(item.get("items") or {}).get("total", 0),
                        owner=owner,
                    )
                )
            url = payload.get("next")
            params = None  # `next` is already a fully-formed URL with its own query string.

        return summaries

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
            raise _PersonalizedPlaylistError()

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
            # Checked against the *raw* page (before dropping title-less/
            # unavailable entries) and against a threshold below the actual
            # cap -- see SPOTIFY_EMBED_TRUNCATION_THRESHOLD for why neither
            # of those can just be an exact `== 100` check on `tracks`.
            possibly_truncated=len(raw_tracks) >= config.SPOTIFY_EMBED_TRUNCATION_THRESHOLD,
            embed_raw_count=len(raw_tracks),
        )

    # -- Fallback for playlists with no public identity (personalized/private) --

    def _fetch_via_user_token(self, playlist_id: str) -> PlaylistInfo:
        token = spotify_auth.get_valid_access_token()
        if not token:
            raise SpotifyPlaylistError(
                "This looks like a personalized playlist (Discover Weekly, a "
                "Daily Mix, Release Radar, etc.) or a private playlist -- "
                "these have no public identity, so Spotify only returns them "
                "to their logged-in owner. Log in with Spotify (see the "
                "login option in the app) and try again."
            )

        headers = {"Authorization": f"Bearer {token}"}
        resp = self._session.get(
            f"{config.SPOTIFY_API_BASE}/playlists/{playlist_id}",
            params={"fields": _PLAYLIST_FIELDS},
            headers=headers,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        if resp.status_code == 404:
            raise SpotifyPlaylistError(
                "Playlist not found, even logged in. Double-check the link, "
                "or make sure you're logged into the Spotify account this "
                "playlist actually belongs to."
            )
        resp.raise_for_status()
        payload = resp.json()

        name = payload.get("name") or f"Playlist {playlist_id}"
        tracks: list[Track] = []
        tracks_page = payload.get("items", {})
        self._append_items(tracks_page.get("items", []), tracks)
        next_url = tracks_page.get("next")
        while next_url:
            page_resp = self._session.get(next_url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
            page_resp.raise_for_status()
            page = page_resp.json()
            self._append_items(page.get("items", []), tracks)
            next_url = page.get("next")

        return PlaylistInfo(playlist_id=playlist_id, name=name, tracks=tracks)

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
        # Prefer the user's own login: testing showed it can read playlist
        # contents in cases where an app-only Client Credentials token gets
        # a flat 401/403 from Spotify, which has gotten stricter about what
        # bare app tokens can read. Client Credentials remains the fallback
        # for anyone who hasn't logged in.
        token = spotify_auth.get_valid_access_token() or self._get_client_credentials_token()
        if not token:
            return  # No usable credentials -- silently keep the embed-page result.

        url = f"{config.SPOTIFY_API_BASE}/playlists/{playlist.playlist_id}/items"
        params: dict | None = {
            "fields": _TRACKS_PAGE_FIELDS,
            # Resume from where the embed page's *raw* page left off, not
            # `len(playlist.tracks)` -- those can diverge once unavailable
            # entries get filtered out of the latter.
            "offset": playlist.embed_raw_count,
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
            track = (item or {}).get("item")
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
