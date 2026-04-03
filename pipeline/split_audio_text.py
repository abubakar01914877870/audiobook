#!/usr/bin/env python3
"""
split_audio_text.py — Split a translated .md file into audio segments
based on ===PAUSE_X=== markers inserted during translation.

Creates an audio_splits/ subfolder with numbered .md files.
Also rewrites the main .md with markers stripped (clean version).

Usage:
    python split_audio_text.py ./clown_vol_1/output/ch_16/Chapter_016_Title.md
"""

import os
import re
import sys
import argparse


PAUSE_PATTERN = re.compile(r'\s*\n===PAUSE_([\d.]+)===\n\s*')


def split_translation(md_path):
    """Split a .md file by pause markers into audio_splits/ subfolder.

    Returns list of (segment_md_path, pause_after_seconds).
    If no markers found, returns empty list (caller should treat whole file as one segment).
    """
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    if not PAUSE_PATTERN.search(text):
        print(f"  No pause markers found in {os.path.basename(md_path)}")
        return []

    folder = os.path.dirname(md_path)
    splits_dir = os.path.join(folder, "audio_splits")
    os.makedirs(splits_dir, exist_ok=True)

    # Chapter number from filename
    stem = os.path.splitext(os.path.basename(md_path))[0]
    ch_match = re.search(r"(\d+)", stem)
    ch_num = ch_match.group(1) if ch_match else "0"

    # Split: [text, pause_sec, text, pause_sec, ..., text]
    parts = PAUSE_PATTERN.split(text)

    segments = []
    for i in range(0, len(parts), 2):
        segment_text = parts[i].strip()
        if not segment_text:
            continue
        pause = float(parts[i + 1]) if i + 1 < len(parts) else 0.0
        segments.append((segment_text, pause))

    # Write numbered segment files
    result = []
    for idx, (seg_text, pause) in enumerate(segments, 1):
        fname = f"{idx:03d}_ch_{ch_num}_pause_{pause:.1f}.md"
        seg_path = os.path.join(splits_dir, fname)
        with open(seg_path, "w", encoding="utf-8") as f:
            f.write(seg_text)
        result.append((seg_path, pause))

    # Rewrite main .md with markers stripped
    clean_text = PAUSE_PATTERN.sub("\n\n", text).strip()
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(clean_text)

    print(f"  Split into {len(result)} audio segments in audio_splits/")
    print(f"  Cleaned pause markers from {os.path.basename(md_path)}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Split translated .md into audio segments by pause markers")
    parser.add_argument("md_path", help="Path to the translated .md file")
    args = parser.parse_args()

    if not os.path.exists(args.md_path):
        print(f"Error: '{args.md_path}' not found.")
        sys.exit(1)

    segments = split_translation(args.md_path)
    if segments:
        print(f"\nCreated {len(segments)} segments:")
        for path, pause in segments:
            print(f"  {os.path.basename(path)}")
    else:
        print("  No segments created (no pause markers found).")


if __name__ == "__main__":
    main()
