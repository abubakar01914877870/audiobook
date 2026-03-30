#!/usr/bin/env python3
"""
generate_image.py — Generate all scene images for a chapter from *_meta.md prompts.

Reads all '### Image Prompt NN — Label' sections, generates each image via Gemini
web UI, downloads and names them serially. Continues on individual failures.

Behaviour:
  - Generates ALL prompts found in meta file — no bulk-skip threshold.
  - Per-image skip: if the output file already exists, that image is skipped.
  - Inter-image cooldown: fixed pause between every image to stay under rate limits.
  - Heavy-load escalation (3 attempts per image, then EXIT):
      Attempt 1 — normal generation
      Attempt 2 — switch to Pro model in Gemini dropdown, retry immediately
      Attempt 3 — wait 5 min, retry
      Still failing — exit the entire process

Usage:
    python generate_image.py ./clown_vol_1/output/ch_11
"""

import os
import sys
import re
import time
import argparse
import subprocess
import shutil
from typing import Optional

CHROME_DATA_DIR = "/Users/abubakarsiddique/Library/Application Support/Google/Chrome"
CHROME_PROFILE  = "Profile 9"   # 'gemini' profile
GEMINI_URL      = "https://gemini.google.com/app"

INTER_IMAGE_COOLDOWN    = 20    # seconds to pause between every image (proactive rate-limit buffer)
HEAVY_LOAD_FINAL_WAIT   = 300   # seconds to wait before the 3rd (final) attempt after heavy-load



# ── file helpers ──────────────────────────────────────────────────────────────

def find_meta_file(folder: str) -> Optional[str]:
    for fname in sorted(os.listdir(folder)):
        if fname.endswith("_meta.md"):
            return os.path.join(folder, fname)
    return None


def extract_all_image_prompts(meta_path: str) -> list:
    """Parse all '### Image Prompt NN — Label' sections.
    Returns list of (num_str, label, prompt_text) tuples ordered by number.
    Falls back to old single-prompt format if new format not found.
    """
    with open(meta_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(
        r'###\s*Image Prompt\s+(\d+)\s*[—\-]+\s*(\w+)\s*\n(.*?)(?=\n###\s*Image Prompt|\n###\s*YouTube|\Z)',
        re.DOTALL
    )
    results = []
    for m in pattern.finditer(content):
        num    = m.group(1).zfill(2)
        label  = m.group(2).strip()
        prompt_block = m.group(3).strip()
        if not prompt_block:
            continue
            
        # Extract just the '**Prompt:** ...' part if the new 3-field structure exists
        prompt_match = re.search(r'\*\*Prompt:\*\*(.*?)(?=\n\*\*|\Z)', prompt_block, re.DOTALL | re.IGNORECASE)
        if prompt_match:
            actual_prompt = prompt_match.group(1).strip()
            if actual_prompt:
                results.append((num, label, actual_prompt))
        else:
            # Fallback for old formatting that doesn't use the explicit fields
            results.append((num, label, prompt_block))

    if results:
        return results

    # Fallback: old format with single "### Image Generation Prompt"
    old_match = re.search(
        r'###\s*Image Generation Prompt\s*\n(.*?)(?=\n###|\Z)',
        content, re.DOTALL
    )
    if old_match:
        return [("01", "Thumbnail", old_match.group(1).strip())]

    return []


def get_output_path(folder: str, stem: str, num: str, label: str) -> str:
    """Build output filepath: stem_NN_thumb.png or stem_NN_scene.png."""
    suffix = "thumb" if label.lower() == "thumbnail" else "scene"
    return os.path.join(folder, f"{stem}_{num}_{suffix}.png")


# ── Chrome / AppleScript helpers ──────────────────────────────────────────────

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


# ── Gemini UI automation ──────────────────────────────────────────────────────

def setup_chrome() -> bool:
    """Kill Chrome, reopen with Gemini profile, enable JS. Returns True on success."""
    print("Closing any existing Chrome instances...")
    subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
    time.sleep(3)

    print("Opening Chrome with 'gemini' profile...")
    subprocess.Popen([
        "open", "-a", "Google Chrome", "--args",
        f"--profile-directory={CHROME_PROFILE}",
        f"--user-data-dir={CHROME_DATA_DIR}",
        GEMINI_URL,
    ])
    time.sleep(6)

    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(1)

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


def wait_for_gemini_ui(max_wait: int = 20) -> bool:
    """Wait until the Gemini input area is present — confirms the UI is fully rendered."""
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
    """Navigate to the Gemini base URL to get a clean new-chat page.

    Always uses set URL (not reload) so we never land back in an existing
    conversation — that would leave the page in a state where the Temporary
    chat button behaves differently.
    """
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

    # Ensure Chrome is frontmost before sending keystrokes via System Events
    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(0.3)
    run_osascript('tell application "System Events" to keystroke "v" using command down')
    time.sleep(1.5)
    print("  Submitting...")
    # Try clicking the Send button directly first (more reliable than keystroke)
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


# ── Error detection ───────────────────────────────────────────────────────────

def detect_gemini_error() -> Optional[str]:
    """Check the Gemini UI for known error messages in the latest response.

    Returns:
        "heavy_load"   — transient overload: cooldown + retry
        "policy_block" — content policy block: skip, no retry
        None           — no error detected (still generating or success)
    """
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

    // Scan the last 3000 chars of visible page text (focuses on recent response)
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
        phrase = result.split(':', 1)[1]
        print(f"  [ERROR DETECTED] Heavy load — matched: '{phrase}'")
        return "heavy_load"
    if result.startswith('policy_block:'):
        phrase = result.split(':', 1)[1]
        print(f"  [ERROR DETECTED] Policy block — matched: '{phrase}'")
        return "policy_block"
    return None


def wait_and_download(output_path: str, max_wait: int = 300) -> str:
    """Poll until generated image appears, download it, move to output_path.

    Returns:
        "success"    — image downloaded successfully
        "heavy_load" — Gemini overload error detected (caller should cooldown + retry)
        "failed"     — other failure (download error, policy block, timeout)
    """
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

        # Check for error messages every 10 seconds (avoid hammering JS)
        if elapsed % 10 == 0:
            error = detect_gemini_error()
            if error == "heavy_load":
                print(f"  Aborting wait — heavy load detected at {elapsed}s.")
                return "heavy_load"
            if error == "policy_block":
                print(f"  Aborting wait — policy block detected at {elapsed}s.")
                return "failed"

    if not img_src:
        # Final error check before declaring timeout
        error = detect_gemini_error()
        if error == "heavy_load":
            return "heavy_load"
        print("\n  Image not found within timeout.")
        return "failed"

    # Brief pause — let the image fully render before interacting
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
        # Primary: use data-test-id from Chrome recording (most reliable)
        result = run_js_in_chrome("""
(function() {
    var btn = document.querySelector('[data-test-id="download-generated-image-button"]');
    if (btn && btn.offsetParent !== null) { btn.click(); return 'clicked:test-id'; }
    // Fallback: search by aria-label / text
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

    # Retry loop: up to 3 hover+click attempts before giving up on download
    clicked = "not found"
    for dl_attempt in range(1, 4):
        hover_depth = 6 + (dl_attempt - 1) * 2  # 6, 8, 10
        print(f"  Hovering image to reveal download button (attempt {dl_attempt}/3, depth={hover_depth})...")
        _hover_image(hover_depth)
        time.sleep(1.5)

        print(f"  Clicking 'Download full size image' (attempt {dl_attempt}/3)...")
        clicked = _click_download()
        if clicked.startswith("clicked"):
            print(f"  {clicked}")
            break

        # Check if a file already landed (silent download from a previous hover click)
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
    # Retry file-wait up to 3 times (handles slow download starts)
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
    """Open the Gemini model dropdown and select the Pro model.

    Returns True if the Pro option was clicked, False if not found.
    Must be called after navigate_to_fresh_chat() and before open_temp_chat().
    """
    print("  Switching to Pro model...", end="", flush=True)

    # Step 1: click the model selector button (shows current model name)
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

    # Step 2: click the Pro option in the opened dropdown
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
    # Close the dropdown if it's still open
    run_osascript('tell application "System Events" to key code 53')  # Escape
    time.sleep(0.5)
    return False


def cooldown_wait(seconds: int, label: str = "Retrying"):
    """Print a live countdown while waiting."""
    print(f"  Waiting {seconds}s — {label}...", flush=True)
    for remaining in range(seconds, 0, -1):
        print(f"  {label} in {remaining}s...  ", end="\r", flush=True)
        time.sleep(1)
    print(f"  {label}...                        ")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate all scene images for a chapter from *_meta.md prompts."
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

    total = len(prompts)
    print(f"\nImage prompts found : {total}")

    # Determine which images still need to be generated (per-image file check only)
    meta_stem = os.path.basename(meta_path).replace("_meta.md", "")
    pending = []
    for num, label, prompt in prompts:
        out_path = get_output_path(folder, meta_stem, num, label)
        if os.path.exists(out_path):
            print(f"  [SKIP] {os.path.basename(out_path)} already exists.")
        else:
            pending.append((num, label, prompt, out_path))

    if not pending:
        print("\nAll images already exist. Nothing to do.")
        sys.exit(0)

    print(f"\n{len(pending)} image(s) to generate (of {total} total).")
    print()

    if not setup_chrome():
        print("Error: Could not set up Chrome/Gemini. Aborting.")
        sys.exit(1)

    success_count = 0
    retry_queue   = []  # images that failed first pass — retried after all others

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
                # else: loop continues to attempt 2 or 3
            else:
                # Non-retriable failure on this attempt — queue for retry pass
                print(f"  [WARN] Image {num} failed ({pass_label}) — will retry after remaining images.")
                return False

        return False

    # ── First pass: all pending images ───────────────────────────────────────
    for i, (num, label, prompt, out_path) in enumerate(pending):
        print(f"\n{'─' * 55}")
        print(f"[{i + 1}/{len(pending)}] Image {num} — {label}")
        print(f"  Output  : {os.path.basename(out_path)}")
        print(f"  Preview : {prompt[:120]}...")

        if i > 0:
            cooldown_wait(INTER_IMAGE_COOLDOWN, label="Next image")

        if attempt_image(num, label, prompt, out_path, "pass 1"):
            success_count += 1
        else:
            retry_queue.append((num, label, prompt, out_path))

    # ── Extended retry passes: up to 3 rounds with 5-min waits between each ──
    # Hard rule: every prompt must have an image. Keep retrying until all succeed
    # or all 3 extended passes are exhausted.
    EXTENDED_WAIT = 5 * 60  # 5 minutes between extended retry passes

    remaining = retry_queue[:]
    for ext_pass in range(1, 4):  # passes 1, 2, 3
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
        for i, (num, label, prompt, out_path) in enumerate(remaining):
            print(f"\n{'─' * 55}")
            print(f"[RETRY {ext_pass}.{i + 1}/{len(remaining)}] Image {num} — {label}")
            print(f"  Output  : {os.path.basename(out_path)}")

            if i > 0:
                cooldown_wait(INTER_IMAGE_COOLDOWN, label="Next retry")

            if attempt_image(num, label, prompt, out_path, f"retry pass {ext_pass}"):
                success_count += 1
            else:
                still_failing.append((num, label, prompt, out_path))

        remaining = still_failing

    still_failed = [num for num, _, _, _ in remaining]

    already_done = total - len(pending)
    print(f"\n{'═' * 55}")
    print("Image generation complete.")
    print(f"  Total prompts : {total}")
    print(f"  Success       : {success_count}")
    if still_failed:
        print(f"  Still failed  : {len(still_failed)}  — images {still_failed}")
        print(f"  All 3 retry passes exhausted. Re-run this script to try again.")
        sys.exit(2)  # exit code 2 = incomplete — master_script can block video generation
    if already_done:
        print(f"  Already done  : {already_done}  (skipped — files existed)")
    print(f"{'═' * 55}")


if __name__ == "__main__":
    main()
