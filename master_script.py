import fitz
import subprocess
import os
import sys
import argparse
import time
import re
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# ── Gemini model priority list ─────────────────────────────────────────────────
# Best → least capable. Tried in order; any that fails is skipped for the
# rest of the run via failed_models — no slow pre-checking needed.
GEMINI_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


def get_available_models() -> list:
    return list(GEMINI_MODELS)

def clean_pdf_text(text):
    """Remove PDF noise (page numbers, repeated blanks) to reduce input tokens."""
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

def clean_llm_output(text: str) -> str:
    """Strip ANSI codes and unwrap markdown code blocks from CLI output."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)
    code_blocks = re.findall(r'```(?:markdown)?\n?(.*?)\n?```', text, re.DOTALL)
    if code_blocks:
        code_blocks.sort(key=len, reverse=True)
        text = code_blocks[0]
    return text.strip()


def call_claude_cli(prompt: str, retries: int = 3) -> Optional[str]:
    """Call the Claude CLI. Returns cleaned text on success, None on any failure.
    Retries up to `retries` times with exponential backoff on transient failures.
    """
    for attempt in range(1, retries + 1):
        try:
            print(f"  [claude] Attempt {attempt}/{retries}...")
            result = subprocess.run(
                ["claude", "-p", prompt, "--output-format", "text"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=1800,
            )
            if result.returncode == 130:
                raise KeyboardInterrupt
            if result.returncode == 0:
                return clean_llm_output(result.stdout)
            print(f"  [claude] CLI error (exit {result.returncode}): {result.stderr.strip()[:120]}")
            if attempt < retries:
                wait = 10 * attempt
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
        except subprocess.TimeoutExpired:
            print(f"  [claude] Timed out (attempt {attempt}/{retries}).")
            if attempt < retries:
                print("  Retrying...")
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(1)
        except FileNotFoundError:
            print("  [claude] 'claude' CLI not found in PATH. Skipping Claude.")
            return None
        except Exception as e:
            print(f"  [claude] Unexpected error: {e}")
            if attempt < retries:
                wait = 10 * attempt
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
    print("  [claude] All retry attempts exhausted.")
    return None


def call_gemini_cli(model: str, prompt: str) -> Optional[str]:
    """Call the Gemini CLI. Returns cleaned text on success, None on any failure."""
    try:
        result = subprocess.run(
            ["gemini", "-m", model, "-p", prompt, "-y"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        if result.returncode == 130:
            raise KeyboardInterrupt
        if result.returncode == 0:
            return clean_llm_output(result.stdout)
        # Non-zero exit — quota exhausted or model error
        stderr = result.stderr.strip()
        if "quota" in stderr.lower() or "429" in stderr or "exhausted" in stderr.lower():
            print(f"  [{model}] Quota exhausted.")
        else:
            print(f"  [{model}] CLI error (exit {result.returncode}): {stderr[:120]}")
        return None
    except subprocess.TimeoutExpired:
        print(f"  [{model}] Timed out.")
        return None
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    except Exception as e:
        print(f"  [{model}] Unexpected error: {e}")
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


def format_for_narration(text: str) -> str:
    """Clean LLM translation output for audio narration.

    - Strip trailing whitespace from every line
    - Collapse 3+ consecutive blank lines down to 2
      (one visible paragraph gap — right for TTS pacing)
    - Strip leading/trailing blank lines from the whole text
    """
    lines = [line.rstrip() for line in text.splitlines()]
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))
    return result.strip()


def run_with_fallback(valid_models: list, failed_models: set, prompt: str, task: str) -> Tuple[Optional[str], Optional[str]]:
    """Try each model in order via the Gemini CLI, skipping any in failed_models.

    Returns (result_text, model_used) on success, or (None, None) if all fail.
    Any model that fails is added to failed_models and automatically skipped
    in all subsequent steps of the same run.
    """
    for model in valid_models:
        if model in failed_models:
            print(f"  Skipping {model} (failed earlier this run).")
            continue
        print(f"  Trying {model} for {task}...")
        result = call_gemini_cli(model, prompt)
        if result:
            return result, model
        print(f"  {model} failed — adding to skip list.")
        failed_models.add(model)
    return None, None


def build_output_filename(pdf_path, chapter_name):
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    num_match = re.search(r'(\d+)', stem)
    if num_match:
        chapter_num = int(num_match.group(1))
        num_str = f"{chapter_num:03d}"
    else:
        num_str = "000"
    return f"Chapter_{num_str}_{chapter_name}.md"

def get_valid_models() -> list:
    """Return the full model list. No slow pre-checking — failures are caught
    live by call_gemini_cli and the model is added to failed_models at that point."""
    models = get_available_models()
    print(f"Models (priority order): {', '.join(models)}")
    return models

def get_chapter_num_from_pdf(pdf_path: str) -> int:
    """Extract the chapter number from a PDF filename like chapter_19_Title.pdf."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    m = re.search(r'(\d+)', stem)
    return int(m.group(1)) if m else 0


def _fmt(seconds: float) -> str:
    """Format seconds as  1h 23m 45s  /  4m 05s  /  9s."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _print_timing_report(timings: list, chapter_label: str):
    """Print a per-task timing table plus total for one chapter."""
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


def process_single_pdf(pdf_path: str, output_base: str, valid_models: list, failed_models: set):
    """Run all pipeline steps for one PDF. Creates ch_N subfolder inside output_base.
    Returns (success: bool, timings: list) where timings is a list of (name, seconds, status)."""
    chapter_num = get_chapter_num_from_pdf(pdf_path)
    output_dir = os.path.join(output_base, f"ch_{chapter_num}")
    os.makedirs(output_dir, exist_ok=True)
    timings = []   # (task_name, elapsed_seconds, status, model)  status: ok | skip | fail

    # ---------------------------------------------------------
    # PART 1: Translation
    # ---------------------------------------------------------
    text = extract_text(pdf_path)
    if not text:
        print(f"Error: No text extracted from {pdf_path}.")
        return False, timings

    chapter_name = extract_chapter_name_from_text(text)
    output_filename = build_output_filename(pdf_path, chapter_name)
    output_md = os.path.join(output_dir, output_filename)

    translate_system_prompt = """Translate the English novel chapter below into Bengali (Cholitobhasha).
Role: Expert Literary Translator
Style: Muhammed Zafar Iqbal — simple, fluid, teen-friendly. No archaic or Sanskrit-heavy words.
Pronouns: সে/তুমি only. Never আপনি/তিনি.
Names: keep consistent. Novel/chapter titles: keep original format.
English terms: Use English words as little as possible. When keeping an English term, do not add the Bengali translation next to it. Just write the English word. Never use the "Bengali word (English_word)" format.
Numbers: Translate all numbers into Bengali words (e.g., 10 -> দশ, 1000 -> এক হাজার, 1203 -> বারোশ তিন) instead of keeping them as digits.
Dialogue: natural, direct, colloquial.
Formatting: Format the output optimally for a voice-over artist reading an audio story. Add appropriate paragraph breaks, empty lines, and spacing to indicate natural pauses, breath spaces, and scene transitions. Make it highly readable for narration.
Output: full translation only — no summary, no commentary, no preamble."""

    full_translate_prompt = translate_system_prompt + "\n\n=== Original English Text ===\n\n" + text

    t0 = time.time()
    if not os.path.exists(output_md):
        print(f"\n--- Processing Translation: {pdf_path} ---")
        print(f"Chapter name detected: '{chapter_name}'")

        print("  Trying Claude CLI for translation...")
        translated_text = call_claude_cli(full_translate_prompt, retries=1)
        if translated_text:
            used_model = "claude"
        else:
            print("  Claude failed — falling back to Gemini models...")
            translated_text, used_model = run_with_fallback(
                valid_models, failed_models, full_translate_prompt, "translation"
            )

        if not translated_text:
            print("All models (Claude + Gemini) failed for translation.")
            timings.append(("1. Translation", time.time() - t0, "fail", ""))
            return False, timings

        cleaned = format_for_narration(translated_text)
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"Success with {used_model}! Saved: {output_md} ({os.path.getsize(output_md):,} bytes)")
        timings.append(("1. Translation", time.time() - t0, "ok", used_model))
    else:
        print(f"\nSkipping translation, output '{output_md}' already exists.")
        timings.append(("1. Translation", time.time() - t0, "skip", ""))

    # ---------------------------------------------------------
    # PART 2: Video Metadata Generation
    # ---------------------------------------------------------
    stem = os.path.splitext(output_filename)[0]
    meta_md = os.path.join(output_dir, f"{stem}_meta.md")

    t0 = time.time()
    if os.path.exists(meta_md):
        print(f"Skipping metadata generation, '{meta_md}' already exists.")
        timings.append(("2. Metadata", time.time() - t0, "skip", ""))
    else:
        print(f"\n--- Processing Metadata: {pdf_path} ---")

        num_match = re.search(r'(\d+)', stem)
        chapter_num_str = num_match.group(1) if num_match else ""
        chapter_name_meta = stem.replace(f"Chapter_{chapter_num_str}_", "").replace("_", " ") if chapter_num_str else stem

        # Fixed description block — written once, not generated by LLM
        fixed_description = f"""Lord of the Mysteries — Chapter {chapter_num_str}: {chapter_name_meta}
মূল লেখক: Cuttlefish That Loves Diving | বাংলায় পড়ুন: https://kalponic.web.app/

#lordofthemysteries #lotm #kleinmoretti #thefool #donghua #webnovel #darkfantasy #fantasybooks #booktok #wuxia #chinesenovel #animestory #fantasyanime #banglaudiobook #banglaaudiobook #bengaliaudiobook #banglagolpo #bangla #bengali #golpo"""

        meta_system_prompt = f"""You are an expert AI assistant generating metadata for a Bengali YouTube audiobook channel.
The following text is the original English chapter of the novel "Lord of the Mysteries".

Based ONLY on this text, generate a single response with two sections.

═══════════════════════════════════════════════════
SECTION 1 — IMAGE GENERATION PROMPT
═══════════════════════════════════════════════════

Identify the single most visually dramatic or emotionally powerful moment in this chapter.
Write an English image generation prompt as one flowing paragraph in this exact order:
style → camera → scene → lighting → palette → mood → text overlay.

Rules:
- Art style: Dark fantasy manga illustration, rich linework, painterly shading, Studio Trigger / Wit Studio aesthetic, 9:16 portrait for mobile.
- Camera: choose the shot type that best fits the scene's emotional weight (e.g. dramatic low-angle portrait, wide atmospheric establishing shot, close-up with shallow depth of field).
- Scene: describe characters (appearance, expression, posture, clothing) and environment with specific detail. Victorian/Edwardian-era setting. Gothic mystery atmosphere. PG-13 safe — no gore, no sexual content, no self-harm.
- Lighting: be specific (e.g. "warm amber gas-lamp chiaroscuro", "cold moonlight rim-lighting with volumetric fog").
- Palette: name 3–4 dominant colors.
- Mood: one sentence.
- Text overlay: the image MUST include this exact Bengali text as glowing, stylized golden typography integrated naturally in the upper area:
  "অধ্যায় {chapter_num_str}: [Bengali translation of '{chapter_name_meta}']"

Output: the prompt as a single paragraph only — no bullet points, no explanation.

═══════════════════════════════════════════════════
SECTION 2 — YOUTUBE VIDEO TITLE
═══════════════════════════════════════════════════

Write one engaging Bengali video title. It MUST include the chapter number ({chapter_num_str}) and the chapter name in Bengali.
Keep it short, punchy, and intriguing — something that makes a viewer want to click.

Output Format — use EXACTLY this Markdown structure, nothing else:

### Image Generation Prompt
[Single paragraph prompt here]

### YouTube Metadata
**Title:** [Bengali title]

**Description:**
{fixed_description}
"""

        full_meta_prompt = meta_system_prompt + "\n\n=== Original English Chapter Text ===\n\n" + text

        # Try Claude first (allow longer timeout — it's slower but higher quality)
        print("  Trying Claude CLI for metadata...")
        response_text = call_claude_cli(full_meta_prompt, retries=1)
        if response_text:
            used_model = "claude"
        else:
            print("  Claude failed — falling back to Gemini models...")
            response_text, used_model = run_with_fallback(
                valid_models, failed_models, full_meta_prompt, "metadata generation"
            )

        if not response_text:
            print("All models failed for metadata generation.")
            timings.append(("2. Metadata", time.time() - t0, "fail", ""))
            return False, timings

        with open(meta_md, "w", encoding="utf-8") as f:
            f.write(response_text)
        print(f"Success with {used_model}! Saved metadata: {meta_md}")
        timings.append(("2. Metadata", time.time() - t0, "ok", used_model))

    # ---------------------------------------------------------
    # PART 3: Image Generation
    # ---------------------------------------------------------
    print(f"\n--- Running Image Generation: {output_dir} ---")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    generate_image_script = os.path.join(script_dir, "generate_image.py")
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, generate_image_script, output_dir],
        text=True
    )
    if result.returncode != 0:
        print(f"Image generation exited with code {result.returncode}.")
        timings.append(("3. Image Generation", time.time() - t0, "fail", ""))
        return False, timings
    timings.append(("3. Image Generation", time.time() - t0, "ok", ""))

    # ---------------------------------------------------------
    # PART 4: Audio Generation
    # ---------------------------------------------------------
    print(f"\n--- Opening Google Docs: {output_dir} ---")
    open_docs_script = os.path.join(script_dir, "generate_audio.py")
    t0 = time.time()
    result = subprocess.run(
        [sys.executable, open_docs_script, output_dir],
        text=True
    )
    if result.returncode != 0:
        print(f"Google Docs step exited with code {result.returncode}.")
        timings.append(("4. Audio Generation", time.time() - t0, "fail", ""))
        return False, timings
    timings.append(("4. Audio Generation", time.time() - t0, "ok", ""))

    # ---------------------------------------------------------
    # PART 5: Video Generation (YouTube + TikTok)
    # ---------------------------------------------------------
    audio_extensions = {".mp3", ".m4a", ".aac", ".wav", ".flac"}
    audio_file = None
    for f in sorted(os.listdir(output_dir)):
        if os.path.splitext(f)[1].lower() in audio_extensions:
            audio_file = f
            break

    generate_video_script = os.path.join(script_dir, "generate_video.py")
    t0 = time.time()
    if not audio_file:
        print(f"\n--- Skipping Video Generation ---")
        print(f"  No audio file found in '{output_dir}'.")
        print(f"  Add the audio file then run:")
        print(f"    python generate_video.py \"{output_dir}\"")
        timings.append(("5. Video Generation", time.time() - t0, "skip", ""))
    else:
        stem = os.path.splitext(output_filename)[0]
        thumbnail = os.path.join(output_dir, f"{stem}_thumbnail.png")
        if not os.path.exists(thumbnail):
            print(f"\n--- Skipping Video Generation ---")
            print(f"  Thumbnail not found: {thumbnail}")
            print(f"  Image generation may not have completed. Run manually:")
            print(f"    python generate_video.py \"{output_dir}\"")
            timings.append(("5. Video Generation", time.time() - t0, "skip", ""))
        else:
            yt_out = os.path.join(output_dir, f"{stem}_thumbnail_youtube.mp4")
            tt_out = os.path.join(output_dir, f"{stem}_thumbnail_tiktok.mp4")
            if os.path.exists(yt_out) and os.path.exists(tt_out):
                print(f"\n--- Skipping Video Generation (both outputs already exist) ---")
                timings.append(("5. Video Generation", time.time() - t0, "skip", ""))
            else:
                print(f"\n--- Running Video Generation: {output_dir} ---")
                result = subprocess.run(
                    [sys.executable, generate_video_script, output_dir],
                    text=True
                )
                if result.returncode != 0:
                    print(f"Video generation exited with code {result.returncode}.")
                    timings.append(("5. Video Generation", time.time() - t0, "fail", ""))
                    return False, timings
                timings.append(("5. Video Generation", time.time() - t0, "ok", ""))

    # ---------------------------------------------------------
    # PART 6: YouTube Upload
    # ---------------------------------------------------------
    stem = os.path.splitext(output_filename)[0]
    yt_video = os.path.join(output_dir, f"{stem}_thumbnail_youtube.mp4")
    t0 = time.time()
    if not os.path.exists(yt_video):
        print(f"\n--- Skipping YouTube Upload (no video found) ---")
        print(f"  Run manually once video is ready:")
        print(f"    python upload_youtube.py \"{output_dir}\"")
        timings.append(("6. YouTube Upload", time.time() - t0, "skip", ""))
    else:
        print(f"\n--- Running YouTube Upload: {output_dir} ---")
        upload_script = os.path.join(script_dir, "upload_youtube.py")
        result = subprocess.run(
            [sys.executable, upload_script, output_dir],
            text=True
        )
        if result.returncode != 0:
            print(f"YouTube upload exited with code {result.returncode}.")
            timings.append(("6. YouTube Upload", time.time() - t0, "fail", ""))
            return False, timings
        timings.append(("6. YouTube Upload", time.time() - t0, "ok", ""))

    # ---------------------------------------------------------
    # PART 7: TikTok Upload
    # ---------------------------------------------------------
    stem = os.path.splitext(output_filename)[0]
    tt_video = os.path.join(output_dir, f"{stem}_thumbnail_tiktok.mp4")
    t0 = time.time()
    if not os.path.exists(tt_video):
        print(f"\n--- Skipping TikTok Upload (no video found) ---")
        print(f"  Run manually once video is ready:")
        print(f"    python upload_tiktok.py \"{output_dir}\"")
        timings.append(("7. TikTok Upload", time.time() - t0, "skip", ""))
    else:
        print(f"\n--- Running TikTok Upload: {output_dir} ---")
        tiktok_script = os.path.join(script_dir, "upload_tiktok.py")
        result = subprocess.run(
            [sys.executable, tiktok_script, output_dir],
            text=True
        )
        if result.returncode != 0:
            print(f"TikTok upload exited with code {result.returncode}.")
            timings.append(("7. TikTok Upload", time.time() - t0, "fail", ""))
            return False, timings
        timings.append(("7. TikTok Upload", time.time() - t0, "ok", ""))

    chapter_label = os.path.basename(pdf_path)
    _print_timing_report(timings, chapter_label)
    return True, timings


def main():
    parser = argparse.ArgumentParser(
        description="Translate PDF chapter(s) and run the full video pipeline.\n"
                    "Mode 1 (single): pass a PDF file + output base dir\n"
                    "Mode 2 (batch):  pass a folder of PDFs + output base dir"
    )
    parser.add_argument("input", help="Path to a single PDF file OR a folder containing PDF files")
    parser.add_argument("output_folder", help="Base output directory (ch_N subfolders will be created here)")
    args = parser.parse_args()

    input_path = args.input
    output_base = args.output_folder

    if not os.path.exists(input_path):
        print(f"Error: Input path '{input_path}' not found.")
        sys.exit(1)

    os.makedirs(output_base, exist_ok=True)
    valid_models = get_valid_models()
    failed_models: set = set()

    if os.path.isfile(input_path):
        # ── Mode 1: single PDF ──────────────────────────────────
        if not input_path.lower().endswith(".pdf"):
            print(f"Error: '{input_path}' is not a PDF file.")
            sys.exit(1)
        ok, _ = process_single_pdf(input_path, output_base, valid_models, failed_models)
        if not ok:
            sys.exit(1)
        print("\nAll tasks completed successfully.")

    elif os.path.isdir(input_path):
        # ── Mode 2: folder of PDFs ──────────────────────────────
        pdf_files = sorted(
            f for f in os.listdir(input_path) if f.lower().endswith(".pdf")
        )
        if not pdf_files:
            print(f"Error: No PDF files found in '{input_path}'.")
            sys.exit(1)

        print(f"Found {len(pdf_files)} PDF(s) in '{input_path}':")
        for f in pdf_files:
            print(f"  {f}")

        # accumulate totals per task across all chapters
        task_totals: dict = {}   # task_name -> total seconds
        batch_total = 0.0
        failed = []

        for i, fname in enumerate(pdf_files, 1):
            pdf_path = os.path.join(input_path, fname)
            print(f"\n{'='*60}")
            print(f"[{i}/{len(pdf_files)}] Processing: {fname}")
            print(f"{'='*60}")
            ok, timings = process_single_pdf(pdf_path, output_base, valid_models, failed_models)
            for name, elapsed, _, _m in timings:
                task_totals[name] = task_totals.get(name, 0.0) + elapsed
                batch_total += elapsed
            if not ok:
                print(f"  FAILED: {fname}")
                failed.append(fname)

        # Batch-wide timing summary
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
