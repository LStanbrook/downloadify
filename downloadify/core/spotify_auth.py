"""
Optional Spotify user login (Authorization Code + PKCE).

Downloadify's default playlist lookup (see `spotify_client.py`) needs no
login at all -- it works for any public, editorial, or user-created
playlist. But *personalized/algorithmic* playlists (Discover Weekly, a
Daily Mix, Release Radar, ...) and a user's own private playlists have no
public, logged-out identity whatsoever -- Spotify won't return them to
anyone but the owning account, official API included. Logging in is the
only way to read those, so this module exists purely as an opt-in fallback
for that case; nothing here runs unless the embed-page lookup reports a
playlist it can't resolve, or the user explicitly asks to log in.

Uses PKCE (Proof Key for Code Exchange), which needs only a Client ID and
no client secret -- the flow Spotify itself recommends for apps that can't
keep a secret confidential (a desktop app or CLI, as opposed to a server):
https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow

The one-time browser login is captured by a short-lived local HTTP server
listening on the redirect URI (127.0.0.1, closed again as soon as the
redirect arrives), the same pattern tools like `gh auth login` use. The
resulting refresh token is cached locally (see `config.SPOTIFY_TOKEN_CACHE_PATH`)
so subsequent runs don't need another login.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass

import requests

from downloadify import config

_LOGIN_TIMEOUT_SECONDS = 180


class SpotifyLoginError(RuntimeError):
    """Raised when the login flow can't complete."""


@dataclass
class _TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float  # unix timestamp, refreshed a little early

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> _TokenSet:
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _CallbackServer(http.server.HTTPServer):
    callback_result: dict[str, str | None] | None = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the one redirect Spotify sends back with `?code=...` (or `?error=...`)."""

    def do_GET(self) -> None:  # noqa: N802 - required name for BaseHTTPRequestHandler
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        server: _CallbackServer = self.server  # type: ignore[assignment]
        server.callback_result = {
            "code": params.get("code", [None])[0],
            "error": params.get("error", [None])[0],
        }
        ok = bool(server.callback_result["code"])
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        message = (
            "Logged in. You can close this tab and return to Downloadify."
            if ok
            else "Login failed or was cancelled. You can close this tab."
        )
        self.wfile.write(f"<html><body><h2>Downloadify</h2><p>{message}</p></body></html>".encode())

    def log_message(self, format_str: str, *args) -> None:  # noqa: A002 - silence default access logging
        pass


def _run_login_flow() -> _TokenSet:
    if not config.SPOTIFY_CLIENT_ID:
        raise SpotifyLoginError(
            "Set SPOTIFY_CLIENT_ID in your .env before logging in -- see the README."
        )

    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    try:
        server = _CallbackServer(("127.0.0.1", config.SPOTIFY_REDIRECT_PORT), _CallbackHandler)
    except OSError as exc:
        raise SpotifyLoginError(
            f"Couldn't start the local login listener on port "
            f"{config.SPOTIFY_REDIRECT_PORT} ({exc}). Close whatever else "
            f"might be using that port and try again."
        ) from exc
    server.timeout = 1

    auth_params = {
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "scope": config.SPOTIFY_AUTH_SCOPE,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    authorize_url = f"{config.SPOTIFY_AUTHORIZE_URL}?{urllib.parse.urlencode(auth_params)}"

    try:
        webbrowser.open(authorize_url)

        deadline = time.time() + _LOGIN_TIMEOUT_SECONDS
        while time.time() < deadline and server.callback_result is None:
            server.handle_request()
        if server.callback_result is None:
            raise SpotifyLoginError(
                "Login timed out waiting for a response from Spotify."
            )
    finally:
        server.server_close()

    result = server.callback_result
    if not result or result.get("error") or not result.get("code"):
        raise SpotifyLoginError(
            f"Spotify login failed: {result.get('error') if result else 'no response'}"
        )

    resp = requests.post(
        config.SPOTIFY_OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": config.SPOTIFY_REDIRECT_URI,
            "client_id": config.SPOTIFY_CLIENT_ID,
            "code_verifier": verifier,
        },
        timeout=config.REQUEST_TIMEOUT_SECONDS,
    )
    if not resp.ok:
        raise SpotifyLoginError(f"Spotify rejected the login: {resp.text}")
    payload = resp.json()
    token_set = _TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_at=time.time() + payload.get("expires_in", 3600) - 30,
    )
    _save_token(token_set)
    return token_set


def _save_token(token_set: _TokenSet) -> None:
    config.SPOTIFY_TOKEN_CACHE_PATH.write_text(json.dumps(token_set.to_dict()), encoding="utf-8")


def _load_token() -> _TokenSet | None:
    if not config.SPOTIFY_TOKEN_CACHE_PATH.exists():
        return None
    try:
        raw = json.loads(config.SPOTIFY_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
        return _TokenSet.from_dict(raw)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _refresh(token_set: _TokenSet) -> _TokenSet | None:
    try:
        resp = requests.post(
            config.SPOTIFY_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_set.refresh_token,
                "client_id": config.SPOTIFY_CLIENT_ID,
            },
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        new_token_set = _TokenSet(
            access_token=payload["access_token"],
            # Spotify doesn't always return a new refresh_token; keep the old one if so.
            refresh_token=payload.get("refresh_token", token_set.refresh_token),
            expires_at=time.time() + payload.get("expires_in", 3600) - 30,
        )
        _save_token(new_token_set)
        return new_token_set
    except (requests.RequestException, ValueError, KeyError):
        return None


def is_logged_in() -> bool:
    return _load_token() is not None


def get_valid_access_token() -> str | None:
    """A usable access token if the user has logged in, refreshing it if needed."""
    token_set = _load_token()
    if not token_set:
        return None
    if time.time() >= token_set.expires_at:
        token_set = _refresh(token_set)
        if not token_set:
            return None
    return token_set.access_token


def login_interactive() -> None:
    """Open a browser for the user to log in; blocks until it succeeds or fails."""
    _run_login_flow()


def logout() -> None:
    config.SPOTIFY_TOKEN_CACHE_PATH.unlink(missing_ok=True)
