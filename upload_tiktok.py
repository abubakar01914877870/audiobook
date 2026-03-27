#!/usr/bin/env python3
"""
upload_tiktok.py — Upload a chapter video to TikTok via TikTok Studio in Chrome.

Usage:
    python upload_tiktok.py ./clown_vol_1/output/ch_16

Reads from the folder:
    *_tiktok.mp4    — video to upload
    *_meta.md       — title (used as caption)

Saves on success:
    *_tiktok_upload.json  — upload record (prevents re-upload on re-run)
"""

import os
import sys
import json
import re
import time
import argparse
import subprocess
import textwrap
from pathlib import Path
from typing import Optional

try:
    import pyautogui
    pyautogui.FAILSAFE = False   # don't abort when mouse hits corner
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False

CHROME_DATA_DIR  = "/Users/abubakarsiddique/Library/Application Support/Google/Chrome"
CHROME_PROFILE   = "Profile 4"   # 'tiktok' profile
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


# ── helpers ───────────────────────────────────────────────────────────────────

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


def find_file(folder: Path, suffix: str) -> Optional[Path]:
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.name.endswith(suffix):
            return f
    return None


# ── meta parsing ─────────────────────────────────────────────────────────────

TIKTOK_CAPTION_LIMIT = 2200

def parse_caption(meta_path: Path) -> str:
    """Extract title + description from meta file to use as TikTok caption."""
    content = meta_path.read_text(encoding="utf-8")
    title_match = re.search(r'\*\*Title:\*\*\s*(.+)', content)
    title = title_match.group(1).strip() if title_match else meta_path.stem.replace("_meta", "").replace("_", " ")

    desc_match = re.search(r'\*\*Description:\*\*\s*\n([\s\S]+?)(?=\n###|\n\*\*|\Z)', content)
    if desc_match:
        description = desc_match.group(1).strip()
        combined = f"{title}\n\n{description}"
        if len(combined) > TIKTOK_CAPTION_LIMIT:
            combined = combined[:TIKTOK_CAPTION_LIMIT - 1] + "…"
        return combined

    return title


# ── upload flow ───────────────────────────────────────────────────────────────

def open_tiktok_studio():
    """Open Chrome with tiktok profile and navigate to TikTok Studio upload."""
    print("  Killing all Chrome instances...")
    subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True)
    time.sleep(4)   # wait for Chrome to fully quit

    print("  Opening Chrome with tiktok profile...")
    subprocess.Popen([
        "open", "-a", "Google Chrome", "--args",
        f"--profile-directory={CHROME_PROFILE}",
        f"--user-data-dir={CHROME_DATA_DIR}",
        TIKTOK_UPLOAD_URL,
    ])
    time.sleep(6)
    run_osascript('tell application "Google Chrome" to activate')

    print("  Waiting for TikTok Studio to load", end="", flush=True)
    for _ in range(30):
        time.sleep(2)
        print(".", end="", flush=True)
        url = run_osascript(
            'tell application "Google Chrome" to return URL of active tab of front window'
        )
        if "tiktok.com" in url:
            print(" ready.")
            return True
    print(" timed out.")
    return False


def run_js_in_frame(js: str) -> str:
    """Try JS in main frame first, then in each iframe."""
    # Try main frame
    result = run_js_in_chrome(js)
    if result and result not in ("not found", "null", "undefined", ""):
        return result

    # Try each iframe by switching focus
    frame_count_raw = run_js_in_chrome("document.querySelectorAll('iframe').length")
    try:
        frame_count = int(frame_count_raw)
    except (ValueError, TypeError):
        frame_count = 0

    print(f"  (checking {frame_count} iframes...)")
    for i in range(frame_count):
        switch = f"""
(function() {{
    var frames = document.querySelectorAll('iframe');
    if (frames[{i}]) {{
        frames[{i}].focus();
        try {{
            return frames[{i}].contentWindow.eval({json.dumps(js)});
        }} catch(e) {{ return 'frame-error:' + e; }}
    }}
    return 'no-frame';
}})()
"""
        r = run_js_in_chrome(switch)
        if r and r not in ("not found", "null", "undefined", "", "no-frame") and not r.startswith("frame-error"):
            print(f"  Found in iframe[{i}]")
            return r

    return "not found"


def dump_page_info():
    """Print visible buttons and inputs for debugging."""
    info = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button, [role=button], input[type=file], label'))
        .filter(function(e) { return e.offsetParent !== null || e.type === 'file'; })
        .map(function(e) { return e.tagName + '|' + (e.type||'') + '|' + (e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,40); })
        .slice(0, 20);
    return btns.join(' || ');
})()
""")
    print(f"  Page elements: {info}")


def enable_js_from_apple_events():
    """Enable 'Allow JavaScript from Apple Events' in Chrome (idempotent)."""
    # Test if JS from Apple Events is already working by running a trivial expression
    test = run_js_in_chrome("1+1")
    if test.strip() == "2":
        return  # already enabled

    print("  Enabling 'Allow JavaScript from Apple Events'...")
    # Try via the Developer menu (must have Developer menu visible in Chrome)
    enable_script = """
tell application "Google Chrome" to activate
delay 0.5
tell application "System Events"
    tell process "Google Chrome"
        try
            click menu item "Allow JavaScript from Apple Events" of menu 1 of menu item "Developer" of menu "View" of menu bar 1
        end try
    end tell
end tell
"""
    subprocess.run(["osascript", "-e", enable_script], capture_output=True, text=True)
    time.sleep(1)
    run_osascript('tell application "Google Chrome" to reload active tab of front window')
    # Wait for the page to reload and settle
    time.sleep(6)
    # Verify it worked
    test2 = run_js_in_chrome("1+1")
    if test2.strip() == "2":
        print("  JS from Apple Events: enabled.")
    else:
        print("  Warning: JS from Apple Events may not be enabled. Enable it manually:")
        print("    Chrome → View → Developer → Allow JavaScript from Apple Events")


def get_chrome_window_bounds() -> Optional[tuple]:
    """Return (x, y, w, h) of the Chrome window content area."""
    raw = run_osascript("""
tell application "Google Chrome"
    set b to bounds of front window
    return ((item 1 of b) as string) & "," & ((item 2 of b) as string) & "," & ((item 3 of b) as string) & "," & ((item 4 of b) as string)
end tell
""")
    try:
        parts = [int(v.strip()) for v in raw.split(",")]
        x1, y1, x2, y2 = parts
        return x1, y1, x2 - x1, y2 - y1
    except Exception as e:
        print(f"  Window bounds parse error: {e!r} raw={raw!r}")
        return None


def select_video_file(video_path: Path) -> bool:
    """Click 'Select video' button and select the file via the OS file picker."""
    if not HAS_PYAUTOGUI:
        print("  Error: pyautogui not installed. Run: pip install pyautogui")
        return False

    enable_js_from_apple_events()

    # After reload, wait for TikTok Studio upload page to be ready
    print("  Waiting for TikTok Studio upload page...", end="", flush=True)
    for _ in range(20):
        time.sleep(2)
        url = run_osascript(
            'tell application "Google Chrome" to return URL of active tab of front window'
        )
        if "tiktok.com" in url.lower():
            print(" ready.")
            break
        print(".", end="", flush=True)
    else:
        print(" continuing anyway.")
    time.sleep(3)   # let JS settle

    # Get screen coordinates from JS — toolbar height computed dynamically
    print("  Getting 'Select video' button screen position via JS...")
    coords_str = run_js_in_chrome("""
(function() {
    var els = Array.from(document.querySelectorAll('button, label, [role="button"]'));
    var btn = els.find(function(b) {
        return (b.innerText || b.textContent || '').trim().toLowerCase().includes('select video');
    });
    if (!btn) {
        var all = els.map(function(b){
            return (b.tagName + ':' + (b.innerText||b.textContent||'').trim().slice(0,25));
        }).join(' | ');
        return 'not found. Elements: ' + all;
    }
    var r = btn.getBoundingClientRect();
    var cx = Math.round(r.left + r.width / 2);
    var cy = Math.round(r.top + r.height / 2);
    var toolbar = window.outerHeight - window.innerHeight;
    var sx = window.screenX + cx;
    var sy = window.screenY + toolbar + cy;
    return sx + ',' + sy;
})()
""")
    print(f"  JS result: {coords_str}")

    if "," in coords_str and not coords_str.startswith("not found"):
        try:
            screen_x, screen_y = [int(v.strip()) for v in coords_str.split(",")]
        except Exception as e:
            print(f"  Coord parse error: {e!r} — dumping page info:")
            dump_page_info()
            return False
    else:
        print("  Button not found — dumping page elements:")
        dump_page_info()
        # Fallback: centre of the drop zone
        bounds = get_chrome_window_bounds()
        if not bounds:
            return False
        win_x, win_y, win_w, win_h = bounds
        toolbar_raw = run_js_in_chrome("window.outerHeight - window.innerHeight")
        try:
            toolbar_h = int(toolbar_raw.strip())
        except Exception:
            toolbar_h = 87
        screen_x = win_x + win_w // 2
        screen_y = win_y + toolbar_h + (win_h - toolbar_h) // 2
        print(f"  Using centre fallback: ({screen_x}, {screen_y})")

    # ── Real mouse click via pyautogui (trusted event — opens file picker) ──
    print(f"  Clicking at ({screen_x}, {screen_y}) via pyautogui...")
    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(0.5)
    pyautogui.moveTo(screen_x, screen_y, duration=0.25)
    time.sleep(0.1)
    pyautogui.click()
    time.sleep(2)   # wait for file picker to appear

    # ── Enter the file path in the OS file picker ────────────────────────────
    print(f"  Entering file path: {video_path.name}")
    # Copy path to clipboard, then paste into Go-to-Folder dialog
    subprocess.run(["pbcopy"], input=str(video_path).encode("utf-8"), check=True)
    pyautogui.hotkey("command", "shift", "g")   # open Go To Folder sheet
    time.sleep(1.5)
    pyautogui.hotkey("command", "a")            # select all (clear existing text)
    time.sleep(0.2)
    pyautogui.hotkey("command", "v")            # paste full file path
    time.sleep(0.5)
    pyautogui.press("return")                   # navigate / select
    time.sleep(0.5)
    pyautogui.press("return")                   # confirm open
    time.sleep(3)
    print("  File selected.")
    return True


def wait_for_upload_complete(max_wait: int = 600) -> bool:
    """Wait until TikTok shows 'Uploaded' in the top-right, then pause 2s."""
    print("  Waiting for upload to complete", end="", flush=True)
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(3)
        elapsed += 3
        print(".", end="", flush=True)
        ready = run_js_in_chrome("""
(function() {
    var body = document.body.innerText;
    if (body.includes('Uploaded')) return 'ready';
    return 'waiting';
})()
""")
        if ready == "ready":
            print(" uploaded!")
            time.sleep(2)   # brief pause before interacting with the form
            return True

    print(" timed out.")
    return False


def fill_caption(caption: str):
    """Type the caption into TikTok's caption field."""
    print("  Filling caption...")

    # Click the caption field
    run_js_in_chrome("""
(function() {
    var selectors = [
        'div[class*="caption"] [contenteditable]',
        'div[contenteditable][data-e2e*="caption"]',
        'div[class*="DraftEditor"]',
        '[placeholder*="caption"]',
        '[placeholder*="Caption"]',
        'div[class*="editor-container"]',
    ];
    for (var s of selectors) {
        var el = document.querySelector(s);
        if (el) { el.focus(); el.click(); return; }
    }
})()
""")
    time.sleep(0.5)

    # Clear existing text and paste caption
    subprocess.run(["pbcopy"], input=caption.encode("utf-8"), check=True)
    run_osascript("""
tell application "System Events"
    keystroke "a" using command down
    delay 0.2
    keystroke "v" using command down
end tell
""")
    time.sleep(1)
    print(f"  Caption set: {caption[:60]}{'...' if len(caption) > 60 else ''}")


def set_privacy_private():
    """Set video privacy to 'Only me' (private)."""
    print("  Setting privacy to private...")

    clicked = run_js_in_chrome("""
(function() {
    var options = Array.from(document.querySelectorAll(
        'div[role="option"], li[role="option"], div[class*="radio"], label'
    ));
    var el = options.find(function(o) {
        var t = (o.innerText || '').toLowerCase();
        return t.includes('only me') || t.includes('private');
    });
    if (el) { el.click(); return 'clicked'; }
    return 'not found';
})()
""")
    if clicked == "clicked":
        print("  Privacy set to private.")
    else:
        print("  Could not find privacy option — set manually if needed.")
    time.sleep(0.5)


def _click_element_by_coords(coords_str: str, label: str) -> bool:
    """Parse 'x,y' coords string and click via pyautogui. Returns True on success."""
    if "," not in coords_str or coords_str.startswith("not found"):
        print(f"  '{label}' not found — coords: {coords_str[:120]}")
        return False
    try:
        sx, sy = [int(v.strip()) for v in coords_str.split(",")]
    except Exception as e:
        print(f"  Coord parse error for '{label}': {e!r}")
        return False
    run_osascript('tell application "Google Chrome" to activate')
    time.sleep(0.3)
    pyautogui.moveTo(sx, sy, duration=0.25)
    time.sleep(0.1)
    pyautogui.click()
    return True


def _get_screen_coords(js: str) -> str:
    """Run JS that returns 'screenX,screenY' and return the raw result."""
    return run_js_in_chrome(js)


def upload_cover_image(thumb_path: Path) -> bool:
    """
    TikTok Studio cover upload flow:
      1. Hover over cover thumbnail to reveal 'Edit cover' overlay button
      2. Click 'Edit cover'
      3. Wait for popup → click 2nd tab ('Upload image')
      4. Select thumbnail via OS file picker
      5. Confirm/save in popup
    """
    if not HAS_PYAUTOGUI:
        return False

    # ── Step 1: hover over cover image to reveal 'Edit cover' overlay ─────────
    print("  Step 1: hovering over cover thumbnail to reveal 'Edit cover'...")
    cover_coords = run_js_in_frame("""
(function() {
    // Find the cover preview container — look for the cover/thumbnail image wrapper
    var el = document.querySelector('[class*="cover-container"], [class*="coverContainer"], [class*="cover-wrapper"], [class*="coverWrapper"]');
    if (!el) {
        // fallback: find by looking for 'Edit cover' button's parent container
        var btn = Array.from(document.querySelectorAll('button, [role="button"], div, span')).find(function(b) {
            var t = (b.innerText || b.textContent || '').trim().toLowerCase();
            return t === 'edit cover';
        });
        el = btn ? btn.parentElement : null;
    }
    if (!el) return 'not found';
    var r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return 'not found';
    var toolbar = window.outerHeight - window.innerHeight;
    return (window.screenX + Math.round(r.left + r.width/2)) + ',' +
           (window.screenY + toolbar + Math.round(r.top + r.height/2));
})()
""")
    if "," in cover_coords and not cover_coords.startswith("not found"):
        try:
            hx, hy = [int(v.strip()) for v in cover_coords.split(",")]
            run_osascript('tell application "Google Chrome" to activate')
            time.sleep(0.3)
            pyautogui.moveTo(hx, hy, duration=0.4)   # hover to reveal overlay
            time.sleep(0.8)
        except Exception:
            pass

    # ── Step 2: click "Edit cover" ────────────────────────────────────────────
    print("  Step 2: clicking 'Edit cover'...")
    coords = run_js_in_frame("""
(function() {
    var btn = Array.from(document.querySelectorAll('button, [role="button"], div, span')).find(function(b) {
        var t = (b.innerText || b.textContent || '').trim().toLowerCase();
        return t === 'edit cover';
    });
    if (!btn) return 'not found';
    var r = btn.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return 'not found';
    var toolbar = window.outerHeight - window.innerHeight;
    return (window.screenX + Math.round(r.left + r.width/2)) + ',' +
           (window.screenY + toolbar + Math.round(r.top + r.height/2));
})()
""")
    print(f"  Edit cover coords: {coords}")
    if not _click_element_by_coords(coords, "Edit cover"):
        print("  Could not find 'Edit cover' — skipping thumbnail.")
        return False
    time.sleep(2.5)   # wait for popup to open

    # ── Step 3: click "Upload cover" inside the popup ─────────────────────────
    print("  Step 3: clicking 'Upload cover' in popup...")
    coords = run_js_in_frame("""
(function() {
    var btn = Array.from(document.querySelectorAll('button, [role="button"], div, span, label')).find(function(b) {
        var t = (b.innerText || b.textContent || '').trim().toLowerCase();
        return t === 'upload cover' || t.includes('upload cover');
    });
    if (!btn || btn.offsetParent === null) return 'not found';
    var r = btn.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return 'not found';
    var toolbar = window.outerHeight - window.innerHeight;
    return (window.screenX + Math.round(r.left + r.width/2)) + ',' +
           (window.screenY + toolbar + Math.round(r.top + r.height/2));
})()
""")
    print(f"  Upload cover coords: {coords}")
    if not _click_element_by_coords(coords, "Upload cover"):
        print("  Could not find 'Upload cover' — skipping thumbnail.")
        return False
    time.sleep(1)

    # ── Step 4: click the 2nd tab inside the popup ────────────────────────────
    print("  Step 4: clicking 2nd tab in cover popup...")
    coords = run_js_in_frame("""
(function() {
    var selectors = ['[role="tab"]', '[role="tablist"] > *', 'div[class*="tab"]', 'li[class*="tab"]'];
    var tabs = [];
    for (var s of selectors) {
        var found = Array.from(document.querySelectorAll(s)).filter(function(e) {
            return e.offsetParent !== null;
        });
        if (found.length >= 2) { tabs = found; break; }
    }
    if (tabs.length < 2) return 'not found';
    var tab = tabs[1];   // 2nd tab (index 1)
    var r = tab.getBoundingClientRect();
    var toolbar = window.outerHeight - window.innerHeight;
    return (window.screenX + Math.round(r.left + r.width/2)) + ',' +
           (window.screenY + toolbar + Math.round(r.top + r.height/2));
})()
""")
    print(f"  2nd tab coords: {coords}")
    if not _click_element_by_coords(coords, "2nd tab"):
        print("  Could not find 2nd tab in popup — skipping thumbnail.")
        return False
    time.sleep(1)

    # ── Step 5: click "Upload image" ──────────────────────────────────────────
    print("  Step 5: clicking 'Upload image'...")
    coords = run_js_in_frame("""
(function() {
    // Try a visible file input accepting images first (use its label/parent for coords)
    var fileInput = Array.from(document.querySelectorAll('input[type="file"]')).find(function(i) {
        return (i.accept || '').toLowerCase().includes('image');
    });
    if (fileInput) {
        var r = fileInput.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) {
            var lbl = (fileInput.id && document.querySelector('label[for="' + fileInput.id + '"]')) ||
                      fileInput.closest('label') || fileInput.parentElement;
            if (lbl) r = lbl.getBoundingClientRect();
        }
        if (r.width > 0 && r.height > 0) {
            var toolbar = window.outerHeight - window.innerHeight;
            return (window.screenX + Math.round(r.left + r.width/2)) + ',' +
                   (window.screenY + toolbar + Math.round(r.top + r.height/2));
        }
    }
    // Fallback: button/label with "upload image" text
    var btn = Array.from(document.querySelectorAll('button, [role="button"], label, span, div')).find(function(b) {
        var t = (b.innerText || b.textContent || '').trim().toLowerCase();
        return t === 'upload image' || t === 'upload photo' || t.includes('upload image');
    });
    if (!btn) return 'not found';
    var r = btn.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return 'not found';
    var toolbar = window.outerHeight - window.innerHeight;
    return (window.screenX + Math.round(r.left + r.width/2)) + ',' +
           (window.screenY + toolbar + Math.round(r.top + r.height/2));
})()
""")
    print(f"  Upload image coords: {coords}")
    if not _click_element_by_coords(coords, "Upload image"):
        print("  Could not find 'Upload image' button — skipping thumbnail.")
        return False
    time.sleep(2)   # wait for OS file picker

    # ── Step 6: select file via OS file picker ────────────────────────────────
    print(f"  Step 6: selecting thumbnail file: {thumb_path.name}")
    subprocess.run(["pbcopy"], input=str(thumb_path).encode("utf-8"), check=True)
    pyautogui.hotkey("command", "shift", "g")
    time.sleep(1.5)
    pyautogui.hotkey("command", "a")
    time.sleep(0.2)
    pyautogui.hotkey("command", "v")
    time.sleep(0.5)
    pyautogui.press("return")
    time.sleep(0.5)
    pyautogui.press("return")
    time.sleep(2)
    print(f"  Thumbnail selected: {thumb_path.name}")

    # ── Step 7: confirm / save in the popup ───────────────────────────────────
    print("  Step 7: confirming cover selection...")
    coords = run_js_in_frame("""
(function() {
    var btn = Array.from(document.querySelectorAll('button')).find(function(b) {
        var t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
        return t === 'confirm' || t === 'save' || t === 'apply' || t === 'done' || t === 'ok';
    });
    if (!btn || btn.offsetParent === null) return 'not found';
    var r = btn.getBoundingClientRect();
    var toolbar = window.outerHeight - window.innerHeight;
    return (window.screenX + Math.round(r.left + r.width/2)) + ',' +
           (window.screenY + toolbar + Math.round(r.top + r.height/2));
})()
""")
    print(f"  Confirm button coords: {coords}")
    if _click_element_by_coords(coords, "Confirm"):
        time.sleep(1)
        print("  Cover confirmed.")
    else:
        print("  Confirm button not found — popup may auto-close after file selection.")

    return True


def wait_for_save_draft_enabled(max_wait: int = 60) -> bool:
    """Wait until the 'Save draft' button exists and is not disabled."""
    print("  Waiting for 'Save draft' button to become enabled", end="", flush=True)
    for _ in range(max_wait // 2):
        time.sleep(2)
        print(".", end="", flush=True)
        state = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var btn = btns.find(function(b) {
        var t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
        return t === 'save draft' || t.includes('save draft');
    });
    if (!btn) return 'not found';
    if (btn.disabled) return 'disabled';
    return 'enabled';
})()
""")
        if state == "enabled":
            print(" ready.")
            return True
        if state == "not found":
            print(".", end="", flush=True)  # still loading
    print(" timed out.")
    return False


def click_save_draft() -> bool:
    """Wait for Save Draft to be enabled, scroll it into view, then click via pyautogui."""
    if not HAS_PYAUTOGUI:
        print("  Error: pyautogui not installed. Run: pip install pyautogui")
        return False

    # Wait for the button to be enabled before doing anything
    if not wait_for_save_draft_enabled():
        print("  Save Draft button never became enabled.")
        return False

    # Scroll the button into view (TikTok uses a custom scroll container)
    print("  Scrolling 'Save draft' button into view...")
    run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var btn = btns.find(function(b) {
        var t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
        return t === 'save draft' || t.includes('save draft');
    });
    if (btn) btn.scrollIntoView({behavior: 'instant', block: 'center'});
})()
""")
    time.sleep(1.5)

    # Get screen coordinates now that the button is in the viewport
    print("  Getting 'Save draft' button screen coordinates...")
    coords = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var btn = btns.find(function(b) {
        var t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
        return t === 'save draft' || t.includes('save draft');
    });
    if (!btn) return 'not found';
    var r = btn.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return 'not found';
    var toolbar = window.outerHeight - window.innerHeight;
    var sx = window.screenX + Math.round(r.left + r.width / 2);
    var sy = window.screenY + toolbar + Math.round(r.top + r.height / 2);
    return sx + ',' + sy + '|rect:' + Math.round(r.top) + ',' + Math.round(r.bottom) + ' viewport:' + window.innerHeight;
})()
""")
    print(f"  Save Draft coords: {coords}")

    coord_part = coords.split("|")[0]
    if not _click_element_by_coords(coord_part, "Save draft"):
        print("  Could not find 'Save draft' button — dumping page elements:")
        dump_page_info()
        return False

    print("  Save Draft clicked.")
    time.sleep(2)   # give popup a moment to appear
    _handle_save_anyway_popup()
    return True


def _handle_save_anyway_popup():
    """If TikTok shows a 'Save draft?' confirmation popup, click 'Save anyway'."""
    coords = run_js_in_chrome("""
(function() {
    var btns = Array.from(document.querySelectorAll('button'));
    var btn = btns.find(function(b) {
        var t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
        return t === 'save anyway' || t.includes('save anyway');
    });
    if (!btn || btn.offsetParent === null) return 'not found';
    var r = btn.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return 'not found';
    var toolbar = window.outerHeight - window.innerHeight;
    var sx = window.screenX + Math.round(r.left + r.width / 2);
    var sy = window.screenY + toolbar + Math.round(r.top + r.height / 2);
    return sx + ',' + sy;
})()
""")
    if "," in coords and not coords.startswith("not found"):
        print("  'Save draft?' popup detected — clicking 'Save anyway'...")
        _click_element_by_coords(coords, "Save anyway")
        time.sleep(1)


def wait_for_draft_redirect(max_wait: int = 60) -> bool:
    """Wait until TikTok redirects to the drafts page after saving."""
    print("  Waiting for redirect to drafts page", end="", flush=True)
    for _ in range(max_wait // 2):
        time.sleep(2)
        print(".", end="", flush=True)
        url = run_osascript(
            'tell application "Google Chrome" to return URL of active tab of front window'
        )
        if "tiktokstudio/content" in url and "draft" in url:
            print(f" redirected! ({url})")
            return True
    print(" timed out (draft may still have been saved — check TikTok Studio).")
    return False


def close_browser():
    """Quit Google Chrome."""
    print("  Closing Chrome...")
    run_osascript('tell application "Google Chrome" to quit')
    time.sleep(2)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Upload chapter video to TikTok.")
    parser.add_argument("folder", help="Chapter output folder (e.g. ./clown_vol_1/output/ch_16)")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"Error: '{folder}' is not a directory.")
        sys.exit(1)

    # ── Locate files ──────────────────────────────────────────────────────────
    video         = find_file(folder, "_tiktok.mp4")
    meta          = find_file(folder, "_meta.md")
    thumbnail     = find_file(folder, "_thumbnail.png")
    upload_record = find_file(folder, "_tiktok_upload.json")

    if not video:
        print(f"No *_tiktok.mp4 found in {folder} — skipping.")
        sys.exit(0)

    if upload_record:
        data = json.loads(upload_record.read_text())
        print(f"Already uploaded to TikTok ({data.get('uploaded_at', 'unknown date')}).")
        print("Delete *_tiktok_upload.json to re-upload.")
        sys.exit(0)

    caption = parse_caption(meta) if meta else video.stem.replace("_tiktok", "")

    print(f"\nTikTok Upload")
    print(f"  Video     : {video.name}")
    print(f"  Thumbnail : {thumbnail.name if thumbnail else 'none'}")
    print(f"  Caption   : {caption[:80]}")

    # ── Browser automation ────────────────────────────────────────────────────
    if not open_tiktok_studio():
        print("Error: Could not open TikTok Studio.")
        sys.exit(1)

    time.sleep(3)

    select_video_file(video)

    if not wait_for_upload_complete():
        print("Warning: Upload confirmation timed out — the video may still be processing.")
        print("Check TikTok Studio manually.")

    fill_caption(caption)
    set_privacy_private()
    time.sleep(1)
    click_save_draft()
    wait_for_draft_redirect()
    close_browser()

    # ── Save upload record ────────────────────────────────────────────────────
    from datetime import datetime
    record = {
        "video": video.name,
        "caption": caption,
        "privacy": "private",
        "saved_as_draft_at": datetime.now().isoformat(),
    }
    record_path = folder / f"{video.stem}_tiktok_upload.json"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(f"\n  Record saved: {record_path.name}")
    print("  Draft saved — check TikTok Studio to confirm.")



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
