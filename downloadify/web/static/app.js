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

let currentJobId = null;
let eventSource = null;

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
