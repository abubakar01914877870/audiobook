#!/usr/bin/env python3
"""
generate_video.py — Export YouTube + TikTok versions of a chapter video.

Usage:
    python generate_video.py /path/to/folder

Outputs (named after the image file):
    Chapter_015_The_Invitation_youtube.mp4
    Chapter_015_The_Invitation_tiktok.mp4
"""

import sys
import subprocess
import json
import re
from pathlib import Path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac"}


# ─────────────────────────────────────────────────────────────────────────────
# File detection
# ─────────────────────────────────────────────────────────────────────────────

def find_files(folder: Path):
    image_file = None
    audio_file = None
    for f in sorted(folder.iterdir()):
        if f.is_file():
            ext = f.suffix.lower()
            if ext in IMAGE_EXTENSIONS and image_file is None:
                image_file = f
            elif ext in AUDIO_EXTENSIONS and audio_file is None:
                audio_file = f
    return image_file, audio_file


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────

def check_ffmpeg():
    for cmd in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([cmd, "-version"], capture_output=True, check=True)
        except FileNotFoundError:
            print(f"Error: '{cmd}' not found. Install FFmpeg and add it to your PATH.")
            sys.exit(1)
        except subprocess.CalledProcessError:
            print(f"Error: '{cmd}' failed to run.")
            sys.exit(1)


def get_audio_duration(audio_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            return float(stream["duration"])
    # Fallback: format-level duration
    result2 = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result2.stdout)["format"]["duration"])


# ─────────────────────────────────────────────────────────────────────────────
# Shared video filter chain
# ─────────────────────────────────────────────────────────────────────────────

def build_vf(fps: int, duration: float, out_w: int, out_h: int) -> str:
    total_frames = int(duration * fps)

    # Pre-scale to 5% larger than the output so zoompan always has real source
    # pixels at max zoom — at z=1.05 the crop window is exactly out_w×out_h
    # sampled at native resolution, with zero interpolation artifacts.
    pre_w = int(out_w * 1.05)
    pre_h = int(out_h * 1.05)
    pre_scale = (
        f"scale={pre_w}:{pre_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={pre_w}:{pre_h}"
    )

    # Ken Burns: slow zoom 1.0 → 1.05 over the full duration.
    # x/y keep the crop centred — text baked into the image stays within the
    # safe zone since we only crop 2.4% from each edge at maximum zoom.
    zoompan = (
        f"zoompan=z='min(zoom+0.0002,1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={total_frames}:s={out_w}x{out_h}:fps={fps}"
    )

    # Colour grading tuned for dark/moody anime with warm golden lamp tones:
    #   saturation 1.15 — makes the golden streetlamp glow richer
    #   brightness  0.01 — minimal lift; keep the intentional darkness
    #   contrast    1.08 — deeper shadows, more cinematic punch
    eq = "eq=saturation=1.15:brightness=0.01:contrast=1.08"

    # Sharpening — 5×5 luma (better for ≥1080p), lighter chroma to avoid fringing
    unsharp = (
        "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.8"
        ":chroma_msize_x=3:chroma_msize_y=3:chroma_amount=0.4"
    )

    vfade_in = "fade=t=in:st=0:d=2"
    vfade_out = f"fade=t=out:st={max(0.0, duration - 2.0):.3f}:d=2"
    pix_fmt = "format=yuv420p"

    return ",".join([pre_scale, zoompan, eq, unsharp, vfade_in, vfade_out, pix_fmt])


def build_af(duration: float) -> str:
    afade_out_start = max(0.0, duration - 3.0)
    return f"afade=t=in:st=0:d=2,afade=t=out:st={afade_out_start:.3f}:d=3"


# ─────────────────────────────────────────────────────────────────────────────
# Platform-specific FFmpeg commands
# ─────────────────────────────────────────────────────────────────────────────

def build_youtube_cmd(image: Path, audio: Path, output: Path, duration: float) -> list:
    """
    YouTube: 1440×2560 (2K vertical).
    Source images are 1536px wide — 2K is a slight downscale (perfect quality).
    Uploading at 2K forces YouTube to use VP9 for all quality tiers, giving
    viewers a noticeably sharper 1080p stream than a native 1080p upload would.
    24 fps | CRF 15 | veryslow | High profile | tune=film | 256k AAC
    """
    return [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image),
        "-i", str(audio),
        "-vf", build_vf(fps=24, duration=duration, out_w=1440, out_h=2560),
        "-af", build_af(duration),
        "-c:v", "libx264",
        "-preset", "veryslow",
        "-crf", "15",
        "-profile:v", "high",
        "-level:v", "5.1",          # level 5.1 covers 2K @ 24fps
        "-tune", "film",
        "-c:a", "aac",
        "-b:a", "256k",
        "-ar", "48000",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-color_range", "tv",
        "-t", str(duration),
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(output),
    ]


def build_tiktok_cmd(image: Path, audio: Path, output: Path, duration: float) -> list:
    """
    TikTok: 1080×1920 (platform hard cap — they do not accept higher).
    TikTok re-encodes on ingest so CRF 18 / slow is the right trade-off.
    30 fps | CRF 18 | slow | High profile | 192k AAC
    """
    return [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image),
        "-i", str(audio),
        "-vf", build_vf(fps=30, duration=duration, out_w=1080, out_h=1920),
        "-af", build_af(duration),
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "18",
        "-profile:v", "high",
        "-level:v", "4.1",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-color_range", "tv",
        "-t", str(duration),
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(output),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Progress + helpers
# ─────────────────────────────────────────────────────────────────────────────

def run_with_progress(cmd: list, duration: float):
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    last_pct = -1
    bar_width = 40

    for line in process.stdout:
        m = re.match(r"out_time_us=(\d+)", line.strip())
        if m:
            elapsed_s = int(m.group(1)) / 1_000_000
            pct = min(100, int(elapsed_s / duration * 100))
            if pct != last_pct:
                last_pct = pct
                bar = "#" * int(bar_width * pct / 100) + "-" * (bar_width - int(bar_width * pct / 100))
                print(f"\r  [{bar}] {pct:3d}%  {elapsed_s:.1f}s / {duration:.1f}s", end="", flush=True)

    process.wait()
    print()

    if process.returncode != 0:
        print("\nFFmpeg error:")
        print(process.stderr.read()[-3000:])
        sys.exit(1)


def ask_overwrite(path: Path) -> bool:
    while True:
        answer = input(f"  '{path.name}' already exists. Overwrite? [y/N]: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        print("  Please enter y or n.")


def human_size(path: Path) -> str:
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_video.py /path/to/folder")
        sys.exit(1)

    folder = Path(sys.argv[1]).expanduser().resolve()
    if not folder.is_dir():
        print(f"Error: '{folder}' is not a directory.")
        sys.exit(1)

    print(f"\nScanning: {folder}")
    check_ffmpeg()

    image, audio = find_files(folder)
    if not image:
        print("Error: No image file found (PNG/JPG/JPEG/WEBP).")
        sys.exit(1)
    if not audio:
        print("Error: No audio file found (MP3/M4A/AAC/WAV/FLAC).")
        sys.exit(1)

    print(f"  Image : {image.name}")
    print(f"  Audio : {audio.name}")

    # Output names derived from the image filename stem
    stem = image.stem
    out_youtube = folder / f"{stem}_youtube.mp4"
    out_tiktok  = folder / f"{stem}_tiktok.mp4"
    print(f"  Output (YouTube) : {out_youtube.name}")
    print(f"  Output (TikTok)  : {out_tiktok.name}")

    print("\nReading audio duration...")
    try:
        duration = get_audio_duration(audio)
    except Exception as e:
        print(f"Error reading audio duration: {e}")
        sys.exit(1)
    print(f"  Duration: {duration:.2f}s  ({duration / 60:.1f} min)")

    # Overwrite checks upfront
    render_youtube = True
    render_tiktok  = True
    if out_youtube.exists():
        render_youtube = ask_overwrite(out_youtube)
    if out_tiktok.exists():
        render_tiktok = ask_overwrite(out_tiktok)

    if not render_youtube and not render_tiktok:
        print("Nothing to render. Exiting.")
        sys.exit(0)

    # ── YouTube render ────────────────────────────────────────────────────────
    if render_youtube:
        print(f"\n[1/2] YouTube  — 1440×2560 (2K) | 24fps | CRF 15 | veryslow | tune=film | 256k AAC")
        cmd = build_youtube_cmd(image, audio, out_youtube, duration)
        run_with_progress(cmd, duration)
        if not out_youtube.exists():
            print("Error: YouTube output was not created.")
            sys.exit(1)
        print(f"  Saved: {out_youtube.name}  ({human_size(out_youtube)})")
    else:
        print(f"\n[1/2] YouTube — skipped.")

    # ── TikTok render ─────────────────────────────────────────────────────────
    if render_tiktok:
        print(f"\n[2/2] TikTok   — 1080×1920 (1080p) | 30fps | CRF 18 | slow | 192k AAC")
        cmd = build_tiktok_cmd(image, audio, out_tiktok, duration)
        run_with_progress(cmd, duration)
        if not out_tiktok.exists():
            print("Error: TikTok output was not created.")
            sys.exit(1)
        print(f"  Saved: {out_tiktok.name}  ({human_size(out_tiktok)})")
    else:
        print(f"\n[2/2] TikTok — skipped.")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n─────────────────────────────────────────")
    print("Done")
    print(f"  Image    : {image.name}")
    print(f"  Audio    : {audio.name}")
    print(f"  Duration : {duration:.2f}s  ({duration / 60:.1f} min)")
    if render_youtube and out_youtube.exists():
        print(f"  YouTube  : {out_youtube.name}  ({human_size(out_youtube)})")
    if render_tiktok and out_tiktok.exists():
        print(f"  TikTok   : {out_tiktok.name}  ({human_size(out_tiktok)})")
    print("─────────────────────────────────────────")


if __name__ == "__main__":
    main()
