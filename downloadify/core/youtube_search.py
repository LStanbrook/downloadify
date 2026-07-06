"""
Finds the best-matching YouTube video for a track by scraping the public
search results page -- no YouTube Data API key involved.

YouTube embeds the entire search results payload as a JSON blob assigned to
`var ytInitialData` inside a <script> tag on the results page. We pull that
blob out with a regex, parse it as JSON, and walk the (fairly deep, but
stable) structure down to each `videoRenderer` entry.

Rather than blindly taking the first result, the top handful of candidates
are scored against the expected artist/track name (and duration, when known
from Spotify) so that obviously-wrong results -- a reaction video, a full
concert, a completely different song that happens to rank nearby -- are
skipped instead of downloaded. If nothing clears the confidence bar, no
video is returned and the track is skipped rather than guessed at.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass

import requests

from downloadify import config

_YT_INITIAL_DATA_PATTERNS = (
    re.compile(r"var ytInitialData\s*=\s*(\{.+?\});", re.DOTALL),
    re.compile(r"window\[['\"]ytInitialData['\"]\]\s*=\s*(\{.+?\});", re.DOTALL),
    re.compile(r"ytInitialData\s*=\s*(\{.+?\})\s*;\s*</script>", re.DOTALL),
)

# How many top search results to actually consider scoring.
_MAX_CANDIDATES = 5

# Words that show up in real uploads' titles but say nothing about which
# song it is -- stripped out before comparing titles so they don't inflate
# or dilute the match score.
_TITLE_NOISE_WORDS = {
    "official", "video", "audio", "lyrics", "lyric", "music", "mv",
    "hd", "hq", "4k", "remastered", "remaster", "visualizer", "ft",
    "feat", "featuring", "explicit", "clean", "version", "topic",
}

# Minimum combined score (0-1) a candidate needs to be accepted at all.
_MIN_ACCEPT_SCORE = 0.45


class YouTubeSearchError(RuntimeError):
    """Raised when the search page can't be fetched or parsed at all."""


@dataclass
class _Candidate:
    video_id: str
    title: str
    duration_seconds: int | None


def _extract_initial_data(html: str) -> dict:
    for pattern in _YT_INITIAL_DATA_PATTERNS:
        match = pattern.search(html)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    raise YouTubeSearchError(
        "Could not parse YouTube's search results page (its layout may have "
        "changed)."
    )


def _parse_duration_seconds(length_text: dict | None) -> int | None:
    """Turn a videoRenderer's `lengthText` (e.g. {"simpleText": "3:34"}) into seconds."""
    if not length_text:
        return None
    simple = length_text.get("simpleText")
    if not simple or not re.fullmatch(r"[\d:]+", simple):
        return None
    parts = [int(p) for p in simple.split(":")]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _iter_candidates(data: dict):
    """Yield a `_Candidate` for every `videoRenderer` found in the search payload."""
    try:
        contents = data["contents"]["twoColumnSearchResultsRenderer"][
            "primaryContents"
        ]["sectionListRenderer"]["contents"]
    except (KeyError, TypeError):
        return

    for section in contents:
        items = section.get("itemSectionRenderer", {}).get("contents", [])
        for item in items:
            renderer = item.get("videoRenderer")
            if not renderer or not renderer.get("videoId"):
                continue
            title_runs = renderer.get("title", {}).get("runs", [])
            title = "".join(run.get("text", "") for run in title_runs)
            yield _Candidate(
                video_id=renderer["videoId"],
                title=title,
                duration_seconds=_parse_duration_seconds(renderer.get("lengthText")),
            )


def _normalize_tokens(text: str) -> set[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return {t for t in text.split() if t and t not in _TITLE_NOISE_WORDS}


def _title_score(candidate_title: str, artist: str, track_name: str) -> float:
    expected_tokens = _normalize_tokens(f"{artist} {track_name}")
    if not expected_tokens:
        return 0.0
    candidate_tokens = _normalize_tokens(candidate_title)
    overlap = len(expected_tokens & candidate_tokens)
    return overlap / len(expected_tokens)


def _duration_score(candidate_seconds: int | None, expected_ms: int | None) -> float:
    if candidate_seconds is None or not expected_ms:
        # Can't compare -- stay neutral rather than penalize or reward.
        return 0.5
    expected_seconds = expected_ms / 1000
    diff = abs(candidate_seconds - expected_seconds)
    if diff <= 5:
        return 1.0
    if diff <= 15:
        return 0.7
    if diff <= 30:
        return 0.35
    return 0.0


def _score(candidate: _Candidate, artist: str, track_name: str, expected_duration_ms: int | None) -> float:
    title_score = _title_score(candidate.title, artist, track_name)
    duration_score = _duration_score(candidate.duration_seconds, expected_duration_ms)
    # Title match matters most; duration is a strong secondary signal when
    # available (it's what catches live/extended/reaction versions that
    # otherwise share almost every title word with the real track).
    return title_score * 0.7 + duration_score * 0.3


def _fetch_search_results_html(query: str) -> str:
    try:
        resp = requests.get(
            config.YOUTUBE_SEARCH_URL,
            params={"search_query": query},
            headers=config.DEFAULT_HTTP_HEADERS,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        raise YouTubeSearchError(f"Failed to reach YouTube: {exc}") from exc


def find_best_video_url(
    artist: str,
    track_name: str,
    expected_duration_ms: int | None = None,
) -> str | None:
    """
    Search YouTube for `artist - track_name` and return the watch URL of the
    best-matching result among the top candidates, or None if nothing found
    is a confident enough match (in which case the caller should skip the
    track rather than download the wrong song).
    """
    html = _fetch_search_results_html(f"{artist} - {track_name}")
    data = _extract_initial_data(html)

    best_candidate: _Candidate | None = None
    best_score = -1.0
    for candidate in itertools.islice(_iter_candidates(data), _MAX_CANDIDATES):
        score = _score(candidate, artist, track_name, expected_duration_ms)
        if score > best_score:
            best_candidate, best_score = candidate, score
        if best_score >= 1.0:
            break

    if best_candidate is None or best_score < _MIN_ACCEPT_SCORE:
        return None
    return f"https://www.youtube.com/watch?v={best_candidate.video_id}"
