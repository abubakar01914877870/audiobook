#!/usr/bin/env python3
"""
Open Gemini web with the 'gemini' Chrome profile, select 'Create image',
paste the image prompt from *_meta.md, and submit.
Uses subprocess + osascript (macOS) — no Playwright, no profile issues.

Usage:
    python generate_image.py ./clown_vol_1/output/ch_11
"""

import os
import sys
import re
import time
import argparse
import subprocess
from typing import Optional

CHROME_DATA_DIR = "/Users/abubakarsiddique/Library/Application Support/Google/Chrome"
CHROME_PROFILE  = "Profile 9"   # 'gemini' profile
GEMINI_URL      = "https://gemini.google.com/app"


# ── helpers ──────────────────────────────────────────────────────────────────

def find_meta_file(folder: str) -> Optional[str]:
    for fname in sorted(os.listdir(folder)):
        if fname.endswith("_meta.md"):
            return os.path.join(folder, fname)
    return None



def extract_image_prompt(meta_path: str) -> Optional[str]:
    with open(meta_path, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(
        r"###\s*Image Generation Prompt\s*\n(.*?)(?=\n###|\Z)",
        content,
        re.DOTALL,
    )
    return match.group(1).strip() if match else None


def run_osascript(script: str) -> str:
    """Run an AppleScript snippet and return stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def run_js_in_chrome(js: str) -> str:
    """Execute JavaScript in Chrome's front tab via AppleScript."""
    # Escape backslashes and double-quotes for AppleScript string embedding
    js_escaped = js.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Google Chrome" to execute active tab of front window javascript "{js_escaped}"'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True
    )
    if result.returncode != 0 and result.stderr:
        print(f"  [JS error] {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def wait_for_gemini(max_wait: int = 30) -> bool:
    """Poll until Chrome's front tab URL contains gemini.google.com."""
    print("Waiting for Gemini to load...", end="", flush=True)
    for _ in range(max_wait):
        url = run_osascript('tell application "Google Chrome" to return URL of active tab of front window')
        if "gemini.google.com" in url:
            print(" ready.")
            return True
        print(".", end="", flush=True)
        time.sleep(1)
    print(" timed out.")
    return False


def enable_js_from_apple_events():
    """Enable 'Allow JavaScript from Apple Events' in Chrome only if currently disabled."""
    print("Checking 'Allow JavaScript from Apple Events' state...")
    # Check the current checked state of the menu item
    check_script = """
tell application "System Events"
    tell process "Google Chrome"
        set mi to menu item "Allow JavaScript from Apple Events" of menu 1 of menu item "Developer" of menu "View" of menu bar 1
        return value of mi
    end tell
end tell
"""
    result = subprocess.run(["osascript", "-e", check_script], capture_output=True, text=True)
    # value is "1" when checked (enabled), "0" when unchecked
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
        print("  Enabled — reloading page to apply...")
        # Reload the tab so the setting takes effect
        run_osascript('tell application "Google Chrome" to reload active tab of front window')
        time.sleep(4)


def check_js_enabled() -> bool:
    """Return True if AppleScript JavaScript execution works in Chrome."""
    result = run_js_in_chrome("1+1")
    return result == "2"


# ── main automation ───────────────────────────────────────────────────────────

def automate_gemini(prompt: str):
    # Step 0 — kill any running Chrome, then open with the gemini profile
    print("Closing any existing Chrome instances...")
    subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
    time.sleep(3)   # wait for Chrome to fully quit

    print("Opening Chrome with 'gemini' profile...")
    subprocess.Popen([
        "open", "-a", "Google Chrome", "--args",
        f"--profile-directory={CHROME_PROFILE}",
        f"--user-data-dir={CHROME_DATA_DIR}",
        GEMINI_URL,
    ])
    time.sleep(6)   # let Chrome open and start loading

    # Bring Chrome to front
    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(1)

    if not wait_for_gemini(max_wait=30):
        print("Error: Gemini did not load in time.")
        sys.exit(1)

    time.sleep(3)   # let the Gemini UI fully render

    # Click "Temporary chat" so the session isn't saved to history
    print("Opening temporary chat...")
    run_js_in_chrome("""
var btn = document.querySelector('button[aria-label="Temporary chat"]');
if (btn) btn.click();
""")
    time.sleep(2)
    # Dismiss the intro dialog if it appears
    run_js_in_chrome("""
var dismiss = document.querySelector('button[aria-label="Dismiss"]');
if (dismiss) dismiss.click();
""")
    time.sleep(1)
    print("  done.")

    # Ensure JS from Apple Events is enabled (needed for automation)
    if not check_js_enabled():
        # Try auto-enable via menu click (needs Accessibility permission)
        enable_js_from_apple_events()
        time.sleep(1)
        if not check_js_enabled():
            print("\n" + "="*60)
            print("ONE-TIME SETUP REQUIRED")
            print("="*60)
            print("\n1. In Chrome: View menu > Developer > Allow JavaScript")
            print("   from Apple Events  (tick the checkbox)")
            print("\n2. Grant Accessibility to your Terminal:")
            print("   Apple menu > System Settings > Privacy & Security")
            print("   > Accessibility > add Terminal (or iTerm2)")
            print("\nAfter doing both, press Enter to continue.")
            print("="*60)
            input("\nPress Enter when ready: ")
            if not check_js_enabled():
                print("Still not working. Please enable 'Allow JavaScript from Apple Events' in Chrome and retry.")
                sys.exit(1)

    # Step 1 — click the tools/extras button (opens Create image, Search, etc.)
    print("Clicking the tools button...")
    add_clicked = run_js_in_chrome("""
(function() {
    var labels = [
        'select tools and upload',
        'Add extras menu',
        'input area menu',
        'Tools'
    ];
    var btns = Array.from(document.querySelectorAll('button,[role=button]'));
    for (var i = 0; i < labels.length; i++) {
        var btn = btns.find(function(b) {
            var lbl = (b.getAttribute('aria-label') || b.innerText || '').trim();
            return lbl.toLowerCase().indexOf(labels[i].toLowerCase()) !== -1;
        });
        if (btn) { btn.click(); return 'clicked:' + (btn.getAttribute('aria-label') || btn.innerText || '').trim().slice(0,40); }
    }
    return 'not found';
})()
""")

    if add_clicked.startswith("clicked"):
        print(f"  {add_clicked}")
        time.sleep(1.5)
    else:
        dump = run_js_in_chrome("""
Array.from(document.querySelectorAll('button,[role=button]'))
  .filter(function(b){ return b.offsetParent !== null; })
  .map(function(b){ return (b.getAttribute('aria-label') || b.innerText || '').trim().slice(0,50); })
  .filter(function(t){ return t; })
  .slice(0, 25)
  .join(' | ')
""")
        print(f"Could not find tools button. Visible buttons: {dump}")
        print("Please click the tools button and 'Create image' manually, then press Enter here.")
        input("Press Enter when 'Create image' mode is active...")

    # Step 2 — click "Create image"
    print("Clicking 'Create image'...")
    img_clicked = run_js_in_chrome("""
(function() {
    var all = Array.from(document.querySelectorAll(
        'li, button, mat-option, [role=menuitem], [role=option]'
    ));
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
    else:
        dump = run_js_in_chrome("""
Array.from(document.querySelectorAll('li,mat-option,[role=menuitem],[role=option]'))
  .filter(function(e){ return e.offsetParent !== null; })
  .map(function(e){ return (e.innerText || '').trim().slice(0, 50); })
  .filter(function(t){ return t; })
  .slice(0, 20)
  .join(' | ')
""")
        print(f"Could not find 'Create image'. Visible menu items: {dump}")
        print("Please click 'Create image' manually, then press Enter here.")
        input("Press Enter when ready...")

    time.sleep(1)

    # Step 3 — paste the prompt into the input
    print("Pasting prompt into Gemini input...")

    # Write prompt to a temp file, then load it in JS to avoid shell escaping issues
    tmp_file = "/tmp/gemini_prompt.txt"
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(prompt)

    # Use pbcopy to put it on clipboard, then Cmd+V
    subprocess.run(["pbcopy"], input=prompt.encode("utf-8"), check=True)
    time.sleep(0.5)

    # Click the input box
    run_js_in_chrome("""
var box = document.querySelector('rich-textarea div[contenteditable="true"]') ||
          document.querySelector('div[contenteditable="true"]');
if (box) box.focus();
""")
    time.sleep(0.5)

    # Cmd+V to paste
    run_osascript('tell application "System Events" to keystroke "v" using command down')
    time.sleep(1)
    print(f"  Prompt pasted ({len(prompt)} chars).")

    # Step 4 — submit
    print("Submitting (Enter)...")
    run_osascript('tell application "System Events" to key code 36')   # key code 36 = Return

    print("\nPrompt submitted. Waiting for image generation (up to 5 min)...")
    return True


def wait_and_download(output_path: str, max_wait: int = 300):
    """Poll until the generated image appears, hover it, click Download full size."""
    import shutil

    # Poll for the generated image — match any large image regardless of URL
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

    if not img_src:
        print("\nImage not found within timeout.")
        return False

    # Hover over the image to reveal action buttons, then click "Download full size image"
    print("Hovering image to reveal download button...")
    hovered = run_js_in_chrome("""
(function() {
    var skipPatterns = ['gstatic', 'google.com/images', 'accounts.google', 'favicon'];
    var imgs = Array.from(document.querySelectorAll('img'));
    var img = imgs.find(function(i) {
        var src = i.src || '';
        if (i.naturalWidth <= 200 || i.naturalHeight <= 200 || !src) return false;
        return !skipPatterns.some(function(p) { return src.indexOf(p) !== -1; });
    });
    if (!img) return 'no image';
    img.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
    img.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
    if (img.parentElement) img.parentElement.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
    return 'hovered';
})()
""")
    time.sleep(1)

    # Snapshot Downloads before clicking
    downloads_dir = os.path.expanduser("~/Downloads")
    before = set(os.listdir(downloads_dir))

    # Click the "Download full size image" button
    print("Clicking 'Download full size image'...")
    clicked = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button,[role=button],a'));
    var btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || b.innerText || '');
        return lbl.toLowerCase().indexOf('download full size') !== -1;
    });
    if (btn) { btn.click(); return 'clicked'; }
    return 'not found';
})()
""")

    if clicked != "clicked":
        print(f"  Download button not found ({clicked}). Retrying after longer hover...")
        # Try hovering more parent levels
        run_js_in_chrome("""
(function() {
    var skipPatterns = ['gstatic', 'google.com/images', 'accounts.google', 'favicon'];
    var imgs = Array.from(document.querySelectorAll('img'));
    var img = imgs.find(function(i) {
        var src = i.src || '';
        if (i.naturalWidth <= 200 || i.naturalHeight <= 200 || !src) return false;
        return !skipPatterns.some(function(p) { return src.indexOf(p) !== -1; });
    });
    if (!img) return;
    var el = img;
    for (var i = 0; i < 6; i++) {
        if (!el) break;
        el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
        el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
        el = el.parentElement;
    }
})()
""")
        time.sleep(1.5)
        clicked = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button,[role=button],a'));
    var btn = btns.find(function(b) {
        var lbl = (b.getAttribute('aria-label') || b.title || b.innerText || '');
        return lbl.toLowerCase().indexOf('download full size') !== -1;
    });
    if (btn) { btn.click(); return 'clicked'; }
    return 'not found';
})()
""")

    if clicked != "clicked":
        print("  Could not find 'Download full size image' button.")
        return False

    print("  Download triggered. Waiting for file in ~/Downloads...")
    matched = None
    for _ in range(30):
        time.sleep(1)
        after = set(os.listdir(downloads_dir))
        new_files = after - before
        imgs = [f for f in new_files if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
        if imgs:
            matched = max(imgs, key=lambda f: os.path.getmtime(os.path.join(downloads_dir, f)))
            break

    if not matched:
        print("  No image file appeared in ~/Downloads.")
        return False

    src = os.path.join(downloads_dir, matched)
    shutil.move(src, output_path)
    print(f"Saved: {output_path} ({os.path.getsize(output_path):,} bytes)")



# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Send image generation prompt from *_meta.md to Gemini web UI."
    )
    parser.add_argument(
        "folder",
        help="Chapter output folder (e.g. ./clown_vol_1/output/ch_11)",
    )
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

    prompt = extract_image_prompt(meta_path)
    if not prompt:
        print("Error: '### Image Generation Prompt' section not found in meta file.")
        sys.exit(1)

    print(f"Prompt    : {prompt[:120]}...")
    print()

    # Derive output image path: same folder, named after the chapter
    meta_stem = os.path.basename(meta_path).replace("_meta.md", "")
    output_path = os.path.join(folder, f"{meta_stem}_thumbnail.png")

    if os.path.exists(output_path):
        print(f"Image already exists: {output_path} — skipping image generation.")
        sys.exit(0)

    automate_gemini(prompt)
    wait_and_download(output_path)


if __name__ == "__main__":
    main()
