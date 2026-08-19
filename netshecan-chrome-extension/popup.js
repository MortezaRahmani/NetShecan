// NetShecan Helper — popup
const statusEl = document.getElementById("status");
const jsonEl = document.getElementById("json");
const copyBtn = document.getElementById("copy");

let currentJson = null;

function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.style.color = ok ? "#5dd27a" : "#ff8a80";
}

async function load() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs.length) {
      setStatus("No active tab.", false);
      return;
    }
    const res = await chrome.tabs.sendMessage(tabs[0].id, { type: "NETSHECAN_GET" });
    if (!res || !res.data) {
      setStatus("Not on a supported provider site, or not logged in.", false);
      return;
    }
    currentJson = JSON.stringify(res.data, null, 2);
    jsonEl.textContent = currentJson;
    copyBtn.disabled = false;
    setStatus("Ready — " + res.data.provider.toUpperCase(), true);
  } catch (e) {
    setStatus("Error: " + e.message, false);
  }
}

copyBtn.addEventListener("click", async () => {
  if (!currentJson) return;
  try {
    await navigator.clipboard.writeText(currentJson);
    setStatus("Copied to clipboard.", true);
  } catch (e) {
    setStatus("Copy failed: " + e.message, false);
  }
});

load();
