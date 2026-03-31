// Background service worker — minimal; UI automation is done in content script.
// Listens for status messages from content script and logs them.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'GROK_STATUS') {
    console.log(`[GrokBG] ${msg.status}: ${msg.message}`);
  }
  sendResponse({ ok: true });
});
