#!/usr/bin/env python3
"""
merge_audio.py — Merge split audio MP3 files with silence gaps between them.

Reads audio_splits/ folder, parses pause duration from filenames,
produces a single merged MP3, then cleans up the splits folder.

Usage:
    python merge_audio.py ./clown_vol_1/output/ch_16
"""

import os
import re
import sys
import shutil
import argparse
import subprocess
import tempfile


def find_split_audio_files(splits_dir):
    """Find and sort split MP3 files. Returns list of (mp3_path, pause_after)."""
    files = []
    for fname in sorted(os.listdir(splits_dir)):
        if not fname.endswith(".mp3"):
            continue
        pause_match = re.search(r"pause_([\d.]+)\.\w+$", fname)
        pause = float(pause_match.group(1)) if pause_match else 0.0
        files.append((os.path.join(splits_dir, fname), pause))
    return files


def probe_sample_rate(mp3_path):
    """Get sample rate from an MP3 file. Returns int or 24000 as fallback."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "stream=sample_rate",
             "-of", "csv=p=0", mp3_path],
            capture_output=True, text=True
        )
        return int(result.stdout.strip())
    except Exception:
        return 24000


def generate_silence(duration_sec, output_path, sample_rate=24000):
    """Generate a silence MP3 file of given duration."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"anullsrc=r={sample_rate}:cl=mono",
         "-t", str(duration_sec),
         "-c:a", "libmp3lame", "-q:a", "2",
         output_path],
        capture_output=True, check=True,
    )


def merge_audio(chapter_folder, cleanup=True):
    """Merge split audio files with pauses into a single MP3.

    Returns path to merged audio, or None if no splits found.
    """
    splits_dir = os.path.join(chapter_folder, "audio_splits")
    if not os.path.isdir(splits_dir):
        return None

    split_files = find_split_audio_files(splits_dir)
    if not split_files:
        print("  No split MP3 files found in audio_splits/")
        return None

    # If only one segment with no pause, just move it
    if len(split_files) == 1:
        mp3_path, _ = split_files[0]
        output_name = _derive_output_name(chapter_folder)
        output_path = os.path.join(chapter_folder, output_name)
        shutil.copy2(mp3_path, output_path)
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"  Single segment — copied as {output_name} ({size_mb:.1f} MB)")
        if cleanup:
            shutil.rmtree(splits_dir)
            print("  Cleaned up audio_splits/")
        return output_path

    print(f"  Merging {len(split_files)} audio segments...")

    output_name = _derive_output_name(chapter_folder)
    output_path = os.path.join(chapter_folder, output_name)

    # Detect sample rate from first file
    sample_rate = probe_sample_rate(split_files[0][0])

    with tempfile.TemporaryDirectory() as tmpdir:
        concat_list = os.path.join(tmpdir, "concat.txt")
        entries = []

        # 3-second silence at the beginning
        intro_silence = os.path.join(tmpdir, "silence_intro.mp3")
        generate_silence(3, intro_silence, sample_rate)
        entries.append(f"file '{intro_silence}'")

        for i, (mp3_path, pause_after) in enumerate(split_files):
            entries.append(f"file '{os.path.abspath(mp3_path)}'")

            # Add silence gap (skip for last segment or zero pause)
            if pause_after > 0 and i < len(split_files) - 1:
                silence_path = os.path.join(tmpdir, f"silence_{i:03d}.mp3")
                generate_silence(pause_after, silence_path, sample_rate)
                entries.append(f"file '{silence_path}'")

        # 3-second silence at the end
        outro_silence = os.path.join(tmpdir, "silence_outro.mp3")
        generate_silence(3, outro_silence, sample_rate)
        entries.append(f"file '{outro_silence}'")

        with open(concat_list, "w") as f:
            f.write("\n".join(entries))

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", concat_list,
             "-c:a", "libmp3lame", "-q:a", "2",
             output_path],
            capture_output=True, text=True,
        )

        if result.returncode != 0:
            # Show only actual error lines, not the banner
            err_lines = [l for l in result.stderr.splitlines() if l.startswith("[") or "Error" in l or "Invalid" in l]
            print(f"  FFmpeg merge failed: {chr(10).join(err_lines[-5:]) if err_lines else result.stderr[-300:]}")
            return None

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"  Merged audio: {output_name} ({size_mb:.1f} MB)")

    if cleanup:
        shutil.rmtree(splits_dir)
        print("  Cleaned up audio_splits/")

    return output_path


def _derive_output_name(chapter_folder):
    """Find the chapter .md and derive the audio output filename."""
    for fname in sorted(os.listdir(chapter_folder)):
        if fname.endswith(".md") and not fname.endswith("_meta.md"):
            return os.path.splitext(fname)[0] + "_audio.mp3"
    return "chapter_audio.mp3"


def main():
    parser = argparse.ArgumentParser(description="Merge split audio files with silence gaps")
    parser.add_argument("folder", help="Chapter output folder (e.g. ./clown_vol_1/output/ch_16)")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep audio_splits/ after merge")
    args = parser.parse_args()

    if not os.path.isdir(args.folder):
        print(f"Error: '{args.folder}' is not a directory.")
        sys.exit(1)

    result = merge_audio(args.folder, cleanup=not args.no_cleanup)
    if not result:
        print("No audio to merge.")
        sys.exit(1)


if __name__ == "__main__":
    main()
