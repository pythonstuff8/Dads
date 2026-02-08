// DOM
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
const progressSection = document.getElementById("progress-section");
const logContainer = document.getElementById("log-container");
const btnClearLog = document.getElementById("btn-clear-log");
const summaryBanner = document.getElementById("summary-banner");
const summaryText = document.getElementById("summary-text");

// Title bar
document.getElementById("btn-minimize").onclick = () => window.api.windowMinimize();
document.getElementById("btn-maximize").onclick = () => window.api.windowMaximize();
document.getElementById("btn-close").onclick = () => window.api.windowClose();

let isScanning = false;
let logStarted = false;

// Folder browse
btnSource.onclick = async () => {
  const f = await window.api.selectFolder();
  if (f) sourcePath.value = f;
};
btnOutput.onclick = async () => {
  const f = await window.api.selectFolder();
  if (f) outputPath.value = f;
};

// Slider
thresholdSlider.oninput = () => {
  thresholdValue.textContent = thresholdSlider.value;
  updateSliderTrack();
};

function updateSliderTrack() {
  const pct = ((thresholdSlider.value - 1) / 59) * 100;
  thresholdSlider.style.background =
    `linear-gradient(90deg, var(--accent-dim) ${pct}%, rgba(255,255,255,0.08) ${pct}%)`;
}
updateSliderTrack();

// Start
btnStart.onclick = async () => {
  const source = sourcePath.value.trim();
  const output = outputPath.value.trim();

  if (!source) return shake(sourcePath);
  if (!output) return shake(outputPath);
  if (source === output) {
    addLog("Source and output folders cannot be the same.", "error");
    return;
  }

  setScanning(true);
  clearLog();
  summaryBanner.classList.add("hidden");
  setProgress(0, 0);

  await window.api.startScan({
    source, output,
    threshold: parseInt(thresholdSlider.value),
  });
};

// Cancel
btnCancel.onclick = async () => {
  await window.api.cancelScan();
  statusText.textContent = "Cancelling...";
};

// Clear log
btnClearLog.onclick = clearLog;

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
      summaryBanner.classList.remove("hidden", "has-errors");
      if (data.errors > 0) summaryBanner.classList.add("has-errors");
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
        statusText.textContent = "Error";
      }
      break;
  }
});

// Helpers
function setScanning(v) {
  isScanning = v;
  btnStart.disabled = v;
  btnCancel.disabled = !v;
  sourcePath.disabled = v;
  outputPath.disabled = v;
  btnSource.disabled = v;
  btnOutput.disabled = v;
  thresholdSlider.disabled = v;
  progressSection.classList.toggle("active", v || progressBar.style.width !== "0%");
  progressSection.classList.toggle("scanning", v);
}

function setProgress(cur, total) {
  const pct = total > 0 ? (cur / total) * 100 : 0;
  progressBar.style.width = pct + "%";
  progressCount.textContent = total > 0 ? `${cur} / ${total}` : "";
  progressSection.classList.add("active");
}

function addLog(msg, type) {
  if (!logStarted) {
    logContainer.innerHTML = "";
    logStarted = true;
  }

  const el = document.createElement("div");
  el.className = "log-line";

  // Auto-classify
  if (!type) {
    if (msg.includes("Original (kept)")) type = "accent";
    else if (msg.includes("Done!")) type = "success";
    else if (msg.includes("Failed") || msg.includes("error") || msg.includes("Error")) type = "error";
  }
  if (type) el.classList.add(type);

  const ts = new Date().toTimeString().slice(0, 8);
  el.innerHTML = `<span class="ts">${ts}</span>${esc(msg)}`;
  logContainer.appendChild(el);
  logContainer.scrollTop = logContainer.scrollHeight;
}

function clearLog() {
  logContainer.innerHTML = '<div class="log-empty">No activity yet. Start a scan to begin.</div>';
  logStarted = false;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function shake(el) {
  el.classList.remove("shake");
  void el.offsetHeight;
  el.classList.add("shake");
  setTimeout(() => el.classList.remove("shake"), 500);
}
