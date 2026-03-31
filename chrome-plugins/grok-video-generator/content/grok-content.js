// Grok Video Generator — content script (grok.com/imagine*)
// Polls localhost:7878/job every 2s, drives Grok UI to generate and download a video.

const SERVER = 'http://localhost:7878';
const POLL_INTERVAL = 2000;

let busy = false;

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

  p.focus();
  p.textContent = '';

  const dt = new DataTransfer();
  dt.setData('text/plain', promptText);
  p.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
  await sleep(400);

  const got = p.textContent || p.innerText || '';
  if (got.length < 5) {
    p.focus();
    document.execCommand('selectAll');
    document.execCommand('insertText', false, promptText);
    log('WARN: Used execCommand fallback for prompt');
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

  // Debug: log all visible buttons in drop-ui (shows in Python log via postStatus)
  if (dropUi) {
    const btnList = Array.from(dropUi.querySelectorAll('button'))
      .filter(b => b.offsetParent !== null)
      .map(b => `[aria=${b.getAttribute('aria-label') || '?'} txt="${b.textContent.trim().slice(0, 20)}"]`)
      .join(', ');
    await postStatus('running', `submitForm debug — buttons: ${btnList.slice(0, 400)}`);
  }

  let btn = null;

  // 1. aria-label="Submit" type="submit" — exact match, no offsetParent check
  //    (offsetParent can be null on position:fixed ancestors even when visible)
  btn = document.querySelector('button[aria-label="Submit"][type="submit"]')
     || document.querySelector('button[aria-label="Submit"]')
     || document.querySelector('form button[type="submit"]');
  if (btn) {
    log(`Submit: found via aria/type selector — ${btn.getAttribute('aria-label')}`);
    btn.click(); await sleep(2000); return;
  }

  // 2. XPath from Chrome DevTools recording
  {
    const res = document.evaluate(
      '//*[@data-testid="drop-ui"]/div/div[2]/div/form/div/div/div[1]/div[3]/div/button',
      document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
    );
    btn = res.singleNodeValue;
    if (btn) { log('Submit: found via XPath'); btn.click(); await sleep(2000); return; }
  }

  // 3. Broad query-bar search
  btn = document.querySelector('div.query-bar button, .query-bar button');
  if (btn) { log('Submit: found via .query-bar'); btn.click(); await sleep(2000); return; }

  // 4. Last resort: Enter key on the prompt field
  const p = findPromptField();
  if (p) {
    log('Submit: using Enter key on prompt field');
    p.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true, cancelable: true }));
    p.dispatchEvent(new KeyboardEvent('keyup',   { key: 'Enter', keyCode: 13, bubbles: true }));
    await sleep(2000);
    return;
  }

  throw new Error('Submit button not found — check "submitForm debug" in log');
}

// ── Wait for video ────────────────────────────────────────────────────────────

async function waitForVideoOnImaginePage() {
  // After submit on /imagine, generation result appears in-page.
  // Look for the download button in the result article area (XPath scoped to article).
  // Do NOT match template gallery videos.
  const MAX_MS = 10 * 60 * 1000;
  const start = Date.now();
  await sleep(5000); // generation takes at least a few seconds

  while (Date.now() - start < MAX_MS) {
    // If page navigated to post page mid-wait, stop — post-page handler takes over
    if (location.pathname.includes('/post/')) return;

    const btn = findDownloadButton();
    if (btn) { log('Download button visible — video ready'); return; }

    // Also accept a <video> inside an article element (result area, not template gallery)
    const articleVideo = document.querySelector('article video[src], main article video');
    if (articleVideo) { log('Article video found — video ready'); return; }

    await sleep(3000);
  }
  throw new Error('Timed out waiting for video on /imagine page');
}

async function waitForVideoOnPostPage() {
  // On /imagine/post/UUID: wait for the generated video to load.
  const MAX_MS = 10 * 60 * 1000;
  const start = Date.now();
  await sleep(5000);

  while (Date.now() - start < MAX_MS) {
    const btn = findDownloadButton();
    if (btn) { log('Download button visible — video ready'); return; }

    const video = document.querySelector('article video[src], video[src]');
    if (video && video.readyState >= 1) { log('Video element ready'); return; }

    await sleep(3000);
  }
  throw new Error('Timed out waiting for video on post page');
}

// ── Download ─────────────────────────────────────────────────────────────────

function findDownloadButton() {
  // Primary: aria-label="Download" — confirmed from button HTML
  const btn = document.querySelector('button[aria-label="Download"], [aria-label="Download"]');
  if (btn) return btn;
  return null;
}

async function clickDownload() {
  const btn = findDownloadButton();
  if (btn) {
    log('Clicking download button');
    btn.click();
    await sleep(2000);
    return;
  }

  // Fallback: grab src from the generated video element and force download
  const video = document.querySelector('article video[src], video[src]');
  if (video && video.src) {
    log('Download via video.src fallback');
    const a = document.createElement('a');
    a.href = video.src;
    a.download = 'grok_video.mp4';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    await sleep(2000);
    return;
  }

  throw new Error('Download button not found and no video src available');
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

function log(msg) { console.log(`[GrokExt] ${msg}`); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Start ────────────────────────────────────────────────────────────────────
setInterval(pollJob, POLL_INTERVAL);
log(`Grok Video Generator loaded — path=${location.pathname}`);
