import fitz
import subprocess
import os
import sys
import argparse
import time
import re
import threading

# Fix for importlib.metadata in Python 3.9
if sys.version_info < (3, 10):
    try:
        import importlib_metadata as metadata
    except ImportError:
        import importlib.metadata as metadata
else:
    import importlib.metadata as metadata

from dotenv import load_dotenv
from character_discovery import build_translation_character_reference_for_pdf

load_dotenv()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def resolve_path(path):
    """If path doesn't exist, scan parent dir for a file whose name matches
    after normalizing Unicode quote variants (e.g. curly apostrophe U+2019)."""
    if os.path.exists(path):
        return path

    def normalize_quotes(s):
        return re.sub(r"[\u2018\u2019\u201a\u201b\u2032\u2035\u0060\u00b4']", "'", s)

    parent = os.path.dirname(path) or '.'
    target = normalize_quotes(os.path.basename(path)).lower()
    try:
        for entry in os.listdir(parent):
            if normalize_quotes(entry).lower() == target:
                return os.path.join(parent, entry)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------------------
# PDF / text helpers
# ---------------------------------------------------------------------------

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


def clean_translation(text):
    """Clean the LLM output to contain only the translated text."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)
    code_blocks = re.findall(r'```(?:markdown)?\n?(.*?)\n?```', text, re.DOTALL)
    if code_blocks:
        code_blocks.sort(key=len, reverse=True)
        text = code_blocks[0]
    return text.strip()


def extract_chapter_name_from_text(text):
    """Extract the chapter title from the first meaningful heading line."""
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
    """Build output filename as Chapter_{number}_{name}.md."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    num_match = re.search(r'(\d+)', stem)
    if num_match:
        chapter_num = int(num_match.group(1))
        num_str = f"{chapter_num:03d}"
    else:
        num_str = "000"
    return f"Chapter_{num_str}_{chapter_name}.md"


# ---------------------------------------------------------------------------
# Claude CLI
# ---------------------------------------------------------------------------

def run_claude_cli(prompt, retries=3):
    """Run the Claude CLI. Returns cleaned text or None if all attempts fail."""
    for attempt in range(1, retries + 1):
        try:
            print(f"  Claude CLI attempt {attempt}/{retries}...")
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
                return clean_translation(result.stdout)
            else:
                print(f"  Claude CLI failed (exit {result.returncode}).")
                if result.stderr:
                    print(f"  STDERR: {result.stderr.strip()}")
                if attempt < retries:
                    wait = 10 * attempt
                    print(f"  Retrying in {wait}s...")
                    time.sleep(wait)
        except subprocess.TimeoutExpired:
            print(f"  Claude CLI timed out (attempt {attempt}/{retries}).")
            if attempt < retries:
                print("  Retrying...")
        except KeyboardInterrupt:
            print("\nProcess interrupted by user. Exiting...")
            sys.exit(1)
        except FileNotFoundError:
            print("Error: 'claude' CLI not found. Skipping Claude, will try Gemini.")
            return None
        except Exception as e:
            print(f"  Exception running Claude CLI: {e}")
            if attempt < retries:
                wait = 10 * attempt
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
    print("  All Claude retry attempts exhausted.")
    return None


# ---------------------------------------------------------------------------
# Gemini CLI
# ---------------------------------------------------------------------------

QUOTA_ERROR_PATTERNS = [
    "exhausted your capacity",
    "No capacity available",
    "RESOURCE_EXHAUSTED",
    "rateLimitExceeded",
    "MODEL_CAPACITY_EXHAUSTED",
]


def score_model(model_name):
    """Score a Gemini model by quality. Higher = better."""
    score = 0
    vm = re.search(r'gemini-(\d+)(?:\.(\d+))?', model_name)
    if vm:
        score += int(vm.group(1)) * 1000
        score += int(vm.group(2) or 0) * 100
    if re.search(r'(?<![a-z])pro(?![a-z])', model_name):
        score += 30
    elif 'flash-lite' in model_name:
        score += 5
    elif 'flash' in model_name:
        score += 15
    elif 'nano' in model_name:
        score += 2
    if 'preview' in model_name or 'exp' in model_name:
        score += 1
    return score


def get_available_models():
    """Return Gemini models sorted by quality (best first)."""
    known_models = [
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    known_models.sort(key=score_model, reverse=True)
    print(f"Gemini candidate models (sorted by quality): {', '.join(known_models)}")
    return known_models


def run_gemini_cli(model, prompt):
    """Run the Gemini CLI. Returns cleaned text or None if failed/quota-hit."""
    try:
        proc = subprocess.Popen(
            ["gemini", "-m", model, "-p", prompt, "-y"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

        stdout_lines = []
        stderr_lines = []
        quota_hit = False
        deadline = time.time() + 300  # 5-minute hard timeout

        def read_stderr():
            nonlocal quota_hit
            for line in proc.stderr:
                stderr_lines.append(line)
                if any(p in line for p in QUOTA_ERROR_PATTERNS):
                    quota_hit = True
                    proc.kill()

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        for line in proc.stdout:
            if quota_hit:
                break
            if time.time() > deadline:
                proc.kill()
                print(f"  Model {model} timed out during translation.")
                return None
            stdout_lines.append(line)

        proc.wait()
        stderr_thread.join(timeout=2)

        if proc.returncode == 130:
            raise KeyboardInterrupt

        if quota_hit:
            print(f"  Model {model} quota exhausted — switching to next model.")
            return None

        if proc.returncode == 0:
            return clean_translation("".join(stdout_lines))
        else:
            stderr_out = "".join(stderr_lines).strip()
            print(f"  Model {model} failed (exit {proc.returncode}).")
            if stderr_out:
                print(f"  Error: {stderr_out.splitlines()[0]}")
            return None

    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
    except Exception as e:
        print(f"  Exception running model {model}: {e}")
        return None


# ---------------------------------------------------------------------------
# Core translation logic — Claude first, Gemini fallback
# ---------------------------------------------------------------------------

def translate_file(pdf_path, output_dir, system_prompt, gemini_models, gemini_start_idx,
                   primary_model="claude"):
    """Translate a single PDF.

    primary_model='claude'  → Claude first, Gemini fallback (default)
    primary_model='gemini'  → Gemini first, Claude fallback

    Returns the Gemini model index that succeeded (so the next file can skip exhausted models).
    """
    text = extract_text(pdf_path)
    if not text:
        print(f"Skipping {pdf_path}, no text extracted.")
        return gemini_start_idx

    chapter_name = extract_chapter_name_from_text(text)
    output_filename = build_output_filename(pdf_path, chapter_name)
    output_md = os.path.join(output_dir, output_filename)

    if os.path.exists(output_md):
        print(f"Skipping {pdf_path}, output already exists: {output_md}")
        return gemini_start_idx

    print(f"\n--- Processing: {pdf_path} ---")
    print(f"Chapter name detected: '{chapter_name}'")
    print(f"Output file: {output_filename}")

    char_ref = build_translation_character_reference_for_pdf(pdf_path, output_dir)
    char_section = f"\n\n{char_ref}" if char_ref else ""
    full_prompt = system_prompt + char_section + "\n\n=== Original English Text ===\n\n" + text

    translated_text = None
    current_idx = gemini_start_idx

    def _try_gemini():
        nonlocal translated_text, current_idx
        idx = current_idx
        while idx < len(gemini_models):
            model = gemini_models[idx]
            print(f"  Trying Gemini model: {model}...")
            result = run_gemini_cli(model, full_prompt)
            if result:
                translated_text = result
                current_idx = idx
                print(f"Success (Gemini/{model})! Saved: {output_md} ({len(result)} chars)")
                return
            print(f"  Model {model} failed. Trying next Gemini model...")
            idx += 1
        current_idx = idx  # all exhausted

    def _try_claude():
        nonlocal translated_text
        print("  Trying Claude CLI...")
        result = run_claude_cli(full_prompt)
        if result:
            translated_text = result
            print(f"Success (Claude)! Saved: {output_md} ({len(result)} chars)")

    if primary_model == "gemini":
        _try_gemini()
        if not translated_text:
            print("All Gemini models failed. Falling back to Claude...")
            _try_claude()
    else:
        _try_claude()
        if not translated_text:
            print("Claude failed. Falling back to Gemini...")
            _try_gemini()

    if translated_text:
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(translated_text)
        print(f"  Saved: {output_md} ({os.path.getsize(output_md)} bytes)")
        return current_idx

    print(f"All models (Claude + Gemini) failed for {pdf_path}.")
    return gemini_start_idx


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Translate the English novel chapter below into Bengali (Cholitobhasha).
Role: Expert Literary Translator
Style: Muhammed Zafar Iqbal — simple, fluid, teen-friendly. No archaic or Sanskrit-heavy words.
Pronouns: সে / তুমি / তোমাকে / তোমার — always these forms. Never তুই / তোকে / তোর. Specifically: use তুমি (never তুই), তোমাকে (never তোকে), তোমার (never তোর).
Names: keep consistent. Novel/chapter titles: keep original format.
English terms: Use English words as little as possible. When keeping an English term, do not add the Bengali translation next to it. Just write the English word. Never use the "Bengali word (English_word)" format.
Numbers: Translate all numbers into Bengali words (e.g., 10 -> দশ, 1000 -> এক হাজার, 1203 -> বারোশ তিন) instead of keeping them as digits.
Dialogue: natural, direct, colloquial.
TTS pronunciation: Verb forms ending in bare ল (e.g. তাকাল, বলল, গেল) are mispronounced by Google Docs TTS — always use the লো form (e.g. তাকালো, বললো, গেলো). Apply this to all past-tense verb endings consistently.
Formatting: Format the output optimally for a voice-over artist reading an audio story. Add appropriate paragraph breaks, empty lines, and spacing to indicate natural pauses, breath spaces, and scene transitions. Make it highly readable for narration.
Output: full translation only — no summary, no commentary, no preamble."""


def main():
    parser = argparse.ArgumentParser(
        description="Translate PDF chapter(s) to Bengali. Uses Claude first, falls back to Gemini."
    )
    parser.add_argument("pdf_input", help="Path to a PDF file or a directory containing PDFs")
    parser.add_argument("output_folder", help="Directory to save the translated markdown files")
    parser.add_argument(
        "--skip-models", default="",
        help="Comma-separated list of Gemini models to skip (quota-exhausted this run)."
    )
    parser.add_argument(
        "--primary-model", choices=["claude", "gemini"], default="claude",
        help="Primary AI model (default: claude). claude = Claude first, Gemini fallback. gemini = Gemini first, Claude fallback.",
    )
    args = parser.parse_args()

    pdf_input = resolve_path(args.pdf_input)
    output_dir = args.output_folder

    if not os.path.exists(pdf_input):
        print(f"Error: Input '{pdf_input}' not found.")
        sys.exit(1)

    if not os.path.exists(output_dir):
        print(f"Creating output folder: {output_dir}")
        os.makedirs(output_dir, exist_ok=True)

    pdf_files = []
    if os.path.isdir(pdf_input):
        pdf_files = [
            os.path.join(pdf_input, f)
            for f in os.listdir(pdf_input)
            if f.lower().endswith(".pdf")
        ]
        pdf_files.sort()
        print(f"Found {len(pdf_files)} PDF files in directory: {pdf_input}")
    else:
        pdf_files = [pdf_input]

    if not pdf_files:
        print("No PDF files found to process.")
        return

    gemini_models = get_available_models()
    if args.skip_models:
        skip_set = {m.strip() for m in args.skip_models.split(",") if m.strip()}
        before = len(gemini_models)
        gemini_models = [m for m in gemini_models if m not in skip_set]
        skipped = before - len(gemini_models)
        if skipped:
            print(f"  Skipping {skipped} quota-exhausted Gemini model(s): {', '.join(skip_set)}")
    gemini_model_idx = 0
    failed_files = []

    try:
        for i, pdf_path in enumerate(pdf_files):
            print(f"\nProgress: {i+1}/{len(pdf_files)}")
            if gemini_model_idx >= len(gemini_models):
                print("All Gemini models exhausted. Claude-only mode for remaining files.")

            prev_idx = gemini_model_idx
            gemini_model_idx = translate_file(
                pdf_path, output_dir, SYSTEM_PROMPT, gemini_models, gemini_model_idx,
                primary_model=args.primary_model,
            )

            # If translate_file returned the same index and file doesn't exist, it failed
            output_check = os.path.join(output_dir, build_output_filename(
                pdf_path, extract_chapter_name_from_text(extract_text(pdf_path) or "")
            ))
            if not os.path.exists(output_check):
                failed_files.append(pdf_path)

            if i < len(pdf_files) - 1:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)

    print("\n--- All tasks completed ---")
    if failed_files:
        print(f"Failed files ({len(failed_files)}):")
        for f in failed_files:
            print(f"  - {f}")
    else:
        print("All files translated successfully.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
