#!/usr/bin/env python3
"""
generate_audio.py — Open a chapter .md in Google Docs and use
'Listen to this tab' to render + download the Bengali audio narration.

Usage:
    python generate_audio.py ./clown_vol_1/output/ch_16
"""

import os
import re
import sys
import time
import shutil
import signal
import argparse
import subprocess
from typing import Optional

# Global cancellation flag — set by SIGINT handler
_cancelled = False

def _sigint_handler(_sig, _frame):
    global _cancelled
    _cancelled = True
    print("\n  [Ctrl+C] Cancelling audio generation...")

signal.signal(signal.SIGINT, _sigint_handler)

CHROME_DATA_DIR = "/Users/abubakarsiddique/Library/Application Support/Google/Chrome"
CHROME_PROFILE  = "Profile 9"


# ── helpers ───────────────────────────────────────────────────────────────────

def find_chapter_md(folder: str) -> Optional[str]:
    for fname in sorted(os.listdir(folder)):
        if fname.endswith(".md") and not fname.endswith("_meta.md"):
            return os.path.join(folder, fname)
    return None


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


# ── download detection ────────────────────────────────────────────────────────

def _interruptible_sleep(seconds: float, granularity: float = 0.3) -> bool:
    """Sleep for `seconds` in small chunks. Returns False if cancelled."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _cancelled:
            return False
        time.sleep(min(granularity, deadline - time.time()))
    return True


def wait_for_stable_size(fpath: str, checks: int = 4, interval: float = 3.0) -> int:
    """Poll until the file size stops changing. Returns final size in bytes."""
    last = -1
    stable = 0
    while stable < checks:
        if not _interruptible_sleep(interval):
            return 0
        try:
            current = os.path.getsize(fpath)
        except FileNotFoundError:
            return 0
        if current == last:
            stable += 1
        else:
            stable = 0
            last = current
        print(
            f"  Stabilising: {current / 1024 / 1024:.2f} MB  "
            f"(unchanged {stable}/{checks})",
            end="\r", flush=True,
        )
    print()
    return last


def wait_for_audio(downloads_dir: str, trigger_time: float,
                   dest_path: str, max_wait: int = 30 * 60,
                   check_interval: float = 3.0) -> bool:
    """
    Watch ~/Downloads for new MP3 files that appear after trigger_time.

    Strategy (avoids the 4 MB threshold bug):
      1. Find any new MP3 (mtime >= trigger_time).
      2. Wait for its size to stabilise — do NOT judge size while downloading.
      3. Final size < 500 KB  → metadata/junk file → delete it, keep waiting.
      4. Final size >= 500 KB → real audio → move to dest_path.

    This correctly handles chapters of any length (1 MB short, 20 MB long).
    """
    SMALL_BYTES    = 500 * 1024   # files smaller than this after stabilising are junk
    seen           = set()        # filenames we have already processed

    elapsed = 0
    while elapsed < max_wait:
        if not _interruptible_sleep(check_interval):
            print("\n  Cancelled — stopping audio wait.")
            return False
        elapsed += check_interval

        for fname in os.listdir(downloads_dir):
            if not fname.lower().endswith(".mp3"):
                continue
            if fname in seen:
                continue

            fpath = os.path.join(downloads_dir, fname)
            try:
                mtime = os.path.getmtime(fpath)
            except FileNotFoundError:
                continue

            # Only care about files created/modified after we triggered
            if mtime < trigger_time - 2:   # 2 s grace for filesystem clock skew
                continue

            seen.add(fname)
            print(f"\n  New MP3 detected: {fname} — waiting for download to finish...")

            final_size = wait_for_stable_size(fpath)

            if _cancelled:
                return False

            if final_size < SMALL_BYTES:
                try:
                    os.remove(fpath)
                    print(f"  Deleted junk file: {fname}  ({final_size / 1024:.0f} KB)")
                except Exception as e:
                    print(f"  Could not delete {fname}: {e}")
            else:
                shutil.move(fpath, dest_path)
                size_mb = os.path.getsize(dest_path) / 1024 / 1024
                print(f"  Audio saved: {os.path.basename(dest_path)}  ({size_mb:.1f} MB)")
                return True

        if elapsed % 30 == 0:
            print(f"  Still waiting for audio... ({int(elapsed)}s)  [Ctrl+C to cancel]", flush=True)

    return False


# ── text cleaning ─────────────────────────────────────────────────────────────

def clean_text_for_audio(text: str) -> str:
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Remove standalone section dividers: ---, ***, ___
        if re.fullmatch(r'[-*_]{3,}', stripped):
            continue
        # Remove lines that are only ellipsis (... or …) — standalone pause markers
        if re.fullmatch(r'[.…]+', stripped):
            continue
        # Strip italic markers (*text* or _text_) — keep the inner text
        line = re.sub(r'\*([^*]+)\*', r'\1', line)
        line = re.sub(r'_([^_]+)_', r'\1', line)
        cleaned.append(line)

    # Collapse 3+ consecutive blank lines into 2
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned))
    return result.strip()


# ── main task ─────────────────────────────────────────────────────────────────

def generate_audio(md_path: str):
    print(f"\nChapter: {os.path.basename(md_path)}")

    try:
        with open(md_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        print(f"  Could not read {md_path}: {e}")
        sys.exit(1)

    text = clean_text_for_audio(raw)
    print(f"  Cleaned: {len(raw):,} → {len(text):,} chars.")

    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    print(f"  Copied {len(text):,} chars to clipboard.")

    # ── Step 1: kill any existing Chrome, then open fresh with correct profile ──
    print("  Closing any existing Chrome session...")
    run_osascript('tell application "Google Chrome" to quit')
    _interruptible_sleep(2)   # let Chrome fully shut down

    print(f"  Launching Chrome — profile {CHROME_PROFILE} (Abu Bakar / myopensky2@gmail.com)...")
    chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    subprocess.Popen([
        chrome_bin,
        f"--profile-directory={CHROME_PROFILE}",
        "--new-window",
        "https://docs.google.com/document/create",
    ])
    _interruptible_sleep(6)
    if _cancelled:
        run_osascript('tell application "Google Chrome" to quit')
        return
    run_osascript('tell application "Google Chrome" to activate')

    print("  Waiting for Google Docs to load", end="", flush=True)
    doc_url = ""
    for _ in range(40):
        time.sleep(2)
        print(".", end="", flush=True)
        doc_url = run_osascript(
            'tell application "Google Chrome" to return URL of active tab of front window'
        )
        if doc_url and "docs.google.com/document/d/" in doc_url:
            print("\n  Google Docs ready.")
            break
    else:
        print(f"\n  Timed out waiting for Google Docs. Last URL: {doc_url}")
        sys.exit(1)

    time.sleep(4)

    # ── Step 3: set document title ────────────────────────────────────────────
    stem = os.path.splitext(os.path.basename(md_path))[0]
    subprocess.run(["pbcopy"], input=stem.encode("utf-8"), check=True)
    time.sleep(0.3)
    title_result = run_js_in_chrome("""
(function(){
    var t = document.querySelector('.docs-title-input');
    if(t){ t.focus(); return 'focused'; }
    return 'not found';
})()
""")
    if title_result == "focused":
        run_osascript("""
tell application "System Events"
    keystroke "a" using command down
    delay 0.2
    keystroke "v" using command down
    delay 0.2
    key code 36
end tell
""")
        time.sleep(1)
        print("  Title set.")
    else:
        print("  Could not find title input — skipping.")

    # ── Step 4: paste chapter content ─────────────────────────────────────────
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.3)
    print("  Pasting chapter content...")
    run_js_in_chrome("""
(function(){
    var ed = document.querySelector('.kix-appview-editor, .docs-texteventtarget-iframe');
    if(ed) ed.focus();
})()
""")
    time.sleep(0.5)
    run_osascript("""
tell application "Google Chrome" to activate
delay 0.3
tell application "System Events"
    keystroke "a" using command down
    delay 0.3
    keystroke "v" using command down
end tell
""")
    time.sleep(3)
    print("  Content pasted.")

    # ── Step 5: trigger 'Listen to this tab' ──────────────────────────────────
    print("  Triggering 'Listen to this tab'...")
    time.sleep(1)

    downloads_dir = os.path.expanduser("~/Downloads")
    trigger_time  = time.time()   # record NOW — any MP3 after this is ours

    run_osascript("""
tell application "Google Chrome" to activate
delay 0.5
tell application "System Events"
    keystroke "/" using option down
    delay 2
    keystroke "Listen to this tab"
    delay 1
    key code 36
end tell
""")
    time.sleep(1)
    print("  Triggered. Waiting for Google Docs to render and download audio...")

    # ── Step 6: wait for audio, move to chapter folder ────────────────────────
    dest_path = os.path.join(os.path.dirname(md_path), f"{stem}_audio.mp3")

    if os.path.exists(dest_path):
        print(f"  Audio already exists: {os.path.basename(dest_path)} — done.")
        return

    ok = wait_for_audio(downloads_dir, trigger_time, dest_path)
    if not ok and not _cancelled:
        print(f"\n  Timed out. Move the MP3 manually to:\n    {dest_path}")

    # ── Step 7: close Chrome ───────────────────────────────────────────────────
    print("  Closing Chrome...")
    run_osascript('tell application "Google Chrome" to quit')


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate Bengali audio via Google Docs 'Listen to this tab'."
    )
    parser.add_argument("folder", help="Chapter output folder (e.g. ./clown_vol_1/output/ch_16)")
    args = parser.parse_args()

    folder = args.folder.rstrip("/")
    if not os.path.isdir(folder):
        print(f"Error: '{folder}' is not a valid directory.")
        sys.exit(1)

    chapter_md = find_chapter_md(folder)
    if not chapter_md:
        print(f"Error: No translated .md file found in '{folder}'. Run translation before generating audio.")
        sys.exit(1)

    # Skip if the expected output file already exists
    _stem = os.path.splitext(os.path.basename(chapter_md))[0]
    _expected_audio = os.path.join(folder, f"{_stem}_audio.mp3")
    if os.path.exists(_expected_audio):
        print(f"Audio already present: {os.path.basename(_expected_audio)} — skipping.")
        sys.exit(0)

    generate_audio(chapter_md)


if __name__ == "__main__":
    main()
    if _cancelled:
        sys.exit(1)
