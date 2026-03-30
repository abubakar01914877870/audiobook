#!/usr/bin/env python3
"""
test_lut_360p.py — Temp script: 360p portrait LUT test on remote render server.

The render server substitutes {input_N} only in -i arguments, not in
-filter_complex strings. So the LUT path must be a literal string in the filter.

Strategy: upload the .cube file normally → server places it in the job dir →
reference it by FILENAME ONLY in lut3d (FFmpeg cwd = job dir on the server).

Usage:
    python test_lut_360p.py /path/to/chapter/folder
    python test_lut_360p.py /path/to/chapter/folder --lut /path/to/file.cube

Output: <folder>/test_lut_360p.mp4
"""

import sys
import json
import re
import random
import subprocess
import time
import argparse
import requests
from pathlib import Path

RENDER_SERVER_URL = "http://192.168.0.14:8765"

DEFAULT_LUT = Path(__file__).parent / "luts" / "Bleech_Bypass_Yellow_01.cube"

OUT_W, OUT_H, FPS = 360, 640, 24
TRANSITION_DURATION = 1.0
TRANSITIONS = ["fade", "fadeblack", "dissolve", "smoothleft", "smoothright",
               "zoomin", "circleopen", "fadewhite", "horzopen", "smoothup"]

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac"}
MIME_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".wav": "audio/wav", ".flac": "audio/flac",
    ".cube": "application/octet-stream",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_audio_duration(audio_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def parse_meta_scores(folder: Path) -> dict:
    meta_file = next(folder.glob("*_meta.md"), None)
    if not meta_file:
        return {}
    content = meta_file.read_text(encoding="utf-8")
    scores = {}
    for block in re.split(r'###\s+Image Prompt\s+', content, flags=re.IGNORECASE)[1:]:
        m_idx = re.match(r'^(\d+)', block)
        m_score = re.search(r'\*\*position_score:\*\*\s*(\d+)', block, re.IGNORECASE)
        if m_idx and m_score:
            scores[int(m_idx.group(1))] = int(m_score.group(1))
    return scores


def find_images(folder: Path, scores: dict) -> list:
    imgs = sorted(f for f in folder.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS)
    result = []
    for img in imgs:
        m = re.search(r'_(\d+)_(thumb|scene)', img.name.lower())
        idx = int(m.group(1)) if m else 999
        is_thumb = bool(m) and m.group(2) == "thumb"
        result.append({"path": img, "idx": idx, "is_thumb": is_thumb, "score": scores.get(idx, 0)})
    return result


def build_timeline(img_data: list, duration: float) -> list:
    THUMB_INTRO = 5.0
    thumb = next((i for i in img_data if i["is_thumb"]), img_data[0])
    events = sorted(
        [{"img": i, "target": (i["score"] / 100.0) * duration} for i in img_data],
        key=lambda e: e["target"],
    )
    first_target = events[0]["target"]
    span = duration - first_target
    scale = (duration - THUMB_INTRO) / span if span > 0 else 1.0

    segments = [{"img": thumb, "start": 0.0, "end": THUMB_INTRO}]
    current = THUMB_INTRO
    for i, ev in enumerate(events):
        nat = (events[i + 1]["target"] - ev["target"]) if i < len(events) - 1 else (duration - ev["target"])
        seg_dur = max(1.0, nat * scale)
        segments.append({"img": ev["img"], "start": current, "end": current + seg_dur})
        current += seg_dur
    segments[-1]["end"] = duration
    return [s for s in segments if s["end"] > s["start"]]


# ── FFmpeg filter chain ───────────────────────────────────────────────────────

def build_filter_chain(segments: list, lut_filename: str) -> tuple:
    """Build filter chain with LUT referenced by filename only.

    The server uploads the .cube file into the job directory and runs FFmpeg
    with that directory as cwd, so a bare filename resolves correctly.
    The server does not substitute {input_N} inside -filter_complex strings,
    so absolute paths or {input_N} cannot be used here.
    """
    td = TRANSITION_DURATION
    transitions = TRANSITIONS.copy()
    random.shuffle(transitions)
    while len(transitions) < len(segments):
        transitions += transitions
    transitions = transitions[:len(segments)]

    unique_files = []
    file_to_idx = {}
    for seg in segments:
        p = seg["img"]["path"]
        if p not in file_to_idx:
            file_to_idx[p] = len(unique_files)
            unique_files.append(p)

    cmd_inputs = []
    for seg in segments:
        cmd_inputs += ["-r", str(FPS), "-loop", "1",
                       "-i", f"{{input_{file_to_idx[seg['img']['path']]}}}"]

    filter_parts = []
    for i in range(len(segments)):
        filter_parts.append(
            f"[{i}:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={OUT_W}:{OUT_H},setsar=1,fps={FPS},format=yuv420p[v{i}]"
        )

    last = "v0"
    for i in range(len(segments) - 1):
        offset = segments[i]["end"] - td
        filter_parts.append(
            f"[{last}][v{i+1}]xfade=transition={transitions[i]}:"
            f"duration={td:.3f}:offset={offset:.3f}[x{i}]"
        )
        last = f"x{i}"

    # Use bare filename — server uploads the .cube to the job dir and FFmpeg
    # runs from there, so no path or escaping needed.
    filter_parts.append(
        f"[{last}]lut3d={lut_filename},format=nv12[outv]"
    )

    return cmd_inputs, ";".join(filter_parts), unique_files


# ── Server ────────────────────────────────────────────────────────────────────

def server_online() -> bool:
    try:
        r = requests.get(f"{RENDER_SERVER_URL}/health", timeout=4)
        return r.status_code == 200
    except Exception:
        return False


def render_on_server(job_cmd: list, uploads: list, out_filename: str, out_path: Path, duration: float):
    file_handles = []
    multipart = []
    try:
        for fpath in uploads:
            mime = MIME_MAP.get(fpath.suffix.lower(), "application/octet-stream")
            fh = open(fpath, "rb")
            file_handles.append(fh)
            multipart.append(("files", (fpath.name, fh, mime)))

        resp = requests.post(f"{RENDER_SERVER_URL}/render", files=multipart, timeout=120, data={
            "command": json.dumps(job_cmd),
            "outputs": json.dumps([out_filename]),
            "duration": str(duration),
        })
        resp.raise_for_status()
        job_id = resp.json()["job_id"]

        last_pct = -1
        while True:
            r = requests.get(f"{RENDER_SERVER_URL}/status/{job_id}", timeout=10)
            data = r.json()
            pct = data.get("progress", 0)
            if pct != last_pct:
                bar = "#" * int(40 * pct / 100) + "-" * (40 - int(40 * pct / 100))
                print(f"\r  [{bar}] {pct:3d}%  {data.get('render_time_seconds', 0):.1f}s",
                      end="", flush=True)
                last_pct = pct
            if data["status"] == "done":
                print()
                break
            elif data["status"] == "failed":
                print()
                raise RuntimeError(f"Render failed: {data.get('error')}")
            time.sleep(2)

        print(f"  Downloading -> {out_path.name}")
        dl = requests.get(f"{RENDER_SERVER_URL}/download/{job_id}/{out_filename}", stream=True)
        dl.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in dl.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    finally:
        for fh in file_handles:
            fh.close()
        try:
            requests.delete(f"{RENDER_SERVER_URL}/job/{job_id}", timeout=5)
        except Exception:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="360p LUT test render on remote server.")
    parser.add_argument("folder", help="Chapter output folder")
    parser.add_argument("--lut", default=str(DEFAULT_LUT),
                        help=f"Local .cube LUT file to upload (default: {DEFAULT_LUT.name})")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    lut_path = Path(args.lut).expanduser().resolve()

    if not folder.is_dir():
        print(f"Error: folder not found: {folder}")
        sys.exit(1)
    if not lut_path.exists():
        print(f"Error: LUT not found: {lut_path}")
        sys.exit(1)

    print(f"\nFolder     : {folder.name}")
    print(f"LUT        : {lut_path.name} (uploaded to server job dir)")
    print(f"Output     : {OUT_W}x{OUT_H} @ {FPS}fps (portrait {OUT_H}p)")

    if not server_online():
        print("\nError: Render server unreachable. This script is remote-only.")
        sys.exit(1)
    print("Server     : online")

    audio = next((f for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXTENSIONS), None)
    if not audio:
        print("Error: no audio file found.")
        sys.exit(1)

    scores = parse_meta_scores(folder)
    img_data = find_images(folder, scores)
    if not img_data:
        print("Error: no images found.")
        sys.exit(1)

    duration = get_audio_duration(audio)
    segments = build_timeline(img_data, duration)

    print(f"\nAudio      : {audio.name} ({duration / 60:.1f} min)")
    print(f"Images     : {len(img_data)} found | {len(segments)} timeline segments")

    cmd_inputs, fc, unique_imgs = build_filter_chain(segments, lut_path.name)

    # Uploads: images + audio + lut (server places all in job dir; lut3d uses filename only)
    all_uploads = unique_imgs + [audio, lut_path]
    audio_stream = len(segments)
    audio_input_idx = len(unique_imgs)

    af = f"afade=t=in:st=0:d=2,afade=t=out:st={max(0.0, duration - 3.0):.3f}:d=3"

    job_cmd = ["-y", "-filter_threads", "8"]
    job_cmd += cmd_inputs
    job_cmd += ["-i", f"{{input_{audio_input_idx}}}"]
    job_cmd += [
        "-filter_complex", fc,
        "-map", "[outv]",
        "-map", f"{audio_stream}:a",
        "-af", af,
        "-c:v", "h264_qsv",
        "-preset", "slow",
        "-profile:v", "high",
        "-level:v", "3.1",
        "-b:v", "800k",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-t", str(duration),
        "-movflags", "+faststart",
        "{output_0}",
    ]

    print(f"\nFilter tail: ...lut3d={lut_path.name},format=nv12[outv]")
    print(f"Rendering on Intel Arc A750 (remote)...")

    out_path = folder / "test_lut_360p.mp4"
    render_on_server(job_cmd, all_uploads, out_path.name, out_path, duration)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\nDone: {out_path.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
