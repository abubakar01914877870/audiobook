// Background service worker — handles chrome.downloads on behalf of content script.
// Also logs ALL network requests/responses from Grok-related URLs to the Python debug server.

// ── Network request logging ───────────────────────────────────────────────────
const NET_SERVER = 'http://localhost:7878/network';

// Queue to batch-send logs (avoids flooding the Python server with individual fetches)
let _netQueue = [];
let _netFlushTimer = null;

function _queueNetLog(entry) {
  _netQueue.push(entry);
  if (!_netFlushTimer) {
    _netFlushTimer = setTimeout(_flushNetQueue, 300); // flush every 300ms
  }
}

function _flushNetQueue() {
  _netFlushTimer = null;
  if (_netQueue.length === 0) return;
  const batch = _netQueue.splice(0, _netQueue.length);
  fetch(NET_SERVER, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(batch),
  }).catch(() => {}); // silent if Python server not running
}

// Observe ALL requests to Grok + its CDN domains
const GROK_URL_PATTERNS = [
  '*://grok.com/*',
  '*://*.grok.com/*',
  '*://*.x.ai/*',
  '*://*.cloudfront.net/*',
  '*://*.amazonaws.com/*',
];

// Log request start (method + URL)
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    _queueNetLog({
      dir: '→ REQ',
      method: details.method,
      url: details.url,
      tabId: details.tabId,
      type: details.type,
      ts: Date.now(),
    });
  },
  { urls: GROK_URL_PATTERNS }
);

// Log successful responses (+ status code)
chrome.webRequest.onCompleted.addListener(
  (details) => {
    _queueNetLog({
      dir: '← RES',
      method: details.method,
      url: details.url,
      status: details.statusCode,
      tabId: details.tabId,
      type: details.type,
      ts: Date.now(),
    });
  },
  { urls: GROK_URL_PATTERNS }
);

// Log failed/blocked requests
chrome.webRequest.onErrorOccurred.addListener(
  (details) => {
    _queueNetLog({
      dir: '✗ ERR',
      method: details.method,
      url: details.url,
      error: details.error,
      tabId: details.tabId,
      type: details.type,
      ts: Date.now(),
    });
  },
  { urls: GROK_URL_PATTERNS }
);

// ── Message handler ───────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'GROK_STATUS') {
    console.log(`[GrokBG] ${msg.status}: ${msg.message}`);
    sendResponse({ ok: true });
    return false;
  }

  if (msg.type === 'DOWNLOAD_VIDEO') {
    const url = msg.url;
    const filename = msg.filename || 'grok_video.mp4';
    console.log(`[GrokBG] DOWNLOAD_VIDEO — url=${url ? url.slice(-80) : 'none'} filename=${filename}`);

    if (!url) {
      console.warn('[GrokBG] DOWNLOAD_VIDEO: no URL provided');
      sendResponse({ ok: false, error: 'no URL' });
      return false;
    }

    // chrome.downloads.download handles cookies automatically (uses Chrome session)
    chrome.downloads.download(
      { url, filename, conflictAction: 'uniquify', saveAs: false },
      (downloadId) => {
        if (chrome.runtime.lastError) {
          console.error('[GrokBG] chrome.downloads.download error:', chrome.runtime.lastError.message);
          sendResponse({ ok: false, error: chrome.runtime.lastError.message });
        } else {
          console.log(`[GrokBG] Download started — downloadId=${downloadId}`);
          sendResponse({ ok: true, downloadId });
        }
      }
    );
    return true; // keep message channel open for async sendResponse
  }

  sendResponse({ ok: true });
  return false;
});
