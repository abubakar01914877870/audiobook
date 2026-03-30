#!/usr/bin/env python3
"""
generate_video.py — Export YouTube + TikTok versions of a chapter video.

Renders EXCLUSIVELY via the Kalponic Render Server (Intel Arc A750 GPU).

Features:
- Parses `position_score` (0-100) from `_meta.md`.
- precisely maps image placement on the audio timeline.
- Thumbnail is universally displayed for the first 5 seconds, then reappears based on its score.
- Removed heavy CPU filtering (Ken Burns pan, film grain, vignette) — vastly improving speed.
- Re-architected exact absolute timeline overlap (`offset=`) for flawless xfade transitioning.

Usage:
    python generate_video.py /path/to/folder

─────────────────────────────────────────────────────────────────────────────
Intel Arc A750 QSV — Pipeline Rules (DO NOT CHANGE without reading this)
─────────────────────────────────────────────────────────────────────────────
Server: FFmpeg v8.1 gyan.dev, --enable-libvpl, driver 32.0.101.8626

RULE 1 — No manual hwupload for plain software→QSV encode
  av1_qsv / hevc_qsv / h264_qsv accept nv12 software frames directly and
  handle GPU upload themselves. Adding hwupload without hardware filters
  causes EINVAL (-22, exit code 4294967274). Only add hwupload if you also
  use scale_qsv / vpp_qsv / overlay_qsv in the same filter chain.

RULE 2 — Filter chain must end with format=nv12, NOT yuv420p
  QSV encoders require nv12 surface format. yuv420p input causes EINVAL.
  Correct tail: [last]format=nv12[outv]

RULE 3 — av1_qsv preset is numeric (1=fastest, 7=slowest)
  Use -preset 4 (medium). String presets like "slow" are not valid for
  av1_qsv. hevc_qsv and h264_qsv accept string presets fine.

RULE 4 — Do NOT add -init_hw_device for plain encode jobs
  -init_hw_device qsv=hw,child_device_type=d3d11 is only needed when the
  filter graph uses hardware filters (scale_qsv, vpp_qsv etc.). Adding it
  to a software pipeline causes EINVAL.

RULE 5 — vpp_qsv detail= param is unreliable across driver versions
  vpp_qsv=procamp=1:brightness=X:contrast=X:saturation=X is fine.
  Adding detail=60 breaks on many driver builds. Omit detail entirely.

IF YOU NEED HARDWARE FILTERS in future, the correct pattern is:
  ffmpeg -init_hw_device qsv=hw,child_device_type=d3d11 -filter_hw_device hw
         -filter_complex "...[last]format=nv12,hwupload=extra_hw_frames=64,
                          scale_qsv=W:H,vpp_qsv=procamp=1:brightness=0.01:
                          contrast=1.08:saturation=1.15[outv]"
         -c:v av1_qsv -preset 4 ...
─────────────────────────────────────────────────────────────────────────────

─────────────────────────────────────────────────────────────────────────────
LUT (.cube) Colour Grading — How to Enable
─────────────────────────────────────────────────────────────────────────────
Usage:
    python generate_video.py /path/to/folder --lut /path/to/file.cube

LUT is applied REMOTE ONLY (Intel Arc A750). Local renders (Apple
VideoToolbox) skip it — build_local_render_job() receives lut_path=None.

── How lut3d works in this pipeline ────────────────────────────────────────
Filter chain tail (remote):
    [last] lut3d=filename.cube, format=nv12 [outv]

  1. Each image is scaled/cropped to output resolution → format=yuv420p
  2. xfade transitions blend the segments
  3. lut3d applies the colour grade (works on yuv420p frames, CPU-side)
  4. format=nv12 converts for the QSV encoder
  5. av1_qsv / h264_qsv encodes on the GPU

── Critical: how the LUT file path reaches FFmpeg ──────────────────────────
The render server substitutes {input_N} placeholders ONLY in -i arguments,
NOT inside -filter_complex strings. Therefore:

  WRONG — placeholder never resolved inside filter_complex:
    lut3d={input_13}          ← server ignores this → EINVAL

  WRONG — absolute Windows path, colon causes filter parse error:
    lut3d=C:/luts/file.cube   ← ':' is FFmpeg option separator → EINVAL

  CORRECT — bare filename, server runs FFmpeg with cwd=job_dir:
    lut3d=Bleech_Bypass_Yellow_01.cube  ← resolved from cwd → works ✓

The server fix that made bare filenames work (renderer.py line 197):
    subprocess.Popen(..., cwd=job.work_dir)   # added 2026-03-30

── To add a new LUT ────────────────────────────────────────────────────────
  1. Drop the .cube file into luts/ on the Mac (source of truth)
  2. Pass it via --lut; it is uploaded to the server's job dir automatically
  3. No changes needed on the server

── vpp_qsv hardware LUT (future / Linux only) ──────────────────────────────
Intel Arc supports hardware LUT via vpp_qsv=lut3d_file=path on Linux with
oneVPL >= 24.1.1. The current server is Windows so this is NOT available.
If the server is ever migrated to Ubuntu, replace lut3d with:
    format=nv12,hwupload=extra_hw_frames=64,vpp_qsv=lut3d_file=file.cube
and add -init_hw_device qsv=hw,child_device_type=d3d11 -filter_hw_device hw
─────────────────────────────────────────────────────────────────────────────
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

# ── Local render codec profiles (Apple VideoToolbox — Mac Mini M2) ────────────
# VideoToolbox requires yuv420p (not nv12), no string/numeric presets.
# -allow_sw 1 falls back to SW encoder if HW is busy.
# -q:v: 0=best quality, 100=worst. 55/60 is a good middle-ground.
# -filter_threads 4: appropriate for M2 8GB unified memory (not 8 like remote).
LOCAL_CODEC_YT = {
    "fps": 24, "cv": "hevc_videotoolbox", "bv": "4M",
    "ba": "256k", "pix_fmt": "yuv420p", "allow_sw": True, "q_v": 55,
}
LOCAL_CODEC_TT = {
    "fps": 30, "cv": "h264_videotoolbox", "bv": "5M",
    "ba": "192k", "pix_fmt": "yuv420p", "allow_sw": True, "q_v": 60,
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS  = {".mp4"}
AUDIO_EXTENSIONS  = {".mp3", ".m4a", ".aac", ".wav", ".flac"}
TRANSITION_DURATION = 1.0
AMBIENT_VOL = 0.07   # Grok video ambient audio: 7% of original (~-23 dB under narration)

# Cinematic transition pool
TRANSITIONS = [
    "fade", "fadeblack", "dissolve", "smoothleft", "smoothright", 
    "zoomin", "circleopen", "fadewhite", "horzopen", "smoothup",
]

MIME_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".mp4": "video/mp4",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".wav": "audio/wav", ".flac": "audio/flac",
    ".cube": "application/octet-stream",
}

# ─────────────────────────────────────────────────────────────────────────────
# Parsing Timelines
# ─────────────────────────────────────────────────────────────────────────────

def parse_meta_scores(folder: Path) -> dict:
    """Parse position_score from _meta.md."""
    meta_file = next(folder.glob("*_meta.md"), None)
    if not meta_file:
        return {}
    
    content = meta_file.read_text(encoding="utf-8")
    scores = {}
    
    blocks = re.split(r'###\s+Image Prompt\s+', content, flags=re.IGNORECASE)
    for block in blocks[1:]:
        m_idx = re.match(r'^(\d+)', block)
        if not m_idx: continue
        idx = int(m_idx.group(1))
        
        m_score = re.search(r'\*\*position_score:\*\*\s*(\d+)', block, flags=re.IGNORECASE)
        if m_score:
            scores[idx] = int(m_score.group(1))
            
    return scores

def find_media_with_scores(folder: Path, scores: dict) -> list:
    """Scan folder for thumbnail and scene media files.

    Accepted files (all returned — no deduplication):
      _NN_thumb.(png|jpg|webp) → is_thumb=True,  is_video=False  (5s intro only)
      _NN_thumb.mp4            → is_thumb=True,  is_video=True   (timeline position, looped)
      _NN_scene.mp4            → is_thumb=False, is_video=True   (timeline, looped)
    Scene PNGs are skipped — intermediate assets only.

    Both thumb PNG and thumb MP4 can be present simultaneously and serve different roles:
      - PNG  → used exclusively for the 5s static title card at t=0
      - MP4  → placed at the thumb's position_score timestamp in the main timeline
    """
    media_data = []

    for f in sorted(folder.iterdir()):
        if not f.is_file():
            continue

        m = re.search(r'_(\d+)_(thumb|scene)', f.name.lower())
        if not m:
            continue

        idx      = int(m.group(1))
        is_thumb = (m.group(2) == 'thumb')
        ext      = f.suffix.lower()

        if ext in VIDEO_EXTENSIONS:
            is_video = True
        elif is_thumb and ext in IMAGE_EXTENSIONS:
            is_video = False
        else:
            continue  # skip scene PNGs and anything else

        score = scores.get(idx, 0)
        media_data.append({
            "path":     f,
            "idx":      idx,
            "is_thumb": is_thumb,
            "is_video": is_video,
            "score":    score,
        })

    return media_data

def build_timeline(img_data: list, duration: float) -> list:
    """Map images to the audio timeline using position_score for ordering and proportional duration.

    Thumb PNG → 5s static title card at t=0 (excluded from timeline events).
    Thumb MP4 → placed at its position_score timestamp in the main timeline (looping).
    Scene MP4s → placed at their position_score timestamps (looping).

    Each segment's natural duration = score gap to next event × total_duration.
    The unused intro gap (0% → first event's score) is distributed proportionally
    across all events via a scale factor, so no single segment absorbs a disproportionate chunk.
    """
    THUMB_INTRO = 5.0

    # Thumb PNG is the static 5s intro card — must be an image, not video
    thumb_intro = next((img for img in img_data if img["is_thumb"] and not img.get("is_video")), None)
    if not thumb_intro:
        # Fallback: no PNG thumb, use MP4 thumb or first entry
        thumb_intro = next((img for img in img_data if img["is_thumb"]), img_data[0] if img_data else None)

    # Timeline events: ALL thumb items are excluded — thumb only appears as the 5s static intro.
    # Scene MP4s are the only timeline events.
    timeline_events = []
    for img in img_data:
        if img["is_thumb"]:
            continue  # thumb (PNG or MP4) is for 5s intro only — never elsewhere in timeline
        target_t = (img["score"] / 100.0) * duration
        timeline_events.append({"img": img, "target": target_t})

    timeline_events.sort(key=lambda e: e["target"])

    if not timeline_events:
        # Only a thumb PNG exists — single static intro for full duration
        return [{"img": thumb_intro, "start": 0.0, "end": duration}]

    # Natural span covered by events: from first event's target to end of audio
    first_target = timeline_events[0]["target"]
    events_natural_span = duration - first_target  # e.g. 558s for score-10 first event on 620s audio

    # Scale factor stretches natural durations to fill the remaining time after the thumb intro
    remaining = duration - THUMB_INTRO
    scale = remaining / events_natural_span if events_natural_span > 0 else 1.0

    segments = [{"img": thumb_intro, "start": 0.0, "end": THUMB_INTRO}]

    current = THUMB_INTRO
    for i, event in enumerate(timeline_events):
        if i < len(timeline_events) - 1:
            natural_dur = timeline_events[i + 1]["target"] - event["target"]
        else:
            natural_dur = duration - event["target"]

        seg_dur = max(10.5, natural_dur * scale)
        segments.append({"img": event["img"], "start": current, "end": current + seg_dur})
        current += seg_dur

    # Clamp last segment to exact audio end to absorb any float rounding
    if segments:
        segments[-1]["end"] = duration

    return [s for s in segments if s["end"] > s["start"]]

def get_audio_duration(audio_path: Path) -> float:
    """Retrieve reliable track length."""
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(audio_path)], 
                           capture_output=True, text=True, check=True)
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(audio_path)], 
                           capture_output=True, text=True, check=True)
        data = json.loads(r.stdout)
        for s in data.get("streams", []):
            if "duration" in s:
                return float(s["duration"])
    raise ValueError(f"Could not read audio duration for {audio_path.name}")

# ─────────────────────────────────────────────────────────────────────────────
# Building FFmpeg Chains
# ─────────────────────────────────────────────────────────────────────────────

def get_shuffled_transitions(n_needed: int) -> list:
    if n_needed <= 0: return []
    res = []
    while len(res) < n_needed + 1:
        chunk = TRANSITIONS.copy()
        random.shuffle(chunk)
        if res and chunk[0] == res[-1]:
            chunk[0], chunk[-1] = chunk[-1], chunk[0]
        res.extend(chunk)
    return res[:n_needed]

def build_ff_commands(segments: list, fps: int, out_w: int, out_h: int, transitions: list,
                      lut_path: Path = None, duration: float = 0.0):
    td = TRANSITION_DURATION
    filter_parts = []
    unique_files = []
    file_to_idx = {}

    for seg in segments:
        p = seg["img"]["path"]
        if p not in file_to_idx:
            file_to_idx[p] = len(unique_files)
            unique_files.append(p)

    cmd_inputs = []
    for i, seg in enumerate(segments):
        p = seg["img"]["path"]
        if seg["img"].get("is_video", False):
            # Grok 10s video clip — loop indefinitely; xfade + final -t handle duration
            cmd_inputs += [
                "-stream_loop", "-1",
                "-i", f"{{input_{file_to_idx[p]}}}"
            ]
        else:
            # Static image (thumbnail)
            cmd_inputs += [
                "-r", str(fps),
                "-loop", "1",
                "-i", f"{{input_{file_to_idx[p]}}}"
            ]

    for i in range(len(segments)):
        filter_parts.append(
            f"[{i}:v]"
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={out_w}:{out_h},setsar=1,fps={fps},"
            f"format=yuv420p[v{i}]"
        )

    if len(segments) == 1:
        last_label = "v0"
    else:
        last_label = "v0"
        for i in range(len(segments) - 1):
            t_name = transitions[i % len(transitions)]
            offset = segments[i]["end"] - td
            filter_parts.append(
                f"[{last_label}][v{i+1}]"
                f"xfade=transition={t_name}:duration={td:.3f}:offset={offset:.3f}"
                f"[x{i}]"
            )
            last_label = f"x{i}"

    lut_filter = f"lut3d={{lut_0}}," if lut_path else ""
    filter_parts.append(
        f"[{last_label}]{lut_filter}format=nv12[outv]"
    )

    # ── Ambient audio: per-segment with crossfade synced to video transitions ──
    # Each segment gets its own audio slice (silence for images, trimmed loop for
    # videos). Slices are chained with acrossfade=d=TRANSITION_DURATION so the
    # ambient sound crossfades exactly when the video xfade happens — no abrupt
    # switches between different ambient sounds.
    narration_idx = len(segments)
    fade_out_st   = max(0.0, duration - 3.0)
    has_video_seg = any(seg["img"].get("is_video", False) for seg in segments)

    if has_video_seg:
        for i, seg in enumerate(segments):
            seg_dur = seg["end"] - seg["start"]
            if seg["img"].get("is_video", False):
                # Trim looped video audio to this segment's exact duration
                filter_parts.append(
                    f"[{i}:a]atrim=duration={seg_dur:.3f},"
                    f"asetpts=PTS-STARTPTS[va{i}]"
                )
            else:
                # Static image — fill with silence so crossfade chain stays intact
                filter_parts.append(
                    f"anullsrc=r=44100:cl=stereo,"
                    f"atrim=duration={seg_dur:.3f},"
                    f"asetpts=PTS-STARTPTS[va{i}]"
                )

        # Chain all slices with acrossfade matching video xfade duration
        prev = "va0"
        for k in range(1, len(segments)):
            out = f"ac{k}"
            filter_parts.append(
                f"[{prev}][va{k}]"
                f"acrossfade=d={td:.3f}[{out}]"
            )
            prev = out

        # Reduce ambient volume, blend with narration, apply fade in/out
        filter_parts.append(f"[{prev}]volume={AMBIENT_VOL}[ambient]")
        filter_parts.append(
            f"[{narration_idx}:a][ambient]"
            f"amix=inputs=2:normalize=0:duration=first:dropout_transition=0[mixed]"
        )
        filter_parts.append(
            f"[mixed]"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fade_out_st:.3f}:d=3[aout]"
        )
        has_ambient = True
    else:
        has_ambient = False

    return cmd_inputs, ";".join(filter_parts), unique_files, has_ambient

def build_ff_commands_local(segments: list, fps: int, out_w: int, out_h: int, transitions: list,
                            lut_path: Path = None, duration: float = 0.0):
    """Same as build_ff_commands() but uses real file paths (no placeholders)
    and ends filter chain with format=yuv420p for Apple VideoToolbox."""
    td = TRANSITION_DURATION
    filter_parts = []
    unique_files = []
    file_to_idx = {}

    for seg in segments:
        p = seg["img"]["path"]
        if p not in file_to_idx:
            file_to_idx[p] = len(unique_files)
            unique_files.append(p)

    cmd_inputs = []
    for i, seg in enumerate(segments):
        p = seg["img"]["path"]
        if seg["img"].get("is_video", False):
            # Grok 10s video clip — loop indefinitely; xfade + final -t handle duration
            cmd_inputs += [
                "-stream_loop", "-1",
                "-i", str(p)
            ]
        else:
            # Static image (thumbnail)
            cmd_inputs += [
                "-r", str(fps),
                "-loop", "1",
                "-i", str(p)
            ]

    for i in range(len(segments)):
        filter_parts.append(
            f"[{i}:v]"
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={out_w}:{out_h},setsar=1,fps={fps},"
            f"format=yuv420p[v{i}]"
        )

    if len(segments) == 1:
        last_label = "v0"
    else:
        last_label = "v0"
        for i in range(len(segments) - 1):
            t_name = transitions[i % len(transitions)]
            offset = segments[i]["end"] - td
            filter_parts.append(
                f"[{last_label}][v{i+1}]"
                f"xfade=transition={t_name}:duration={td:.3f}:offset={offset:.3f}"
                f"[x{i}]"
            )
            last_label = f"x{i}"

    # yuv420p required by VideoToolbox (not nv12 like QSV)
    if lut_path:
        safe_lut = str(lut_path).replace("'", "\\'")
        lut_filter = f"lut3d='{safe_lut}',"
    else:
        lut_filter = ""
    filter_parts.append(f"[{last_label}]{lut_filter}format=yuv420p[outv]")

    # ── Ambient audio: per-segment with crossfade synced to video transitions ──
    narration_idx = len(segments)
    fade_out_st   = max(0.0, duration - 3.0)
    has_video_seg = any(seg["img"].get("is_video", False) for seg in segments)

    if has_video_seg:
        for i, seg in enumerate(segments):
            seg_dur = seg["end"] - seg["start"]
            if seg["img"].get("is_video", False):
                filter_parts.append(
                    f"[{i}:a]atrim=duration={seg_dur:.3f},"
                    f"asetpts=PTS-STARTPTS[va{i}]"
                )
            else:
                filter_parts.append(
                    f"anullsrc=r=44100:cl=stereo,"
                    f"atrim=duration={seg_dur:.3f},"
                    f"asetpts=PTS-STARTPTS[va{i}]"
                )

        prev = "va0"
        for k in range(1, len(segments)):
            out = f"ac{k}"
            filter_parts.append(
                f"[{prev}][va{k}]"
                f"acrossfade=d={td:.3f}[{out}]"
            )
            prev = out

        filter_parts.append(f"[{prev}]volume={AMBIENT_VOL}[ambient]")
        filter_parts.append(
            f"[{narration_idx}:a][ambient]"
            f"amix=inputs=2:normalize=0:duration=first:dropout_transition=0[mixed]"
        )
        filter_parts.append(
            f"[mixed]"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fade_out_st:.3f}:d=3[aout]"
        )
        has_ambient = True
    else:
        has_ambient = False

    return cmd_inputs, ";".join(filter_parts), unique_files, has_ambient

def build_render_job(segments, audio_path, duration, out_w, out_h, codec_info, lut_path: Path = None):
    transitions = get_shuffled_transitions(len(segments))
    cmd_inputs, fc, unique_files, has_ambient = build_ff_commands(
        segments, codec_info["fps"], out_w, out_h, transitions, lut_path, duration
    )

    all_uploads = unique_files + [audio_path]
    audio_idx = len(unique_files)

    # Resolve LUT placeholder to its upload index
    if lut_path:
        lut_idx = len(all_uploads)
        all_uploads.append(lut_path)
        fc = fc.replace("{lut_0}", f"{{input_{lut_idx}}}")

    cmd = ["-y", "-filter_threads", "8"]
    cmd += cmd_inputs
    cmd += ["-i", f"{{input_{audio_idx}}}"]

    cmd += ["-filter_complex", fc, "-map", "[outv]"]

    if has_ambient:
        # Audio fade + mix already handled inside filter_complex → map [aout] directly
        cmd += ["-map", "[aout]"]
    else:
        # No video audio — apply fade via -af on the raw narration stream
        audio_stream = len(segments)
        af = f"afade=t=in:st=0:d=2,afade=t=out:st={max(0.0, duration - 3.0):.3f}:d=3"
        cmd += ["-map", f"{audio_stream}:a", "-af", af]

    cmd += [
        "-c:v", codec_info["cv"],
        "-preset", codec_info.get("preset", "slow"),
        "-b:v", codec_info["bv"]
    ]
    if codec_info.get("profile"): cmd += ["-profile:v", codec_info["profile"]]
    if codec_info.get("level"):   cmd += ["-level:v", codec_info["level"]]

    cmd += [
        "-c:a", "aac",
        "-b:a", codec_info["ba"],
        "-ar", "48000",
        "-t", str(duration),
        "-movflags", "+faststart",
        "{output_0}"
    ]
    return cmd, all_uploads

def build_local_render_job(segments: list, audio_path: Path, duration: float,
                            out_w: int, out_h: int, codec_info: dict,
                            output_path: Path, lut_path: Path = None) -> list:
    """Build a complete local ffmpeg command for Apple VideoToolbox rendering."""
    transitions = get_shuffled_transitions(len(segments))
    cmd_inputs, fc, unique_files, has_ambient = build_ff_commands_local(
        segments, codec_info["fps"], out_w, out_h, transitions, lut_path, duration
    )

    cmd = ["ffmpeg", "-y", "-filter_threads", "4"]
    cmd += cmd_inputs
    cmd += ["-i", str(audio_path)]
    cmd += ["-filter_complex", fc, "-map", "[outv]"]

    if has_ambient:
        cmd += ["-map", "[aout]"]
    else:
        audio_stream = len(segments)
        af = f"afade=t=in:st=0:d=2,afade=t=out:st={max(0.0, duration - 3.0):.3f}:d=3"
        cmd += ["-map", f"{audio_stream}:a", "-af", af]

    cmd += [
        "-c:v", codec_info["cv"],
        "-b:v", codec_info["bv"],
        "-q:v", str(codec_info["q_v"]),
    ]
    if codec_info.get("allow_sw"):
        cmd += ["-allow_sw", "1"]
    cmd += [
        "-pix_fmt", codec_info["pix_fmt"],
        "-c:a", "aac",
        "-b:a", codec_info["ba"],
        "-ar", "48000",
        "-t", str(duration),
        "-movflags", "+faststart",
        str(output_path),
    ]
    return cmd

# ─────────────────────────────────────────────────────────────────────────────
# Server Execution
# ─────────────────────────────────────────────────────────────────────────────

def render_on_server(job_cmd: list, unique_uploads: list, output_filename: str, output_path: Path, duration: float):
    file_handles = []
    multipart_files = []
    try:
        for fpath in unique_uploads:
            mime = MIME_MAP.get(fpath.suffix.lower(), "application/octet-stream")
            fh = open(fpath, "rb")
            file_handles.append(fh)
            multipart_files.append(("files", (fpath.name, fh, mime)))
            
        resp = requests.post(f"{RENDER_SERVER_URL}/render", files=multipart_files, timeout=120, data={
            "command": json.dumps(job_cmd),
            "outputs": json.dumps([output_filename]),
            "duration": str(duration),
        })
        resp.raise_for_status()
        job_id = resp.json()["job_id"]
        
        # Poll
        last_pct = -1
        while True:
            r = requests.get(f"{RENDER_SERVER_URL}/status/{job_id}", timeout=10)
            data = r.json()
            st = data["status"]
            pct = data.get("progress", 0)
            
            if pct != last_pct:
                bar = "#" * int(40 * pct / 100) + "-" * (40 - int(40 * pct / 100))
                print(f"\r  [{bar}] {pct:3d}%  {data.get('render_time_seconds', 0):.1f}s", end="", flush=True)
                last_pct = pct
                
            if st == "done":
                print()
                break
            elif st == "failed":
                print()
                raise RuntimeError(f"Server Render Exception: {data.get('error')}")
            time.sleep(2)
            
        print(f"  Downloading remote output -> {output_path.name}")
        dl_resp = requests.get(f"{RENDER_SERVER_URL}/download/{job_id}/{output_filename}", stream=True)
        dl_resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in dl_resp.iter_content(chunk_size=1024*1024):
                f.write(chunk)
                
    finally:
        for fh in file_handles: fh.close()
        try: requests.delete(f"{RENDER_SERVER_URL}/job/{job_id}", timeout=5)
        except: pass

def render_locally(cmd: list, duration: float):
    """Run ffmpeg locally and show a real-time progress bar (Apple VideoToolbox)."""
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, bufsize=1)
    time_pattern = re.compile(r'time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})')
    last_pct = -1

    for line in proc.stderr:
        m = time_pattern.search(line)
        if m and duration > 0:
            h, mn, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            elapsed = h * 3600 + mn * 60 + s + cs / 100.0
            pct = min(100, int(elapsed / duration * 100))
            if pct != last_pct:
                bar = "#" * int(40 * pct / 100) + "-" * (40 - int(40 * pct / 100))
                print(f"\r  [{bar}] {pct:3d}%  {elapsed:.1f}s", end="", flush=True)
                last_pct = pct

    proc.wait()
    print()
    if proc.returncode != 0:
        raise RuntimeError(f"Local FFmpeg render failed (exit {proc.returncode})")

def human_size(p: Path):
    s = p.stat().st_size
    for u in ["B", "KB", "MB", "GB"]:
        if s < 1024: return f"{s:.1f} {u}"
        s /= 1024
    return f"{s:.1f} TB"

def check_server_with_retries(n: int = 3) -> bool:
    for attempt in range(1, n + 1):
        print(f"  [attempt {attempt}/{n}] Contacting render server...")
        try:
            resp = requests.get(f"{RENDER_SERVER_URL}/health", timeout=3)
            if resp.headers is not None:
                print("  Render server online.")
                return True
        except Exception:
            pass
        print(f"  Attempt {attempt} failed.")
        if attempt < n:
            time.sleep(2)
    print(f"  Render server unreachable after {n} attempts.")
    return False

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate YouTube + TikTok chapter videos.")
    parser.add_argument("folder", help="Chapter output folder")
    parser.add_argument("--lut", metavar="FILE", help="Optional .cube LUT file for colour grading")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    lut_path = Path(args.lut).expanduser().resolve() if args.lut else None

    if lut_path and not lut_path.exists():
        print(f"Error: LUT file not found: {lut_path}")
        sys.exit(1)

    print(f"\nAnalyzing: {folder}")
    if lut_path:
        print(f"  LUT      : {lut_path.name}")
    
    use_remote = check_server_with_retries(3)
    if not use_remote:
        print("  Falling back to local render (Apple VideoToolbox — Mac Mini M2).")
        
    audio = next((f for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXTENSIONS), None)
    if not audio:
        print("Error: Audio missing.")
        sys.exit(1)
        
    scores     = parse_meta_scores(folder)
    media_data = find_media_with_scores(folder, scores)

    if not media_data:
        print("Error: No thumbnail image or scene MP4s found.")
        print("  Run generate_image_video.py first to generate Grok video clips.")
        sys.exit(1)

    # Warn if scene PNGs exist but no scene MP4s (video generation not done yet)
    scene_mp4s = [m for m in media_data if m["is_video"] and not m["is_thumb"]]
    scene_pngs = [f for f in folder.iterdir()
                  if re.search(r'_\d+_scene\.(png|jpg|webp)$', f.name.lower())]
    if not scene_mp4s and scene_pngs:
        print(f"Error: Found {len(scene_pngs)} scene PNG(s) but no scene MP4s.")
        print("  Grok videos have not been generated yet.")
        print("  Run generate_image_video.py to generate video clips, then re-run this script.")
        sys.exit(1)

    duration = get_audio_duration(audio)
    segments = build_timeline(media_data, duration)

    video_segs = [s for s in segments if s["img"].get("is_video")]
    print(f"\n  Audio    : {audio.name} ({duration / 60:.1f} min)")
    print(f"  Ambient  : {'ON  (' + str(len(video_segs)) + ' video clips at ' + str(int(AMBIENT_VOL*100)) + '% volume)' if video_segs else 'OFF (no video clips)'}")
    print(f"  Timeline Segments ({len(segments)} blocks, min 10.5s each):")
    for i, s in enumerate(segments):
        kind = "image" if not s['img'].get('is_video') else "video"
        loops = (s['end'] - s['start']) / 10.0
        loop_str = f"  ~{loops:.1f}x loop" if s['img'].get('is_video') else ""
        print(f"    [{i+1}] {s['start']:>6.1f}s -> {s['end']:>6.1f}s | {s['img']['path'].name} [{kind}]{loop_str}")

    thumb_img = next((m["path"] for m in media_data if m["is_thumb"]), media_data[0]["path"])
    stem = re.sub(r'_\d+_(thumb|scene).*', '', thumb_img.stem)
    
    out_yt = folder / f"{stem}_youtube.mp4"
    out_tt = folder / f"{stem}_tiktok.mp4"
    
    if out_yt.exists():
        print(f"  Skipping YouTube render — {out_yt.name} already exists.")
    if out_tt.exists():
        print(f"  Skipping TikTok render  — {out_tt.name} already exists.")
    yt_req = not out_yt.exists()
    tt_req = not out_tt.exists()
    
    # Render YouTube
    if yt_req:
        if use_remote:
            print(f"\n[1/2] YouTube Rendering | 1440x2560 (2K) | av1_qsv | 24 FPS  [REMOTE]")
            cmd, files = build_render_job(segments, audio, duration, 1440, 2560, {
                "fps": 24, "cv": "av1_qsv", "bv": "3M", "ba": "256k", "preset": "4"
            }, lut_path)
            render_on_server(cmd, files, out_yt.name, out_yt, duration)
        else:
            print(f"\n[1/2] YouTube Rendering | 1440x2560 (2K) | hevc_videotoolbox | 24 FPS  [LOCAL]")
            cmd = build_local_render_job(segments, audio, duration, 1440, 2560, LOCAL_CODEC_YT, out_yt, None)  # LUT remote-only
            render_locally(cmd, duration)
        print(f"  Saved: {human_size(out_yt)}")

    # Render TikTok
    if tt_req:
        if use_remote:
            print(f"\n[2/2] TikTok Rendering | 1080x1920 | h264_qsv | 30 FPS  [REMOTE]")
            cmd, files = build_render_job(segments, audio, duration, 1080, 1920, {
                "fps": 30, "cv": "h264_qsv", "bv": "5M", "ba": "192k",
                "profile": "high", "level": "4.1"
            }, lut_path)
            render_on_server(cmd, files, out_tt.name, out_tt, duration)
        else:
            print(f"\n[2/2] TikTok Rendering | 1080x1920 | h264_videotoolbox | 30 FPS  [LOCAL]")
            cmd = build_local_render_job(segments, audio, duration, 1080, 1920, LOCAL_CODEC_TT, out_tt, None)  # LUT remote-only
            render_locally(cmd, duration)
        print(f"  Saved: {human_size(out_tt)}")
        
if __name__ == "__main__":
    main()
