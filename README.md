# Downloadify

Paste a **public Spotify playlist link**, and Downloadify will:

1. Fetch every track (title + artist) from that playlist by reading Spotify's public embed page — no login, no API key, no OAuth flow.
2. Search YouTube for `"artist - track name"` by scraping the public search results page (no YouTube Data API key).
3. Take the first video result and download its audio with **yt-dlp**, converting it to **MP3**.
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
│       └── static/                  # index.html / style.css / app.js
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
- Error dialogs if something goes wrong (bad URL, private playlist, network errors, etc.)

### Web app

```powershell
python main.py --mode web
# optionally: python main.py --mode web --host 0.0.0.0 --port 8000
```

Then open **http://127.0.0.1:8000** in a browser. Paste a playlist URL, optionally
change the output folder (this is a path *on the machine running the server*),
and click **Download Playlist**. Logs stream live into the page (also printed
to the terminal running `uvicorn`), with the same **Show full logs** checkbox
and self-resetting progress bar as the desktop app.

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
whatever tracks fall past the 100-track cap via the official, fully-paginated
Spotify Web API — the embed page is still tried first either way.

This takes about 2 minutes and is completely free — it does **not** give
Downloadify (or anyone) access to your account, just to public catalog data:

1. Go to https://developer.spotify.com/dashboard and log in with any Spotify
   account (free tier is fine).
2. Click **Create app**.
3. Fill in the form:
   - **App name**: anything, e.g. `Downloadify`
   - **App description**: anything, e.g. `Personal playlist downloader`
   - **Redirect URI**: required by the form but unused by Downloadify — enter
     `http://127.0.0.1:8080` and click **Add**.
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

Only **public** playlists are supported (private/collaborative playlists
aren't visible via either method).

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

#### Publishing this app / letting many people use it

If you're planning to put this on GitHub for others to download and run
themselves (or to run the web app for others to use), a few things are worth
knowing:

- **Don't bake your own Spotify API key into the published code.** Spotify
  rate-limits per credential, not per end user. If everyone's copy of the app
  shared one embedded key, all of their traffic would draw from the same
  quota — the app would get *less* reliable the more popular it got, and a
  key sitting in a public repo isn't confidential anymore either, which is a
  violation of Spotify's Developer Terms on its own. This is exactly why the
  default path needs no key at all, and why the optional key above is
  something each user adds for themselves, not something you publish.
- **A full Spotify login flow (like Exportify uses) was considered and isn't
  recommended here.** New Spotify apps start in "Development Mode," which
  caps *user-authorizing* apps (Authorization Code flow, i.e. "log in with
  Spotify") at 25 distinct Spotify accounts unless Spotify approves an
  Extended Quota Mode review. Given this app's purpose is downloading audio,
  that review is a real risk of rejection, and either way it adds a login
  step that the credential-free embed-page method makes unnecessary. It isn't
  used here.
- **YouTube requests already scale fine** — every user's copy of the app
  makes its own YouTube search/download requests from their own machine, so
  there's no shared quota to worry about there at all.

Net effect: the app as shipped has no shared secret, no login wall, and no
single point that gets rate-limited as usage grows — the one tradeoff is the
100-track cap on very large playlists unless a user opts into their own key.

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
a 192kbps MP3 named `<Artist> - <Track>.mp3` inside
`downloadify_downloads/<Playlist Name>/`.

Up to 3 tracks are processed concurrently (via `asyncio`) to keep things fast
without hammering YouTube.

If a download fails (most commonly a transient `HTTP 403` from YouTube, which
tends to clear up on its own), Downloadify automatically retries that track
once more a couple of seconds later before giving up and marking it failed.

---

## 5. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "That doesn't look like a public Spotify playlist link" | Make sure the URL looks like `https://open.spotify.com/playlist/<id>`. |
| "Playlist not found" / "Spotify refused access" | The playlist is private, deleted, or region-locked. Only public playlists work. |
| "Spotify says this playlist doesn't exist for a logged-out visitor" | This is almost always a **personalized/algorithmic playlist** -- Discover Weekly, a Daily Mix, Release Radar, etc. These are generated per Spotify account and have no fixed public identity, so they simply can't be read without logging in as their owner, which Downloadify deliberately never does. Public, user-created or editorial playlists (the kind you can share a link to and anyone can open) work fine. |
| "Could not read/parse playlist data from Spotify" | Spotify's embed page layout may have changed, or a network issue occurred. Check your internet connection; if it persists, the embed page's HTML structure may need updating in `spotify_client.py`. |
| "Note: Spotify's no-login embed page only exposes the first 100 tracks..." | Informational only — this playlist has over 100 tracks. Set up your own free `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` (see above) to fetch the rest. |
| "ffprobe and ffmpeg not found" / "yt-dlp finished but no output file was found" | ffmpeg isn't on `PATH` for the process running Downloadify. If you just installed it, fully close and reopen your terminal/VS Code (a new tab isn't enough). If that's not convenient right now, set `FFMPEG_LOCATION` in `.env` to ffmpeg's folder instead — see [Prerequisites](#ffmpeg-required--yt-dlp-needs-it-to-produce-mp3s). |
| "No confident match found on YouTube -- skipped" | Downloadify deliberately didn't download anything for this track because none of the top 5 YouTube results looked like a confident match (see [YouTube search — match filtering](#youtube-search--no-api-key-with-match-filtering)). Usually happens with obscure tracks, unusual title formatting, or remixes. |
| "unable to download video data: HTTP Error 403: Forbidden" (still fails after retrying) | Usually a transient YouTube throttle -- Downloadify already retries once automatically. If it keeps happening across many tracks, try again in a few minutes, or update yt-dlp (`pip install -U yt-dlp`), since YouTube periodically changes things in ways that need a newer version. |
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
