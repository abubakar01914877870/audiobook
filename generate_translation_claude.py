import fitz
import subprocess
import os
import sys
import argparse
import time
import re


def resolve_path(path):
    """If path doesn't exist, scan parent dir for a file whose name matches
    after normalizing Unicode quote variants (e.g. curly apostrophe U+2019 vs ASCII apostrophe)."""
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

from dotenv import load_dotenv

load_dotenv()


# Helper function to extract text from a PDF
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
    # Fix hyphenated words broken across lines (e.g., "trans-\nlate" -> "translate")
    text = re.sub(r'([a-zA-Z])-\n+([a-zA-Z])', r'\1\2', text)

    # Collapse multiple spaces into a single space
    text = re.sub(r'[ \t]+', ' ', text)

    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Drop standalone page numbers (e.g. "1", "42", "- 5 -")
        if re.fullmatch(r'[-–—]?\s*\d+\s*[-–—]?', stripped):
            continue
        cleaned.append(stripped)

    # Collapse 3+ consecutive blank lines into a single blank line
    result = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned))
    return result.strip()


# Helper function to clean the translation output
def clean_translation(text):
    """Clean the LLM output to contain only the translated text."""
    # Remove ANSI escape sequences (e.g. CLI colored output)
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)

    # Some models return the text wrapped in triple backticks
    code_blocks = re.findall(r'```(?:markdown)?\n?(.*?)\n?```', text, re.DOTALL)
    if code_blocks:
        code_blocks.sort(key=len, reverse=True)
        text = code_blocks[0]

    return text.strip()


# Helper to extract chapter name from raw PDF text
def extract_chapter_name_from_text(text):
    """Try to extract the chapter title from the first non-empty heading line of the text.
    Falls back to 'Untitled' if nothing is found."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip lines that are purely a chapter number like "Chapter 214" or "214"
        if re.match(r'^(chapter\s+)?\d+$', line, re.IGNORECASE):
            continue
        # Accept the first meaningful line as the chapter name
        name = re.sub(r'[^\w\s-]', '', line)   # remove special chars
        name = re.sub(r'_', ' ', name)           # convert underscores to spaces
        name = re.sub(r'\s+', ' ', name).strip() # normalise whitespace
        # Strip leading "Chapter NNN" prefix so we don't duplicate it in the filename
        name = re.sub(r'^chapter\s+\d+[\s:\-\.]*', '', name, flags=re.IGNORECASE).strip()
        if name:
            return name[:60]  # cap to avoid overly long filenames
    return "Untitled"


# Helper function to run the Claude CLI
def run_claude_cli(prompt, retries=3):
    """Run the Claude CLI with the specified prompt.
    Returns the cleaned stdout text, or None if failed.
    Retries up to `retries` times on timeout or transient failure.
    """
    for attempt in range(1, retries + 1):
        try:
            print(f"  Claude CLI attempt {attempt}/{retries}...")
            result = subprocess.run(
                ["claude", "-p", prompt, "--output-format", "text"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=1800  # 30 minutes — plenty for any chapter translation
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
                print(f"  Retrying...")
        except KeyboardInterrupt:
            print("\nProcess interrupted by user. Exiting...")
            sys.exit(1)
        except FileNotFoundError:
            print("Error: 'claude' CLI not found. Please ensure Claude CLI is installed and in your PATH.")
            sys.exit(1)
        except Exception as e:
            print(f"  Exception running Claude CLI: {e}")
            if attempt < retries:
                wait = 10 * attempt
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)

    print("  All retry attempts exhausted.")
    return None


# Helper to build the output filename following the convention: Chapter_{number}_{name}.md
def build_output_filename(pdf_path, chapter_name):
    """Build output filename as Chapter_{number}_{name}.md.
    Parses the chapter number from the input PDF filename.
    Expects filenames like: Chapter_0214.pdf, Chapter_14.pdf, chapter_214_foo.pdf, or 214.pdf
    """
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    num_match = re.search(r'(\d+)', stem)
    if num_match:
        chapter_num = int(num_match.group(1))
        num_str = f"{chapter_num:03d}"  # zero-pad to at least 3 digits
    else:
        num_str = "000"
    return f"Chapter_{num_str}_{chapter_name}.md"


# Helper function to translate a single file
def translate_file(pdf_path, output_dir, system_prompt):
    """Translate a single PDF to Bengali markdown."""
    text = extract_text(pdf_path)
    if not text:
        print(f"Skipping {pdf_path}, no text extracted.")
        return False

    chapter_name = extract_chapter_name_from_text(text)
    output_filename = build_output_filename(pdf_path, chapter_name)
    output_md = os.path.join(output_dir, output_filename)

    if os.path.exists(output_md):
        print(f"Skipping {pdf_path}, output already exists: {output_md}")
        return True

    print(f"\n--- Processing: {pdf_path} ---")
    print(f"Chapter name detected: '{chapter_name}'")
    print(f"Output file: {output_filename}")

    full_prompt = system_prompt + "\n\n=== Original English Text ===\n\n" + text

    print(f"Running translation with Claude CLI...")
    translated_text = run_claude_cli(full_prompt)

    if translated_text:
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(translated_text)
        print(f"Success! Translated: {output_md} ({os.path.getsize(output_md)} bytes)")
        return True
    else:
        print(f"Translation failed for {pdf_path}.")
        return False


def main():
    parser = argparse.ArgumentParser(description="Translate PDF chapter(s) to Bengali using Claude CLI.")
    parser.add_argument("pdf_input", help="Path to a PDF file or a directory containing PDFs")
    parser.add_argument("output_folder", help="Directory to save the translated markdown files")

    args = parser.parse_args()

    pdf_input = args.pdf_input
    output_dir = args.output_folder

    pdf_input = resolve_path(pdf_input)
    if not os.path.exists(pdf_input):
        print(f"Error: Input '{pdf_input}' not found.")
        sys.exit(1)

    if not os.path.exists(output_dir):
        print(f"Creating output folder: {output_dir}")
        os.makedirs(output_dir, exist_ok=True)

    system_prompt = """Translate the English novel chapter below into Bengali (Cholitobhasha).
Role: Expert Literary Translator
Style: Muhammed Zafar Iqbal — simple, fluid, teen-friendly. No archaic or Sanskrit-heavy words.
Pronouns: সে/তুমি only. Never আপনি/তিনি.
Names: keep consistent. Novel/chapter titles: keep original format.
English terms: Use English words as little as possible. When keeping an English term, do not add the Bengali translation next to it. Just write the English word. Never use the "Bengali word (English_word)" format.
Numbers: Translate all numbers into Bengali words (e.g., 10 -> দশ, 1000 -> এক হাজার, 1203 -> বারোশ তিন) instead of keeping them as digits.
Dialogue: natural, direct, colloquial.
Formatting: Format the output optimally for a voice-over artist reading an audio story. Add appropriate paragraph breaks, empty lines, and spacing to indicate natural pauses, breath spaces, and scene transitions. Make it highly readable for narration.
Output: full translation only — no summary, no commentary, no preamble."""

    # Collect all PDF files to process
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

    failed_files = []

    try:
        for i, pdf_path in enumerate(pdf_files):
            print(f"\nProgress: {i+1}/{len(pdf_files)}")
            success = translate_file(pdf_path, output_dir, system_prompt)
            if not success:
                failed_files.append(pdf_path)
            # Small sleep between files
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
