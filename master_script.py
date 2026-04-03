import fitz
import subprocess
import os
import sys
import signal
import argparse
import time
import re
from typing import Optional

from dotenv import load_dotenv

# Add pipeline/ to path so character_discovery can be imported
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))
from character_discovery import (
    discover_characters_in_chapter,
    load_characters,
    CHARACTERS_JSON_PATH,
)

load_dotenv()

YOUTUBE_PLAYLIST = "Lord of Mysteries- Clown vol 1"


def run_step(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, forwarding Ctrl+C and killing it on interrupt."""
    proc = subprocess.Popen(cmd, text=True, **kwargs)
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  [Ctrl+C] Stopping subprocess...")
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode)


# ---------------------------------------------------------------------------
# PDF helpers (used to resolve output filename early for skip checks)
# ---------------------------------------------------------------------------

def clean_pdf_text(text):
    text = re.sub(r'([a-zA-Z])-\n+([a-zA-Z])', r'\1\2', text)
    text = re.sub(r'[ \t]+', ' ', text)
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r'[-–—]?\s*\d+\s*[-–—]?', stripped):
            continue
        cleaned.append(stripped)
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned))
    return result.strip()


def extract_text(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        return clean_pdf_text(text)
    except Exception as e:
        print(f"Error reading PDF {pdf_path}: {e}")
        return None


def extract_chapter_name_from_text(text):
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r'^(chapter\s+)?\d+$', line, re.IGNORECASE):
            continue
        name = re.sub(r'[^\w\s-]', '', line)
        name = re.sub(r'_', ' ', name)
        name = re.sub(r'\s+', ' ', name).strip()
        name = re.sub(r'^chapter\s+\d+[\s:\-\.]*', '', name, flags=re.IGNORECASE).strip()
        if name:
            return name[:60]
    return "Untitled"


def build_output_filename(pdf_path, chapter_name):
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    num_match = re.search(r'(\d+)', stem)
    if num_match:
        chapter_num = int(num_match.group(1))
        num_str = f"{chapter_num:03d}"
    else:
        num_str = "000"
    return f"Chapter_{num_str}_{chapter_name}.md"


def get_chapter_num_from_pdf(pdf_path: str) -> int:
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    m = re.search(r'(\d+)', stem)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# File finders
# ---------------------------------------------------------------------------

def count_image_prompts_in_meta(meta_path: str) -> int:
    try:
        with open(meta_path, encoding="utf-8") as f:
            content = f.read()
        n = len(re.findall(r'###\s*Image Prompt\s+\d+', content))
        if n:
            return n
        if re.search(r'###\s*Image Generation Prompt', content):
            return 1
        return 0
    except Exception:
        return 0


def find_thumbnail_image(output_dir: str) -> Optional[str]:
    for f in sorted(os.listdir(output_dir)):
        if re.search(r'_(thumb|thumbnail)\.(png|jpg|jpeg|webp)$', f.lower()):
            return os.path.join(output_dir, f)
    return None


def find_video_file(output_dir: str, suffix: str) -> Optional[str]:
    for f in sorted(os.listdir(output_dir)):
        if f.endswith(f"_{suffix}.mp4"):
            return os.path.join(output_dir, f)
    return None


# ---------------------------------------------------------------------------
# Timing / reporting
# ---------------------------------------------------------------------------

def _fmt(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _print_timing_report(timings: list, chapter_label: str):
    total = sum(t for _, t, _, _ in timings)
    print(f"\n{'─'*62}")
    print(f"  Time Report — {chapter_label}")
    print(f"{'─'*62}")
    for name, elapsed, status, model in timings:
        bar = "✓" if status == "ok" else ("–" if status == "skip" else "✗")
        model_tag = f"  [{model}]" if model else ""
        print(f"  {bar}  {name:<28}  {_fmt(elapsed):>10}{model_tag}")
    print(f"{'─'*62}")
    print(f"     {'TOTAL':<28}  {_fmt(total):>10}")
    print(f"{'─'*62}")


def _print_task_report(task_name: str, status: str, elapsed: float, model: str = "", details: list = None):
    bar = "✓" if status == "ok" else ("–" if status == "skip" else "✗")
    model_tag = f"  [{model}]" if model else ""
    print(f"\n  {'─'*52}")
    print(f"  {bar}  {task_name:<26}  {_fmt(elapsed):>8}{model_tag}")
    for line in (details or []):
        print(f"      {line}")
    print(f"  {'─'*52}")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_single_pdf(pdf_path: str, output_base: str, failed_models: set,
                       youtube_playlist: str = "", path: str = "image", model: str = "claude",
                       render: str = "intel"):
    """Run all pipeline steps for one PDF in sequence.

    Steps (--path=image, default):
      0. Character Discovery
      1. Translation       (generate_translation.py — Claude first, Gemini fallback)
      2. Video Metadata    (generate_video_meta.py — AI decides scene count)
      3. Audio Generation  (generate_audio.py)
      4. Image Generation  (generate_image.py — Gemini scene images, count from meta)
      5. Video Render      (render_images.py — static image timeline)
      6. YouTube Upload    (upload_youtube.py)
      7. TikTok Upload     (upload_tiktok.py)

    Steps (--path=video):
      0-3. Same as above
      4. Image Generation  (generate_image.py — Gemini scene images, count from meta)
      5. Video Generation  (generate_video.py  — Grok scene MP4 clips)
      6. Video Render      (render_videos.py — Grok clip timeline)
      7. YouTube Upload    (upload_youtube.py)
      8. TikTok Upload     (upload_tiktok.py)

    Returns (success: bool, timings: list).
    """
    chapter_num = get_chapter_num_from_pdf(pdf_path)
    output_dir = os.path.join(output_base, f"ch_{chapter_num}")
    os.makedirs(output_dir, exist_ok=True)
    timings = []   # (task_name, elapsed_seconds, status, model)

    script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline")

    # Resolve output filename early — needed to check if translation exists
    text = extract_text(pdf_path)
    if not text:
        print(f"Error: No text extracted from {pdf_path}.")
        return False, timings

    chapter_name = extract_chapter_name_from_text(text)
    output_filename = build_output_filename(pdf_path, chapter_name)
    output_md = os.path.join(output_dir, output_filename)
    chapter_filename_stem = os.path.splitext(output_filename)[0]

    # ---------------------------------------------------------
    # STEP 0: Character Discovery
    # ---------------------------------------------------------
    print(f"\n--- Running Character Discovery: chapter {chapter_num} ---")
    t0 = time.time()
    _before_count = len(load_characters(CHARACTERS_JSON_PATH).get("characters", {}))
    _disc_ok, characters_in_chapter, _disc_model = discover_characters_in_chapter(
        text, chapter_num, output_dir, CHARACTERS_JSON_PATH, failed_models, chapter_filename_stem,
        primary_model=model,
    )
    _disc_status = "skip" if _disc_model == "skip" else "ok"
    _disc_elapsed = time.time() - t0
    timings.append(("0. Character Discovery", _disc_elapsed, _disc_status, _disc_model))
    _after_count = len(load_characters(CHARACTERS_JSON_PATH).get("characters", {}))
    _disc_details = [
        f"Characters in chapter ({len(characters_in_chapter)}): "
        + (", ".join(characters_in_chapter) if characters_in_chapter else "none"),
    ]
    if _disc_status != "skip":
        _disc_details.append(f"New to DB: {_after_count - _before_count}  |  Total in DB: {_after_count}")
    else:
        _disc_details.append(f"Total in DB: {_after_count}")
    _print_task_report("Character Discovery", _disc_status, _disc_elapsed, _disc_model, _disc_details)

    # ---------------------------------------------------------
    # STEP 1: Translation
    # ---------------------------------------------------------
    t0 = time.time()
    if not os.path.exists(output_md):
        print(f"\n--- Running Translation: {pdf_path} ---")
        trans_cmd = [sys.executable, os.path.join(script_dir, "generate_translation.py"), pdf_path, output_dir]
        trans_cmd += ["--primary-model", model]
        if failed_models:
            trans_cmd += ["--skip-models", ",".join(sorted(failed_models))]
        result = run_step(trans_cmd)
        _t_elapsed = time.time() - t0
        if result.returncode != 0:
            print(f"Translation exited with code {result.returncode}.")
            timings.append(("1. Translation", _t_elapsed, "fail", ""))
            _print_task_report("Translation", "fail", _t_elapsed, "", [f"Exit code: {result.returncode}"])
            return False, timings
        _t_order = "claude→gemini" if model == "claude" else "gemini→claude"
        timings.append(("1. Translation", _t_elapsed, "ok", _t_order))
        _print_task_report("Translation", "ok", _t_elapsed, _t_order, [
            f"File: {os.path.basename(output_md)}"
        ])
    else:
        _t_elapsed = time.time() - t0
        print(f"\nSkipping translation — '{output_md}' already exists.")
        timings.append(("1. Translation", _t_elapsed, "skip", ""))
        _print_task_report("Translation", "skip", _t_elapsed, "", [
            f"Already exists: {os.path.basename(output_md)}"
        ])

    # ---------------------------------------------------------
    # STEP 2: Video Metadata (AI decides scene count from text)
    # ---------------------------------------------------------
    stem = os.path.splitext(output_filename)[0]
    meta_md = os.path.join(output_dir, f"{stem}_meta.md")

    print(f"\n--- Running Metadata Generation: {pdf_path} ---")
    t0 = time.time()
    meta_cmd = [sys.executable, os.path.join(script_dir, "generate_video_meta.py"), pdf_path, output_dir]
    meta_cmd += ["--primary-model", model]
    if failed_models:
        meta_cmd += ["--skip-models", ",".join(sorted(failed_models))]
    result = run_step(meta_cmd)
    _m_elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"Metadata generation exited with code {result.returncode}.")
        timings.append(("2. Metadata", _m_elapsed, "fail", ""))
        _print_task_report("Video Metadata", "fail", _m_elapsed, "", [f"Exit code: {result.returncode}"])
        return False, timings
    prompt_count = count_image_prompts_in_meta(meta_md)
    timings.append(("2. Metadata", _m_elapsed, "ok", "external"))
    _print_task_report("Video Metadata", "ok", _m_elapsed, "external", [
        f"File: {os.path.basename(meta_md)}  ({prompt_count} image prompts, AI-decided)"
    ])

    # ---------------------------------------------------------
    # STEP 3: Audio Generation
    # ---------------------------------------------------------
    print(f"\n--- Running Audio Generation: {output_dir} ---")
    t0 = time.time()
    result = run_step(
        [sys.executable, os.path.join(script_dir, "generate_audio.py"), output_dir]
    )
    _au_elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"Audio generation exited with code {result.returncode}.")
        timings.append(("3. Audio Generation", _au_elapsed, "fail", ""))
        _print_task_report("Audio Generation", "fail", _au_elapsed, "", [f"Exit code: {result.returncode}"])
        return False, timings
    timings.append(("3. Audio Generation", _au_elapsed, "ok", ""))
    _print_task_report("Audio Generation", "ok", _au_elapsed, "", [])

    # ---------------------------------------------------------
    # STEP 4: Image Generation (Gemini — always required for both paths)
    # ---------------------------------------------------------
    print(f"\n--- Running Image Generation: {output_dir} ---")
    t0 = time.time()
    result = run_step(
        [sys.executable, os.path.join(script_dir, "generate_image.py"), output_dir]
    )
    _ig_elapsed = time.time() - t0
    # exit 2 = image retries exhausted (hard block)
    if result.returncode == 2:
        print(f"Image generation exited with code {result.returncode} (images incomplete).")
        timings.append(("4. Image Generation", _ig_elapsed, "fail", ""))
        _print_task_report("Image Generation", "fail", _ig_elapsed, "", [f"Exit code: {result.returncode} — images missing"])
        return False, timings
    if result.returncode != 0:
        print(f"Image generation exited with code {result.returncode}.")
        timings.append(("4. Image Generation", _ig_elapsed, "fail", ""))
        _print_task_report("Image Generation", "fail", _ig_elapsed, "", [f"Exit code: {result.returncode}"])
        return False, timings
    timings.append(("4. Image Generation", _ig_elapsed, "ok", "gemini"))
    _print_task_report("Image Generation", "ok", _ig_elapsed, "gemini", ["All scene images generated."])

    # ---------------------------------------------------------
    # STEP 5 (video path only): Grok Video Generation
    # ---------------------------------------------------------
    if path == "video":
        print(f"\n--- Running Grok Video Generation: {output_dir} ---")
        t0 = time.time()
        result = run_step(
            [sys.executable, os.path.join(script_dir, "generate_video.py"), output_dir]
        )
        _gv_elapsed = time.time() - t0
        if result.returncode != 0:
            print(f"Video generation exited with code {result.returncode}.")
            timings.append(("5. Grok Video Gen", _gv_elapsed, "fail", ""))
            _print_task_report("Grok Video Gen", "fail", _gv_elapsed, "", [f"Exit code: {result.returncode}"])
            return False, timings
        timings.append(("5. Grok Video Gen", _gv_elapsed, "ok", "grok"))
        _print_task_report("Grok Video Gen", "ok", _gv_elapsed, "grok", ["All Grok scene videos generated."])

    # ---------------------------------------------------------
    # STEP 5/6: Render (YouTube + TikTok)
    # ---------------------------------------------------------
    render_script = "render_videos.py" if path == "video" else "render_images.py"
    step_label = "6. Video Render" if path == "video" else "5. Video Render"
    print(f"\n--- Running Video Render [{path} path, {render}]: {output_dir} ---")
    t0 = time.time()
    result = run_step(
        [sys.executable, os.path.join(script_dir, render_script), output_dir, "--render", render]
    )
    _vg_elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"Video render exited with code {result.returncode}.")
        timings.append((step_label, _vg_elapsed, "fail", ""))
        _print_task_report("Video Render", "fail", _vg_elapsed, "", [f"Exit code: {result.returncode}"])
        return False, timings
    yt_out = find_video_file(output_dir, "youtube")
    tt_out = find_video_file(output_dir, "tiktok")
    _vg_details = []
    if yt_out:
        _vg_details.append(f"YouTube: {os.path.basename(yt_out)}")
    if tt_out:
        _vg_details.append(f"TikTok:  {os.path.basename(tt_out)}")
    timings.append((step_label, _vg_elapsed, "ok", ""))
    _print_task_report("Video Render", "ok", _vg_elapsed, "", _vg_details)

    # ---------------------------------------------------------
    # STEP 6: YouTube Upload
    # ---------------------------------------------------------
    yt_video = find_video_file(output_dir, "youtube")
    t0 = time.time()
    if not yt_video:
        print(f"\n--- Skipping YouTube Upload (no video found) ---")
        print(f"  Run manually: python upload_youtube.py \"{output_dir}\"")
        _yt_elapsed = time.time() - t0
        timings.append(("6. YouTube Upload", _yt_elapsed, "skip", ""))
        _print_task_report("YouTube Upload", "skip", _yt_elapsed, "", ["No YouTube video — run video generation first."])
    else:
        print(f"\n--- Running YouTube Upload: {output_dir} ---")
        yt_cmd = [sys.executable, os.path.join(script_dir, "upload_youtube.py"), output_dir]
        if youtube_playlist:
            yt_cmd += ["--playlist", youtube_playlist]
        result = run_step(yt_cmd)
        _yt_elapsed = time.time() - t0
        if result.returncode != 0:
            print(f"YouTube upload exited with code {result.returncode}.")
            timings.append(("6. YouTube Upload", _yt_elapsed, "fail", ""))
            _print_task_report("YouTube Upload", "fail", _yt_elapsed, "", [f"Exit code: {result.returncode}"])
            return False, timings
        timings.append(("6. YouTube Upload", _yt_elapsed, "ok", ""))
        _print_task_report("YouTube Upload", "ok", _yt_elapsed, "", [
            f"Video: {os.path.basename(yt_video)}"
            + (f"  |  Playlist: {youtube_playlist}" if youtube_playlist else "")
        ])

    # ---------------------------------------------------------
    # STEP 7: TikTok Upload
    # ---------------------------------------------------------
    tt_video = find_video_file(output_dir, "tiktok")
    t0 = time.time()
    if not tt_video:
        print(f"\n--- Skipping TikTok Upload (no video found) ---")
        print(f"  Run manually: python upload_tiktok.py \"{output_dir}\"")
        _tt_elapsed = time.time() - t0
        timings.append(("7. TikTok Upload", _tt_elapsed, "skip", ""))
        _print_task_report("TikTok Upload", "skip", _tt_elapsed, "", ["No TikTok video — run video generation first."])
    else:
        print(f"\n--- Running TikTok Upload: {output_dir} ---")
        result = run_step(
            [sys.executable, os.path.join(script_dir, "upload_tiktok.py"), output_dir]
        )
        _tt_elapsed = time.time() - t0
        if result.returncode != 0:
            print(f"TikTok upload exited with code {result.returncode}.")
            timings.append(("7. TikTok Upload", _tt_elapsed, "fail", ""))
            _print_task_report("TikTok Upload", "fail", _tt_elapsed, "", [f"Exit code: {result.returncode}"])
            return False, timings
        timings.append(("7. TikTok Upload", _tt_elapsed, "ok", ""))
        _print_task_report("TikTok Upload", "ok", _tt_elapsed, "", [f"Video: {os.path.basename(tt_video)}"])

    _print_timing_report(timings, os.path.basename(pdf_path))
    return True, timings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Run the full audiobook pipeline for one or more chapters.\n"
            "  Single:  python master_script.py chapter_01.pdf ./output\n"
            "  Batch:   python master_script.py ./chapter_split/ ./output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Single PDF file OR folder containing PDFs")
    parser.add_argument("output_folder", help="Base output directory (ch_N subfolders created here)")
    parser.add_argument(
        "--playlist", default=YOUTUBE_PLAYLIST,
        help=f"YouTube playlist name (default: \"{YOUTUBE_PLAYLIST}\"). Pass empty string to skip."
    )
    parser.add_argument(
        "--model", choices=["claude", "gemini"], default="claude",
        help=(
            "Primary AI model for text generation (default: claude). "
            "claude = Claude first, Gemini fallback. "
            "gemini = Gemini first, Claude fallback."
        )
    )
    parser.add_argument(
        "--path", choices=["image", "video"], default="image",
        help=(
            "Pipeline render path (default: image). "
            "image = generate_image.py → render_video_images.py (static Gemini images). "
            "video = generate_image.py → generate_video.py → render_video_videos.py (Grok clips)."
        )
    )
    parser.add_argument(
        "--render", choices=["intel", "apple"], default="intel",
        help=(
            "Render target (default: intel). "
            "intel = Intel QSV remote server (falls back to apple if unreachable). "
            "apple = Apple VideoToolbox local (M2)."
        )
    )
    args = parser.parse_args()

    input_path = args.input
    output_base = args.output_folder

    if not os.path.exists(input_path):
        print(f"Error: Input path '{input_path}' not found.")
        sys.exit(1)

    os.makedirs(output_base, exist_ok=True)
    failed_models: set = set()

    if os.path.isfile(input_path):
        if not input_path.lower().endswith(".pdf"):
            print(f"Error: '{input_path}' is not a PDF file.")
            sys.exit(1)
        ok, _ = process_single_pdf(input_path, output_base, failed_models, args.playlist, args.path, args.model, args.render)
        if not ok:
            sys.exit(1)
        print("\nAll tasks completed successfully.")

    elif os.path.isdir(input_path):
        pdf_files = sorted(f for f in os.listdir(input_path) if f.lower().endswith(".pdf"))
        if not pdf_files:
            print(f"Error: No PDF files found in '{input_path}'.")
            sys.exit(1)

        print(f"Found {len(pdf_files)} PDF(s) in '{input_path}':")
        for f in pdf_files:
            print(f"  {f}")

        task_totals: dict = {}
        batch_total = 0.0
        failed = []

        for i, fname in enumerate(pdf_files, 1):
            pdf_path = os.path.join(input_path, fname)
            print(f"\n{'='*60}")
            print(f"[{i}/{len(pdf_files)}] Processing: {fname}")
            print(f"{'='*60}")
            ok, timings = process_single_pdf(pdf_path, output_base, failed_models, args.playlist, args.path, args.model, args.render)
            for name, elapsed, _, _m in timings:
                task_totals[name] = task_totals.get(name, 0.0) + elapsed
                batch_total += elapsed
            if not ok:
                print(f"  FAILED: {fname}")
                failed.append(fname)

        print(f"\n{'═'*52}")
        print(f"  BATCH TIME REPORT — {len(pdf_files)} chapter(s)")
        print(f"{'═'*52}")
        for name, total in task_totals.items():
            print(f"  {name:<28}  {_fmt(total):>10}")
        print(f"{'─'*52}")
        print(f"  {'GRAND TOTAL':<28}  {_fmt(batch_total):>10}")
        print(f"{'═'*52}")

        print(f"\nBatch complete. {len(pdf_files) - len(failed)}/{len(pdf_files)} succeeded.")
        if failed:
            print("Failed files:")
            for f in failed:
                print(f"  {f}")
            sys.exit(1)
        print("All tasks completed successfully.")

    else:
        print(f"Error: '{input_path}' is neither a file nor a directory.")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
