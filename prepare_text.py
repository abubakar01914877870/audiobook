"""
prepare_text.py — AI text-generation sub-pipeline.

Runs only the three Claude/Gemini steps for one or more chapter PDFs:
  0. Character Discovery  (character_discovery.py)
  1. Translation          (generate_translation.py)
  2. Video Metadata       (generate_video_meta.py)

Usage:
  # Single chapter
  python prepare_text.py chapter_058.pdf ./output

  # Batch (all PDFs in a folder)
  python prepare_text.py ./chapter_split/ ./output

  # Gemini-first (Claude fallback)
  python prepare_text.py chapter_058.pdf ./output --model=gemini
"""

import fitz
import subprocess
import os
import sys
import argparse
import time
import re
from typing import Optional

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))
from character_discovery import (
    discover_characters_in_chapter,
    load_characters,
    CHARACTERS_JSON_PATH,
)

load_dotenv()


# ---------------------------------------------------------------------------
# PDF helpers
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
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned)).strip()


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
    num_str = f"{int(num_match.group(1)):03d}" if num_match else "000"
    return f"Chapter_{num_str}_{chapter_name}.md"


def get_chapter_num_from_pdf(pdf_path: str) -> int:
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    m = re.search(r'(\d+)', stem)
    return int(m.group(1)) if m else 0


def count_image_prompts_in_meta(meta_path: str) -> int:
    try:
        with open(meta_path, encoding="utf-8") as f:
            content = f.read()
        n = len(re.findall(r'###\s*Image Prompt\s+\d+', content))
        if n:
            return n
        return 1 if re.search(r'###\s*Image Generation Prompt', content) else 0
    except Exception:
        return 0


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


def _print_task_report(task_name: str, status: str, elapsed: float, model: str = "", details: list = None):
    bar = "✓" if status == "ok" else ("–" if status == "skip" else "✗")
    model_tag = f"  [{model}]" if model else ""
    print(f"\n  {'─'*52}")
    print(f"  {bar}  {task_name:<26}  {_fmt(elapsed):>8}{model_tag}")
    for line in (details or []):
        print(f"      {line}")
    print(f"  {'─'*52}")


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


# ---------------------------------------------------------------------------
# Core: process one PDF through the 3 AI steps
# ---------------------------------------------------------------------------

def process_single_pdf(pdf_path: str, output_base: str, failed_models: set,
                       model: str = "claude") -> tuple:
    """Run character discovery, translation, and video metadata for one PDF.

    Steps:
      0. Character Discovery  (in-process via character_discovery module)
      1. Translation          (generate_translation.py subprocess)
      2. Video Metadata       (generate_video_meta.py subprocess)

    Returns (success: bool, timings: list).
    """
    chapter_num = get_chapter_num_from_pdf(pdf_path)
    output_dir = os.path.join(output_base, f"ch_{chapter_num}")
    os.makedirs(output_dir, exist_ok=True)
    timings = []

    script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline")

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
        result = subprocess.run(trans_cmd, text=True)
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
        print(f"\nSkipping translation — '{os.path.basename(output_md)}' already exists.")
        timings.append(("1. Translation", _t_elapsed, "skip", ""))
        _print_task_report("Translation", "skip", _t_elapsed, "", [
            f"Already exists: {os.path.basename(output_md)}"
        ])

    # ---------------------------------------------------------
    # STEP 2: Video Metadata
    # ---------------------------------------------------------
    stem = os.path.splitext(output_filename)[0]
    meta_md = os.path.join(output_dir, f"{stem}_meta.md")

    print(f"\n--- Running Metadata Generation: {pdf_path} ---")
    t0 = time.time()
    meta_cmd = [sys.executable, os.path.join(script_dir, "generate_video_meta.py"), pdf_path, output_dir]
    meta_cmd += ["--primary-model", model]
    if failed_models:
        meta_cmd += ["--skip-models", ",".join(sorted(failed_models))]
    result = subprocess.run(meta_cmd, text=True)
    _m_elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"Metadata generation exited with code {result.returncode}.")
        timings.append(("2. Video Metadata", _m_elapsed, "fail", ""))
        _print_task_report("Video Metadata", "fail", _m_elapsed, "", [f"Exit code: {result.returncode}"])
        return False, timings
    prompt_count = count_image_prompts_in_meta(meta_md)
    timings.append(("2. Video Metadata", _m_elapsed, "ok", "external"))
    _print_task_report("Video Metadata", "ok", _m_elapsed, "external", [
        f"File: {os.path.basename(meta_md)}  ({prompt_count} image prompts)"
    ])

    _print_timing_report(timings, os.path.basename(pdf_path))
    return True, timings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "AI text-generation sub-pipeline: character discovery + translation + video metadata.\n"
            "  Single:  python prepare_text.py chapter_058.pdf ./output\n"
            "  Batch:   python prepare_text.py ./chapter_split/ ./output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Single PDF file OR folder containing PDFs")
    parser.add_argument("output_folder", help="Base output directory (ch_N subfolders created here)")
    parser.add_argument(
        "--model", choices=["claude", "gemini"], default="claude",
        help=(
            "Primary AI model (default: claude). "
            "claude = Claude first, Gemini fallback. "
            "gemini = Gemini first, Claude fallback."
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
        ok, _ = process_single_pdf(input_path, output_base, failed_models, args.model)
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
            ok, timings = process_single_pdf(pdf_path, output_base, failed_models, args.model)
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
