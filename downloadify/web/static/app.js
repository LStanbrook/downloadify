// Frontend logic for Downloadify's web mode.
// Submits a playlist URL to the backend, then opens a Server-Sent Events
// stream to show live progress/log output as the backend downloads tracks.

const form = document.getElementById("download-form");
const urlInput = document.getElementById("playlist-url");
const outputInput = document.getElementById("output-dir");
const downloadBtn = document.getElementById("download-btn");
const cancelBtn = document.getElementById("cancel-btn");
const progressFill = document.getElementById("progress-fill");
const statusText = document.getElementById("status-text");
const logView = document.getElementById("log-view");
const fullLogsCheckbox = document.getElementById("full-logs-checkbox");
const spotifyLoginStatus = document.getElementById("spotify-login-status");
const spotifyLoginBtn = document.getElementById("spotify-login-btn");
const browsePlaylistsBtn = document.getElementById("browse-playlists-btn");
const playlistPickerDialog = document.getElementById("playlist-picker-dialog");
const playlistPickerList = document.getElementById("playlist-picker-list");
const playlistPickerCancel = document.getElementById("playlist-picker-cancel");

let currentJobId = null;
let eventSource = null;
let spotifyLoggedIn = false;

function appendLog(line) {
  logView.textContent += line + "\n";
  logView.scrollTop = logView.scrollHeight;
}

function setRunning(isRunning) {
  downloadBtn.disabled = isRunning;
  cancelBtn.disabled = !isRunning;
  urlInput.disabled = isRunning;
  outputInput.disabled = isRunning;
}

function setProgress(done, total) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  progressFill.style.width = pct + "%";
  statusText.textContent = total > 0 ? `Downloading... ${done}/${total} tracks` : "Starting...";
}

function resetProgress() {
  progressFill.style.width = "0%";
}

function setStatus(text, statusClass) {
  statusText.textContent = text;
  statusText.classList.remove("status-success", "status-warning", "status-error");
  if (statusClass) {
    statusText.classList.add(statusClass);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const playlistUrl = urlInput.value.trim();
  if (!playlistUrl) return;

  logView.textContent = "";
  progressFill.style.width = "0%";
  setStatus("Starting...");
  setRunning(true);

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        playlist_url: playlistUrl,
        output_dir: outputInput.value.trim() || "downloadify_downloads",
      }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(err.detail || "Failed to start download");
    }

    const { job_id } = await response.json();
    currentJobId = job_id;
    streamJob(job_id);
  } catch (err) {
    appendLog(`ERROR: ${err.message}`);
    setStatus("Failed to start.", "status-error");
    setRunning(false);
  }
});

cancelBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  cancelBtn.disabled = true;
  setStatus("Cancelling...");
  await fetch(`/api/jobs/${currentJobId}/cancel`, { method: "POST" });
});

function streamJob(jobId) {
  if (eventSource) {
    eventSource.close();
  }
  eventSource = new EventSource(`/api/jobs/${jobId}/events`);

  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch (data.type) {
      case "log":
        if (!data.verbose || fullLogsCheckbox.checked) {
          appendLog(data.message);
        }
        break;
      case "progress":
        setProgress(data.done, data.total);
        break;
      case "track": {
        const icon = data.status === "done" ? "SUCCESS" : "FAIL";
        const reason = data.status !== "done" && data.error ? ` -- ${data.error}` : "";
        appendLog(`[${icon}] ${data.track}${reason}`);
        break;
      }
      case "done":
        setStatus(`Finished: ${data.succeeded}/${data.total} downloaded.`, "status-success");
        appendLog(`Saved to: ${data.output_folder}`);
        setRunning(false);
        resetProgress();
        break;
      case "cancelled":
        setStatus(
          `Cancelled: ${data.succeeded}/${data.total} downloaded before stopping.`,
          "status-warning"
        );
        appendLog(`Saved to: ${data.output_folder}`);
        setRunning(false);
        resetProgress();
        break;
      case "error":
        setStatus("Failed.", "status-error");
        appendLog(`ERROR: ${data.message}`);
        setRunning(false);
        resetProgress();
        break;
      case "stream_end":
        eventSource.close();
        break;
      default:
        break;
    }
  };

  eventSource.onerror = () => {
    // The server closes the stream normally once the job finishes; only
    // report an error state if we were still expecting more events.
    if (!downloadBtn.disabled) return;
    eventSource.close();
  };
}

// Spotify login is entirely optional -- only needed for personalized
// playlists (Discover Weekly, a Daily Mix, ...) or a user's own private
// playlists. Regular public playlists never require it.
function updateSpotifyLoginUI() {
  spotifyLoginStatus.textContent = spotifyLoggedIn
    ? "Logged into Spotify"
    : "Not logged into Spotify";
  spotifyLoginBtn.textContent = spotifyLoggedIn ? "Log out" : "Log in with Spotify";
  spotifyLoginBtn.disabled = false;
  browsePlaylistsBtn.disabled = !spotifyLoggedIn;
}

async function refreshSpotifyLoginStatus() {
  try {
    const resp = await fetch("/api/spotify/status");
    const data = await resp.json();
    spotifyLoggedIn = Boolean(data.logged_in);
  } catch {
    spotifyLoggedIn = false;
  }
  updateSpotifyLoginUI();
}

spotifyLoginBtn.addEventListener("click", async () => {
  if (spotifyLoggedIn) {
    await fetch("/api/spotify/logout", { method: "POST" });
    spotifyLoggedIn = false;
    updateSpotifyLoginUI();
    return;
  }

  spotifyLoginBtn.disabled = true;
  spotifyLoginStatus.textContent = "Waiting for login in your browser...";
  try {
    const resp = await fetch("/api/spotify/login", { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || "Login failed");
    }
    spotifyLoggedIn = true;
    updateSpotifyLoginUI();
  } catch (err) {
    spotifyLoggedIn = false;
    spotifyLoginStatus.textContent = `Login failed: ${err.message}`;
    spotifyLoginBtn.textContent = "Log in with Spotify";
    spotifyLoginBtn.disabled = false;
  }
});

refreshSpotifyLoginStatus();

// Lets a playlist be chosen from the logged-in user's own library instead
// of pasting a link -- useful for playlists that don't resolve reliably by
// URL (e.g. a personalized playlist, or one whose link isn't handy).
browsePlaylistsBtn.addEventListener("click", async () => {
  browsePlaylistsBtn.disabled = true;
  browsePlaylistsBtn.textContent = "Loading...";
  try {
    const resp = await fetch("/api/spotify/my-playlists");
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || "Couldn't list playlists");
    }
    const { playlists } = await resp.json();
    renderPlaylistPicker(playlists);
    playlistPickerDialog.showModal();
  } catch (err) {
    appendLog(`ERROR: ${err.message}`);
  } finally {
    browsePlaylistsBtn.disabled = false;
    browsePlaylistsBtn.textContent = "Browse my playlists…";
  }
});

function renderPlaylistPicker(playlists) {
  playlistPickerList.textContent = "";
  if (!playlists.length) {
    const empty = document.createElement("p");
    empty.textContent = "No playlists were found in your Spotify account.";
    playlistPickerList.appendChild(empty);
    return;
  }
  for (const playlist of playlists) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "playlist-picker-row";
    const ownerText = playlist.owner ? `  (by ${playlist.owner})` : "";
    row.innerHTML = `${escapeHtml(playlist.name)} <span class="track-count">— ${playlist.track_count} tracks${escapeHtml(ownerText)}</span>`;
    row.addEventListener("click", () => {
      urlInput.value = playlist.url;
      playlistPickerDialog.close();
    });
    playlistPickerList.appendChild(row);
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

playlistPickerCancel.addEventListener("click", () => playlistPickerDialog.close());
