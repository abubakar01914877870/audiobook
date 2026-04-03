#!/usr/bin/env python3
"""
generate_video.py — Generate Grok videos for scene images from *_meta.md prompts.

Rules:
  • image missing          → skip (image required for video)
  • image done + video done → skip
  • image done + video miss → generate video
  • thumbnail label         → always skip (thumbnails never get videos)

Video generation uses the Grok web UI via a Chrome extension + local HTTP server.
Chrome is killed after each video.

Usage:
    python generate_video.py ./clown_vol_1/output/ch_11
"""

import os
import sys
import re
import time
import argparse
import subprocess
import shutil
import signal
import base64
from typing import Optional


def _handle_sigint(_sig, _frame):
    print("\n\nInterrupted — killing Chrome and exiting...")
    os.system("pkill -x 'Google Chrome' 2>/dev/null")
    os._exit(1)

# Register immediately at import time — not just in main()
signal.signal(signal.SIGINT, _handle_sigint)


# ── Grok constants ────────────────────────────────────────────────────────────
GROK_CHROME_PROFILE = "Profile 11"
GROK_URL            = "https://grok.com/"
GROK_EXTENSION_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "chrome-plugins", "grok-video-generator"
)
GROK_VIDEO_WAIT = 600    # max seconds to wait for video generation (10 min)
GROK_FILE_WAIT  = 120    # max seconds to wait for MP4 in Downloads
GROK_DEBUG      = True
GROK_LOG_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "grok_debug.log")


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────

def find_meta_file(folder: str) -> Optional[str]:
    for fname in sorted(os.listdir(folder)):
        if fname.endswith("_meta.md"):
            return os.path.join(folder, fname)
    return None


def _parse_image_prompt_blocks(meta_path: str) -> list:
    with open(meta_path, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(
        r'###\s*Image Prompt\s+(\d+)\s*[—\-]+\s*(\w+)\s*\n(.*?)(?=\n###\s*Image Prompt|\n###\s*YouTube|\Z)',
        re.DOTALL
    )
    return [(m.group(1).zfill(2), m.group(2).strip(), m.group(3).strip())
            for m in pattern.finditer(content)]


def extract_all_image_prompts(meta_path: str) -> list:
    results = []
    for num, label, block in _parse_image_prompt_blocks(meta_path):
        m = re.search(r'\*\*Prompt:\*\*(.*?)(?=\n\*\*|\Z)', block, re.DOTALL | re.IGNORECASE)
        actual = m.group(1).strip() if m else block
        if actual:
            results.append((num, label, actual))
    if results:
        return results
    with open(meta_path, "r", encoding="utf-8") as f:
        content = f.read()
    old = re.search(r'###\s*Image Generation Prompt\s*\n(.*?)(?=\n###|\Z)', content, re.DOTALL)
    if old:
        return [("01", "Thumbnail", old.group(1).strip())]
    return []


def extract_all_video_prompts(meta_path: str) -> list:
    results = []
    for _num, _label, block in _parse_image_prompt_blocks(meta_path):
        m = re.search(r'\*\*Video Prompt:\*\*(.*?)(?=\n\*\*|\Z)', block, re.DOTALL | re.IGNORECASE)
        results.append(m.group(1).strip() if m else "")
    return results


def get_output_path(folder: str, stem: str, num: str, label: str) -> str:
    suffix = "thumb" if label.lower() == "thumbnail" else "scene"
    return os.path.join(folder, f"{stem}_{num}_{suffix}.png")


def get_video_output_path(image_path: str) -> str:
    return os.path.splitext(image_path)[0] + ".mp4"


# ─────────────────────────────────────────────────────────────────────────────
# Chrome / AppleScript helpers
# ─────────────────────────────────────────────────────────────────────────────

def run_osascript(script: str) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.stdout.strip()


def run_js_in_chrome(js: str) -> str:
    js_escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Google Chrome" to execute active tab of front window javascript "{js_escaped}"'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0 and result.stderr:
        print(f"  [JS error] {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def check_js_enabled() -> bool:
    return run_js_in_chrome("1+1") == "2"


def enable_js_from_apple_events():
    print("  Checking 'Allow JavaScript from Apple Events'...")
    check_script = """
tell application "System Events"
    tell process "Google Chrome"
        set mi to menu item "Allow JavaScript from Apple Events" of menu 1 of menu item "Developer" of menu "View" of menu bar 1
        return value of mi
    end tell
end tell
"""
    result = subprocess.run(["osascript", "-e", check_script], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip() == "1":
        print("  Already enabled.")
        return
    print("  Enabling 'Allow JavaScript from Apple Events'...")
    enable_script = """
tell application "Google Chrome" to activate
delay 0.5
tell application "System Events"
    tell process "Google Chrome"
        click menu item "Allow JavaScript from Apple Events" of menu 1 of menu item "Developer" of menu "View" of menu bar 1
    end tell
end tell
"""
    result = subprocess.run(["osascript", "-e", enable_script], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Could not auto-enable: {result.stderr.strip()[:120]}")
    else:
        print("  Enabled — reloading page...")
        run_osascript('tell application "Google Chrome" to reload active tab of front window')
        time.sleep(4)


def _open_chrome_with_profile(profile: str, url: str, boot_wait: int = 6):
    subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
    time.sleep(3)
    subprocess.Popen([
        "open", "-a", "Google Chrome", "--args",
        f"--profile-directory={profile}",
        url,
    ])
    time.sleep(boot_wait)
    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(1)


def wait_for_grok(max_wait: int = 40) -> bool:
    print("  Waiting for Grok to load...", end="", flush=True)
    for _ in range(max_wait):
        url = run_osascript(
            'tell application "Google Chrome" to return URL of active tab of front window'
        )
        if "grok.com" in url:
            print(" ready.")
            return True
        print(".", end="", flush=True)
        time.sleep(1)
    print(" timed out.")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Grok debug helpers
# ─────────────────────────────────────────────────────────────────────────────

_grok_log_fh = None


def _grok_write(line: str):
    print(line)
    if _grok_log_fh:
        _grok_log_fh.write(line + "\n")
        _grok_log_fh.flush()


def _grok_log(label: str, msg: str):
    if GROK_DEBUG:
        _grok_write(f"  [GROK:{label}] {msg}")


def _grok_dump_dom(label: str):
    if not GROK_DEBUG:
        return
    _grok_write(f"\n  [GROK-DOM:{label}] ══ DOM DUMP ══════════════════════════════")
    url = run_osascript('tell application "Google Chrome" to return URL of active tab of front window')
    _grok_write(f"  [GROK-DOM:{label}] URL: {url}")
    btns = run_js_in_chrome(r"""
(function() {
    var btns = Array.from(document.querySelectorAll('button,[role=button]'));
    return btns
        .filter(function(b) { return b.offsetParent !== null; })
        .map(function(b) {
            return (b.getAttribute('aria-label') || b.innerText || b.title || '')
                   .trim().replace(/\n/g,' ').slice(0,50);
        })
        .filter(function(t) { return t.length > 0; })
        .slice(0, 25)
        .join(' | ');
})()
""")
    _grok_write(f"  [GROK-DOM:{label}] Buttons: {btns}")
    text = run_js_in_chrome(r"(document.body.innerText || '').replace(/\n+/g,' ').trim().slice(-500)")
    _grok_write(f"  [GROK-DOM:{label}] Page text (last 500): {text}")
    _grok_write(f"  [GROK-DOM:{label}] ═════════════════════════════════════════════\n")


# ─────────────────────────────────────────────────────────────────────────────
# Grok helpers
# ─────────────────────────────────────────────────────────────────────────────

def setup_grok_chrome() -> bool:
    print("  Opening Chrome with 'grok' profile...")
    _open_chrome_with_profile(GROK_CHROME_PROFILE, GROK_URL, boot_wait=8)
    if not wait_for_grok(max_wait=40):
        print("  Error: Grok did not load in time.")
        return False
    if not check_js_enabled():
        enable_js_from_apple_events()
        time.sleep(1)
    return True


def grok_navigate_to_image_to_video() -> bool:
    _grok_log("navigate", "--- navigate_to_image_to_video ---")
    _grok_dump_dom("nav-start")
    r = run_js_in_chrome(r"""
(function() {
    var el = document.querySelector('div.pb-1 > div:nth-of-type(4) span');
    if (el && el.offsetParent !== null) { el.click(); return 'clicked:recording-selector'; }
    var links = Array.from(document.querySelectorAll('a,button,[role=button],[role=link],span'));
    var media = links.find(function(l) {
        var t = (l.innerText || l.textContent || '').trim().toLowerCase();
        return (t === 'media' || t === 'create' || t === 'aurora'
                || t.indexOf('image') !== -1 || t.indexOf('video') !== -1)
               && l.offsetParent !== null && t.length < 30;
    });
    if (media) { media.click(); return 'clicked:text:' + (media.innerText||'').trim().slice(0,30); }
    var nav = Array.from(document.querySelectorAll('nav a, nav button, aside a, aside button'));
    var navText = nav.filter(function(n) { return n.offsetParent !== null; })
        .map(function(n) { return (n.innerText||n.textContent||'').trim().slice(0,30); })
        .filter(function(t) { return t.length > 0; }).join(' | ');
    return 'not found — nav items: ' + (navText || 'none');
})()
""")
    _grok_log("step3-nav", r)
    if r.startswith('not found'):
        _grok_dump_dom("step3-fail")
        return False
    time.sleep(2)
    r = run_js_in_chrome(r"""
(function() {
    var el = document.querySelector('button.text-primary-foreground span');
    if (el && el.offsetParent !== null) { el.click(); return 'clicked:recording-selector'; }
    var dropBtns = Array.from(document.querySelectorAll('[data-testid="drop-ui"] button'));
    if (dropBtns.length >= 2) { dropBtns[1].click(); return 'clicked:drop-ui-btn[1]'; }
    if (dropBtns.length >= 1) { dropBtns[0].click(); return 'clicked:drop-ui-btn[0]'; }
    var all = Array.from(document.querySelectorAll('button,[role=button],[role=tab]'));
    var vid = all.find(function(b) {
        var t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
        return t.indexOf('video') !== -1 && b.offsetParent !== null;
    });
    if (vid) { vid.click(); return 'clicked:text-video:' + (vid.innerText||'').trim().replace(/\n/g,' ').slice(0,30); }
    var visible = all
        .filter(function(b) { return b.offsetParent !== null; })
        .map(function(b) { return (b.getAttribute('aria-label') || b.innerText || '').trim().replace(/\n/g,' ').slice(0,30); })
        .filter(function(t) { return t.length > 0; }).slice(0, 15).join(' | ');
    return 'not found — visible buttons: ' + visible;
})()
""")
    _grok_log("step4-image-to-video-btn", r)
    if r.startswith('not found'):
        _grok_dump_dom("step4-fail")
        return False
    time.sleep(2)
    return True


def grok_select_quality_and_duration() -> bool:
    _grok_log("quality", "--- select_quality_and_duration ---")
    r = run_js_in_chrome(r"""
(function() {
    var el = document.querySelector('div.flex-wrap > div:nth-of-type(2) button.text-primary span');
    if (el && el.offsetParent !== null) { el.click(); return 'clicked:recording-selector'; }
    var spans = Array.from(document.querySelectorAll('span,button'));
    var el720 = spans.find(function(s) { return s.textContent.trim() === '720p' && s.offsetParent !== null; });
    if (el720) { el720.click(); return 'clicked:text-720p'; }
    return 'not found';
})()
""")
    _grok_log("step5-720p", r)
    time.sleep(1)
    r = run_js_in_chrome(r"""
(function() {
    var el = document.querySelector('div:nth-of-type(3) button.text-primary span');
    if (el && el.offsetParent !== null) { el.click(); return 'clicked:recording-selector'; }
    var spans = Array.from(document.querySelectorAll('span,button'));
    var el10s = spans.find(function(s) { return s.textContent.trim() === '10s' && s.offsetParent !== null; });
    if (el10s) { el10s.click(); return 'clicked:text-10s'; }
    return 'not found';
})()
""")
    _grok_log("step6-10s", r)
    time.sleep(1)
    return True


def grok_upload_image(image_path: str) -> bool:
    _grok_log("upload", f"--- upload_image: {image_path} ---")
    if not os.path.exists(image_path):
        _grok_log("upload", f"ERROR: image file does not exist: {image_path}")
        return False
    _grok_log("upload", f"Image file on disk: {os.path.getsize(image_path):,} bytes")
    _grok_dump_dom("upload-start")
    abs_image_path = os.path.abspath(image_path)
    _grok_log("upload", "Injecting image via DataTransfer (base64 chunks)...")
    try:
        with open(abs_image_path, 'rb') as _f:
            _raw = _f.read()
        _b64 = base64.b64encode(_raw).decode('ascii')
        run_js_in_chrome("window._imgChunks = []")
        _CHUNK = 80000
        _chunks = [_b64[i:i+_CHUNK] for i in range(0, len(_b64), _CHUNK)]
        _grok_log("upload-inject", f"Sending {len(_chunks)} chunks...")
        for _i, _chunk in enumerate(_chunks):
            run_js_in_chrome(f"window._imgChunks.push('{_chunk}')")
            if _i % 20 == 19:
                _grok_log("upload-inject", f"  {_i+1}/{len(_chunks)} chunks sent")
        _fname = os.path.basename(abs_image_path)
        _ext   = os.path.splitext(_fname)[1].lower().lstrip('.')
        _mime  = {'png': 'image/png', 'jpg': 'image/jpeg',
                  'jpeg': 'image/jpeg', 'webp': 'image/webp'}.get(_ext, 'image/png')
        inject_result = run_js_in_chrome(f"""
(function() {{
    try {{
        var b64 = window._imgChunks.join('');
        window._imgChunks = null;
        var binStr = atob(b64);
        var arr = new Uint8Array(binStr.length);
        for (var i = 0; i < binStr.length; i++) arr[i] = binStr.charCodeAt(i);
        var blob = new Blob([arr], {{type: '{_mime}'}});
        var file = new File([blob], '{_fname}', {{type: '{_mime}'}});
        var fi = document.querySelector('input[type="file"]');
        var fiOk = false;
        if (fi) {{
            try {{ var dt = new DataTransfer(); dt.items.add(file); fi.files = dt.files; fiOk = true; }} catch(e) {{}}
            fi.dispatchEvent(new Event('change', {{bubbles: true}}));
            fi.dispatchEvent(new Event('input',  {{bubbles: true}}));
        }}
        var dropEl = document.querySelector('[data-testid="drop-ui"]');
        if (!dropEl) {{
            var els = Array.from(document.querySelectorAll('*'));
            dropEl = els.find(function(e) {{
                return (e.innerText || '').toLowerCase().indexOf('upload or drop') !== -1
                       && e.offsetParent !== null;
            }});
        }}
        var dropOk = false;
        if (dropEl) {{
            var dt2 = new DataTransfer(); dt2.items.add(file);
            dropEl.dispatchEvent(new DragEvent('dragenter', {{bubbles: true, dataTransfer: dt2}}));
            dropEl.dispatchEvent(new DragEvent('dragover',  {{bubbles: true, cancelable: true, dataTransfer: dt2}}));
            dropEl.dispatchEvent(new DragEvent('drop',      {{bubbles: true, cancelable: true, dataTransfer: dt2}}));
            dropOk = true;
        }}
        return 'injected:fi=' + fiOk + ' drop=' + dropOk + ' size=' + file.size;
    }} catch(e) {{
        return 'inject-error:' + e.message;
    }}
}})()
""")
        _grok_log("upload-inject", inject_result)
    except Exception as _e:
        _grok_log("upload-inject", f"Exception during injection: {_e}")

    time.sleep(3)
    for attempt in range(15):
        r = run_js_in_chrome(r"""
(function() {
    var fi = document.querySelector('input[type="file"]');
    if (fi && fi.files && fi.files.length > 0)
        return 'uploaded:file-input name=' + fi.files[0].name;
    var imgs = Array.from(document.querySelectorAll('img'));
    var thumb = imgs.find(function(i) {
        return i.naturalWidth > 30 && i.offsetParent !== null
               && (i.src.indexOf('blob:') === 0 || i.src.indexOf('data:') === 0);
    });
    if (thumb) return 'uploaded:blob-preview';
    var body = (document.body.innerText || '').toLowerCase();
    if (body.indexOf('upload or drop') === -1 && body.indexOf('drop image') === -1)
        return 'possibly-uploaded:upload-text-gone';
    return 'waiting';
})()
""")
        _grok_log(f"upload-verify-{attempt+1}/15", r)
        if r.startswith('uploaded:'):
            _grok_log("upload", "Image upload confirmed.")
            return True
        time.sleep(1.5)

    _grok_log("upload", "Upload not confirmed after all attempts — proceeding anyway.")
    return True


def grok_type_prompt(video_prompt: str) -> bool:
    _grok_log("prompt", "--- grok_type_prompt ---")
    if not video_prompt:
        _grok_log("prompt", "No video prompt — skipping text entry.")
        return True
    subprocess.run(["pbcopy"], input=video_prompt.encode("utf-8"), check=True)
    _grok_log("prompt", "Prompt copied to clipboard.")

    # Retry loop: paste prompt, verify it stuck, retry if React re-render wiped it
    for attempt in range(3):
        if attempt > 0:
            _grok_log("prompt", f"Retry {attempt+1}/3 — re-pasting prompt...")
            time.sleep(2)  # wait for re-render to settle

        coords = run_js_in_chrome(r"""
(function() {
    var xpathExpr = '//*[@data-testid="drop-ui"]/div/div[2]/div/form/div/div/div/div[2]/div[2]/div/div/div/div/p';
    var res = document.evaluate(xpathExpr, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
    var el = res.singleNodeValue;
    if (!el) {
        var ces = Array.from(document.querySelectorAll('[contenteditable="true"]'));
        el = ces.find(function(c) {
            var r = c.getBoundingClientRect();
            return r.top < 500 && r.width > 200 && c.offsetParent !== null;
        }) || null;
    }
    if (!el || el.offsetParent === null) return 'not-found';
    var r = el.getBoundingClientRect();
    var x = Math.round(window.screenX + r.left + r.width / 2);
    var y = Math.round(window.screenY + r.top  + r.height / 2);
    return x + ',' + y + ':' + el.tagName;
})()
""")
        _grok_log("prompt-coords", coords)
        if 'not-found' in coords or ',' not in coords:
            _grok_log("prompt", "ERROR: Could not get screen coords for prompt field.")
            return False
        sx = coords.split(',')[0]
        sy = coords.split(',')[1].split(':')[0]
        run_osascript('tell application "Google Chrome" to activate')
        time.sleep(0.4)
        run_osascript(f'tell application "System Events" to click at {{{sx}, {sy}}}')
        time.sleep(0.4)
        url_after = run_osascript('tell application "Google Chrome" to return URL of active tab of front window')
        if '/imagine/post/' in url_after or 'grok.com/imagine' not in url_after:
            _grok_log("prompt", f"ERROR: Click navigated away ({url_after}).")
            return False
        run_osascript('tell application "System Events" to keystroke "v" using command down')
        time.sleep(1.0)
        verify = run_js_in_chrome(r"""
(function() {
    var p = document.querySelector('[data-testid="drop-ui"] form p');
    if (p) {
        var v = (p.innerText || p.textContent || '').trim();
        if (v.length > 10) return 'prompt-ok:' + v.length + ' chars';
    }
    var ce = document.querySelector('[data-testid="drop-ui"] form [contenteditable="true"]');
    if (ce) {
        var v = (ce.innerText || ce.textContent || '').trim();
        if (v.length > 10) return 'prompt-ok:' + v.length + ' chars';
    }
    return 'prompt-MISSING';
})()
""")
        _grok_log("prompt-verify", verify)
        if 'prompt-MISSING' not in verify:
            _grok_log("prompt", f"Prompt confirmed on attempt {attempt+1}.")
            return True
        _grok_log("prompt", f"Prompt MISSING after paste (attempt {attempt+1}/3) — React may have re-rendered.")

    _grok_log("prompt", "ERROR: Prompt not in drop-ui field after 3 attempts.")
    return False


def grok_submit_form(video_prompt: str) -> bool:
    _grok_log("submit", "--- grok_submit_form ---")
    image_ok = run_js_in_chrome(r"""
(function() {
    var imgs = Array.from(document.querySelectorAll('img'));
    var thumb = imgs.find(function(i) {
        return i.naturalWidth > 30 && i.offsetParent !== null
               && (i.src.indexOf('blob:') === 0 || i.src.indexOf('data:') === 0);
    });
    if (thumb) return 'image-ok:blob-preview';
    var fi = document.querySelector('input[type="file"]');
    if (fi && fi.files && fi.files.length > 0) return 'image-ok:file-input';
    var body = (document.body.innerText || '').toLowerCase();
    if (body.indexOf('upload or drop') === -1) return 'image-ok:upload-text-gone';
    return 'image-MISSING';
})()
""")
    _grok_log("pre-submit-check", f"image={image_ok}")
    if 'image-MISSING' in image_ok:
        _grok_log("pre-submit-check", "ABORT: Reference image not confirmed.")
        return False

    if video_prompt:
        prompt_verify = run_js_in_chrome(r"""
(function() {
    var p = document.querySelector('[data-testid="drop-ui"] form p');
    if (p) {
        var v = (p.innerText || p.textContent || '').trim();
        if (v.length > 10) return 'prompt-ok:' + v.length + ' chars';
    }
    var ce = document.querySelector('[data-testid="drop-ui"] form [contenteditable="true"]');
    if (ce) {
        var v = (ce.innerText || ce.textContent || '').trim();
        if (v.length > 10) return 'prompt-ok:' + v.length + ' chars';
    }
    return 'prompt-MISSING';
})()
""")
        _grok_log("pre-submit-check", f"prompt={prompt_verify}")
        if 'prompt-MISSING' in prompt_verify:
            _grok_log("pre-submit-check", "ABORT: Prompt not in page.")
            return False

    _grok_log("pre-submit-check", "Both confirmed — submitting.")
    r = run_js_in_chrome(r"""
(function() {
    var el = document.querySelector('div.query-bar > div.absolute svg');
    if (el && el.offsetParent !== null) {
        el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        return 'clicked:query-bar-svg';
    }
    var btns = Array.from(document.querySelectorAll('button,[role=button]'));
    var btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || '').toLowerCase();
        return (lbl.indexOf('submit') !== -1 || lbl.indexOf('generate') !== -1)
               && b.offsetParent !== null;
    });
    if (btn) { btn.click(); return 'clicked:aria:' + (btn.getAttribute('aria-label') || '').trim(); }
    return 'not-found';
})()
""")
    _grok_log("submit", r)
    time.sleep(8)
    return True


def wait_for_grok_video_and_download(video_output_path: str) -> str:
    _grok_log("wait-video", f"--- wait_for_grok_video_and_download ({GROK_VIDEO_WAIT}s max) ---")
    caff = subprocess.Popen(["caffeinate", "-d", "-t", str(GROK_VIDEO_WAIT + 300)])
    try:
        _grok_log("wait-video", "Initial 60s wait before first poll...")
        time.sleep(60)
        elapsed = 60
        last_state = ""
        video_ready = False
        while elapsed < GROK_VIDEO_WAIT:
            time.sleep(10)
            elapsed += 10
            state = run_js_in_chrome(r"""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var visibleBtnLabels = btns
        .filter(function(b) { return b.offsetParent !== null; })
        .map(function(b) { return (b.getAttribute('aria-label') || b.innerText || '').trim().toLowerCase(); });
    var cancelVideoPresent = visibleBtnLabels.some(function(l) {
        return l.indexOf('cancel video') !== -1 || l.indexOf('cancel') !== -1;
    });
    var dlLabel = visibleBtnLabels.find(function(l) { return l.indexOf('download') !== -1; });
    var dlPresent = !!dlLabel;
    var vid = document.querySelector('video');
    var vidSrc = vid ? (vid.src || vid.currentSrc || '') : '';
    var isUserVideo = vidSrc.indexOf('assets.grok.com/users') !== -1;
    if (isUserVideo && !cancelVideoPresent) { return 'ready:video-element'; }
    if (dlPresent && !cancelVideoPresent) { return 'ready:download-btn label=' + dlLabel; }
    if (dlPresent && cancelVideoPresent) { return 'generating:cancel-video-present'; }
    var body = (document.body.innerText || '').toLowerCase().slice(-2000);
    if (body.indexOf('failed') !== -1) return 'error:failed';
    if (body.indexOf('could not generate') !== -1) return 'error:could-not-generate';
    if (body.indexOf('try again') !== -1) return 'error:try-again';
    var snippet = (document.body.innerText || '').replace(/\n+/g, ' ').trim().slice(-150);
    return 'generating — page: ' + snippet;
})()
""")
            if state != last_state:
                _grok_log(f"poll-{elapsed}s", state)
                last_state = state
            else:
                print(f"  [GROK:poll] ...{elapsed}s/{GROK_VIDEO_WAIT}s", end="\r", flush=True)
            if state.startswith('ready'):
                print()
                video_ready = True
                break
            if state.startswith('error'):
                print()
                return 'failed'
        if not video_ready:
            print()
            _grok_log("wait-video", f"Timeout — video not ready after {GROK_VIDEO_WAIT}s.")
            return 'timeout'

        _grok_log("wait-video", f"Video ready at {elapsed}s.")
        downloads_dir = os.path.expanduser("~/Downloads")
        before = set(os.listdir(downloads_dir))

        play_result = run_js_in_chrome(r"""
(function() {
    var vid = document.querySelector('video');
    if (!vid) return 'no-video-element';
    if (vid.paused) { vid.play(); return 'play:started'; }
    return 'play:already-playing';
})()
""")
        _grok_log("video-play", play_result)
        time.sleep(2)

        video_src = run_js_in_chrome(r"""
(function() {
    var vid = document.querySelector('video');
    if (!vid) return '';
    return vid.src || vid.currentSrc || '';
})()
""")
        if video_src and video_src.startswith('https://assets.grok.com/users'):
            fetch_js = r"""
(function() {
    var vid = document.querySelector('video');
    if (!vid) return 'no-video';
    var src = vid.src || vid.currentSrc;
    if (!src) return 'no-src';
    fetch(src, {credentials: 'include'})
        .then(function(r) { return r.blob(); })
        .then(function(blob) {
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'grok_video_blob.mp4';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        })
        .catch(function(e) { console.warn('grok-fetch error:', e); });
    return 'fetch-blob:triggered';
})()
"""
            _grok_log("download-fetch", run_js_in_chrome(fetch_js))
            time.sleep(3)

        r = run_js_in_chrome(r"""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var dl = btns.find(function(b) {
        return (b.getAttribute('aria-label') || '').toLowerCase().indexOf('download') !== -1
               && b.offsetParent !== null;
    });
    if (dl) { dl.click(); return 'clicked:' + dl.getAttribute('aria-label'); }
    return 'not found';
})()
""")
        _grok_log("download-btn", r)
        time.sleep(1)
        run_osascript('tell application "Google Chrome" to activate')
        time.sleep(0.3)
        run_osascript('tell application "System Events" to key code 36')

        matched = None
        for tick in range(GROK_FILE_WAIT):
            time.sleep(1)
            after = set(os.listdir(downloads_dir))
            new_files = after - before
            mp4s = [f for f in new_files if f.lower().endswith(".mp4")]
            crdownloads = [f for f in new_files if f.lower().endswith(".crdownload")]
            if mp4s:
                nonzero = [f for f in mp4s if os.path.getsize(os.path.join(downloads_dir, f)) > 0]
                if nonzero:
                    matched = max(nonzero, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
                    break
                elif not crdownloads:
                    matched = max(mp4s, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
                    break
        if not matched:
            return 'failed'

        src = os.path.join(downloads_dir, matched)
        prev_size = -1
        stable_count = 0
        for _ in range(120):
            time.sleep(2)
            try:
                cur_size = os.path.getsize(src)
            except OSError:
                cur_size = 0
            if cur_size > 0 and cur_size == prev_size:
                stable_count += 1
                if stable_count >= 3:
                    break
            else:
                stable_count = 0
            prev_size = cur_size

        if not os.path.exists(src) or os.path.getsize(src) == 0:
            return 'failed'

        shutil.move(src, video_output_path)
        _grok_log("download-done", f"Saved: {os.path.basename(video_output_path)}  ({os.path.getsize(video_output_path):,} bytes)")
        return 'success'
    finally:
        caff.terminate()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP server helper
# ─────────────────────────────────────────────────────────────────────────────

def _run_server(server, state: dict, lock):
    while True:
        with lock:
            done   = state["done"]
            failed = state["ext_status"] == "failed"
        if done or failed:
            break
        server.handle_request()


# ─────────────────────────────────────────────────────────────────────────────
# Extension-based video generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_grok_video_via_extension(image_path: str, video_prompt: str, video_output_path: str) -> str:
    """Generate a Grok video using the Chrome extension + local HTTP server.

    Returns 'success', 'failed', or 'timeout'.
    """
    import threading
    import json
    import datetime
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import traceback

    global _grok_log_fh
    log_path = os.path.abspath(GROK_LOG_PATH)
    _grok_log_fh = open(log_path, "w", encoding="utf-8")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _grok_write(f"\n{'═'*60}")
    _grok_write(f"  GROK SESSION START (extension)  {ts}")
    _grok_write(f"  Log file: {log_path}")
    _grok_write(f"{'═'*60}")
    _grok_log("ext", "════ generate_grok_video_via_extension START ════")
    _grok_log("ext", f"  image      : {image_path}")
    _grok_log("ext", f"  video out  : {video_output_path}")
    _grok_log("ext", f"  prompt     : {(video_prompt or '')[:120]}...")

    if not os.path.exists(image_path):
        _grok_log("ext", f"ERROR: image file not found: {image_path}")
        return 'failed'

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")
    image_filename = os.path.basename(image_path)

    state = {
        "job": {
            "status": "pending",
            "prompt": video_prompt or "",
            "image_b64": image_b64,
            "image_filename": image_filename,
        },
        "ext_status": "pending",
        "ext_message": "",
        "done": False,
    }
    state_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            _grok_log("http", fmt % args)

        def do_GET(self):
            if self.path == '/job':
                with state_lock:
                    body = json.dumps(state["job"]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == '/status':
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                except Exception:
                    data = {}
                with state_lock:
                    state["ext_status"] = data.get("status", "unknown")
                    state["ext_message"] = data.get("message", "")
                    msg_text = state["ext_message"]
                    if msg_text.startswith("video_url_detected:"):
                        detected_url = msg_text[len("video_url_detected:"):].strip()
                        if detected_url and not state.get("video_url"):
                            state["video_url"] = detected_url
                            _grok_log("ext-network", f"VIDEO URL DETECTED: {detected_url}")
                    elif msg_text.startswith("downloading_via_extension:"):
                        _grok_log("ext-network", f"DOWNLOAD TRIGGERED: {msg_text[len('downloading_via_extension:'):]}")
                    elif msg_text.startswith("video_ready:"):
                        _grok_log("ext-network", f"VIDEO GENERATION CONFIRMED: {msg_text}")
                    if state["ext_status"] in ("running", "done", "failed"):
                        state["job"]["status"] = state["ext_status"]
                    if state["ext_status"] == "done":
                        state["done"] = True
                _grok_log("ext-status", f"{state['ext_status']}: {state['ext_message']}")
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            elif self.path == '/log':
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    msg = json.loads(body).get("message", "")
                except Exception:
                    msg = body.decode(errors="replace")
                _grok_log("ext-js", msg)
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            elif self.path == '/network':
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    entries = json.loads(body)
                    if isinstance(entries, dict):
                        entries = [entries]
                except Exception:
                    entries = []
                for e in entries:
                    url    = e.get("url", "")
                    status = e.get("status", "")
                    is_video = "mp4" in url.lower() or "video" in url.lower()
                    tag = "net-VIDEO" if is_video else "net"
                    _grok_log(tag, f"{e.get('dir','NET')}  {e.get('method','')}  {url}  [{status}]")
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 7878), Handler)
    server.timeout = 1
    server_thread = threading.Thread(target=_run_server, args=(server, state, state_lock), daemon=True)
    server_thread.start()
    _grok_log("ext", "HTTP server started on localhost:7878")

    try:
        _grok_log("ext", "Opening Chrome with Grok profile at grok.com/imagine...")
        subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
        time.sleep(2)
        ext_path = os.path.abspath(GROK_EXTENSION_PATH)
        subprocess.Popen([
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            f"--profile-directory={GROK_CHROME_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--load-extension={ext_path}",
            "https://grok.com/imagine",
        ])

        _grok_log("ext", "Waiting for extension to connect...")
        deadline = time.time() + 30
        while time.time() < deadline:
            with state_lock:
                s = state["ext_status"]
            if s in ("running", "done", "failed"):
                break
            time.sleep(1)

        _grok_log("ext", "Waiting for extension to complete video generation...")
        deadline = time.time() + 12 * 60
        while time.time() < deadline:
            with state_lock:
                s    = state["ext_status"]
                msg  = state["ext_message"]
                done = state["done"]
            _grok_log("ext-poll", f"status={s} msg={msg}")
            if done:
                _grok_log("ext", "Extension reported done")
                break
            if s == "failed":
                _grok_log("ext", f"Extension reported failure: {msg}")
                if 'NO_PROMPT' in msg:
                    _grok_log("ext", "DETECTED: generation without prompt — returning failed_no_prompt")
                    return 'failed_no_prompt'
                return 'failed'
            time.sleep(5)
        else:
            _grok_log("ext", "Timed out waiting for extension")
            return 'timeout'

        time.sleep(5)
        downloads_dir = os.path.expanduser("~/Downloads")
        with state_lock:
            captured_url = state.get("video_url", "")
        _grok_log("ext", f"Scanning {downloads_dir} for new MP4...")

        start_ts   = time.time() - 12 * 60
        found_mp4  = None
        deadline2  = time.time() + GROK_FILE_WAIT
        tick       = 0
        while time.time() < deadline2:
            all_files = os.listdir(downloads_dir)
            mp4s = [
                f for f in all_files
                if f.lower().endswith(".mp4")
                and os.path.getmtime(os.path.join(downloads_dir, f)) >= start_ts
                and os.path.getsize(os.path.join(downloads_dir, f)) > 1024
            ]
            crdownloads = [f for f in all_files if f.endswith(".crdownload")]
            if mp4s and not crdownloads:
                found_mp4 = max(mp4s, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
                sz = os.path.getsize(os.path.join(downloads_dir, found_mp4))
                _grok_log("ext-dl", f"MP4 found: {found_mp4} ({sz:,} bytes)")
                break
            time.sleep(3)
            tick += 1

        if not found_mp4:
            _grok_log("ext", f"ERROR: No MP4 found in {downloads_dir} after {GROK_FILE_WAIT}s")
            return 'failed'

        src = os.path.join(downloads_dir, found_mp4)
        os.makedirs(os.path.dirname(video_output_path), exist_ok=True)
        shutil.move(src, video_output_path)
        _grok_log("ext", f"Moved {found_mp4} → {video_output_path}")
        subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
        _grok_log("ext", "Chrome killed after successful move.")
        return 'success'

    except Exception as e:
        _grok_log("ext", f"Unexpected exception: {e}")
        _grok_log("ext", traceback.format_exc())
        return 'failed'
    finally:
        subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
        server.server_close()
        time.sleep(1)
        _grok_log("ext", "Chrome closed. ════ generate_grok_video_via_extension END ════")
        if _grok_log_fh:
            _grok_log_fh.close()
            _grok_log_fh = None
            print(f"  [GROK] Log saved → {os.path.abspath(GROK_LOG_PATH)}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate Grok videos for scene images in a chapter output folder."
    )
    parser.add_argument("folder", help="Chapter output folder (e.g. ./clown_vol_1/output/ch_11)")
    args = parser.parse_args()

    folder = args.folder.rstrip("/")
    if not os.path.isdir(folder):
        print(f"Error: '{folder}' is not a valid directory.")
        sys.exit(1)

    meta_path = find_meta_file(folder)
    if not meta_path:
        print(f"Error: No *_meta.md file found in '{folder}'.")
        sys.exit(1)

    print(f"Meta file : {meta_path}")

    prompts = extract_all_image_prompts(meta_path)
    if not prompts:
        print("Error: No image prompts found in meta file.")
        sys.exit(1)

    video_prompts = extract_all_video_prompts(meta_path)
    while len(video_prompts) < len(prompts):
        video_prompts.append("")

    meta_stem = os.path.basename(meta_path).replace("_meta.md", "")
    total     = len(prompts)

    all_work = []
    for idx, (num, label, _prompt) in enumerate(prompts):
        out_path   = get_output_path(folder, meta_stem, num, label)
        video_path = get_video_output_path(out_path)
        vp         = video_prompts[idx]
        img_done   = os.path.exists(out_path)
        vid_done   = True if label.lower() == "thumbnail" else (os.path.exists(video_path) if vp else True)
        all_work.append({
            'num': num, 'label': label,
            'out_path': out_path, 'video_path': video_path, 'vp': vp,
            'img_done': img_done, 'vid_done': vid_done,
        })

    print(f"\nTotal prompts: {total}")
    print(f"{'─' * 62}")
    print(f"  {'#':>3}  {'Label':12}  {'Image':6}  {'Video':6}  Action")
    print(f"{'─' * 62}")
    for w in all_work:
        img_s = "DONE " if w['img_done'] else "miss "
        vid_s = "DONE " if w['vid_done'] else ("miss " if w['vp'] else "n/a  ")
        if w['label'].lower() == 'thumbnail':
            action = "skip (thumbnail)"
        elif w['vid_done']:
            action = "skip"
        elif not w['img_done']:
            action = "skip (image missing)"
        elif not w['vp']:
            action = "skip (no video prompt)"
        else:
            action = "generate video"
        print(f"  {w['num']:>3}  {w['label']:12}  {img_s}  {vid_s}  {action}")
    print(f"{'─' * 62}")

    needs_work = any(
        not w['vid_done'] and w['img_done'] and w['vp'] and w['label'].lower() != 'thumbnail'
        for w in all_work
    )
    if not needs_work:
        print("\nAll videos already exist or no video prompts available. Nothing to do.")
        sys.exit(0)

    video_success = 0
    for i, w in enumerate(all_work):
        if w['label'].lower() == 'thumbnail':
            print(f"\n  [SKIP] {w['num']} — {w['label']} — thumbnail never gets a video.")
            continue
        if w['vid_done']:
            print(f"\n  [SKIP] {w['num']} — {w['label']} — video done.")
            continue
        if not w['img_done']:
            print(f"\n  [SKIP] {w['num']} — {w['label']} — image missing, run generate_image.py first.")
            continue
        if not w['vp']:
            print(f"\n  [SKIP] {w['num']} — {w['label']} — no video prompt in meta.")
            continue

        print(f"\n{'─' * 55}")
        print(f"[{i + 1}/{total}] Video {w['num']} — {w['label']}")
        print(f"  Image : {os.path.basename(w['out_path'])}")
        print(f"  Video : {os.path.basename(w['video_path'])}")

        MAX_PROMPT_RETRIES = 3
        for retry in range(MAX_PROMPT_RETRIES + 1):
            if retry > 0:
                print(f"\n  [RETRY {retry}/{MAX_PROMPT_RETRIES}] Prompt was lost — killing Chrome, restarting video {w['num']}...")
                subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
                time.sleep(5)

            result = generate_grok_video_via_extension(w['out_path'], w['vp'], w['video_path'])

            if result == 'success':
                w['vid_done'] = True
                video_success += 1
                print(f"  [OK] Video {w['num']} done.")
                break
            elif result == 'failed_no_prompt' and retry < MAX_PROMPT_RETRIES:
                print(f"\n  [NO PROMPT] Video {w['num']} — generation started without prompt.")
                continue  # retry
            else:
                print(f"\n  [FATAL] Video {w['num']} {result}.")
                print(f"  Hard rule: video failure stops the pipeline. Fix the issue and re-run.")
                sys.exit(1)

    print(f"\n{'═' * 55}")
    print(f"Done. {video_success} video(s) generated.")
    print(f"{'═' * 55}")


if __name__ == "__main__":
    main()
