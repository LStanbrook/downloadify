# Downloadify

Paste a **public Spotify playlist link**, and Downloadify will:

1. Fetch every track (title + artist) from that playlist by reading Spotify's public embed page — no login, no API key, no OAuth flow required for regular public playlists. (Personalized playlists like Discover Weekly and private playlists need one optional, one-time login — see below.)
2. Search YouTube for `"artist - track name"` by scraping the public search results page (no YouTube Data API key).
3. Take the best-matching video result and download its audio with **yt-dlp**, converting it to **MP3**.
4. Save everything to `downloadify_downloads/<playlist name>/<artist - track>.mp3`.

It ships with two interchangeable front ends:

- **Desktop GUI** (PyQt6)
- **Web app** (FastAPI + a small HTML/JS frontend)

Both call into the same core pipeline, so behavior is identical either way.

---

## Project layout

```
downloadify/
├── main.py                     # Entrypoint: `python main.py --mode gui|web`
├── requirements.txt
├── requirements-web.txt         # Web-only deps for the hosted deployment (no PyQt6)
├── Dockerfile                    # Builds the hosted web app (see render.yaml)
├── render.yaml                   # Render Blueprint -- one-click hosted deployment
├── .env.example                 # Optional per-user Spotify credentials (playlists over 100 tracks)
├── downloadify/
│   ├── config.py                 # Paths, constants, .env loading
│   ├── core/
│   │   ├── models.py              # Track / PlaylistInfo / TrackResult dataclasses
│   │   ├── utils.py                # Filename sanitizing, playlist URL parsing
│   │   ├── spotify_client.py       # Public playlist lookup (no login)
│   │   ├── youtube_search.py       # HTML-scraped YouTube search
│   │   ├── downloader.py           # yt-dlp -> MP3 wrapper
│   │   └── pipeline.py             # Orchestrates the full async flow
│   ├── gui/
│   │   ├── main_window.py          # PyQt6 window
│   │   └── worker.py                # QThread running the async pipeline
│   └── web/
│       ├── server.py                # FastAPI routes + SSE log streaming
│       ├── job_manager.py           # Background job tracking
│       └── static/                  # index.html / app.js -- marketing homepage + embedded download tool
└── downloadify_downloads/        # Default output folder
```

---

## 1. Prerequisites

### Python

Python 3.10+ is required (the codebase uses modern type-hint syntax like `str | None`).

### FFmpeg (required — yt-dlp needs it to produce MP3s)

**Windows:**
1. Download a build from https://www.gyan.dev/ffmpeg/builds/ (the "release essentials" zip is enough).
2. Extract it, e.g. to `C:\ffmpeg`.
3. Add `C:\ffmpeg\bin` to your `PATH` (System Properties → Environment Variables → Path → New).
4. Open a **new** terminal and verify: `ffmpeg -version`.

Alternatively, with a package manager:
```powershell
winget install ffmpeg
# or
choco install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt update && sudo apt install ffmpeg
```

**After installing, you must fully close and reopen your terminal (or VS Code
entirely, not just a new tab) before `ffmpeg` is recognized** — installers
update your `PATH` at the OS level, but any already-running terminal keeps
the environment it started with. Verify with `ffmpeg -version` in the new
window.

If you'd rather not restart anything right now (or the download still fails
with `ffprobe and ffmpeg not found`), set `FFMPEG_LOCATION` in `.env` to the
folder containing `ffmpeg.exe`/`ffprobe.exe` instead — Downloadify will use
it directly regardless of `PATH`:
```
FFMPEG_LOCATION=C:\ffmpeg\bin
```

### yt-dlp

Installed automatically via `requirements.txt` (used as a Python library, not a separate CLI install).

---

## 2. Setup

Open this folder in VS Code, then in an integrated terminal:

```powershell
# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

`.env` is already created with blank values. It's only needed if you want to
enable full support for playlists over 100 tracks — see
[Getting full playlists over 100 tracks](#getting-full-playlists-over-100-tracks-optional)
below. Nothing needs to be set for normal use.

---

## 3. Running

### Desktop GUI (default)

```powershell
python main.py
# same as:
python main.py --mode gui
```

This opens a window with:
- A playlist URL field
- An output folder field with a **Browse…** button
- **Download Playlist** / **Cancel** buttons
- A live progress bar (resets to empty once the run finishes) and scrolling log window
- A **Show full logs** checkbox (see [Concise vs. full logs](#concise-vs-full-logs) below)
- A **Log in with Spotify** button in the top-right, only needed for personalized/private playlists (see [Personalized and private playlists](#personalized-and-private-playlists-optional-login))
- A **Browse my playlists…** button (enabled once logged in) to pick a playlist from your own library instead of pasting a link
- Error dialogs if something goes wrong (bad URL, playlist unreachable, network errors, etc.)

### Web app

```powershell
python main.py --mode web
# optionally: python main.py --mode web --host 0.0.0.0 --port 8000
```

Then open **http://127.0.0.1:8000** in a browser — the download tool is
embedded directly on the homepage (scroll to, or click **Try it in your
browser** to jump to, the "Try it right now" section). Paste a playlist URL,
optionally change the output folder (this is a path *on the machine running
the server*), and click **Download Playlist**. Logs stream live into the page (also printed
to the terminal running `uvicorn`), with the same **Show full logs** checkbox,
self-resetting progress bar, and optional **Log in with Spotify** /
**Browse my playlists…** buttons as the desktop app.

#### Hosting it publicly, for other people to use

Running `python main.py --mode web` as above is meant for **one person on
their own machine** — the output folder is a real filesystem path and the
optional Spotify login is a single, shared login for whoever's running it.
Neither is safe to expose to strangers on a shared, public server as-is.

Setting the `PUBLIC_DEPLOYMENT=true` environment variable switches the same
app into a **public mode** built for exactly that instead:

- The output-folder field is removed. Every job writes into its own
  server-generated temp folder — a client can never influence where files
  are written (closes a path-traversal hole a raw client-supplied path
  would otherwise open).
- Finished downloads are zipped and served as a **Download ZIP** button in
  the browser, instead of being left in a folder on the server.
- A job's files are deleted automatically `PUBLIC_JOB_TTL_SECONDS` (default
  3600) after it finishes, so disk usage on a shared host stays bounded.
- No more than `PUBLIC_MAX_CONCURRENT_JOBS` (default 3) playlists download
  at once, server-wide — anyone else gets a clear "try again in a minute"
  instead of the server being overwhelmed.
- The Spotify login and "Browse my playlists…" endpoints aren't registered
  at all — routing a stranger's own Spotify session through infrastructure
  you control is a meaningfully bigger trust ask than a login that never
  leaves their own machine, so public mode simply doesn't offer it. Regular
  public playlists work exactly as normal.

**Deploying to [Render](https://render.com) (recommended — free tier available):**

1. Push this repo to your own GitHub account (or fork it).
2. In the [Render dashboard](https://dashboard.render.com), click
   **New +** → **Blueprint**, and point it at your repo. Render reads
   [`render.yaml`](render.yaml) in the repo root and configures everything
   automatically (Docker build, port, `PUBLIC_DEPLOYMENT=true`, and the
   other settings above) — no manual setup needed.
3. Click **Apply**. The first build takes a few minutes (installing ffmpeg
   + Python deps into the image); Render gives you a
   `https://<service-name>.onrender.com` URL once it's live.

Free-tier Render services sleep after 15 minutes of inactivity, so the first
request after a quiet period takes 30-60 seconds to spin back up — normal,
not a bug.

This also works on any other host that can run a Dockerfile (Railway,
Fly.io, a VPS, ...) — build [`Dockerfile`](Dockerfile) and set the same
environment variables shown in `render.yaml`. The image excludes the desktop
GUI's dependencies (see `requirements-web.txt`) since a headless container
has no display for PyQt6 to attach to.

### Concise vs. full logs

By default, the log window only shows one line per track (`[SUCCESS] Artist - Track`
or `[FAIL] Artist - Track -- reason`) plus a handful of overall milestones
(playlist resolved, done summary). Check **Show full logs** to additionally see
every yt-dlp/search detail per track -- useful for debugging why a specific
track failed, but noisy for a normal run of a dozen-plus tracks.

### Re-downloading a playlist

If you download the same playlist again into the same output folder,
Downloadify deletes that playlist's existing folder first and replaces it
with a fresh copy, rather than merging old and new files together. This
keeps the folder from accumulating stale files (e.g. tracks later removed
from the playlist, or leftovers from a run that used different filenames).
If you've manually added your own files into a Downloadify-managed playlist
folder, move them out first -- they'll be deleted along with everything else
in that folder on the next run.

### VS Code

Two ready-made debug configurations are included in `.vscode/launch.json`:
- **Downloadify: Desktop GUI**
- **Downloadify: Web App**

Open the "Run and Debug" panel and pick one, or just run the `python main.py …`
commands above in the integrated terminal.

---

## 4. How it works

### Spotify lookup — no login, no API key, by default

Downloadify's default (and only required) method reads playlists straight off
Spotify's own public **embed page**
(`open.spotify.com/embed/playlist/<id>`) — the same lightweight page Spotify
serves when a playlist is embedded on a blog or in a tweet. That page ships
its track list as plain JSON inside the page's own markup, so reading it is
just a normal webpage fetch: no login, no API key, no per-app credential of
any kind, and it works identically no matter how many people are running
Downloadify at once, since each person's copy just loads its own copy of that
public page. Its one limitation: **it only exposes the first ~100 tracks** of
a playlist. When that happens, Downloadify logs a clear note and keeps going
with the tracks it does have rather than failing.

This design was deliberate, not just "the simple option" — see
[Why not the official Spotify API by default?](#why-not-the-official-spotify-api-by-default)
below for what was tried and ruled out.

#### Getting full playlists over 100 tracks (optional)

If you regularly work with large playlists, you can optionally add your own
free Spotify API credentials. When set, Downloadify uses them *only* to fetch
whatever tracks fall past the 100-track cap via the official Spotify Web API
— the embed page is still tried first either way.

**A caveat worth knowing**: Spotify has been progressively locking down what
an app-only (Client Credentials) token can read -- its dedicated
track-listing endpoint returns `401 Unauthorized` for Client Credentials on
at least some playlists (confirmed by testing against a real editorial
playlist). Because of this, if you're logged in (see
[Personalized and private playlists](#personalized-and-private-playlists-optional-login)
below), Downloadify prefers your login over the Client Credentials key for
this extension, since testing confirmed it reliably reads far larger
playlists (2500+ tracks) that the app-only key can't. If you're not logged
in, it falls back to Client Credentials, which may or may not succeed
depending on the playlist. Either way, this always degrades gracefully --
worst case, you keep the first 100 tracks from the embed page rather than
the app crashing.

This takes about 2 minutes and is completely free — it does **not** give
Downloadify (or anyone) access to your account, just to public catalog data:

1. Go to https://developer.spotify.com/dashboard and log in with any Spotify
   account (free tier is fine).
2. Click **Create app**.
3. Fill in the form:
   - **App name**: anything, e.g. `Downloadify`
   - **App description**: anything, e.g. `Personal playlist downloader`
   - **Redirect URI**: enter exactly `http://127.0.0.1:8080` and click **Add**.
     This is used if you ever log in for a personalized/private playlist (see
     [Personalized and private playlists](#personalized-and-private-playlists-optional-login)
     below) — it must match exactly, but nothing is sent there unless you
     click "Log in with Spotify" yourself.
   - Check the **Web API** checkbox under "Which API/SDKs are you planning to use?"
   - Agree to the terms and click **Save**.
4. On the app's page, click **Settings**.
5. Copy the **Client ID**. Click **View client secret** and copy it too.
6. In this project's root folder, open `.env` (copy it from `.env.example` if
   it doesn't exist yet) and paste your values in:
   ```
   SPOTIFY_CLIENT_ID=paste_your_client_id_here
   SPOTIFY_CLIENT_SECRET=paste_your_client_secret_here
   ```
7. Save the file. No restart of anything else is needed — Downloadify reads
   `.env` automatically each time it starts.

**Important if you're publishing this project**: `.env` is already in
`.gitignore` — keep it that way, and never commit real credentials or paste
them into a public repo. This key is meant to be **per-user**: each person
who runs Downloadify should create and use their own free key if they want
it, the same way each person's copy already makes its own YouTube requests
from their own machine. See
[Publishing this app / letting many people use it](#publishing-this-app--letting-many-people-use-it)
below for the reasoning.

#### Personalized and private playlists (optional login)

Regular public, editorial, and user-created playlists work with zero setup.
But **personalized/algorithmic playlists** — Discover Weekly, a Daily Mix,
Release Radar, and the like — are a fundamentally different case: they have
no public identity at all. They're computed per Spotify account, and Spotify
will not return them to anyone but the logged-in owner, official Web API
included. The same is true of your own **private** playlists. There is no
way around this short of logging in as that account.

So Downloadify supports an optional, one-time login for exactly this case.
It's never needed for a normal public playlist, and nothing about the
default experience changes if you never use it. As a bonus, it also makes
[extending past 100 tracks](#getting-full-playlists-over-100-tracks-optional)
more reliable -- testing confirmed a logged-in account can fetch playlists
thousands of tracks long, well beyond what the Client Credentials key alone
could manage.

1. Set up `SPOTIFY_CLIENT_ID` as described above (the login reuses it; no
   client secret is needed for this part).
2. Click **Log in with Spotify** (top-right in the desktop app, or in the
   web app's header) and finish logging in in the browser tab that opens.
3. Try the playlist link again, or click **Browse my playlists…** (enabled
   once logged in) to pick straight from your own library instead of
   pasting a link at all — this also doubles as a way to check whether a
   given playlist is visible to the API at all before you try it.

**Blend playlists are a known exception this can't fix.** Testing against a
real account confirmed Blends don't appear in that account's own playlist
library via the API, and a Blend link 404s from the official API even fully
logged in as a participant. That looks like a deliberate Spotify platform
restriction specific to Blends (a collaborative, multi-account feature),
separate from -- and not fixed by -- the login above. Discover Weekly, Daily
Mix, Release Radar, and genuinely private playlists are the cases logging in
is meant to (and, per testing, does) solve.

Behind the scenes this uses the OAuth **Authorization Code + PKCE** flow —
the standard Spotify recommends for apps that can't keep a secret
confidential, so no client secret is involved. A short-lived local server on
`127.0.0.1:8080` (matching the Redirect URI above) catches Spotify's
response, exchanges it for a token, and closes itself immediately after. The
resulting login is cached in `.spotify_token_cache.json` (already
`.gitignore`d) so you only have to do this once; click **Log out** (or
delete that file) to forget it.

**This is different from, and much narrower than, a "login wall."** It's
scoped to read-only access to your own playlists (`playlist-read-private
playlist-read-collaborative`), used only when a playlist specifically needs
it, and — like the optional API key — it's per-user: each person sets this
up with their own free Client ID, so it doesn't create a shared credential
or a shared quota. See
[Publishing this app](#publishing-this-app--letting-many-people-use-it)
below for why that distinction matters if you're sharing this project.

#### Why not the official Spotify API by default?

Two things were tried and deliberately ruled out while building this:

- **Anonymous tokens (`open.spotify.com/get_access_token`)** — the trick
  Spotify's own logged-out web player uses. It got outright blocked (HTTP 403,
  "bot management") from at least one test network during development.
- **The token embedded inside the embed page's own HTML** — a lesser-known
  trick that *does* work, but calling the official `api.spotify.com` with it
  got that IP **rate-limited for ~12 hours** (`Retry-After: 42695` seconds)
  after fairly light testing traffic.

Both routes go through `api.spotify.com`, which turned out to apply
aggressive, long-duration throttling well beyond what a casual user would
expect to trigger. The embed *page* itself (a normal, CDN-cached webpage, not
an API call) never triggered any of this. So rather than build the default,
no-setup experience on a foundation that can quietly wall off a user for half
a day, Downloadify only uses `api.spotify.com` as an opt-in, per-user
enhancement — never as something the app depends on by default.

The one exception is the login flow described above, and it's deliberately
kept narrow: it only ever runs when a specific playlist can't be resolved
any other way, not on every request the way the two rejected approaches
above would have been, so it doesn't reproduce the same throttling risk at
any meaningful scale.

#### Publishing this app / letting many people use it

If you're planning to put this on GitHub for others to download and run
themselves (or to run the web app for others to use), a few things are worth
knowing. Both the source checkout and the packaged `Downloadify.exe`
releases work the same way here: **no key needed by default**, and the
optional `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` (for the 100+ track
extension and login) is something each person is expected to set up for
themselves — in `.env` next to the source, or `.env` next to the exe. This
was tried the other way (a shared Client ID baked into the published exe so
login worked with zero setup) and deliberately reverted once it became clear
that approach can't actually scale, for reasons worth understanding before
you consider hard-coding your own key:

- Spotify rate-limits per credential, not per end user. If everyone's copy of
  the app shared one embedded key, all of their traffic would draw from the
  same quota — the app would get *less* reliable the more popular it got, and
  a key sitting in a public repo isn't confidential anymore either, which is
  a violation of Spotify's Developer Terms on its own.
- New Spotify apps start in "Development Mode," which as of Spotify's
  February 2026 policy change caps *user-authorizing* apps (Authorization
  Code flow, i.e. "log in with Spotify") at **5** distinct accounts under one
  Client ID — down from a previous cap of 25 — and each of those 5 has to be
  manually allowlisted by email in the app's dashboard before they can even
  attempt to log in. The app owner's own Spotify account must also be
  Premium for the app to function in Development Mode at all now.
- The escape hatch from that cap, Extended Quota Mode, is no longer
  realistically reachable for a project like this one. As of May 2025,
  Spotify restricts it to registered *organizations* (not individuals) with
  **250,000+ monthly active users already**, demonstrated commercial
  viability, and alignment with Spotify's own platform strategy — Spotify's
  own announcement states over 95% of applicants are rejected even among
  those who qualify to apply. A personal or open-source tool doesn't meet
  the bar to apply, regardless of how the request is written, which is why
  baking in a shared key for public distribution no longer buys anything: it
  would work for at most 5 hand-picked people, permanently, not the general
  public downloading the exe.
- YouTube requests scale fine regardless of any of the above — every user's
  copy of the app makes its own YouTube search/download requests from their
  own machine, so there's no shared quota to worry about there at all.

Net effect: whether run from source or as the packaged exe, the app as
shipped has no shared secret, no shared login, and no single point that gets
rate-limited as usage grows — the tradeoff is the 100-track cap (and
personalized/private playlists) unless a user opts into their own free
Client ID, which takes about two minutes per person and has no cap of its
own since each person's login only ever counts against *their own* app's
5-user allowance.

Net effect: running from source has no shared secret, no shared login, and
no single point that gets rate-limited as usage grows, at the cost of each
user setting up their own free key for the 100-track extension and login.
The published exe trades that isolation for working out of the box, at the
cost of a shared quota and login cap across everyone who downloads it.

### YouTube search — no API key, with match filtering

For each track, Downloadify requests
`https://www.youtube.com/results?search_query=<artist> - <track>` as a plain
HTML page and extracts the `ytInitialData` JSON blob YouTube embeds in the
page for its own frontend to consume. No YouTube Data API key is used or
required.

Rather than blindly grabbing the first result, Downloadify scores the top 5
candidates against the expected artist/track name and (when known) the
track's duration from Spotify:

- **Title match** — how many meaningful words from `"<artist> <track>"` show
  up in the candidate's video title (noise words like "official", "video",
  "lyrics", "remastered" etc. are ignored so they don't skew the score).
- **Duration match** — how close the video's length is to Spotify's reported
  track length. This is what catches full concerts, extended DJ mixes, or
  reaction videos that otherwise share the right title words.

The candidate with the best combined score is downloaded — but only if that
score clears a minimum confidence bar. If nothing in the top 5 results is a
confident enough match, **the track is skipped** (logged clearly, marked as
failed in the summary) rather than downloading something that's likely the
wrong song.

### Downloading

The resulting `https://www.youtube.com/watch?v=...` URL is handed to
**yt-dlp** (used as a Python library), which downloads the best available
audio stream and uses its `FFmpegExtractAudio` postprocessor to convert it to
a 320kbps MP3 (`config.AUDIO_QUALITY_KBPS`) named `<Artist> - <Track>.mp3`
inside `downloadify_downloads/<Playlist Name>/`.

Worth knowing: YouTube's own source audio is itself compressed, typically
around 128-160kbps Opus/AAC, so encoding at 320kbps doesn't add fidelity
that was never there -- but it does avoid throwing away any *more* of it
than YouTube's own compression already did, which a lower target bitrate
would.

Up to 3 tracks are processed concurrently (via `asyncio`) to keep things fast
without hammering YouTube.

If a download fails (most commonly a transient `HTTP 403` from YouTube, which
tends to clear up on its own), Downloadify automatically retries that track
once more a couple of seconds later before giving up and marking it failed.

If a playlist contains the same track twice (or two different tracks that
happen to share an identical "Artist - Title"), the later one is saved as
`<Artist> - <Track> (2).mp3` rather than silently overwriting the first --
otherwise the reported success count wouldn't match the number of files
actually on disk.

---

## 5. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "That doesn't look like a public Spotify playlist link" | Make sure the URL looks like `https://open.spotify.com/playlist/<id>`. |
| "Playlist not found" / "Spotify refused access" | The playlist is private, deleted, or region-locked. Only public playlists work. |
| "This looks like a personalized playlist... Log in with Spotify and try again" | Discover Weekly, a Daily Mix, Release Radar, or a private playlist -- these have no public identity, so Spotify only returns them to their logged-in owner. Set `SPOTIFY_CLIENT_ID` in `.env` and click **Log in with Spotify**, then try again -- see [Personalized and private playlists](#personalized-and-private-playlists-optional-login). |
| "Set SPOTIFY_CLIENT_ID in your .env before logging in" | The login button needs a Client ID first (no secret required for this part) -- see [Getting full playlists over 100 tracks](#getting-full-playlists-over-100-tracks-optional) for how to get one free. |
| "Couldn't start the local login listener on port 8080" | Something else on your machine is already using port 8080. Close it and try logging in again. |
| "Could not read/parse playlist data from Spotify" | Spotify's embed page layout may have changed, or a network issue occurred. Check your internet connection; if it persists, the embed page's HTML structure may need updating in `spotify_client.py`. |
| "Note: Spotify's no-login embed page only exposes the first 100 tracks..." | Informational only — this playlist has over 100 tracks. Set up your own free `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` (see above) to fetch the rest. |
| "ffprobe and ffmpeg not found" / "yt-dlp finished but no output file was found" | ffmpeg isn't on `PATH` for the process running Downloadify. If you just installed it, fully close and reopen your terminal/VS Code (a new tab isn't enough). If that's not convenient right now, set `FFMPEG_LOCATION` in `.env` to ffmpeg's folder instead — see [Prerequisites](#ffmpeg-required--yt-dlp-needs-it-to-produce-mp3s). |
| "No confident match found on YouTube -- skipped" | Downloadify deliberately didn't download anything for this track because none of the top 5 YouTube results looked like a confident match (see [YouTube search — match filtering](#youtube-search--no-api-key-with-match-filtering)). Usually happens with obscure tracks, unusual title formatting, or remixes. |
| "unable to download video data: HTTP Error 403: Forbidden" (still fails after retrying) | Usually a transient YouTube throttle -- Downloadify already retries once automatically. If it keeps happening across many tracks, try again in a few minutes, or update yt-dlp (`pip install -U yt-dlp`), since YouTube periodically changes things in ways that need a newer version. |
| Reported "X/X downloaded successfully" but fewer files than X are in the folder | The playlist has the same track listed more than once (or two tracks that share an identical "Artist - Title"). Downloadify names the repeat `<Artist> - <Track> (2).mp3` so it doesn't overwrite the first -- check the folder for `(2)`/`(3)` files before assuming something's missing. |
| Some tracks fail while most succeed | Usually a YouTube search returning no confident match, or a video being region-locked/removed/403'd even after the automatic retry. Check the log window (enable **Show full logs**) for the specific error per track. |
| GUI window doesn't open | Make sure `PyQt6` installed correctly (`pip install PyQt6`); on some Linux distros you may need system Qt libraries. |

---

## 6. Notes for contributors

- Everything Spotify/YouTube/yt-dlp related lives in `downloadify/core/` and is
  UI-agnostic — both the GUI and web app just call `DownloadPipeline.run(...)`.
- The pipeline reports progress via plain callbacks (`on_log`, `on_progress`,
  `on_track_done`), which the GUI turns into Qt signals and the web app turns
  into Server-Sent Events.
- No YouTube or Spotify API keys are stored or required anywhere in this repo.
- The optional Spotify login lives in `downloadify/core/spotify_auth.py`,
  isolated from the rest of `spotify_client.py` -- it's only ever invoked
  from the one fallback path for personalized/private playlists, and the
  GUI/web layers only call `is_logged_in()` / `login_interactive()` /
  `logout()`, never touching tokens directly. Its cache file
  (`.spotify_token_cache.json`, gitignored) holds a real refresh token --
  treat it like a credential, same as `.env`.
- **Spotify's playlist-object field naming changed at some point after this
  app's initial development**: what the API docs call `tracks` (both the
  `/v1/playlists/{id}` field and its `/tracks` sub-endpoint, plus each
  entry's own `track` field) now only works under the name `items`
  (`/v1/playlists/{id}/items`, `items.items(item(...))`) -- the old names
  now 404/403. This was only caught by testing against a real logged-in
  account; if Spotify's API behaves unexpectedly again in the future,
  re-verifying current field names live (not just trusting cached
  documentation) is the fastest way to find out why.
- The embed page's track count can land anywhere close to, but under, its
  ~100-track cap (some entries can be missing a title -- unavailable/local
  tracks -- and get filtered out of `PlaylistInfo.tracks`). `possibly_truncated`
  and the extension's resume offset are deliberately computed from the *raw*
  page size (`PlaylistInfo.embed_raw_count`), not `len(tracks)` -- using the
  filtered count for either silently under-reports large playlists or skips/
  reprocesses tracks when extending past the cap. If you're touching
  `spotify_client.py`, keep that distinction intact.
