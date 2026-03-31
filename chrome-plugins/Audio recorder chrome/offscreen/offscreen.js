/**
 * offscreen.js — runs in the offscreen document.
 *
 * Creates a blob URL (only possible with DOM access) and sends it back
 * to the service worker, which calls chrome.downloads.download().
 */

'use strict';

async function run() {
  console.debug('[GDocsAudio] Offscreen document loaded — requesting download data');

  let data;
  try {
    data = await chrome.runtime.sendMessage({ type: 'OFFSCREEN_READY' });
  } catch (err) {
    console.error('[GDocsAudio] Offscreen failed to contact service worker:', err);
    return;
  }

  if (!data || !data.byteArray) {
    console.warn('[GDocsAudio] Offscreen received no download data');
    return;
  }

  const { byteArray, contentType, filename } = data;

  const uint8  = new Uint8Array(byteArray);
  const blob   = new Blob([uint8], { type: contentType || 'audio/mpeg' });
  const blobUrl = URL.createObjectURL(blob);

  console.debug('[GDocsAudio] Blob URL created, sending to SW for download:', filename);

  // Send blob URL back to SW — only the SW has chrome.downloads access
  try {
    await chrome.runtime.sendMessage({
      type: 'BLOB_URL_READY',
      blobUrl,
      filename,
      bytes: uint8.byteLength,
    });
  } catch (err) {
    console.error('[GDocsAudio] Failed to send blob URL to SW:', err);
  }

  // Revoke after 2 minutes
  setTimeout(() => URL.revokeObjectURL(blobUrl), 120_000);
}

run();
