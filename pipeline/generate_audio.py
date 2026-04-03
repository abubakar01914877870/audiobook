#!/usr/bin/env python3
"""
generate_audio.py — Generate Bengali audio narration via Google Docs
'Listen to this tab'.

Workflow:
  1. Split the translated .md by ===PAUSE_X=== markers into audio_splits/
  2. For each segment: open Google Docs, paste text, trigger Listen, download MP3
  3. Merge all segment MP3s with silence gaps into a single chapter audio
  4. Clean up audio_splits/

If no pause markers exist, falls back to processing the whole .md at once.

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

from split_audio_text import split_translation
from merge_audio import merge_audio

# Global cancellation flag — set by SIGINT handler
_cancelled = False

def _sigint_handler(_sig, _frame):
    global _cancelled
    _cancelled = True
    print("\n  [Ctrl+C] Cancelling audio generation...")

signal.signal(signal.SIGINT, _sigint_handler)

CHROME_DATA_DIR = "/Users/abubakarsiddique/Library/Application Support/Google/Chrome"
CHROME_PROFILE  = "Profile 9"


# -- helpers -------------------------------------------------------------------

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


# -- download detection --------------------------------------------------------

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

    Strategy:
      The Chrome plugin may fire multiple downloads as audio chunks arrive.
      Each new download contains ALL chunks accumulated so far, so the LAST
      file is always the most complete.

      1. Collect every new MP3 that appears after trigger_time.
      2. Wait for each to stabilise (size stops changing).
      3. After a quiet period (no new files for QUIET_SECONDS), take the
         largest file as the final audio.
      4. Delete any earlier partial downloads.
    """
    QUIET_SECONDS  = 12   # no new MP3 for this long → audio generation done
    collected      = []   # list of (path, size) — stabilised files
    seen           = set()

    elapsed = 0
    quiet_elapsed = 0     # seconds since last new file was collected

    while elapsed < max_wait:
        if not _interruptible_sleep(check_interval):
            print("\n  Cancelled — stopping audio wait.")
            return False
        elapsed += check_interval

        found_new = False
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

            if mtime < trigger_time - 2:
                continue

            seen.add(fname)
            found_new = True
            print(f"\n  New MP3 detected: {fname} — waiting for download to finish...")

            final_size = wait_for_stable_size(fpath)
            if _cancelled:
                return False

            if final_size > 0:
                collected.append((fpath, final_size))
                print(f"  Collected: {fname}  ({final_size / 1024:.0f} KB)")

        if found_new:
            quiet_elapsed = 0
        elif collected:
            quiet_elapsed += check_interval

        # Quiet period reached — pick the largest file
        if collected and quiet_elapsed >= QUIET_SECONDS:
            # Sort by size descending — largest is the most complete
            collected.sort(key=lambda x: x[1], reverse=True)
            best_path, best_size = collected[0]

            # Move the best file to destination
            shutil.move(best_path, dest_path)
            size_mb = os.path.getsize(dest_path) / 1024 / 1024
            print(f"  Audio saved: {os.path.basename(dest_path)}  ({size_mb:.1f} MB)")

            # Delete partial downloads
            for fpath, _ in collected[1:]:
                try:
                    os.remove(fpath)
                    print(f"  Deleted partial: {os.path.basename(fpath)}")
                except Exception:
                    pass

            return True

        if elapsed % 30 == 0:
            n = len(collected)
            status = f"{n} file(s) collected" if n else "waiting for first file"
            print(f"  Still waiting... ({int(elapsed)}s, {status})  [Ctrl+C to cancel]", flush=True)

    # Timeout — still try to use what we have
    if collected:
        collected.sort(key=lambda x: x[1], reverse=True)
        best_path, _ = collected[0]
        shutil.move(best_path, dest_path)
        size_mb = os.path.getsize(dest_path) / 1024 / 1024
        print(f"  Timeout, but saving best file: {os.path.basename(dest_path)}  ({size_mb:.1f} MB)")
        for fpath, _ in collected[1:]:
            try:
                os.remove(fpath)
            except Exception:
                pass
        return True

    return False


# -- text cleaning -------------------------------------------------------------

def clean_text_for_audio(text: str) -> str:
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r'[-*_]{3,}', stripped):
            continue
        if re.fullmatch(r'[.…]+', stripped):
            continue
        line = re.sub(r'\*([^*]+)\*', r'\1', line)
        line = re.sub(r'_([^_]+)_', r'\1', line)
        # Strip trailing ellipsis (... or …) from words — causes TTS issues
        line = re.sub(r'\.{2,}', '', line)
        line = re.sub(r'…', '', line)
        cleaned.append(line)
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned))
    return result.strip()


# -- Google Docs interaction ---------------------------------------------------

def open_chrome_google_docs():
    """Launch Chrome with a new Google Doc. Returns True if doc loaded."""
    print("  Closing any existing Chrome session...")
    run_osascript('tell application "Google Chrome" to quit')
    _interruptible_sleep(2)

    print(f"  Launching Chrome — profile {CHROME_PROFILE}...")
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
        return False
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
            return True
    print(f"\n  Timed out waiting for Google Docs. Last URL: {doc_url}")
    return False


def set_doc_title(title: str):
    """Set the Google Docs document title."""
    subprocess.run(["pbcopy"], input=title.encode("utf-8"), check=True)
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
        print(f"  Title set: {title}")
    else:
        print("  Could not find title input — skipping.")


def paste_and_trigger_listen(text: str):
    """Paste text into the doc and trigger 'Listen to this tab'."""
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.3)
    print("  Pasting content...")
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
    print("  Content pasted. Triggering 'Listen to this tab'...")
    time.sleep(1)

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
    print("  Triggered. Waiting for audio download...")


def close_chrome():
    """Close Chrome."""
    print("  Closing Chrome...")
    run_osascript('tell application "Google Chrome" to quit')


# -- single segment audio generation ------------------------------------------

def generate_audio_for_text(text: str, title: str, dest_mp3: str) -> bool:
    """Open Google Docs, paste text, trigger Listen, download MP3.

    Returns True if audio was downloaded successfully.
    """
    if os.path.exists(dest_mp3):
        size_mb = os.path.getsize(dest_mp3) / 1024 / 1024
        print(f"  Audio already exists: {os.path.basename(dest_mp3)} ({size_mb:.1f} MB) — skipping.")
        return True

    cleaned = clean_text_for_audio(text)
    print(f"  Text: {len(text):,} -> {len(cleaned):,} chars (cleaned)")

    if not open_chrome_google_docs():
        return False
    if _cancelled:
        close_chrome()
        return False

    set_doc_title(title)

    downloads_dir = os.path.expanduser("~/Downloads")
    trigger_time = time.time()

    paste_and_trigger_listen(cleaned)

    ok = wait_for_audio(downloads_dir, trigger_time, dest_mp3)
    if not ok and not _cancelled:
        print(f"\n  Timed out. Move the MP3 manually to:\n    {dest_mp3}")

    close_chrome()
    _interruptible_sleep(2)  # let Chrome fully quit before next iteration
    return ok


# -- main workflow -------------------------------------------------------------

def generate_audio(chapter_folder: str):
    """Full audio generation: split -> generate per segment -> merge -> cleanup."""
    chapter_md = find_chapter_md(chapter_folder)
    if not chapter_md:
        print(f"Error: No translated .md file found in '{chapter_folder}'.")
        sys.exit(1)

    stem = os.path.splitext(os.path.basename(chapter_md))[0]
    final_audio = os.path.join(chapter_folder, f"{stem}_audio.mp3")

    # Already done?
    if os.path.exists(final_audio):
        print(f"Audio already present: {os.path.basename(final_audio)} — skipping.")
        return

    print(f"\nChapter: {os.path.basename(chapter_md)}")

    # Step 1: Split by pause markers, or resume from existing splits
    splits_dir = os.path.join(chapter_folder, "audio_splits")

    if os.path.isdir(splits_dir) and any(f.endswith(".md") for f in os.listdir(splits_dir)):
        # Resume from existing splits (markers already stripped from main .md)
        print("  Resuming from existing audio_splits/...")
        segments = []
        for fname in sorted(os.listdir(splits_dir)):
            if not fname.endswith(".md"):
                continue
            # Extract pause value — match digits/dots between "pause_" and ".md"
            pause_match = re.search(r"pause_([\d.]+)\.md$", fname)
            pause = float(pause_match.group(1)) if pause_match else 0.0
            segments.append((os.path.join(splits_dir, fname), pause))
        print(f"  Found {len(segments)} existing segments.")
    else:
        segments = split_translation(chapter_md)

    # No pause markers — process whole file as one segment
    if not segments:
        print("  No pause markers — generating audio from full chapter.")
        with open(chapter_md, "r", encoding="utf-8") as f:
            text = f.read()
        generate_audio_for_text(text, stem, final_audio)
        return

    # Step 2: Generate audio for each segment
    print(f"\n  Generating audio for {len(segments)} segments...")
    all_ok = True
    for i, (seg_md, pause) in enumerate(segments, 1):
        if _cancelled:
            print("\n  Cancelled.")
            return

        seg_stem = os.path.splitext(os.path.basename(seg_md))[0]
        seg_mp3 = os.path.join(splits_dir, seg_stem + ".mp3")

        print(f"\n  --- Segment {i}/{len(segments)} (pause after: {pause}s) ---")

        with open(seg_md, "r", encoding="utf-8") as f:
            seg_text = f.read()

        ok = generate_audio_for_text(seg_text, f"{stem}_part_{i:03d}", seg_mp3)
        if not ok:
            print(f"  Failed to generate audio for segment {i}.")
            all_ok = False
            break

    if not all_ok or _cancelled:
        print("\n  Audio generation incomplete. Run again to resume from where it stopped.")
        return

    # Step 3: Merge all segments with pauses
    print(f"\n  --- Merging audio segments ---")
    merged = merge_audio(chapter_folder, cleanup=True)
    if merged:
        print(f"\n  Done! Final audio: {os.path.basename(merged)}")
    else:
        print("\n  Merge failed. Split files preserved in audio_splits/ for manual recovery.")


# -- entry point ---------------------------------------------------------------

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

    generate_audio(folder)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
    if _cancelled:
        sys.exit(1)
