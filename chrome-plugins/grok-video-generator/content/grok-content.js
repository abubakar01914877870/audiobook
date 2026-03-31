// Grok Video Generator — content script (grok.com/imagine*)
// Polls localhost:7878/job every 2s, drives Grok UI to generate and download a video.

const SERVER = 'http://localhost:7878';
const POLL_INTERVAL = 2000;

let busy = false;

// ── Network interception — capture the generated_video.mp4 URL ───────────────
let capturedVideoUrl = null;

// PerformanceObserver watches all resource loads in the page
try {
  const perfObserver = new PerformanceObserver(list => {
    for (const entry of list.getEntries()) {
      if (entry.name && /generated_video.*\.mp4|\.mp4.*generated_video/i.test(entry.name)) {
        if (!capturedVideoUrl) {
          capturedVideoUrl = entry.name;
          log('PerformanceObserver: captured video URL — ' + entry.name);
          postStatus('running', 'video_url_detected: ' + entry.name);
        }
      }
    }
  });
  perfObserver.observe({ type: 'resource', buffered: true });
} catch (e) {
  log('PerformanceObserver not available: ' + e.message);
}

// Also intercept fetch() in the page world by injecting a tiny script tag.
// This catches URLs that PerformanceObserver might miss (e.g. blob: URLs, SSE).
(function injectNetworkSniffer() {
  const script = document.createElement('script');
  script.textContent = `(function() {
    const _origFetch = window.fetch;
    window.fetch = function(input, init) {
      const url = (typeof input === 'string') ? input : (input && input.url) || '';
      if (url && /generated_video.*\\.mp4|\\.mp4.*generated_video/i.test(url)) {
        window.__grokCapturedVideoUrl = url;
        document.dispatchEvent(new CustomEvent('__grokVideoUrl', { detail: url }));
      }
      return _origFetch.apply(this, arguments);
    };
    const _XHR = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
      if (url && /generated_video.*\\.mp4|\\.mp4.*generated_video/i.test(url)) {
        window.__grokCapturedVideoUrl = url;
        document.dispatchEvent(new CustomEvent('__grokVideoUrl', { detail: url }));
      }
      return _XHR.apply(this, arguments);
    };
  })();`;
  (document.head || document.documentElement).appendChild(script);
  script.remove();

  // Listen for the custom event from the injected script
  document.addEventListener('__grokVideoUrl', (e) => {
    if (!capturedVideoUrl) {
      capturedVideoUrl = e.detail;
      log('fetch/XHR intercept: captured video URL — ' + e.detail);
      postStatus('running', 'video_url_detected: ' + e.detail);
    }
  });
})();

async function pollJob() {
  if (busy) return;
  let job;
  try {
    const r = await fetch(`${SERVER}/job`, { cache: 'no-store' });
    if (!r.ok) return;
    job = await r.json();
  } catch (_) {
    return;
  }
  if (!job) return;

  // New job on the form page
  if (job.status === 'pending' && !location.pathname.includes('/post/')) {
    busy = true;
    log('Job received — starting automation');
    try { await runJob(job); }
    catch (err) { log('ERROR: ' + err.message); await postStatus('failed', err.message); }
    finally { busy = false; }
    return;
  }

  // Job was submitted and page navigated to the result post page — finish it here
  if (job.status === 'running' && location.pathname.includes('/imagine/post/')) {
    busy = true;
    log('On result page — waiting for generation to finish...');
    try { await finishOnResultPage(); }
    catch (err) { log('ERROR on result page: ' + err.message); await postStatus('failed', err.message); }
    finally { busy = false; }
    return;
  }
}

// ── Main job flow (runs on /imagine) ────────────────────────────────────────

async function runJob(job) {
  await postStatus('running', 'started');

  // Redirect if we're on a stale post page
  if (location.pathname.includes('/post/')) {
    location.href = 'https://grok.com/imagine';
    return;
  }

  await step('Switch to Video tab', switchToVideoTab);
  await step('Select 720p',         () => clickButtonByText('720p'));
  await step('Select 10s',          () => clickButtonByText('10s'));
  await step('Type prompt',         () => typePrompt(job.prompt));
  await step('Upload image',        () => uploadImage(job.image_b64, job.image_filename));
  await sleep(5000); // wait for Grok to finish processing the uploaded image
  await step('Submit form',         submitForm);

  // Reset stale captured URL — PerformanceObserver picks up template/preview videos
  // from page load. We only want URLs captured AFTER submission.
  capturedVideoUrl = null;
  log('capturedVideoUrl reset after submit');

  // Verify generation actually started — look for a loading/progress indicator
  await step('Verify generation started', verifyGenerationStarted);

  // Wait up to 5s to see if page navigates to a post/result page
  await sleep(3000);
  if (location.pathname.includes('/post/')) {
    log('Navigated to result post page — handing off to post-page handler');
    // finishOnResultPage() will be called by the next pollJob tick on the new page
    return;
  }

  // Still on /imagine — result appears inline
  await step('Wait for video',  waitForVideoOnImaginePage);
  await step('Download video',  clickDownload);
  await postStatus('done', 'video downloaded');
  log('Job complete (inline result)');
}

// ── Finish job on the /imagine/post/UUID result page ────────────────────────

async function finishOnResultPage() {
  capturedVideoUrl = null; // reset stale page-load URLs
  await step('Wait for video', waitForVideoOnPostPage);
  await step('Download video', clickDownload);
  await postStatus('done', 'video downloaded');
  log('Job complete (post-page result)');
}

// ── Step wrapper ─────────────────────────────────────────────────────────────

async function step(name, fn) {
  log(`Step: ${name}`);
  await postStatus('running', `step: ${name}`);
  await fn();
}

// ── Navigation / tab switching ───────────────────────────────────────────────

async function switchToVideoTab() {
  // From recording: button.text-primary-foreground span — the "Video" tab
  // Try exact text match on buttons, tabs, anchors
  for (const el of document.querySelectorAll('button, [role="tab"], a')) {
    if (el.textContent.trim() === 'Video' && el.offsetParent !== null) {
      el.click(); await sleep(1000); return;
    }
  }
  // Span inside a button
  for (const span of document.querySelectorAll('span')) {
    if (span.textContent.trim() === 'Video' && span.offsetParent !== null) {
      const btn = span.closest('button, [role="tab"], a');
      if (btn) { btn.click(); await sleep(1000); return; }
    }
  }
  log('WARN: Video tab not found — assuming already in video mode');
  await sleep(500);
}

function clickButtonByText(text) {
  const root = document.querySelector('[data-testid="drop-ui"]') || document;
  for (const el of root.querySelectorAll('button, span')) {
    if (el.textContent.trim() === text && el.offsetParent !== null) {
      el.click(); return;
    }
  }
  log(`WARN: button "${text}" not found — skipping`);
}

// ── Prompt entry ─────────────────────────────────────────────────────────────

async function typePrompt(promptText) {
  const p = findPromptField();
  if (!p) throw new Error('Prompt field not found');

  log(`typePrompt: field found — tag=${p.tagName} ce="${p.contentEditable}" existing="${(p.textContent||'').slice(0,30)}"`);

  p.focus();
  p.textContent = '';

  const dt = new DataTransfer();
  dt.setData('text/plain', promptText);
  p.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
  await sleep(400);

  const got = p.textContent || p.innerText || '';
  log(`typePrompt: after paste text="${got.slice(0, 60)}"`);
  if (got.length < 5) {
    p.focus();
    document.execCommand('selectAll');
    document.execCommand('insertText', false, promptText);
    log('typePrompt: used execCommand fallback');
  }
  await sleep(300);
}

function findPromptField() {
  const xpath = '//*[@data-testid="drop-ui"]/div/div[2]/div/form/div/div/div/div[2]/div[2]/div/div/div/div/p';
  const res = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
  const el = res.singleNodeValue;
  if (el && el.offsetParent !== null) return el;

  const form = document.querySelector('[data-testid="drop-ui"] form');
  if (form) {
    const p = form.querySelector('[contenteditable="true"] p, [contenteditable] p');
    if (p) return p;
    return form.querySelector('[contenteditable="true"], [contenteditable]') || null;
  }
  return null;
}

// ── Image upload ─────────────────────────────────────────────────────────────

async function uploadImage(imageB64, filename) {
  const input = document.querySelector('[data-testid="drop-ui"] form input[type="file"][accept="image/*"]')
             || document.querySelector('[data-testid="drop-ui"] input[type="file"]');
  if (!input) throw new Error('File input not found');

  const mimeType = filename.endsWith('.png') ? 'image/png' : 'image/jpeg';
  const bytes = Uint8Array.from(atob(imageB64), c => c.charCodeAt(0));
  const file = new File([bytes], filename, { type: mimeType });

  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(1500);
}

// ── Submit ───────────────────────────────────────────────────────────────────

async function submitForm() {
  const dropUi = document.querySelector('[data-testid="drop-ui"]');

  // Debug: log ALL visible buttons on the page with type + aria-label
  const allBtns = Array.from(document.querySelectorAll('button'))
    .filter(b => b.offsetParent !== null)
    .map(b => `[type=${b.type || '?'} aria="${b.getAttribute('aria-label') || ''}" txt="${b.textContent.trim().slice(0, 20)}"]`)
    .join(', ');
  await postStatus('running', `submitForm debug ALL buttons: ${allBtns.slice(0, 800)}`);

  let btn = null;

  // 1. XPath from Puppeteer recording — most specific, points exactly to the generate button
  {
    const res = document.evaluate(
      '//*[@data-testid="drop-ui"]/div/div[2]/div/form/div/div/div[1]/div[3]/div/button',
      document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
    );
    btn = res.singleNodeValue;
    if (btn) {
      if (btn.disabled) {
        log(`Submit: button disabled — polling until enabled...`);
        const maxWait = 120000;
        const start = Date.now();
        while (btn.disabled) {
          if (Date.now() - start > maxWait) {
            log(`Submit: button still disabled after 120s — falling through`);
            btn = null;
            break;
          }
          await sleep(300);
          // re-query in case the DOM replaced the node
          const res2 = document.evaluate(
            '//*[@data-testid="drop-ui"]/div/div[2]/div/form/div/div/div[1]/div[3]/div/button',
            document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
          );
          btn = res2.singleNodeValue;
          if (!btn) { log('Submit: button node gone from DOM — falling through'); break; }
        }
      }
      if (btn && !btn.disabled) {
        log(`Submit: clicking enabled button (aria="${btn.getAttribute('aria-label')}")`);
        btn.click();
        await sleep(2000);
        return;
      }
    }
  }

  // 2. query-bar — from recording: div.query-bar > div.absolute button
  btn = document.querySelector('[data-testid="drop-ui"] div.query-bar div.absolute button')
     || document.querySelector('div.query-bar > div.absolute button')
     || document.querySelector('div.query-bar button');
  if (btn) { log(`Submit: found via query-bar — aria="${btn.getAttribute('aria-label')}"`); btn.click(); await sleep(2000); return; }

  // 3. aria-label="Submit" (fallback — may match multiple)
  btn = document.querySelector('button[aria-label="Submit"]');
  if (btn) { log(`Submit: found via aria-label`); btn.click(); await sleep(2000); return; }

  // 4. Last resort: Enter key on the prompt field
  const p = findPromptField();
  if (p) {
    log('Submit: using Enter key on prompt field');
    p.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true, cancelable: true }));
    p.dispatchEvent(new KeyboardEvent('keyup',   { key: 'Enter', keyCode: 13, bubbles: true }));
    await sleep(2000);
    return;
  }

  throw new Error('Submit button not found — check "submitForm debug ALL buttons" in log');
}

// ── Verify generation started ────────────────────────────────────────────────

async function verifyGenerationStarted() {
  // After submit, Grok shows "Generating" text and/or a "Cancel" button on screen.
  // We poll for up to 20s looking for these signals.
  log('verifyGenerationStarted: watching for "Generating" or "Cancel" on screen...');
  await postStatus('running', 'verifyGenerationStarted: waiting for Generating/Cancel signal...');

  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await sleep(1000);

    // Strongest signal: page navigated to /post/ result page
    if (location.pathname.includes('/post/')) {
      log('✅ verifyGenerationStarted: navigated to /post/ — generation CONFIRMED');
      await postStatus('running', '✅ Generation confirmed: navigated to /post/');
      return;
    }

    // Check all visible text on page for "generating" or "cancel"
    const bodyText = document.body.innerText || '';
    const hasGenerating = /generating/i.test(bodyText);
    const hasCancel     = /\bcancel\b/i.test(bodyText);

    if (hasGenerating || hasCancel) {
      log(`✅ verifyGenerationStarted: found "${hasGenerating ? 'Generating' : ''}${hasCancel ? ' Cancel' : ''}" on screen — generation CONFIRMED`);
      await postStatus('running', `✅ Generation confirmed: screen shows "${hasGenerating ? 'Generating' : ''}${hasCancel ? ' Cancel' : ''}"`);
      return;
    }

    // Submit button gone/disabled = form accepted
    const submitXp = document.evaluate(
      '//*[@data-testid="drop-ui"]/div/div[2]/div/form/div/div/div[1]/div[3]/div/button',
      document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
    ).singleNodeValue;
    if (!submitXp) {
      log('✅ verifyGenerationStarted: submit button gone from DOM — generation CONFIRMED');
      await postStatus('running', '✅ Generation confirmed: submit button removed from DOM');
      return;
    }
  }

  // Did not detect start signal — log warning but do NOT abort (might still be running)
  log('⚠️ verifyGenerationStarted: no "Generating"/"Cancel" detected after 20s — submit may have failed');
  await postStatus('running', '⚠️ WARNING: no generation start signal — check if submit button worked');
}

// ── Wait for video ────────────────────────────────────────────────────────────

async function waitForVideoOnImaginePage() {
  const MAX_MS = 10 * 60 * 1000;  // 10 min total
  const MIN_MS = 2 * 60 * 1000;   // 2 min minimum — Grok always takes at least this long
  const start = Date.now();

  log('waitForVideoOnImaginePage: waiting minimum 2 minutes before checking...');
  await postStatus('running', 'waiting_for_video: min 2min wait started');

  while (Date.now() - start < MAX_MS) {
    const elapsed = Math.round((Date.now() - start) / 1000);

    // If page navigated to post page mid-wait, stop — post-page handler takes over
    if (location.pathname.includes('/post/')) {
      log(`[${elapsed}s] Navigated to /post/ mid-wait`);
      return;
    }

    // Enforce minimum 2-minute wait — don't check for video before then
    if (Date.now() - start < MIN_MS) {
      if (elapsed % 30 === 0) {
        await postStatus('running', `waiting_for_video: ${elapsed}s / 120s minimum`);
      }
      await sleep(3000);
      continue;
    }

    // After 2 min — start checking for completion signals
    if (capturedVideoUrl) {
      log(`[${elapsed}s] Video URL captured — generation complete: ${capturedVideoUrl}`);
      await postStatus('running', `video_ready: URL captured at ${elapsed}s`);
      return;
    }

    const btn = findDownloadButton();
    if (btn) { log(`[${elapsed}s] Download button visible — video ready`); return; }

    const articleVideo = document.querySelector('article video[src], main article video');
    if (articleVideo) {
      const src = articleVideo.src || articleVideo.currentSrc;
      if (src && !capturedVideoUrl) { capturedVideoUrl = src; }
      log(`[${elapsed}s] Article video found (src=${src ? src.slice(-60) : 'none'})`);
      if (src) await postStatus('running', 'video_url_detected: ' + src);
      return;
    }

    if (elapsed % 15 === 0) {
      await postStatus('running', `waiting_for_video: ${elapsed}s elapsed`);
    }
    await sleep(3000);
  }
  throw new Error('Timed out waiting for video on /imagine page');
}

async function waitForVideoOnPostPage() {
  const MAX_MS = 10 * 60 * 1000;  // 10 min total
  const MIN_MS = 2 * 60 * 1000;   // 2 min minimum
  const start = Date.now();

  log('waitForVideoOnPostPage: waiting minimum 2 minutes before checking...');
  await postStatus('running', 'waiting_for_video: min 2min wait started (post page)');

  while (Date.now() - start < MAX_MS) {
    const elapsed = Math.round((Date.now() - start) / 1000);

    // Enforce minimum 2-minute wait
    if (Date.now() - start < MIN_MS) {
      if (elapsed % 30 === 0) {
        await postStatus('running', `waiting_for_video: ${elapsed}s / 120s minimum`);
      }
      await sleep(3000);
      continue;
    }

    if (capturedVideoUrl) {
      log(`[${elapsed}s] Video URL captured — generation complete: ${capturedVideoUrl}`);
      await postStatus('running', `video_ready: URL captured at ${elapsed}s`);
      return;
    }

    const btn = findDownloadButton();
    if (btn) { log(`[${elapsed}s] Download button visible — video ready`); return; }

    const video = document.querySelector('article video[src], video[src]');
    if (video && video.readyState >= 1) {
      const src = video.src || video.currentSrc;
      if (src && !capturedVideoUrl) { capturedVideoUrl = src; }
      log(`[${elapsed}s] Video element ready (src=${src ? src.slice(-60) : 'none'})`);
      if (src) await postStatus('running', 'video_url_detected: ' + src);
      return;
    }

    if (elapsed % 15 === 0) {
      await postStatus('running', `waiting_for_video: ${elapsed}s elapsed`);
    }
    await sleep(3000);
  }
  throw new Error('Timed out waiting for video on post page');
}

// ── Download ─────────────────────────────────────────────────────────────────

function findDownloadButton() {
  // 1. XPath from Puppeteer recording — button[5] in the article action bar
  const xpRes = document.evaluate(
    '//*[@data-testid="drop-ui"]/div/div[1]/div/main/article/div/div[4]/div[2]/button[5]',
    document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
  );
  const xpBtn = xpRes.singleNodeValue;
  if (xpBtn && xpBtn.offsetParent !== null) return xpBtn;

  // 2. aria-label="Download"
  const btn = document.querySelector('button[aria-label="Download"], [aria-label="Download"]');
  if (btn) return btn;

  return null;
}

async function clickDownload() {
  // ── Try to resolve the direct video URL ──────────────────────────────────
  // Priority: 1) captured via PerformanceObserver / fetch intercept
  //           2) <video src> on the page
  //           3) <source src> inside a video
  //           4) download button href
  let videoUrl = capturedVideoUrl;

  if (!videoUrl) {
    const video = document.querySelector('article video[src], video[src]');
    if (video) {
      const src = video.src || video.currentSrc;
      if (src && (src.startsWith('http') || src.startsWith('blob'))) {
        videoUrl = src;
        log('Resolved video URL from <video src>: ' + src.slice(-80));
      }
    }
  }

  if (!videoUrl) {
    const source = document.querySelector('article source[src*=".mp4"], video source[src*=".mp4"]');
    if (source && source.src) {
      videoUrl = source.src;
      log('Resolved video URL from <source src>: ' + videoUrl.slice(-80));
    }
  }

  // ── If we have the URL, use chrome.downloads.download (bypasses CORS) ────
  if (videoUrl) {
    log('Triggering chrome.downloads.download for: ' + videoUrl.slice(-80));
    await postStatus('running', 'downloading_via_extension: ' + videoUrl);

    await new Promise((resolve) => {
      chrome.runtime.sendMessage(
        { type: 'DOWNLOAD_VIDEO', url: videoUrl, filename: 'grok_video.mp4' },
        (resp) => {
          if (resp && resp.downloadId) {
            log('chrome.downloads.download started — downloadId=' + resp.downloadId);
          } else {
            log('WARN: chrome.downloads.download — no downloadId in response');
          }
          resolve();
        }
      );
    });
    await sleep(3000);
    return;
  }

  // ── Fallback: click the Download button (works for same-origin or pre-authed URLs) ──
  const btn = findDownloadButton();
  if (btn) {
    log('WARN: No video URL captured — falling back to download button click');
    await postStatus('running', 'fallback: clicking download button (no URL captured)');
    btn.click();
    await sleep(3000);
    return;
  }

  throw new Error('Download failed: no video URL captured and no download button found');
}

// ── Helpers ──────────────────────────────────────────────────────────────────

async function postStatus(status, message) {
  try {
    await fetch(`${SERVER}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, message }),
    });
  } catch (_) {}
}

function log(msg) {
  console.log(`[GrokExt] ${msg}`);
  // Fire-and-forget to Python debug server so logs appear in grok_debug.log
  fetch(`${SERVER}/log`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: msg }),
  }).catch(() => {});
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Start ────────────────────────────────────────────────────────────────────
setInterval(pollJob, POLL_INTERVAL);
log(`Grok Video Generator loaded — path=${location.pathname}`);
