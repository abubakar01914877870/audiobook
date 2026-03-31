#!/usr/bin/env python3
"""
generate_image_video.py — Generate scene images (Gemini) then Grok videos, per prompt.

For each prompt in *_meta.md the script enforces this rule:
  • image done  + video done  → skip both
  • image done  + video miss  → generate video only
  • image miss  + video miss  → generate image THEN video
  • image miss  + video done  → generate image (video already present)
  • image miss  → NEVER attempt video (image is mandatory for video)

Image generation uses the Gemini web UI (Profile 9 — 'gemini').
Video generation uses the Grok web UI (Profile 10 — 'grok').
Chrome is killed between every profile switch.

Usage:
    python generate_image_video.py ./clown_vol_1/output/ch_11
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
    subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
    sys.exit(1)

# ── Gemini constants ──────────────────────────────────────────────────────────
CHROME_DATA_DIR       = "/Users/abubakarsiddique/Library/Application Support/Google/Chrome"
CHROME_PROFILE        = "Profile 9"    # 'gemini' profile
GEMINI_URL            = "https://gemini.google.com/app"
INTER_IMAGE_COOLDOWN  = 20             # seconds between images (rate-limit buffer)
HEAVY_LOAD_FINAL_WAIT = 300            # seconds before 3rd Gemini attempt

# ── Grok constants ────────────────────────────────────────────────────────────
GROK_CHROME_PROFILE = "Profile 10"    # 'grok' profile
GROK_URL            = "https://grok.com/"
GROK_EXTENSION_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "chrome-plugins", "grok-video-generator"
)
GROK_VIDEO_WAIT     = 600             # max seconds to wait for video generation (10 min)
GROK_FILE_WAIT      = 120             # max seconds to wait for MP4 in Downloads
GROK_DEBUG          = True            # enable verbose step-by-step debug logs
GROK_LOG_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "grok_debug.log")


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────

def find_meta_file(folder: str) -> Optional[str]:
    for fname in sorted(os.listdir(folder)):
        if fname.endswith("_meta.md"):
            return os.path.join(folder, fname)
    return None


def _parse_image_prompt_blocks(meta_path: str) -> list:
    """Return list of (num_str, label, block_text) for every ### Image Prompt NN block."""
    with open(meta_path, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(
        r'###\s*Image Prompt\s+(\d+)\s*[—\-]+\s*(\w+)\s*\n(.*?)(?=\n###\s*Image Prompt|\n###\s*YouTube|\Z)',
        re.DOTALL
    )
    return [(m.group(1).zfill(2), m.group(2).strip(), m.group(3).strip())
            for m in pattern.finditer(content)]


def extract_all_image_prompts(meta_path: str) -> list:
    """Return list of (num_str, label, prompt_text) ordered by number.
    Falls back to old single-prompt format.
    """
    results = []
    for num, label, block in _parse_image_prompt_blocks(meta_path):
        m = re.search(r'\*\*Prompt:\*\*(.*?)(?=\n\*\*|\Z)', block, re.DOTALL | re.IGNORECASE)
        actual = m.group(1).strip() if m else block
        if actual:
            results.append((num, label, actual))

    if results:
        return results

    # Fallback: old single-prompt format
    with open(meta_path, "r", encoding="utf-8") as f:
        content = f.read()
    old = re.search(r'###\s*Image Generation Prompt\s*\n(.*?)(?=\n###|\Z)', content, re.DOTALL)
    if old:
        return [("01", "Thumbnail", old.group(1).strip())]
    return []


def extract_all_video_prompts(meta_path: str) -> list:
    """Return list of video_prompt strings aligned by index with extract_all_image_prompts().
    Missing/empty entries become ''.
    """
    results = []
    for _num, _label, block in _parse_image_prompt_blocks(meta_path):
        m = re.search(r'\*\*Video Prompt:\*\*(.*?)(?=\n\*\*|\Z)', block, re.DOTALL | re.IGNORECASE)
        results.append(m.group(1).strip() if m else "")
    return results


def get_output_path(folder: str, stem: str, num: str, label: str) -> str:
    suffix = "thumb" if label.lower() == "thumbnail" else "scene"
    return os.path.join(folder, f"{stem}_{num}_{suffix}.png")


def get_video_output_path(image_path: str) -> str:
    """Derive .mp4 path from .png path (same name, different extension)."""
    return os.path.splitext(image_path)[0] + ".mp4"


# ─────────────────────────────────────────────────────────────────────────────
# Chrome / AppleScript helpers (Gemini — unchanged from generate_image.py)
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


def wait_for_gemini(max_wait: int = 30) -> bool:
    print("  Waiting for Gemini to load...", end="", flush=True)
    for _ in range(max_wait):
        url = run_osascript(
            'tell application "Google Chrome" to return URL of active tab of front window'
        )
        if "gemini.google.com" in url:
            print(" ready.")
            return True
        print(".", end="", flush=True)
        time.sleep(1)
    print(" timed out.")
    return False


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
    """Kill Chrome and reopen with the given profile directory and URL."""
    subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
    time.sleep(3)
    subprocess.Popen([
        "open", "-a", "Google Chrome", "--args",
        f"--profile-directory={profile}",
        f"--user-data-dir={CHROME_DATA_DIR}",
        url,
    ])
    time.sleep(boot_wait)
    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(1)


def setup_chrome() -> bool:
    """Kill Chrome, open with Gemini profile, enable JS. Returns True on success."""
    print("Closing any existing Chrome instances...")
    _open_chrome_with_profile(CHROME_PROFILE, GEMINI_URL, boot_wait=6)

    if not wait_for_gemini(max_wait=30):
        print("Error: Gemini did not load in time.")
        return False

    wait_for_gemini_ui(max_wait=20)

    if not check_js_enabled():
        enable_js_from_apple_events()
        time.sleep(1)
        if not check_js_enabled():
            print("\n" + "=" * 60)
            print("ONE-TIME SETUP REQUIRED")
            print("=" * 60)
            print("\n1. In Chrome: View > Developer > Allow JavaScript from Apple Events")
            print("2. System Settings > Privacy & Security > Accessibility > add Terminal")
            input("\nPress Enter when ready: ")
            if not check_js_enabled():
                print("Still not working. Aborting.")
                return False

    return True


def reopen_gemini_chrome() -> bool:
    """Reopen Chrome with the Gemini profile (called after Grok session closed Chrome)."""
    print("\n  Reopening Chrome with Gemini profile...")
    _open_chrome_with_profile(CHROME_PROFILE, GEMINI_URL, boot_wait=6)
    if not wait_for_gemini(max_wait=30):
        print("  Error: Gemini did not reload.")
        return False
    wait_for_gemini_ui(max_wait=20)
    if not check_js_enabled():
        enable_js_from_apple_events()
        time.sleep(1)
    return True


def wait_for_gemini_ui(max_wait: int = 20) -> bool:
    print("  Waiting for Gemini UI...", end="", flush=True)
    for _ in range(max_wait):
        result = run_js_in_chrome("""
(function() {
    var input = document.querySelector('rich-textarea div[contenteditable="true"]') ||
                document.querySelector('div[contenteditable="true"]');
    return input ? 'ready' : 'loading';
})()
""")
        if result == "ready":
            print(" ready.")
            return True
        print(".", end="", flush=True)
        time.sleep(1)
    print(" timed out (will try anyway).")
    return False


def navigate_to_fresh_chat() -> bool:
    print("  Navigating to fresh Gemini chat...")
    run_osascript(
        f'tell application "Google Chrome" to set URL of active tab of front window to "{GEMINI_URL}"'
    )
    time.sleep(3)
    run_osascript('tell application "Google Chrome" to activate')

    if not wait_for_gemini(max_wait=30):
        time.sleep(3)
        if not wait_for_gemini(max_wait=30):
            print("  Error: Gemini did not load.")
            return False

    wait_for_gemini_ui(max_wait=20)
    return True


def click_main_menu() -> bool:
    result = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button,[role=button]'));
    var btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || b.innerText || '').trim();
        return lbl === 'Main menu';
    });
    if (btn) { btn.click(); return 'clicked'; }
    return 'not found';
})()
""")
    return result == 'clicked'


def find_and_click_temp_chat() -> tuple:
    result = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button,[role=button],a'));

    var btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || '').trim().toLowerCase();
        return lbl === 'temporary chat';
    });
    if (btn && btn.offsetParent !== null) { btn.click(); return 'clicked:aria-label'; }

    btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || '').trim().toLowerCase();
        return lbl.indexOf('temporary') !== -1;
    });
    if (btn && btn.offsetParent !== null) { btn.click(); return 'clicked:aria-partial'; }

    btn = btns.find(function(b) {
        var txt = (b.innerText || b.textContent || '').trim().toLowerCase();
        return txt.indexOf('temporary') !== -1 && txt.length < 40 && b.offsetParent !== null;
    });
    if (btn) { btn.click(); return 'clicked:text'; }

    return 'not found';
})()
""")
    return result.startswith('clicked'), result


def open_temp_chat() -> bool:
    print("  Looking for Temporary chat button (direct)...", end="", flush=True)
    found, result = find_and_click_temp_chat()
    if found:
        print(f" {result}")
    else:
        print(" not visible yet.")
        print("  Clicking Main menu to reveal Temporary chat...", end="", flush=True)
        if not click_main_menu():
            print(" 'Main menu' button not found.")
            return False
        print(" done.")
        time.sleep(1.5)

        print("  Looking for Temporary chat button (after Main menu)...", end="", flush=True)
        for attempt in range(5):
            found, result = find_and_click_temp_chat()
            if found:
                print(f" {result}")
                break

            if attempt == 4:
                debug = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button,[role=button],a'));
    return btns
        .filter(function(b) { return b.offsetParent !== null; })
        .map(function(b) {
            return (b.getAttribute('aria-label') || b.innerText || b.title || '').trim().slice(0,30);
        })
        .filter(function(t) { return t.length > 0; })
        .slice(0, 35)
        .join(' | ');
})()
""")
                print(f"\n  [DEBUG] Temporary chat not found after Main menu click.")
                print(f"  [DEBUG] Visible buttons: {debug}")
                return False

            print(".", end="", flush=True)
            time.sleep(1.5)

    time.sleep(1.5)

    run_js_in_chrome("""
(function() {
    var btn = document.querySelector('button[aria-label="Dismiss"]') ||
              document.querySelector('button[aria-label="Close"]');
    if (btn) btn.click();
})()
""")
    time.sleep(1)
    print("  Temporary chat mode active.")
    return True


def click_tools_and_create_image() -> bool:
    print("  Clicking tools button...")
    add_clicked = run_js_in_chrome("""
(function() {
    var labels = ['select tools and upload','Add extras menu','input area menu','Tools'];
    var btns = Array.from(document.querySelectorAll('button,[role=button]'));
    for (var i = 0; i < labels.length; i++) {
        var btn = btns.find(function(b) {
            var lbl = (b.getAttribute('aria-label') || b.innerText || '').trim();
            return lbl.toLowerCase().indexOf(labels[i].toLowerCase()) !== -1;
        });
        if (btn) {
            btn.click();
            return 'clicked:' + (btn.getAttribute('aria-label') || btn.innerText || '').trim().slice(0,40);
        }
    }
    return 'not found';
})()
""")

    if add_clicked.startswith("clicked"):
        print(f"  {add_clicked}")
        time.sleep(1.5)
    else:
        print("  Tools button not found. Please click it manually then press Enter.")
        input("  Press Enter when done: ")

    print("  Clicking 'Create image'...")
    img_clicked = run_js_in_chrome("""
(function() {
    var all = Array.from(document.querySelectorAll('li,button,mat-option,[role=menuitem],[role=option]'));
    var el = all.find(function(e) {
        return e.offsetParent !== null &&
               (e.innerText || '').trim().toLowerCase().indexOf('create image') !== -1;
    });
    if (el) { el.click(); return 'clicked'; }
    return 'not found';
})()
""")

    if img_clicked == "clicked":
        print("  Clicked 'Create image'.")
        time.sleep(1)
        return True

    print("  Could not find 'Create image'. Please click it manually then press Enter.")
    input("  Press Enter when ready: ")
    return True


def paste_and_submit(prompt: str) -> bool:
    print(f"  Pasting prompt ({len(prompt)} chars)...")
    try:
        subprocess.run(["pbcopy"], input=prompt.encode("utf-8"), check=True)
    except Exception as e:
        print(f"  Error copying to clipboard: {e}")
        return False

    time.sleep(0.5)

    run_js_in_chrome("""
var box = document.querySelector('rich-textarea div[contenteditable="true"]') ||
          document.querySelector('div[contenteditable="true"]');
if (box) box.focus();
""")
    time.sleep(0.5)

    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(0.3)
    run_osascript('tell application "System Events" to keystroke "v" using command down')
    time.sleep(1.5)
    print("  Submitting...")
    sent = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button,[role=button]'));
    var btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || '').toLowerCase();
        return lbl.indexOf('send') !== -1 && b.offsetParent !== null;
    });
    if (btn) { btn.click(); return 'clicked'; }
    return 'not found';
})()
""")
    if sent != 'clicked':
        run_osascript('tell application "Google Chrome" to activate')
        time.sleep(0.2)
        run_osascript('tell application "System Events" to key code 36')
    print("  Prompt submitted. Waiting for image (up to 5 min)...")
    return True


def detect_gemini_error() -> Optional[str]:
    result = run_js_in_chrome("""
(function() {
    var heavyPhrases = [
        'heavy load', 'try again later', 'try again soon',
        'too many requests', 'service unavailable', 'overloaded',
        'having trouble', 'something went wrong', 'request couldn\\'t be processed',
        'temporarily unavailable', 'try again in'
    ];
    var policyPhrases = [
        'can\\'t generate', 'cannot generate', 'against our policies',
        'content policy', 'not able to help', 'can\\'t help with that',
        'unable to create', 'not allowed'
    ];
    var bodyText = (document.body.innerText || '').toLowerCase();
    var recentText = bodyText.slice(-3000);
    for (var i = 0; i < heavyPhrases.length; i++) {
        if (recentText.indexOf(heavyPhrases[i]) !== -1) {
            return 'heavy_load:' + heavyPhrases[i];
        }
    }
    for (var j = 0; j < policyPhrases.length; j++) {
        if (recentText.indexOf(policyPhrases[j]) !== -1) {
            return 'policy_block:' + policyPhrases[j];
        }
    }
    return 'none';
})()
""")

    if result.startswith('heavy_load:'):
        print(f"  [ERROR DETECTED] Heavy load — matched: '{result.split(':', 1)[1]}'")
        return "heavy_load"
    if result.startswith('policy_block:'):
        print(f"  [ERROR DETECTED] Policy block — matched: '{result.split(':', 1)[1]}'")
        return "policy_block"
    return None


def wait_and_download(output_path: str, max_wait: int = 300) -> str:
    find_img_js = """
(function() {
    var skipPatterns = ['gstatic', 'google.com/images', 'accounts.google', 'favicon'];
    var imgs = Array.from(document.querySelectorAll('img'));
    for (var i = imgs.length - 1; i >= 0; i--) {
        var img = imgs[i];
        var src = img.src || '';
        if (img.naturalWidth > 200 && img.naturalHeight > 200 && src) {
            var skip = skipPatterns.some(function(p) { return src.indexOf(p) !== -1; });
            if (!skip) return src;
        }
    }
    return '';
})()
"""
    elapsed = 0
    img_src = ""

    while elapsed < max_wait:
        time.sleep(2)
        elapsed += 2
        print(f"  Checking for image... ({elapsed}s)", end="\r", flush=True)

        img_src = run_js_in_chrome(find_img_js)
        if img_src:
            print(f"\n  Image found ({elapsed}s).")
            break

        if elapsed % 10 == 0:
            error = detect_gemini_error()
            if error == "heavy_load":
                print(f"  Aborting wait — heavy load detected at {elapsed}s.")
                return "heavy_load"
            if error == "policy_block":
                print(f"  Aborting wait — policy block detected at {elapsed}s.")
                return "failed"

    if not img_src:
        error = detect_gemini_error()
        if error == "heavy_load":
            return "heavy_load"
        print("\n  Image not found within timeout.")
        return "failed"

    time.sleep(2)

    downloads_dir = os.path.expanduser("~/Downloads")
    before = set(os.listdir(downloads_dir))

    def _hover_image(depth: int):
        run_js_in_chrome(f"""
(function() {{
    var skipPatterns = ['gstatic', 'google.com/images', 'accounts.google', 'favicon'];
    var imgs = Array.from(document.querySelectorAll('img'));
    var img = imgs.find(function(i) {{
        var src = i.src || '';
        if (i.naturalWidth <= 200 || i.naturalHeight <= 200 || !src) return false;
        return !skipPatterns.some(function(p) {{ return src.indexOf(p) !== -1; }});
    }});
    if (!img) return;
    var el = img;
    for (var j = 0; j < {depth}; j++) {{
        if (!el) break;
        el.dispatchEvent(new MouseEvent('mouseover', {{bubbles: true}}));
        el.dispatchEvent(new MouseEvent('mouseenter', {{bubbles: true}}));
        el = el.parentElement;
    }}
}})()
""")

    def _click_download():
        result = run_js_in_chrome("""
(function() {
    var btn = document.querySelector('[data-test-id="download-generated-image-button"]');
    if (btn && btn.offsetParent !== null) { btn.click(); return 'clicked:test-id'; }
    var btns = Array.from(document.querySelectorAll('button,[role=button],a'));
    var b = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || b.innerText || '');
        return lbl.toLowerCase().indexOf('download full size') !== -1;
    });
    if (b) { b.click(); return 'clicked:text'; }
    return 'not found';
})()
""")
        return result

    clicked = "not found"
    for dl_attempt in range(1, 4):
        hover_depth = 6 + (dl_attempt - 1) * 2
        print(f"  Hovering image to reveal download button (attempt {dl_attempt}/3, depth={hover_depth})...")
        _hover_image(hover_depth)
        time.sleep(1.5)

        print(f"  Clicking 'Download full size image' (attempt {dl_attempt}/3)...")
        clicked = _click_download()
        if clicked.startswith("clicked"):
            print(f"  {clicked}")
            break

        surprise = [
            f for f in (set(os.listdir(downloads_dir)) - before)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        if surprise:
            matched_fallback = max(surprise, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
            src = os.path.join(downloads_dir, matched_fallback)
            shutil.move(src, output_path)
            print(f"  [OK] Recovered from Downloads: {os.path.basename(output_path)}  ({os.path.getsize(output_path):,} bytes)")
            return "success"

        if dl_attempt < 3:
            print(f"  Download button not found — waiting 3s before next hover attempt...")
            time.sleep(3)

    if not clicked.startswith("clicked"):
        print("  Could not find 'Download full size image' button after 3 attempts.")
        return "failed"

    print("  Download triggered. Waiting for file in ~/Downloads...")
    matched = None
    for dl_wait_attempt in range(1, 4):
        for _ in range(30):
            time.sleep(1)
            after = set(os.listdir(downloads_dir))
            new_files = after - before
            imgs = [f for f in new_files if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
            if imgs:
                matched = max(imgs, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
                break
        if matched:
            break
        if dl_wait_attempt < 3:
            print(f"  File not yet in Downloads — re-clicking download (attempt {dl_wait_attempt + 1}/3)...")
            _hover_image(8)
            time.sleep(1.5)
            _click_download()

    if not matched:
        print("  No image file appeared in ~/Downloads after retries.")
        return "failed"

    src = os.path.join(downloads_dir, matched)
    shutil.move(src, output_path)
    print(f"  [OK] Saved: {os.path.basename(output_path)}  ({os.path.getsize(output_path):,} bytes)")
    return "success"


def switch_to_pro_model() -> bool:
    print("  Switching to Pro model...", end="", flush=True)
    opened = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button,[role=button],[role=combobox]'));
    var btn = btns.find(function(b) {
        var txt = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').toLowerCase();
        return (txt.indexOf('flash') !== -1 || txt.indexOf('pro') !== -1 ||
                txt.indexOf('gemini 2') !== -1 || txt.indexOf('model') !== -1) &&
               b.offsetParent !== null;
    });
    if (btn) { btn.click(); return 'opened'; }
    return 'not found';
})()
""")

    if opened != 'opened':
        print(" model selector not found.")
        return False

    time.sleep(1.5)

    clicked = run_js_in_chrome("""
(function() {
    var options = Array.from(document.querySelectorAll(
        '[role=option],[role=menuitem],[role=listitem],li,mat-option,.model-option'
    ));
    var pro = options.find(function(o) {
        var txt = (o.innerText || o.textContent || '').toLowerCase();
        return (txt.indexOf('pro') !== -1 || txt.indexOf('2.5') !== -1) &&
               o.offsetParent !== null;
    });
    if (pro) { pro.click(); return 'clicked'; }
    return 'not found';
})()
""")

    if clicked == 'clicked':
        print(" done.")
        time.sleep(1)
        return True

    print(" Pro option not found in dropdown.")
    run_osascript('tell application "System Events" to key code 53')
    time.sleep(0.5)
    return False


def cooldown_wait(seconds: int, label: str = "Retrying"):
    print(f"  Waiting {seconds}s — {label}...", flush=True)
    for remaining in range(seconds, 0, -1):
        print(f"  {label} in {remaining}s...  ", end="\r", flush=True)
        time.sleep(1)
    print(f"  {label}...                        ")


# ─────────────────────────────────────────────────────────────────────────────
# Grok debug helpers
# ─────────────────────────────────────────────────────────────────────────────

_grok_log_fh = None   # file handle opened by generate_grok_video, closed in finally


def _grok_write(line: str):
    """Write line to terminal and to the log file (if open)."""
    print(line)
    if _grok_log_fh:
        _grok_log_fh.write(line + "\n")
        _grok_log_fh.flush()


def _grok_log(label: str, msg: str):
    if GROK_DEBUG:
        _grok_write(f"  [GROK:{label}] {msg}")


def _grok_dump_dom(label: str):
    """Dump Grok page DOM state — URL, visible buttons, inputs, images, page text."""
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

    inputs = run_js_in_chrome(r"""
(function() {
    var els = Array.from(document.querySelectorAll('input,textarea,[contenteditable="true"]'));
    return els
        .map(function(e) {
            return e.tagName
                + '[type=' + (e.type || '') + ']'
                + '[testid=' + (e.getAttribute('data-testid') || '') + ']'
                + '[accept=' + (e.accept || '') + ']'
                + '[placeholder=' + (e.placeholder || '') + ']'
                + '[visible=' + (e.offsetParent !== null ? 'yes' : 'no') + ']';
        })
        .slice(0, 10)
        .join(' | ');
})()
""")
    _grok_write(f"  [GROK-DOM:{label}] Inputs: {inputs}")

    imgs = run_js_in_chrome(r"""
(function() {
    var imgs = Array.from(document.querySelectorAll('img'));
    return imgs
        .filter(function(i) { return i.offsetParent !== null && i.naturalWidth > 30; })
        .map(function(i) {
            return '[' + i.naturalWidth + 'x' + i.naturalHeight + '] ' + i.src.slice(0,80);
        })
        .slice(0, 8)
        .join('\n        ');
})()
""")
    _grok_write(f"  [GROK-DOM:{label}] Images: {imgs}")

    text = run_js_in_chrome(r"(document.body.innerText || '').replace(/\n+/g,' ').trim().slice(-500)")
    _grok_write(f"  [GROK-DOM:{label}] Page text (last 500): {text}")
    _grok_write(f"  [GROK-DOM:{label}] ═════════════════════════════════════════════\n")


# ─────────────────────────────────────────────────────────────────────────────
# Grok helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def setup_grok_chrome() -> bool:
    """Kill Chrome, open with Grok profile, wait for load. Returns True on success."""
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
    """Steps 3-4: click nav sidebar item then Image-to-Video button."""
    _grok_log("navigate", "--- navigate_to_image_to_video ---")
    _grok_dump_dom("nav-start")

    # Step 3 — sidebar nav item
    r = run_js_in_chrome(r"""
(function() {
    // Recording selector
    var el = document.querySelector('div.pb-1 > div:nth-of-type(4) span');
    if (el && el.offsetParent !== null) { el.click(); return 'clicked:recording-selector'; }
    // Fallback: nav links containing media/create/aurora/image/video
    var links = Array.from(document.querySelectorAll('a,button,[role=button],[role=link],span'));
    var media = links.find(function(l) {
        var t = (l.innerText || l.textContent || '').trim().toLowerCase();
        return (t === 'media' || t === 'create' || t === 'aurora'
                || t.indexOf('image') !== -1 || t.indexOf('video') !== -1)
               && l.offsetParent !== null && t.length < 30;
    });
    if (media) { media.click(); return 'clicked:text:' + (media.innerText||'').trim().slice(0,30); }
    // List all visible nav-like elements for debug
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
    _grok_dump_dom("after-step3")

    # Step 4 — Image to Video button
    r = run_js_in_chrome(r"""
(function() {
    // Recording selector
    var el = document.querySelector('button.text-primary-foreground span');
    if (el && el.offsetParent !== null) { el.click(); return 'clicked:recording-selector'; }
    // Fallback 1: drop-ui buttons
    var dropBtns = Array.from(document.querySelectorAll('[data-testid="drop-ui"] button'));
    if (dropBtns.length >= 2) { dropBtns[1].click(); return 'clicked:drop-ui-btn[1]'; }
    if (dropBtns.length >= 1) { dropBtns[0].click(); return 'clicked:drop-ui-btn[0]'; }
    // Fallback 2: any button/tab with 'video' in text
    var all = Array.from(document.querySelectorAll('button,[role=button],[role=tab]'));
    var vid = all.find(function(b) {
        var t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
        return t.indexOf('video') !== -1 && b.offsetParent !== null;
    });
    if (vid) { vid.click(); return 'clicked:text-video:' + (vid.innerText||'').trim().replace(/\n/g,' ').slice(0,30); }
    // List all visible buttons for debug
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
    _grok_dump_dom("after-step4")
    return True


def grok_select_quality_and_duration() -> bool:
    """Steps 5-6: select 720p quality and 10s duration."""
    _grok_log("quality", "--- select_quality_and_duration ---")

    # Step 5 — 720p
    r = run_js_in_chrome(r"""
(function() {
    // Recording selector
    var el = document.querySelector('div.flex-wrap > div:nth-of-type(2) button.text-primary span');
    if (el && el.offsetParent !== null) { el.click(); return 'clicked:recording-selector'; }
    // Fallback: any span/button with exact text '720p'
    var spans = Array.from(document.querySelectorAll('span,button'));
    var el720 = spans.find(function(s) {
        return s.textContent.trim() === '720p' && s.offsetParent !== null;
    });
    if (el720) { el720.click(); return 'clicked:text-720p tag=' + el720.tagName; }
    // List all visible quality/duration option texts for debug
    var opts = spans.filter(function(s) { return s.offsetParent !== null; })
        .map(function(s) { return s.textContent.trim(); })
        .filter(function(t) { return /^\d{3,4}p$|^\d+s$/.test(t); });
    return 'not found — quality/duration options visible: ' + (opts.join(' | ') || 'none');
})()
""")
    _grok_log("step5-720p", r)
    time.sleep(1)

    # Step 6 — 10s
    r = run_js_in_chrome(r"""
(function() {
    // Recording selector
    var el = document.querySelector('div:nth-of-type(3) button.text-primary span');
    if (el && el.offsetParent !== null) { el.click(); return 'clicked:recording-selector'; }
    // Fallback: any span/button with exact text '10s'
    var spans = Array.from(document.querySelectorAll('span,button'));
    var el10s = spans.find(function(s) {
        return s.textContent.trim() === '10s' && s.offsetParent !== null;
    });
    if (el10s) { el10s.click(); return 'clicked:text-10s tag=' + el10s.tagName; }
    var opts = spans.filter(function(s) { return s.offsetParent !== null; })
        .map(function(s) { return s.textContent.trim(); })
        .filter(function(t) { return /^\d+s$/.test(t); });
    return 'not found — duration options visible: ' + (opts.join(' | ') || 'none');
})()
""")
    _grok_log("step6-10s", r)
    time.sleep(1)
    return True


def grok_upload_image(image_path: str) -> bool:
    """Upload reference image: find file input, drive macOS file chooser, verify preview."""
    _grok_log("upload", f"--- upload_image: {image_path} ---")

    if not os.path.exists(image_path):
        _grok_log("upload", f"ERROR: image file does not exist: {image_path}")
        return False
    _grok_log("upload", f"Image file on disk: {os.path.getsize(image_path):,} bytes")

    _grok_dump_dom("upload-start")

    abs_image_path = os.path.abspath(image_path)

    # ── Primary approach: inject base64 data directly via DataTransfer (no file dialog) ──
    # This bypasses all coordinate-click / file-dialog / Cmd+Shift+G fragility.
    _grok_log("upload", "Injecting image via DataTransfer (base64 chunks)...")
    try:
        with open(abs_image_path, 'rb') as _f:
            _raw = _f.read()
        _b64 = base64.b64encode(_raw).decode('ascii')
        _grok_log("upload-inject", f"{len(_raw):,} bytes → {len(_b64):,} base64 chars")

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

        // 1. Set on file input + dispatch change
        var fi = document.querySelector('input[type="file"]');
        var fiOk = false;
        if (fi) {{
            try {{ var dt = new DataTransfer(); dt.items.add(file); fi.files = dt.files; fiOk = true; }} catch(e) {{}}
            fi.dispatchEvent(new Event('change', {{bubbles: true}}));
            fi.dispatchEvent(new Event('input',  {{bubbles: true}}));
        }}

        // 2. Simulate drop on the upload drop-zone
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
        inject_result = f"exception:{_e}"

    # Verify — wait up to ~22s for Grok to process the upload
    time.sleep(3)
    _upload_confirmed = False
    for attempt in range(15):
        r = run_js_in_chrome(r"""
(function() {
    var fi = document.querySelector('input[type="file"]');
    if (fi && fi.files && fi.files.length > 0)
        return 'uploaded:file-input name=' + fi.files[0].name + ' size=' + fi.files[0].size;
    var imgs = Array.from(document.querySelectorAll('img'));
    var thumb = imgs.find(function(i) {
        return i.naturalWidth > 30 && i.offsetParent !== null
               && (i.src.indexOf('blob:') === 0 || i.src.indexOf('data:') === 0);
    });
    if (thumb) return 'uploaded:blob-preview src=' + thumb.src.slice(0, 60);
    var body = (document.body.innerText || '').toLowerCase();
    if (body.indexOf('upload or drop') === -1 && body.indexOf('drop image') === -1)
        return 'possibly-uploaded:upload-text-gone';
    return 'waiting (upload area still showing)';
})()
""")
        _grok_log(f"upload-verify-{attempt+1}/15", r)
        if r.startswith('uploaded:'):
            _grok_log("upload", "Image upload confirmed.")
            _upload_confirmed = True
            break
        time.sleep(1.5)

    if _upload_confirmed:
        return True

    # ── Fallback: coordinate click → macOS file chooser → Cmd+Shift+G ─────────
    _grok_log("upload", "DataTransfer unconfirmed — falling back to coordinate click...")
    _grok_dump_dom("upload-inject-fail")

    coords_js = run_js_in_chrome(r"""
(function() {
    var fi = document.querySelector('input[type="file"]');
    var el = fi ? fi.parentElement : null;
    while (el && el !== document.body) {
        var r = el.getBoundingClientRect();
        if (r.width >= 80 && r.height >= 40 && el.offsetParent !== null) break;
        el = el.parentElement;
    }
    if (!el || el === document.body) {
        var all = Array.from(document.querySelectorAll('*'));
        var txt = all.find(function(e) {
            return (e.innerText || '').trim().toLowerCase().indexOf('upload or drop') !== -1
                   && e.offsetParent !== null;
        });
        if (txt) el = txt;
    }
    if (!el || el === document.body) return 'not found';
    var r = el.getBoundingClientRect();
    var toolbarH = window.outerHeight - window.innerHeight;
    return Math.round(r.left + r.width/2) + ',' + Math.round(r.top + r.height/2) + ',' + toolbarH;
})()
""")
    _grok_log("fallback-coords", coords_js)

    if coords_js != 'not found' and ',' in coords_js:
        el_cx, el_cy, toolbar_h = map(int, coords_js.split(','))
        win_raw = run_osascript('tell application "Google Chrome" to return bounds of front window')
        _grok_log("fallback-win-bounds", win_raw)
        try:
            win_x, win_y = int(win_raw.split(',')[0].strip()), int(win_raw.split(',')[1].strip())
        except Exception:
            win_x, win_y = 0, 0
        screen_x = win_x + el_cx
        screen_y = win_y + toolbar_h + el_cy
        _grok_log("fallback-click-coords", f"screen=({screen_x},{screen_y})")

        run_osascript('tell application "Google Chrome" to activate')
        time.sleep(0.3)
        run_osascript(f'tell application "System Events" to click at {{{screen_x}, {screen_y}}}')
        _grok_log("fallback-click", "Click sent. Waiting for file dialog...")
        time.sleep(3)

        try:
            subprocess.run(["pbcopy"], input=abs_image_path.encode("utf-8"), check=True)
        except Exception as _e:
            _grok_log("fallback-clipboard", f"ERROR: {_e}")

        run_osascript("""
tell application "System Events"
    delay 1.0
    keystroke "g" using {command down, shift down}
    delay 1.0
    keystroke "v" using {command down}
    delay 0.5
    key code 36
    delay 1.0
    key code 36
end tell
""")
        _grok_log("fallback-file-chooser", "Cmd+Shift+G executed. Waiting for upload...")
        time.sleep(4)

    # Final verify
    for attempt in range(10):
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
    if (body.indexOf('upload or drop') === -1) return 'possibly-uploaded:upload-text-gone';
    return 'waiting (upload area still showing)';
})()
""")
        _grok_log(f"fallback-verify-{attempt+1}/10", r)
        if r.startswith('uploaded:'):
            _grok_log("upload", "Fallback upload confirmed.")
            return True
        time.sleep(1.5)

    _grok_dump_dom("upload-fail")
    _grok_log("upload", "Upload not confirmed after all attempts — proceeding anyway.")
    return True  # proceed; Grok may have silently accepted the file


def grok_type_prompt(video_prompt: str) -> bool:
    """Type the video prompt into the [data-testid=drop-ui] <p> field via clipboard paste.
    Must be called BEFORE uploading the reference image (matches Grok's expected input order).
    Returns False if the prompt is missing from the field after pasting.
    """
    _grok_log("prompt", "--- grok_type_prompt ---")

    if not video_prompt:
        _grok_log("prompt", "No video prompt — skipping text entry.")
        return True

    _grok_log("prompt", f"Prompt ({len(video_prompt)} chars): {video_prompt[:120]}...")

    # Copy prompt to clipboard
    subprocess.run(["pbcopy"], input=video_prompt.encode("utf-8"), check=True)
    _grok_log("prompt", "Prompt copied to clipboard.")

    # Get the screen coordinates of the prompt field using the EXACT XPath from the browser
    # recording. CSS querySelector("form p") still returns template-card <p> elements because
    # the whole gallery is inside the same form. The XPath pins the exact node.
    coords = run_js_in_chrome(r"""
(function() {
    // Exact XPath from Chrome browser recording — points to the prompt <p> inside the form
    var xpathExpr = '//*[@data-testid="drop-ui"]/div/div[2]/div/form/div/div/div/div[2]/div[2]/div/div/div/div/p';
    var res = document.evaluate(xpathExpr, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
    var el = res.singleNodeValue;

    // Fallback 1: the contenteditable inside the second div[2] of drop-ui (parent of the <p>)
    if (!el) {
        var xpathCe = '//*[@data-testid="drop-ui"]/div/div[2]/div/form/div/div/div/div[2]/div[2]/div/div/div/div';
        var res2 = document.evaluate(xpathCe, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
        el = res2.singleNodeValue;
    }
    // Fallback 2: the contenteditable whose bounding rect is in the upper half of the page
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
    return x + ',' + y + ':' + el.tagName + ':top=' + Math.round(r.top);
})()
""")
    _grok_log("prompt-coords", coords)

    if 'not-found' in coords or ',' not in coords:
        _grok_log("prompt", "ERROR: Could not get screen coords for prompt field.")
        return False

    sx, rest = coords.split(',', 1)
    parts = rest.split(':')
    sy = parts[0]
    tag = ':'.join(parts[1:]) if len(parts) > 1 else '?'

    # Activate Chrome, then do a REAL OS-level mouse click at the element's screen position.
    # JS element.focus() called via AppleScript remote-JS is ignored by the browser (no user
    # gesture context), so the element never gets real OS keyboard focus. A System Events
    # click at the actual screen coordinates gives genuine focus that accepts keystrokes.
    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(0.4)
    run_osascript(f'tell application "System Events" to click at {{{sx}, {sy}}}')
    _grok_log("prompt-click", f"real-click at ({sx},{sy}) tag={tag}")
    time.sleep(0.4)

    # Verify the click did NOT navigate away from /imagine (template cards cause navigation)
    url_after = run_osascript(
        'tell application "Google Chrome" to return URL of active tab of front window'
    )
    _grok_log("prompt-url-after-click", url_after)
    if '/imagine/post/' in url_after or '/imagine/post/' in url_after:
        _grok_log("prompt", f"ERROR: Click navigated to post page ({url_after}) — hit a template card. Fix: check form-p selector.")
        return False
    if 'grok.com/imagine' not in url_after:
        _grok_log("prompt", f"ERROR: Click navigated away from /imagine ({url_after}).")
        return False

    # Paste via Cmd+V — element now has real OS focus, React will receive the paste event
    run_osascript('tell application "System Events" to keystroke "v" using command down')
    time.sleep(1.0)
    _grok_log("prompt-insert", f"clipboard-paste:cmd-v at ({sx},{sy})")

    # Verify the prompt landed in the form's prompt field (not a template card)
    verify = run_js_in_chrome(r"""
(function() {
    var p = document.querySelector('[data-testid="drop-ui"] form p');
    if (p) {
        var v = (p.innerText || p.textContent || '').trim();
        if (v.length > 10) return 'prompt-ok:form-p ' + v.length + ' chars: ' + v.slice(0, 80);
    }
    var ce = document.querySelector('[data-testid="drop-ui"] form [contenteditable="true"]');
    if (ce) {
        var v = (ce.innerText || ce.textContent || '').trim();
        if (v.length > 10) return 'prompt-ok:form-ce ' + v.length + ' chars: ' + v.slice(0, 80);
    }
    return 'prompt-MISSING';
})()
""")
    _grok_log("prompt-verify", verify)

    if 'prompt-MISSING' in verify:
        _grok_log("prompt", "ERROR: Prompt not in drop-ui field after paste.")
        _grok_dump_dom("prompt-fail")
        return False

    return True


def grok_submit_form(video_prompt: str) -> bool:
    """Pre-submit check (image + prompt present) then click the Submit button.
    Must be called AFTER both grok_type_prompt() and grok_upload_image().
    Returns True (submit fired or best-effort), False only if image is missing.
    """
    _grok_log("submit", "--- grok_submit_form ---")

    # ── Pre-submit validation ─────────────────────────────────────────────────
    _grok_log("pre-submit-check", "Verifying reference image + prompt before submit...")

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

    prompt_verify = run_js_in_chrome(r"""
(function() {
    var p = document.querySelector('[data-testid="drop-ui"] form p');
    if (p) {
        var v = (p.innerText || p.textContent || '').trim();
        if (v.length > 10) return 'prompt-ok:form-p ' + v.length + ' chars: ' + v.slice(0, 80);
    }
    var ce = document.querySelector('[data-testid="drop-ui"] form [contenteditable="true"]');
    if (ce) {
        var v = (ce.innerText || ce.textContent || '').trim();
        if (v.length > 10) return 'prompt-ok:form-ce ' + v.length + ' chars: ' + v.slice(0, 80);
    }
    return 'prompt-MISSING';
})()
""")

    _grok_log("pre-submit-check", f"image={image_ok}")
    _grok_log("pre-submit-check", f"prompt={prompt_verify}")

    if 'image-MISSING' in image_ok:
        _grok_log("pre-submit-check", "ABORT: Reference image not confirmed.")
        _grok_dump_dom("pre-submit-abort")
        return False

    if video_prompt and 'prompt-MISSING' in prompt_verify:
        _grok_log("pre-submit-check", "ABORT: Prompt not in page.")
        _grok_dump_dom("pre-submit-abort")
        return False

    _grok_log("pre-submit-check", "Both confirmed — submitting.")
    _grok_dump_dom("before-submit")

    # ── Click Submit ──────────────────────────────────────────────────────────
    # Selector confirmed by browser recording: div.query-bar > div.absolute svg
    r = run_js_in_chrome(r"""
(function() {
    var el = document.querySelector('div.query-bar > div.absolute svg');
    if (el && el.offsetParent !== null) {
        el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        return 'clicked:query-bar-svg';
    }
    // Fallback: aria-label Submit button
    var btns = Array.from(document.querySelectorAll('button,[role=button]'));
    var btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || '').toLowerCase();
        return (lbl.indexOf('submit') !== -1 || lbl.indexOf('generate') !== -1)
               && b.offsetParent !== null;
    });
    if (btn) { btn.click(); return 'clicked:aria:' + (btn.getAttribute('aria-label') || btn.title || '').trim(); }
    var visible = btns
        .filter(function(b) { return b.offsetParent !== null; })
        .map(function(b) { return (b.getAttribute('aria-label') || b.innerText || '').trim().slice(0, 30); })
        .filter(Boolean).slice(0, 15).join(' | ');
    return 'not-found — buttons: ' + visible;
})()
""")
    _grok_log("submit", r)
    if r.startswith('not-found'):
        _grok_dump_dom("submit-fail")
    time.sleep(8)

    # ── Confirm submission fired ──────────────────────────────────────────────
    submit_ok = run_js_in_chrome(r"""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var labels = btns.filter(function(b) { return b.offsetParent !== null; })
                     .map(function(b) { return (b.getAttribute('aria-label') || b.innerText || '').trim().toLowerCase(); });
    if (labels.some(function(l) { return l.indexOf('cancel') !== -1; }))
        return 'confirmed:cancel-button-visible';
    if (window.location.href.indexOf('/imagine/post/') !== -1)
        return 'confirmed:url=' + window.location.href.slice(0, 60);
    return 'not-confirmed';
})()
""")
    _grok_log("submit-check", submit_ok)

    if 'not-confirmed' in submit_ok:
        # Check if React cleared the prompt field (= submit already succeeded)
        import re as _re
        field_check = run_js_in_chrome(r"""
(function() {
    var p = document.querySelector('[data-testid="drop-ui"] p');
    if (p) return 'p:' + (p.innerText || p.textContent || '').trim().length;
    var ce = document.querySelector('[contenteditable="true"]');
    if (ce) return 'ce:' + (ce.innerText || ce.textContent || '').trim().length;
    return 'no-field:0';
})()
""")
        _grok_log("submit-retry-precheck", field_check)
        _m = _re.search(r':(\d+)$', field_check)
        field_len = int(_m.group(1)) if _m else 999

        if field_len < 10:
            _grok_log("submit-retry", "Field cleared by React — 1st submit succeeded, no retry needed.")
        else:
            _grok_log("submit-retry", "Prompt still in field — retrying with Enter key...")
            run_osascript('tell application "Google Chrome" to activate')
            time.sleep(0.2)
            run_osascript('tell application "System Events" to key code 36')
            time.sleep(3)
            submit_ok2 = run_js_in_chrome(r"""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var labels = btns.filter(function(b) { return b.offsetParent !== null; })
                     .map(function(b) { return (b.getAttribute('aria-label') || b.innerText || '').trim().toLowerCase(); });
    if (labels.some(function(l) { return l.indexOf('cancel') !== -1; }))
        return 'confirmed:cancel-button-visible';
    if (window.location.href.indexOf('/imagine/post/') !== -1)
        return 'confirmed:url=' + window.location.href.slice(0, 60);
    return 'not-confirmed';
})()
""")
            _grok_log("submit-retry-check", submit_ok2)

    return True


def wait_for_grok_video_and_download(video_output_path: str) -> str:
    """Poll until Grok video is ready, play it, then download MP4.
    Returns 'success', 'timeout', or 'failed'.
    """
    _grok_log("wait-video", f"--- wait_for_grok_video_and_download ({GROK_VIDEO_WAIT}s max) ---")

    # Prevent Mac display sleep while waiting for video generation
    caff = subprocess.Popen(["caffeinate", "-d", "-t", str(GROK_VIDEO_WAIT + 300)])

    try:
        # Wait at least 60s before first poll — Grok video generation always takes time
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

    // "Cancel Video" means the video is STILL generating — not ready yet
    var cancelVideoPresent = visibleBtnLabels.some(function(l) {
        return l.indexOf('cancel video') !== -1 || l.indexOf('cancel') !== -1;
    });

    // Download button present
    var dlLabel = visibleBtnLabels.find(function(l) { return l.indexOf('download') !== -1; });
    var dlPresent = !!dlLabel;

    // User-generated video element — reject gallery demo videos
    var vid = document.querySelector('video');
    var vidSrc = vid ? (vid.src || vid.currentSrc || '') : '';
    var isUserVideo = vidSrc.indexOf('assets.grok.com/users') !== -1;

    if (isUserVideo && !cancelVideoPresent) {
        return 'ready:video-element src=' + vidSrc.slice(0, 80);
    }
    if (dlPresent && !cancelVideoPresent) {
        // Extra check: "Extend from Frame" button only appears on video posts
        var hasExtend = visibleBtnLabels.some(function(l) { return l.indexOf('extend') !== -1; });
        if (hasExtend) return 'ready:download-btn+extend-from-frame label=' + dlLabel;
        return 'ready:download-btn (no cancel-video present) label=' + dlLabel;
    }
    if (dlPresent && cancelVideoPresent) {
        return 'generating:download-btn-present-but-cancel-video-also-present (video still processing)';
    }

    // Error detection
    var body = (document.body.innerText || '').toLowerCase().slice(-2000);
    if (body.indexOf('failed') !== -1) return 'error:failed';
    if (body.indexOf('could not generate') !== -1) return 'error:could-not-generate';
    if (body.indexOf('try again') !== -1) return 'error:try-again';

    var snippet = (document.body.innerText || '').replace(/\n+/g, ' ').trim().slice(-150);
    return 'generating — page: ' + snippet;
})()
""")
            # Only log if state changed
            if state != last_state:
                _grok_log(f"poll-{elapsed}s", state)
                last_state = state
            else:
                print(f"  [GROK:poll] ...{elapsed}s/{GROK_VIDEO_WAIT}s (state unchanged)", end="\r", flush=True)

            if state.startswith('ready'):
                print()
                video_ready = True
                break
            if state.startswith('error'):
                print()
                _grok_dump_dom("video-error")
                return 'failed'

        if not video_ready:
            print()
            _grok_log("wait-video", f"Timeout — video not ready after {GROK_VIDEO_WAIT}s.")
            _grok_dump_dom("video-timeout")
            return 'timeout'

        _grok_log("wait-video", f"Video ready at {elapsed}s.")
        _grok_dump_dom("before-download")

        downloads_dir = os.path.expanduser("~/Downloads")
        before = set(os.listdir(downloads_dir))

        # Step 1: Make sure video is playing (click Play if paused), then wait 2s
        play_result = run_js_in_chrome(r"""
(function() {
    var vid = document.querySelector('video');
    if (!vid) return 'no-video-element';
    if (vid.paused) {
        vid.play();
        return 'play:started src=' + (vid.src || vid.currentSrc || '').slice(0, 60);
    }
    return 'play:already-playing src=' + (vid.src || vid.currentSrc || '').slice(0, 60);
})()
""")
        _grok_log("video-play", play_result)
        time.sleep(2)

        # Step 2: Try JS fetch-blob download (uses Chrome's auth cookies — most reliable)
        video_src = run_js_in_chrome(r"""
(function() {
    var vid = document.querySelector('video');
    if (!vid) return '';
    return vid.src || vid.currentSrc || '';
})()
""")
        _grok_log("video-src", video_src[:80] if video_src else "none")

        if video_src and video_src.startswith('https://assets.grok.com/users'):
            _grok_log("download-method", "Trying JS fetch-blob download (credentials:include)...")
            fetch_js = r"""
(function() {
    var vid = document.querySelector('video');
    if (!vid) return 'no-video';
    var src = vid.src || vid.currentSrc;
    if (!src) return 'no-src';
    fetch(src, {credentials: 'include'})
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.blob();
        })
        .then(function(blob) {
            if (blob.size === 0) { console.warn('grok-fetch: blob is 0 bytes'); return; }
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'grok_video_blob.mp4';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        })
        .catch(function(e) { console.warn('grok-fetch error:', e); });
    return 'fetch-blob:triggered for ' + src.slice(0, 60);
})()
"""
            fr = run_js_in_chrome(fetch_js)
            _grok_log("download-fetch", fr)
            time.sleep(3)

        # Step 3: Also click the Download button (belt + suspenders)
        r = run_js_in_chrome(r"""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var dl = btns.find(function(b) {
        return (b.getAttribute('aria-label') || '').toLowerCase().indexOf('download') !== -1
               && b.offsetParent !== null;
    });
    if (dl) { dl.click(); return 'clicked:aria-label=' + dl.getAttribute('aria-label'); }
    var visible = btns
        .filter(function(b) { return b.offsetParent !== null; })
        .map(function(b) { return (b.getAttribute('aria-label') || b.innerText || '').trim().replace(/\n/g,' ').slice(0,30); })
        .filter(function(t) { return t.length > 0; }).slice(0, 15).join(' | ');
    return 'not found — buttons: ' + visible;
})()
""")
        _grok_log("download-btn", r)
        if r.startswith('not found'):
            _grok_dump_dom("download-btn-fail")

        # Dismiss any "Save As" dialog Chrome may show (press Enter to accept default location)
        time.sleep(1)
        run_osascript('tell application "Google Chrome" to activate')
        time.sleep(0.3)
        run_osascript('tell application "System Events" to key code 36')  # Return/Enter

        # Step 4: Watch ~/Downloads for MP4 to appear.
        # Chrome may write a 0-byte .mp4 placeholder before the download fills it,
        # or it may write a .crdownload that renames to .mp4 when done.
        # Only accept a non-zero .mp4, or wait for a .crdownload to finish.
        _grok_log("download-wait", f"Watching ~/Downloads for MP4 (up to {GROK_FILE_WAIT}s)...")
        matched = None
        for tick in range(GROK_FILE_WAIT):
            time.sleep(1)
            after = set(os.listdir(downloads_dir))
            new_files = after - before
            mp4s = [f for f in new_files if f.lower().endswith(".mp4")]
            crdownloads = [f for f in new_files if f.lower().endswith(".crdownload")]
            if mp4s:
                # Prefer a non-zero .mp4; ignore 0-byte placeholders if a .crdownload is still active
                nonzero = [f for f in mp4s
                           if os.path.getsize(os.path.join(downloads_dir, f)) > 0]
                if nonzero:
                    matched = max(nonzero, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
                    _grok_log("download-wait", f"Non-zero MP4 appeared at {tick+1}s: {matched}")
                    break
                elif not crdownloads:
                    # 0-byte MP4(s) and no active .crdownload — take the newest one anyway
                    matched = max(mp4s, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
                    _grok_log("download-wait", f"0-byte MP4 (no crdownload) at {tick+1}s: {matched}")
                    break
                else:
                    _grok_log("download-wait", f"{tick+1}s: 0-byte mp4 present, waiting for crdownload to finish: {crdownloads}")
            elif crdownloads and tick % 5 == 4:
                _grok_log("download-wait", f"{tick+1}s: crdownload active: {crdownloads}")
            elif tick % 10 == 9:
                _grok_log("download-wait", f"{tick+1}s elapsed, no MP4 yet. crdownload={crdownloads} new={list(new_files)[:5]}")

        if not matched:
            _grok_log("download-wait", f"No MP4 in ~/Downloads after {GROK_FILE_WAIT}s.")
            _grok_dump_dom("download-fail")
            return 'failed'

        # Step 5: Wait for the file to be fully written — poll until size is stable and > 0
        # Chrome writes the file progressively; moving a 0-byte or partial file loses the video.
        src = os.path.join(downloads_dir, matched)
        _grok_log("download-stable", "Waiting for file to finish writing...")
        prev_size = -1
        stable_count = 0
        for stable_tick in range(120):  # up to 2 min for large file to finish
            time.sleep(2)
            try:
                cur_size = os.path.getsize(src)
            except OSError:
                cur_size = 0
            _grok_log("download-stable", f"{stable_tick*2+2}s: {cur_size:,} bytes")
            # If file stayed at 0 bytes for 20s, check if a different (non-zero) MP4 appeared
            if cur_size == 0 and stable_tick >= 9:
                after_rescan = set(os.listdir(downloads_dir))
                new_now = after_rescan - before
                alt_mp4s = [f for f in new_now
                            if f.lower().endswith(".mp4") and f != matched
                            and os.path.getsize(os.path.join(downloads_dir, f)) > 0]
                if alt_mp4s:
                    matched = max(alt_mp4s, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
                    src = os.path.join(downloads_dir, matched)
                    _grok_log("download-stable", f"Switched to non-zero MP4: {matched}")
                    prev_size = -1
                    stable_count = 0
                    continue
            if cur_size > 0 and cur_size == prev_size:
                stable_count += 1
                if stable_count >= 3:  # unchanged for 6 consecutive seconds = done
                    _grok_log("download-stable", f"File stable at {cur_size:,} bytes — download complete.")
                    break
            else:
                stable_count = 0
            prev_size = cur_size
        else:
            _grok_log("download-stable", "WARNING: file never stabilised — moving anyway.")

        final_size = os.path.getsize(src) if os.path.exists(src) else 0
        if final_size == 0:
            _grok_log("download-stable", "ERROR: file is 0 bytes after waiting — download failed.")
            return 'failed'

        shutil.move(src, video_output_path)
        _grok_log("download-done", f"Saved: {os.path.basename(video_output_path)}  ({os.path.getsize(video_output_path):,} bytes)")
        return 'success'

    finally:
        caff.terminate()


def generate_grok_video_via_extension(image_path: str, video_prompt: str, video_output_path: str) -> str:
    """Generate a Grok video using the Chrome extension + local HTTP server approach.

    Architecture:
      1. Start a tiny HTTP server on localhost:7878 serving job JSON and accepting status POSTs.
      2. Open Chrome at grok.com/imagine with the Grok profile.
      3. The installed Chrome extension polls /job, drives the Grok UI, downloads the video.
      4. Python polls ~/Downloads for a new MP4 and moves it to video_output_path.

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

    # ── Encode image as base64 ────────────────────────────────────────────────
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")
    image_filename = os.path.basename(image_path)

    # ── Shared state for HTTP server thread ───────────────────────────────────
    state = {
        "job": {
            "status": "pending",
            "prompt": video_prompt or "",
            "image_b64": image_b64,
            "image_filename": image_filename,
        },
        "ext_status": "pending",   # updated by extension POSTs
        "ext_message": "",
        "done": False,
    }
    state_lock = threading.Lock()

    # ── HTTP server ───────────────────────────────────────────────────────────
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # suppress default access logs
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
                    # Capture video URL if reported by extension
                    msg_text = state["ext_message"]
                    if msg_text.startswith("video_url_detected:"):
                        detected_url = msg_text[len("video_url_detected:"):].strip()
                        if detected_url and not state.get("video_url"):
                            state["video_url"] = detected_url
                            _grok_log("ext-network", f"VIDEO URL DETECTED: {detected_url}")
                    elif msg_text.startswith("downloading_via_extension:"):
                        dl_url = msg_text[len("downloading_via_extension:"):].strip()
                        _grok_log("ext-network", f"DOWNLOAD TRIGGERED via chrome.downloads: {dl_url}")
                    elif msg_text.startswith("video_ready:"):
                        _grok_log("ext-network", f"VIDEO GENERATION CONFIRMED: {msg_text}")
                    # Mirror running/done into the job so page reloads don't restart
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
                # Individual log line from content script log()
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
                # Batch of network request/response entries from the background script
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    entries = json.loads(body)
                    if isinstance(entries, dict):
                        entries = [entries]  # single entry, wrap in list
                except Exception:
                    entries = []
                for e in entries:
                    direction = e.get("dir", "NET")
                    method    = e.get("method", "")
                    url       = e.get("url", "")
                    status    = e.get("status", "")
                    err       = e.get("error", "")
                    rtype     = e.get("type", "")
                    # Highlight video-related URLs
                    is_video = "mp4" in url.lower() or "video" in url.lower()
                    tag = "net-VIDEO" if is_video else "net"
                    status_str = f"  [{status}]" if status else ""
                    err_str    = f"  ERR={err}" if err else ""
                    type_str   = f"  ({rtype})" if rtype else ""
                    _grok_log(tag, f"{direction}  {method}  {url}{status_str}{err_str}{type_str}")
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
        # ── Open Chrome at grok.com/imagine ──────────────────────────────────
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

        # Wait for Chrome to open and extension to connect (up to 30s)
        _grok_log("ext", "Waiting for extension to connect...")
        deadline = time.time() + 30
        while time.time() < deadline:
            with state_lock:
                s = state["ext_status"]
            if s in ("running", "done", "failed"):
                break
            time.sleep(1)

        # ── Wait for extension to finish (up to 12 min) ───────────────────────
        _grok_log("ext", "Waiting for extension to complete video generation...")
        deadline = time.time() + 12 * 60
        while time.time() < deadline:
            with state_lock:
                s = state["ext_status"]
                msg = state["ext_message"]
                done = state["done"]
            _grok_log("ext-poll", f"status={s} msg={msg}")
            if done:
                _grok_log("ext", "Extension reported done")
                break
            if s == "failed":
                _grok_log("ext", f"Extension reported failure: {msg}")
                return 'failed'
            time.sleep(5)
        else:
            _grok_log("ext", "Timed out waiting for extension")
            return 'timeout'

        # ── Find the downloaded MP4 ───────────────────────────────────────────
        # Give Chrome a moment to register the download after the extension signals done
        time.sleep(5)

        downloads_dir = os.path.expanduser("~/Downloads")
        with state_lock:
            captured_url = state.get("video_url", "")
        _grok_log("ext", f"Scanning {downloads_dir} for new MP4...")
        _grok_log("ext", f"Captured video URL: {captured_url or '(none — download button was used)'}")

        # Snapshot before — anything newer than session start is a candidate
        start_ts = time.time() - 12 * 60  # generous — anything in last 12 min
        found_mp4 = None
        deadline2 = time.time() + GROK_FILE_WAIT
        tick = 0
        while time.time() < deadline2:
            all_files = os.listdir(downloads_dir)
            mp4s = [
                f for f in all_files
                if f.lower().endswith(".mp4")
                and os.path.getmtime(os.path.join(downloads_dir, f)) >= start_ts
                and os.path.getsize(os.path.join(downloads_dir, f)) > 1024  # > 1 KB
            ]
            crdownloads = [f for f in all_files if f.endswith(".crdownload")]
            if mp4s and not crdownloads:
                found_mp4 = max(mp4s, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
                sz = os.path.getsize(os.path.join(downloads_dir, found_mp4))
                _grok_log("ext-dl", f"MP4 found: {found_mp4} ({sz:,} bytes)")
                break
            if mp4s and crdownloads:
                _grok_log("ext-dl", f"MP4 present but crdownload still active: {crdownloads} — waiting...")
            elif crdownloads:
                _grok_log("ext-dl", f"crdownload active (download in progress): {crdownloads}")
            elif tick % 5 == 0:
                _grok_log("ext-dl", f"{tick*3}s: no MP4 yet. Files in Downloads: {[f for f in all_files if not f.startswith('.')][:8]}")
            time.sleep(3)
            tick += 1

        if not found_mp4:
            _grok_log("ext", f"ERROR: No MP4 found in {downloads_dir} after {GROK_FILE_WAIT}s")
            _grok_log("ext", f"  Captured URL was: {captured_url or '(none)'}")
            _grok_log("ext", f"  Files in Downloads: {[f for f in os.listdir(downloads_dir) if not f.startswith('.')][:15]}")
            return 'failed'

        src = os.path.join(downloads_dir, found_mp4)
        os.makedirs(os.path.dirname(video_output_path), exist_ok=True)
        shutil.move(src, video_output_path)
        _grok_log("ext", f"Moved {found_mp4} → {video_output_path}")
        return 'success'

    except Exception as e:
        _grok_log("ext", f"Unexpected exception: {e}")
        _grok_log("ext", traceback.format_exc())
        return 'failed'
    finally:
        server.shutdown()
        subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
        time.sleep(2)
        _grok_log("ext", "Chrome closed. ════ generate_grok_video_via_extension END ════")
        if _grok_log_fh:
            _grok_log_fh.close()
            _grok_log_fh = None
            print(f"  [GROK] Log saved → {os.path.abspath(GROK_LOG_PATH)}")


def _run_server(server: "HTTPServer", state: dict, lock: "threading.Lock"):
    """Run HTTP server until state['done'] or state['ext_status'] == 'failed'."""
    import threading
    while True:
        with lock:
            done = state["done"]
            failed = state["ext_status"] == "failed"
        if done or failed:
            break
        server.handle_request()


def generate_grok_video(image_path: str, video_prompt: str, video_output_path: str) -> str:
    """Full Grok video generation flow for one image.
    Chrome is ALWAYS killed in finally regardless of outcome.
    Returns 'success', 'failed', or 'timeout'.
    """
    import traceback
    import datetime

    global _grok_log_fh
    log_path = os.path.abspath(GROK_LOG_PATH)
    _grok_log_fh = open(log_path, "w", encoding="utf-8")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _grok_write(f"\n{'═'*60}")
    _grok_write(f"  GROK SESSION START  {ts}")
    _grok_write(f"  Log file: {log_path}")
    _grok_write(f"{'═'*60}")

    _grok_log("generate", "════ generate_grok_video START ════")
    _grok_log("generate", f"  image      : {image_path}")
    _grok_log("generate", f"  video out  : {video_output_path}")
    _grok_log("generate", f"  prompt     : {video_prompt[:120] if video_prompt else '(none)'}...")
    try:
        _grok_log("generate", "Step 1/2 — setup_grok_chrome")
        if not setup_grok_chrome():
            _grok_log("generate", "FAILED at setup_grok_chrome")
            return 'failed'

        _grok_log("generate", "Step 3/4 — navigate_to_image_to_video")
        if not grok_navigate_to_image_to_video():
            _grok_log("generate", "FAILED at navigate_to_image_to_video")
            return 'failed'

        _grok_log("generate", "Step 5/6 — select_quality_and_duration")
        grok_select_quality_and_duration()

        _grok_log("generate", "Step 7 — type_prompt (before image upload, matches Grok UI order)")
        if not grok_type_prompt(video_prompt):
            _grok_log("generate", "FAILED at type_prompt")
            return 'failed'

        _grok_log("generate", "Step 8-11 — upload_image")
        if not grok_upload_image(image_path):
            _grok_log("generate", "FAILED at upload_image")
            return 'failed'

        _grok_log("generate", "Step 12 — submit_form")
        if not grok_submit_form(video_prompt):
            _grok_log("generate", "FAILED at submit_form (image or prompt missing)")
            return 'failed'

        _grok_log("generate", "Step 12 — wait_for_grok_video_and_download")
        result = wait_for_grok_video_and_download(video_output_path)
        _grok_log("generate", f"Result: {result}")
        return result
    except Exception as e:
        _grok_log("generate", f"Unexpected exception: {e}")
        _grok_log("generate", traceback.format_exc())
        return 'failed'
    finally:
        subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
        time.sleep(2)
        _grok_log("generate", "Chrome closed. ════ generate_grok_video END ════")
        if _grok_log_fh:
            _grok_log_fh.close()
            _grok_log_fh = None
            print(f"  [GROK] Log saved → {os.path.abspath(GROK_LOG_PATH)}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGINT, _handle_sigint)

    parser = argparse.ArgumentParser(
        description="Generate scene images (Gemini) and Grok videos per chapter prompt."
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
    total = len(prompts)

    # ── Build work list with current state ───────────────────────────────────
    all_work = []
    for idx, (num, label, prompt) in enumerate(prompts):
        out_path   = get_output_path(folder, meta_stem, num, label)
        video_path = get_video_output_path(out_path)
        vp         = video_prompts[idx]
        img_done   = os.path.exists(out_path)
        # Thumbnail images never get a Grok video — skip video generation entirely
        vid_done   = True if label.lower() == "thumbnail" else (os.path.exists(video_path) if vp else True)
        all_work.append({
            'num': num, 'label': label, 'prompt': prompt,
            'out_path': out_path, 'video_path': video_path, 'vp': vp,
            'img_done': img_done, 'vid_done': vid_done,
        })

    # ── Status report ─────────────────────────────────────────────────────────
    print(f"\nTotal prompts: {total}")
    print(f"{'─' * 62}")
    print(f"  {'#':>3}  {'Label':12}  {'Image':6}  {'Video':6}  Action")
    print(f"{'─' * 62}")
    for w in all_work:
        img_s  = "DONE " if w['img_done'] else "miss "
        vid_s  = "DONE " if w['vid_done'] else ("miss " if w['vp'] else "n/a  ")
        if w['img_done'] and w['vid_done']:
            action = "skip"
        elif w['img_done'] and not w['vid_done']:
            action = "video only"
        else:
            # image missing — video may or may not exist (edge case: image manually deleted)
            action = "image + video" if (w['vp'] and not w['vid_done']) else "image only"
        print(f"  {w['num']:>3}  {w['label']:12}  {img_s}  {vid_s}  {action}")
    print(f"{'─' * 62}")

    needs_any_work = any(not w['img_done'] or not w['vid_done'] for w in all_work)

    if not needs_any_work:
        print("\nAll images and videos already exist. Nothing to do.")
        sys.exit(0)

    # ── Nested helper: generate one image (up to 3 attempts) ─────────────────
    def attempt_image(num, _label, prompt, out_path, pass_label):
        """Try up to 3 times to generate one image. Returns True on success."""
        for attempt in range(1, 4):
            if attempt == 2:
                print(f"\n  [HEAVY LOAD] Attempt 1 failed — switching to Pro model (attempt 2/3)...")
            elif attempt == 3:
                print(f"\n  [HEAVY LOAD] Attempt 2 failed — waiting {HEAVY_LOAD_FINAL_WAIT}s before final attempt (3/3)...")
                cooldown_wait(HEAVY_LOAD_FINAL_WAIT, label="Final attempt")

            if not navigate_to_fresh_chat():
                print(f"  [WARN] Could not load Gemini for image {num}")
                return False

            if attempt == 2:
                switch_to_pro_model()

            if not open_temp_chat():
                print(f"  [ABORT] Could not enter Temporary chat mode for image {num}.")
                return False

            if not click_tools_and_create_image():
                print(f"  [WARN] Could not switch to image mode for image {num}")
                return False

            if not paste_and_submit(prompt):
                print(f"  [WARN] Could not submit prompt for image {num}")
                return False

            result = wait_and_download(out_path)

            if result == "success":
                return True

            if result == "heavy_load":
                if attempt == 3:
                    print(f"\n  [FATAL] All 3 attempts hit heavy load for image {num}.")
                    print(f"  Gemini is severely overloaded. Exiting to avoid further waste.")
                    sys.exit(1)
            else:
                print(f"  [WARN] Image {num} failed ({pass_label}) — will retry after remaining images.")
                return False

        return False

    # ── Run one image+video item (used by both first pass and retry passes) ───
    def process_item(w, pass_label, images_generated_ref, gemini_open_ref):
        """
        Generate image if needed, then video if image is/was present.
        images_generated_ref: [int] mutable count for cooldown tracking.
        gemini_open_ref: [bool] mutable flag tracking Gemini Chrome state.
        Returns (image_ok, video_ok).
        """
        img_ok = w['img_done']
        vid_ok = w['vid_done']

        # ── Image generation ──────────────────────────────────────────────────
        if not w['img_done']:
            # Open Gemini Chrome if not already open
            if not gemini_open_ref[0]:
                if not setup_chrome():
                    print("  Error: Could not open Gemini Chrome. Aborting.")
                    sys.exit(1)
                gemini_open_ref[0] = True

            if images_generated_ref[0] > 0:
                cooldown_wait(INTER_IMAGE_COOLDOWN, label="Next image")

            ok = attempt_image(w['num'], w['label'], w['prompt'], w['out_path'], pass_label)
            images_generated_ref[0] += 1

            if not ok:
                print(f"  [SKIP VIDEO] Image {w['num']} failed — not attempting video.")
                return False, False

            w['img_done'] = True
            img_ok = True

        # ── Video generation (only if image exists) ───────────────────────────
        if not w['vid_done'] and w['vp']:
            print(f"\n  Generating Grok video for image {w['num']}...")
            # generate_grok_video kills whatever Chrome is open (Gemini or none)
            gemini_open_ref[0] = False
            result = generate_grok_video_via_extension(w['out_path'], w['vp'], w['video_path'])
            if result == 'success':
                w['vid_done'] = True
                vid_ok = True
                print(f"  [OK] Video {w['num']} done.")
            else:
                print(f"\n  [FATAL] Video {w['num']} {result}.")
                print(f"  Hard rule: video failure stops the pipeline. Fix the issue and re-run.")
                sys.exit(1)
        elif w['vid_done']:
            vid_ok = True

        return img_ok, vid_ok

    # ── State tracking ────────────────────────────────────────────────────────
    gemini_open    = [False]  # mutable ref so process_item can update it
    images_gen     = [0]      # mutable count for cooldown tracking
    success_count  = 0
    video_success  = 0
    retry_queue    = []

    # ── First pass ────────────────────────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print(f"FIRST PASS — {total} prompts")
    print(f"{'═' * 55}")

    for i, w in enumerate(all_work):
        if w['img_done'] and w['vid_done']:
            print(f"\n  [SKIP] {w['num']} — {w['label']} — both done.")
            continue

        print(f"\n{'─' * 55}")
        print(f"[{i + 1}/{total}] Image {w['num']} — {w['label']}")
        print(f"  Image : {os.path.basename(w['out_path'])}")
        if w['vp']:
            print(f"  Video : {os.path.basename(w['video_path'])}")

        img_ok, vid_ok = process_item(w, "pass 1", images_gen, gemini_open)

        if img_ok:
            success_count += 1
        if vid_ok and w['vp']:
            video_success += 1

        if not img_ok:
            retry_queue.append(w)

    # ── Extended retry passes (images only; video attempted after each success) ─
    EXTENDED_WAIT = 5 * 60

    remaining = retry_queue[:]
    for ext_pass in range(1, 4):
        if not remaining:
            break
        print(f"\n{'═' * 55}")
        if ext_pass == 1:
            print(f"RETRY PASS {ext_pass}/3 — {len(remaining)} image(s) failed. Retrying now...")
        else:
            print(f"RETRY PASS {ext_pass}/3 — {len(remaining)} image(s) still missing.")
            print(f"  Waiting {EXTENDED_WAIT // 60} minutes before retry pass {ext_pass}...")
            cooldown_wait(EXTENDED_WAIT, label=f"Extended retry pass {ext_pass}")
        print(f"{'═' * 55}")

        still_failing = []
        for i, w in enumerate(remaining):
            print(f"\n{'─' * 55}")
            print(f"[RETRY {ext_pass}.{i + 1}/{len(remaining)}] Image {w['num']} — {w['label']}")

            img_ok, vid_ok = process_item(w, f"retry pass {ext_pass}", images_gen, gemini_open)

            if img_ok:
                success_count += 1
            if vid_ok and w['vp']:
                video_success += 1
            if not img_ok:
                still_failing.append(w)

        remaining = still_failing

    # ── Final report ──────────────────────────────────────────────────────────
    still_failed_imgs   = [w['num'] for w in remaining]
    still_failed_videos = [w['num'] for w in all_work if w['vp'] and not w['vid_done']]

    print(f"\n{'═' * 55}")
    print("Generation complete.")
    print(f"  Total prompts    : {total}")
    print(f"  Images succeeded : {success_count}")
    print(f"  Videos succeeded : {video_success}")

    if still_failed_imgs:
        print(f"  Images failed    : {len(still_failed_imgs)} — {still_failed_imgs}")
        print(f"  All 3 retry passes exhausted. Re-run to try again.")
        sys.exit(2)

    if still_failed_videos:
        print(f"  Videos failed    : {len(still_failed_videos)} — {still_failed_videos}")
        print(f"  Re-run to retry failed videos (images are present).")
        # Exit 0 — images are complete; video failures are non-blocking for master_script

    print(f"{'═' * 55}")


if __name__ == "__main__":
    main()
