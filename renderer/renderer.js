// DOM Elements
const sourcePath = document.getElementById("source-path");
const outputPath = document.getElementById("output-path");
const btnSource = document.getElementById("btn-source");
const btnOutput = document.getElementById("btn-output");
const thresholdSlider = document.getElementById("threshold-slider");
const thresholdValue = document.getElementById("threshold-value");
const btnStart = document.getElementById("btn-start");
const btnCancel = document.getElementById("btn-cancel");
const statusText = document.getElementById("status-text");
const progressCount = document.getElementById("progress-count");
const progressBar = document.getElementById("progress-bar");
const progressGlow = document.getElementById("progress-glow");
const progressSection = document.getElementById("progress-section");
const logContainer = document.getElementById("log-container");
const btnClearLog = document.getElementById("btn-clear-log");
const summaryBanner = document.getElementById("summary-banner");
const summaryText = document.getElementById("summary-text");

// Title bar controls
document.getElementById("btn-minimize").addEventListener("click", () => window.api.windowMinimize());
document.getElementById("btn-maximize").addEventListener("click", () => window.api.windowMaximize());
document.getElementById("btn-close").addEventListener("click", () => window.api.windowClose());

// State
let isScanning = false;
let logStarted = false;

// Folder selection
btnSource.addEventListener("click", async () => {
  const folder = await window.api.selectFolder();
  if (folder) sourcePath.value = folder;
});

btnOutput.addEventListener("click", async () => {
  const folder = await window.api.selectFolder();
  if (folder) outputPath.value = folder;
});

// Threshold slider
thresholdSlider.addEventListener("input", () => {
  const val = thresholdSlider.value;
  thresholdValue.textContent = val;
  updateSliderFill();
});

function updateSliderFill() {
  const pct = ((thresholdSlider.value - thresholdSlider.min) / (thresholdSlider.max - thresholdSlider.min)) * 100;
  thresholdSlider.style.background = `linear-gradient(90deg, var(--accent) ${pct}%, var(--border-color) ${pct}%)`;
}
updateSliderFill();

// Start scan
btnStart.addEventListener("click", async () => {
  const source = sourcePath.value.trim();
  const output = outputPath.value.trim();

  if (!source) {
    shakeElement(sourcePath);
    return;
  }
  if (!output) {
    shakeElement(outputPath);
    return;
  }
  if (source === output) {
    addLog("Source and output folders cannot be the same.", "error");
    return;
  }

  setScanning(true);
  clearLog();
  summaryBanner.classList.add("hidden");
  setProgress(0, 0);

  await window.api.startScan({
    source,
    output,
    threshold: parseInt(thresholdSlider.value),
  });
});

// Cancel scan
btnCancel.addEventListener("click", async () => {
  await window.api.cancelScan();
  statusText.textContent = "Cancelling...";
});

// Clear log
btnClearLog.addEventListener("click", clearLog);

// Backend events
window.api.onBackendEvent((data) => {
  switch (data.event) {
    case "status":
      statusText.textContent = data.message;
      break;

    case "log":
      addLog(data.message);
      break;

    case "progress":
      setProgress(data.current, data.total);
      break;

    case "complete":
      setScanning(false);
      summaryBanner.classList.remove("hidden", "error-summary");
      if (data.errors > 0) {
        summaryBanner.classList.add("error-summary");
      }
      summaryText.textContent = data.summary;
      break;

    case "cancelled":
      setScanning(false);
      break;

    case "error":
      addLog(data.message, "error");
      break;

    case "process-exit":
      if (isScanning) {
        setScanning(false);
        addLog("Backend process exited unexpectedly.", "error");
        statusText.textContent = "Error - process exited";
      }
      break;
  }
});

// Helpers
function setScanning(scanning) {
  isScanning = scanning;
  btnStart.disabled = scanning;
  btnCancel.disabled = !scanning;
  sourcePath.disabled = scanning;
  outputPath.disabled = scanning;
  btnSource.disabled = scanning;
  btnOutput.disabled = scanning;
  thresholdSlider.disabled = scanning;

  if (scanning) {
    progressSection.classList.add("active", "scanning");
  } else {
    progressSection.classList.remove("scanning");
  }
}

function setProgress(current, total) {
  const pct = total > 0 ? (current / total) * 100 : 0;
  progressBar.style.width = pct + "%";
  progressGlow.style.width = pct + "%";
  progressCount.textContent = `${current} / ${total}`;
  progressSection.classList.add("active");
}

function addLog(message, type = "") {
  if (!logStarted) {
    logContainer.innerHTML = "";
    logStarted = true;
  }

  const entry = document.createElement("div");
  entry.className = "log-entry" + (type ? ` ${type}` : "");

  const now = new Date();
  const ts = now.toTimeString().slice(0, 8);

  // Classify log messages
  let entryType = type;
  if (!entryType) {
    if (message.includes("Original (kept)")) entryType = "highlight";
    else if (message.includes("Done!")) entryType = "success";
    else if (message.includes("Failed") || message.includes("error") || message.includes("Error")) entryType = "error";
  }
  if (entryType) entry.className = `log-entry ${entryType}`;

  entry.innerHTML = `<span class="timestamp">[${ts}]</span>${escapeHtml(message)}`;
  logContainer.appendChild(entry);
  logContainer.scrollTop = logContainer.scrollHeight;
}

function clearLog() {
  logContainer.innerHTML = '<div class="log-placeholder">Waiting for scan to start...</div>';
  logStarted = false;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function shakeElement(el) {
  el.style.animation = "none";
  el.offsetHeight; // Trigger reflow
  el.style.animation = "shake 0.4s ease";
  el.style.borderColor = "var(--danger)";
  el.style.boxShadow = "0 0 0 3px var(--danger-glow)";
  setTimeout(() => {
    el.style.borderColor = "";
    el.style.boxShadow = "";
  }, 1500);
}

// Add shake keyframes dynamically
const style = document.createElement("style");
style.textContent = `
  @keyframes shake {
    0%, 100% { transform: translateX(0); }
    20% { transform: translateX(-6px); }
    40% { transform: translateX(6px); }
    60% { transform: translateX(-4px); }
    80% { transform: translateX(4px); }
  }
`;
document.head.appendChild(style);
