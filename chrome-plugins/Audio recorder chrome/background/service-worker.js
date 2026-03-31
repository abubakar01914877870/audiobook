/**
 * service-worker.js — handles popup notifications only.
 * Downloads are triggered directly from the content script (injector.js).
 */

'use strict';

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

  if (message.type === 'DOWNLOAD_TRIGGERED') {
    // Forward to popup if it's open
    chrome.runtime.sendMessage({
      type: 'DOWNLOAD_STARTED',
      filename: message.filename,
      bytes: message.bytes,
    }).catch(() => {});
    sendResponse({ ok: true });
    return false;
  }

  if (message.type === 'GET_STATUS') {
    sendResponse({ status: 'ready' });
    return false;
  }
});

console.log('[GDocsAudio] Service worker started');
