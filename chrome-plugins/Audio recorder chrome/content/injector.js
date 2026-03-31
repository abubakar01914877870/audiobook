/**
 * injector.js — runs in the ISOLATED world at document_start.
 *
 * 1. Injects page-world.js into the MAIN world.
 * 2. Receives captured audio buffer via CustomEvent.
 * 3. Creates a same-origin blob URL and triggers download via <a download>.
 *    Chrome always respects the filename on same-origin blob downloads.
 */

(function () {
  'use strict';

  // ─── 1. Inject page-world.js into MAIN world ──────────────────────────────

  const script = document.createElement('script');
  script.src = chrome.runtime.getURL('content/page-world.js');
  script.onload = () => script.remove();
  (document.head || document.documentElement).appendChild(script);

  // ─── 2. Helpers ───────────────────────────────────────────────────────────

  function getFilename(contentType) {
    const raw = document.title
      .replace(/\s*[-–]\s*Google Docs\s*$/i, '')
      .replace(/[/\\:*?"<>|]/g, '_')
      .replace(/\s+/g, ' ')
      .trim() || 'google-docs-audio';

    const ext = (contentType || '').includes('webm') ? 'webm'
              : (contentType || '').includes('ogg')  ? 'ogg'  : 'mp3';

    return `${raw}_audio.${ext}`;
  }

  function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a   = document.createElement('a');
    a.href            = url;
    a.download        = filename;
    a.style.display   = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      if (a.parentNode) a.parentNode.removeChild(a);
      URL.revokeObjectURL(url);
    }, 60_000);
  }

  // ─── 3. Handle captured audio ─────────────────────────────────────────────

  let lastSize  = 0;
  let busy      = false;

  window.addEventListener('__gdocs_audio_captured__', (event) => {
    const { buffer, contentType, chunkCount } = event.detail;

    if (!buffer || buffer.byteLength === 0) return;
    if (buffer.byteLength === lastSize && busy) return;

    lastSize = buffer.byteLength;
    busy     = true;

    const filename = getFilename(contentType);

    console.debug('[GDocsAudio] Triggering download:', filename,
      (buffer.byteLength / 1024 / 1024).toFixed(2) + ' MB',
      chunkCount + ' chunk(s)');

    try {
      const blob = new Blob([buffer], { type: contentType || 'audio/mpeg' });
      triggerDownload(blob, filename);

      // Notify popup
      chrome.runtime.sendMessage({
        type: 'DOWNLOAD_TRIGGERED',
        filename,
        bytes: buffer.byteLength,
      }).catch(() => {});
    } catch (err) {
      console.error('[GDocsAudio] Download error:', err);
    } finally {
      setTimeout(() => { busy = false; }, 5000);
    }
  });

})();
