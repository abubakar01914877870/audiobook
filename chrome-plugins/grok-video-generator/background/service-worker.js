// Background service worker — handles chrome.downloads on behalf of content script.

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
