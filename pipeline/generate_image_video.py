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
from typing import Optional

# ── Gemini constants ──────────────────────────────────────────────────────────
CHROME_DATA_DIR       = "/Users/abubakarsiddique/Library/Application Support/Google/Chrome"
CHROME_PROFILE        = "Profile 9"    # 'gemini' profile
GEMINI_URL            = "https://gemini.google.com/app"
INTER_IMAGE_COOLDOWN  = 20             # seconds between images (rate-limit buffer)
HEAVY_LOAD_FINAL_WAIT = 300            # seconds before 3rd Gemini attempt

# ── Grok constants ────────────────────────────────────────────────────────────
GROK_CHROME_PROFILE = "Profile 10"    # 'grok' profile
GROK_URL            = "https://grok.com/"
GROK_VIDEO_WAIT     = 300             # max seconds to wait for video generation (5 min)
GROK_FILE_WAIT      = 60              # max seconds to wait for MP4 in Downloads


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
    # Step 3 — sidebar nav item (4th item in the nav list)
    r = run_js_in_chrome("""
(function() {
    var el = document.querySelector('div.pb-1 > div:nth-of-type(4) span');
    if (el) { el.click(); return 'clicked'; }
    return 'not found';
})()
""")
    if r != 'clicked':
        print(f"  [Grok] Step 3 (nav item) not found.")
        return False
    print("  [Grok] Nav item clicked.")
    time.sleep(2)

    # Step 4 — Image to Video button inside the form
    r = run_js_in_chrome("""
(function() {
    var el = document.querySelector('button.text-primary-foreground span');
    if (el) { el.click(); return 'clicked'; }
    // Fallback: second button in the form area
    var btns = Array.from(document.querySelectorAll('[data-testid="drop-ui"] button'));
    if (btns.length >= 2) { btns[1].click(); return 'clicked:fallback'; }
    return 'not found';
})()
""")
    if r == 'not found':
        print(f"  [Grok] Step 4 (Image to Video button) not found.")
        return False
    print(f"  [Grok] Image-to-Video mode selected ({r}).")
    time.sleep(2)
    return True


def grok_select_quality_and_duration() -> bool:
    """Steps 5-6: select 720p quality and 10s duration."""
    # Step 5 — 720p
    r = run_js_in_chrome("""
(function() {
    var el = document.querySelector('div.flex-wrap > div:nth-of-type(2) button.text-primary span');
    if (!el) {
        var spans = Array.from(document.querySelectorAll('span'));
        el = spans.find(function(s) { return s.textContent.trim() === '720p' && s.offsetParent !== null; });
    }
    if (el) { el.click(); return 'clicked'; }
    return 'not found';
})()
""")
    print(f"  [Grok] 720p: {r}")
    time.sleep(1)

    # Step 6 — 10s
    r = run_js_in_chrome("""
(function() {
    var el = document.querySelector('div:nth-of-type(3) button.text-primary span');
    if (!el) {
        var spans = Array.from(document.querySelectorAll('span'));
        el = spans.find(function(s) { return s.textContent.trim() === '10s' && s.offsetParent !== null; });
    }
    if (el) { el.click(); return 'clicked'; }
    return 'not found';
})()
""")
    print(f"  [Grok] 10s: {r}")
    time.sleep(1)
    return True


def grok_upload_image(image_path: str) -> bool:
    """Steps 7-10: focus text area → upload icon → span.hidden → macOS file chooser."""
    # Step 7 — focus text area
    run_js_in_chrome("""
(function() {
    var el = document.querySelector('[data-testid="drop-ui"] p');
    if (el) el.click();
})()
""")
    time.sleep(1)

    # Step 8 — click upload icon SVG
    run_js_in_chrome("""
(function() {
    var el = document.querySelector('form > div > div > div > div.relative path');
    if (el) { el.dispatchEvent(new MouseEvent('click', {bubbles: true})); return 'clicked'; }
    return 'not found';
})()
""")
    time.sleep(1)

    # Step 9 — click span.hidden to open file chooser
    run_js_in_chrome("""
(function() {
    var el = document.querySelector('span.hidden');
    if (el) { el.click(); return 'clicked'; }
    return 'not found';
})()
""")
    time.sleep(2)  # wait for macOS file chooser to appear

    # Step 10 — drive the macOS file chooser via AppleScript
    # Copy path to clipboard first (handles long paths / spaces reliably)
    try:
        subprocess.run(["pbcopy"], input=image_path.encode("utf-8"), check=True)
    except Exception as e:
        print(f"  [Grok] Could not copy path to clipboard: {e}")
        return False

    run_osascript("""
tell application "System Events"
    delay 1.5
    keystroke "g" using {command down, shift down}
    delay 1.0
    keystroke "v" using {command down}
    delay 0.5
    key code 36
    delay 1.0
    key code 36
end tell
""")
    time.sleep(3)  # wait for file input change event + UI preview

    # Confirm upload by polling for image preview in UI
    for _ in range(10):
        result = run_js_in_chrome("""
(function() {
    var imgs = document.querySelectorAll('[data-testid="drop-ui"] img');
    return imgs.length > 0 ? 'uploaded' : 'waiting';
})()
""")
        if result == 'uploaded':
            print("  [Grok] Image upload confirmed.")
            return True
        time.sleep(1)

    print("  [Grok] Upload preview not detected — proceeding anyway.")
    return True  # proceed; file input change may have fired silently


def grok_enter_prompt_and_submit(video_prompt: str) -> bool:
    """Paste video prompt into text area and click submit (step 11)."""
    if video_prompt:
        try:
            subprocess.run(["pbcopy"], input=video_prompt.encode("utf-8"), check=True)
        except Exception as e:
            print(f"  [Grok] Could not copy prompt: {e}")

        # Focus text area
        run_js_in_chrome("""
(function() {
    var el = document.querySelector('[data-testid="drop-ui"] p');
    if (el) el.click();
})()
""")
        time.sleep(0.5)
        run_osascript('tell application "Google Chrome" to activate')
        time.sleep(0.3)
        run_osascript('tell application "System Events" to keystroke "v" using command down')
        time.sleep(1)
        print(f"  [Grok] Video prompt pasted ({len(video_prompt)} chars).")
    else:
        print("  [Grok] No video prompt — submitting image only.")

    # Step 11 — click submit
    r = run_js_in_chrome("""
(function() {
    var el = document.querySelector('div.query-bar > div.absolute svg');
    if (el) { el.dispatchEvent(new MouseEvent('click', {bubbles: true})); return 'clicked'; }
    // Fallback: button with aria-label containing submit/send
    var btns = Array.from(document.querySelectorAll('button,[role=button]'));
    var btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || '').toLowerCase();
        return (lbl.indexOf('submit') !== -1 || lbl.indexOf('send') !== -1) && b.offsetParent !== null;
    });
    if (btn) { btn.click(); return 'clicked:fallback'; }
    return 'not found';
})()
""")
    print(f"  [Grok] Submit: {r}")
    time.sleep(2)
    return True


def wait_for_grok_video_and_download(video_output_path: str) -> str:
    """Poll until Grok video is ready, click download (step 12), move MP4.
    Returns 'success', 'timeout', or 'failed'.
    """
    check_js = """
(function() {
    // Aria-label download button (most robust)
    var btns = Array.from(document.querySelectorAll('button'));
    var dl = btns.find(function(b) {
        return (b.getAttribute('aria-label') || '').toLowerCase().indexOf('download') !== -1
               && b.offsetParent !== null;
    });
    if (dl) return 'ready';
    // Recording selector: button:nth-of-type(5) > svg
    var el = document.querySelector('button:nth-of-type(5) > svg');
    if (el && el.offsetParent !== null) return 'ready';
    // Video element present
    if (document.querySelector('video')) return 'ready';
    // Error states
    var body = (document.body.innerText || '').toLowerCase().slice(-2000);
    if (body.indexOf('failed') !== -1 || body.indexOf('could not generate') !== -1
        || body.indexOf('try again') !== -1) return 'error';
    return 'generating';
})()
"""
    elapsed = 0
    while elapsed < GROK_VIDEO_WAIT:
        time.sleep(5)
        elapsed += 5
        print(f"  [Grok] Generating video... ({elapsed}s / {GROK_VIDEO_WAIT}s)", end="\r", flush=True)
        state = run_js_in_chrome(check_js)
        if state == 'ready':
            break
        if state == 'error':
            print(f"\n  [Grok] Generation error detected at {elapsed}s.")
            return 'failed'
    else:
        print(f"\n  [Grok] Timeout — video not ready after {GROK_VIDEO_WAIT}s.")
        return 'timeout'

    print(f"\n  [Grok] Video ready ({elapsed}s). Clicking download...")

    downloads_dir = os.path.expanduser("~/Downloads")
    before = set(os.listdir(downloads_dir))

    # Step 12 — click download button
    run_js_in_chrome("""
(function() {
    // Prefer aria-label
    var btns = Array.from(document.querySelectorAll('button'));
    var dl = btns.find(function(b) {
        return (b.getAttribute('aria-label') || '').toLowerCase().indexOf('download') !== -1
               && b.offsetParent !== null;
    });
    if (dl) { dl.click(); return 'clicked:aria'; }
    // Recording selector
    var el = document.querySelector('button:nth-of-type(5) > svg');
    if (el) { el.dispatchEvent(new MouseEvent('click', {bubbles: true})); return 'clicked:nth'; }
    return 'not found';
})()
""")
    time.sleep(2)

    # Wait for MP4 in Downloads
    matched = None
    for _ in range(GROK_FILE_WAIT):
        time.sleep(1)
        after = set(os.listdir(downloads_dir))
        new_files = after - before
        mp4s = [f for f in new_files if f.lower().endswith(".mp4")]
        if mp4s:
            matched = max(mp4s, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
            break

    if not matched:
        print(f"  [Grok] No MP4 appeared in ~/Downloads after {GROK_FILE_WAIT}s.")
        return 'failed'

    src = os.path.join(downloads_dir, matched)
    shutil.move(src, video_output_path)
    print(f"  [Grok] Video saved: {os.path.basename(video_output_path)}  ({os.path.getsize(video_output_path):,} bytes)")
    return 'success'


def generate_grok_video(image_path: str, video_prompt: str, video_output_path: str) -> str:
    """Full Grok video generation flow for one image.
    Chrome is ALWAYS killed in finally regardless of outcome.
    Returns 'success', 'failed', or 'timeout'.
    """
    try:
        if not setup_grok_chrome():
            return 'failed'
        if not grok_navigate_to_image_to_video():
            return 'failed'
        grok_select_quality_and_duration()
        if not grok_upload_image(image_path):
            return 'failed'
        grok_enter_prompt_and_submit(video_prompt)
        return wait_for_grok_video_and_download(video_output_path)
    except Exception as e:
        print(f"  [Grok] Unexpected exception: {e}")
        return 'failed'
    finally:
        subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
        time.sleep(2)
        print("  [Grok] Chrome closed.")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
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
        vid_done   = os.path.exists(video_path) if vp else True
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
            result = generate_grok_video(w['out_path'], w['vp'], w['video_path'])
            if result == 'success':
                w['vid_done'] = True
                vid_ok = True
                print(f"  [OK] Video {w['num']} done.")
            else:
                print(f"  [WARN] Video {w['num']} {result} — continuing.")
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
