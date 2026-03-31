/**
 * page-world.js — runs in the MAIN world (same JS context as Google Docs).
 *
 * Strategy: Hook MediaSource + SourceBuffer.appendBuffer to capture the
 * raw audio chunks that Google Docs feeds into the media pipeline after
 * receiving and decoding the BrowserChannel/protobuf TTS stream.
 *
 * Also hooks fetch as a secondary path for any plain audio responses.
 */

(function () {
  'use strict';

  const DEBOUNCE_MS = 3000; // dispatch 3 s after last chunk

  // ─── MediaSource / SourceBuffer hook (PRIMARY) ────────────────────────────

  const audioSessions = new Map(); // mediaSource → { mimeType, chunks[], timer }

  function createSession(mimeType) {
    return { mimeType, chunks: [], timer: null, byteLength: 0 };
  }

  function scheduleDispatch(session) {
    clearTimeout(session.timer);
    session.timer = setTimeout(() => dispatchSession(session), DEBOUNCE_MS);
  }

  function dispatchSession(session) {
    if (session.chunks.length === 0) return;
    if (session.dispatched) return;
    session.dispatched = true;

    const total = session.byteLength;
    const merged = new Uint8Array(total);
    let offset = 0;
    for (const chunk of session.chunks) {
      merged.set(new Uint8Array(chunk), offset);
      offset += chunk.byteLength;
    }

    console.debug(
      '[GDocsAudio] MediaSource capture complete —',
      session.chunks.length, 'chunk(s),',
      (total / 1024).toFixed(1), 'KB,',
      'type:', session.mimeType
    );

    window.dispatchEvent(new CustomEvent('__gdocs_audio_captured__', {
      detail: {
        buffer: merged.buffer,
        contentType: mimeTypeToContentType(session.mimeType),
        chunkCount: session.chunks.length,
        source: 'mediasource',
      },
    }));
  }

  function mimeTypeToContentType(mimeType) {
    if (!mimeType) return 'audio/mpeg';
    if (mimeType.includes('mp4') || mimeType.includes('aac')) return 'audio/mp4';
    if (mimeType.includes('webm') || mimeType.includes('opus')) return 'audio/webm';
    if (mimeType.includes('ogg')) return 'audio/ogg';
    if (mimeType.includes('mpeg') || mimeType.includes('mp3')) return 'audio/mpeg';
    // Google TTS over MediaSource is often audio/mp4 with AAC codec
    return 'audio/mp4';
  }

  function isAudioMime(mimeType) {
    if (!mimeType) return false;
    return mimeType.startsWith('audio/') ||
           mimeType.includes('audio') ||
           // Google uses 'audio/mp4; codecs="mp4a.40.2"' or 'audio/webm; codecs=opus'
           mimeType.includes('mp4a') ||
           mimeType.includes('opus');
  }

  // Patch MediaSource.prototype.addSourceBuffer
  const _addSourceBuffer = MediaSource.prototype.addSourceBuffer;
  MediaSource.prototype.addSourceBuffer = function (mimeType) {
    const sourceBuffer = _addSourceBuffer.apply(this, [mimeType]);

    if (isAudioMime(mimeType)) {
      console.debug('[GDocsAudio] Audio SourceBuffer created, mimeType:', mimeType);

      const session = createSession(mimeType);
      audioSessions.set(sourceBuffer, session);

      // Hook appendBuffer to capture each audio chunk
      const _appendBuffer = sourceBuffer.appendBuffer.bind(sourceBuffer);
      sourceBuffer.appendBuffer = function (data) {
        try {
          const buf = data instanceof ArrayBuffer
            ? data.slice(0)
            : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);

          if (buf.byteLength > 0) {
            session.chunks.push(buf);
            session.byteLength += buf.byteLength;
            session.dispatched = false;
            scheduleDispatch(session);
          }
        } catch (e) {
          console.warn('[GDocsAudio] appendBuffer hook error:', e);
        }
        return _appendBuffer(data);
      };
    }

    return sourceBuffer;
  };

  // ─── Fetch hook (SECONDARY — for direct audio responses) ─────────────────

  const TTS_PATTERNS = [
    /\/tts(\?|$)/,
    /texttospeech\.googleapis\.com/,
    /speechs3proto2-pa\.googleapis\.com/,
    /generativelanguage\.googleapis\.com/,
  ];

  function isTtsUrl(url) {
    try {
      const full = new URL(url, location.href).href;
      return TTS_PATTERNS.some((re) => re.test(full));
    } catch (_) {
      return false;
    }
  }

  const _originalFetch = window.fetch.bind(window);

  window.fetch = async function (...args) {
    const request = args[0];
    const url = typeof request === 'string' ? request
      : request instanceof Request ? request.url : '';

    const response = await _originalFetch(...args);

    const contentType = response.headers.get('content-type') || '';

    // Only capture direct audio responses (not the protobuf stream —
    // that's handled by the MediaSource hook above)
    if (contentType.startsWith('audio/') && isTtsUrl(url)) {
      const clone = response.clone();
      clone.arrayBuffer().then((buffer) => {
        if (buffer.byteLength > 0) {
          console.debug('[GDocsAudio] Direct audio fetch captured, bytes:', buffer.byteLength);
          window.dispatchEvent(new CustomEvent('__gdocs_audio_captured__', {
            detail: { buffer, contentType, chunkCount: 1, source: 'fetch' },
          }));
        }
      }).catch(() => {});
    }

    return response;
  };

  // ─── Blob URL fallback — watch for <audio src="blob:..."> ─────────────────

  function tryBlobSrc(audioEl) {
    const src = audioEl.src || audioEl.currentSrc;
    if (!src || !src.startsWith('blob:')) return;

    _originalFetch(src)
      .then((r) => r.arrayBuffer().then((buf) => ({
        buf,
        ct: r.headers.get('content-type') || 'audio/mpeg',
      })))
      .then(({ buf, ct }) => {
        if (buf.byteLength > 0) {
          console.debug('[GDocsAudio] Blob URL fallback captured, bytes:', buf.byteLength);
          window.dispatchEvent(new CustomEvent('__gdocs_audio_captured__', {
            detail: { buffer: buf, contentType: ct, chunkCount: 1, source: 'blob-url' },
          }));
        }
      })
      .catch(() => {});
  }

  function attachAudioListeners(el) {
    if (el.readyState >= 3) {
      tryBlobSrc(el);
    } else {
      el.addEventListener('canplay', () => tryBlobSrc(el), { once: true });
      el.addEventListener('loadeddata', () => tryBlobSrc(el), { once: true });
    }
  }

  new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.tagName === 'AUDIO') attachAudioListeners(node);
        node.querySelectorAll?.('audio').forEach(attachAudioListeners);
      }
      if (m.type === 'attributes' && m.target.tagName === 'AUDIO') {
        attachAudioListeners(m.target);
      }
    }
  }).observe(document.documentElement, {
    childList: true, subtree: true,
    attributes: true, attributeFilter: ['src'],
  });

  document.querySelectorAll('audio').forEach(attachAudioListeners);

  console.debug('[GDocsAudio] page-world.js injected — MediaSource hook active');
})();
