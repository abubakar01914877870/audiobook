'use strict';

const dot  = document.getElementById('statusDot');
const text = document.getElementById('statusText');
const sub  = document.getElementById('statusSub');
const autoDownloadToggle = document.getElementById('autoDownload');

function setStatus(state, message, detail) {
  dot.className = 'status-dot ' + (state || '');
  text.textContent = message;
  sub.textContent = detail || '';
}

function formatBytes(bytes) {
  if (bytes < 1024)        return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// ─── Load saved preferences ───────────────────────────────────────────────────

chrome.storage.sync.get({ autoDownload: true }, (prefs) => {
  autoDownloadToggle.checked = prefs.autoDownload;
});

autoDownloadToggle.addEventListener('change', () => {
  chrome.storage.sync.set({ autoDownload: autoDownloadToggle.checked });
});

// ─── Listen for status updates from the service worker ───────────────────────

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === 'DOWNLOAD_STARTED') {
    setStatus('ready', 'Download started!', `${message.filename} · ${formatBytes(message.bytes)}`);
  }
});

// ─── Check current tab ───────────────────────────────────────────────────────

chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const tab = tabs[0];
  if (!tab) return;

  if (tab.url && tab.url.startsWith('https://docs.google.com/document/')) {
    setStatus('ready', 'Waiting for audio generation...', 'Click "Listen" in Google Docs');
  } else {
    setStatus('', 'Not a Google Doc', 'Navigate to a Google Docs document');
  }
});
