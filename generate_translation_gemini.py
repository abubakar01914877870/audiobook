import fitz
import subprocess
import os
import sys
import argparse
import time
import re
import concurrent.futures

# Fix for importlib.metadata in Python 3.9
if sys.version_info < (3, 10):
    try:
        import importlib_metadata as metadata
    except ImportError:
        import importlib.metadata as metadata
    
    if not hasattr(metadata, 'packages_distributions'):
        # Polyfill or patch if needed, but usually just having importlib_metadata installed is enough
        # if the library is using 'importlib_metadata' instead of 'importlib.metadata'.
        # Some libraries incorrectly use 'importlib.metadata' on 3.9.
        pass
else:
    import importlib.metadata as metadata

from dotenv import load_dotenv

load_dotenv()

def get_available_models():
    """Return the predefined prioritized list of Gemini models."""
    prioritized_models = [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite"
    ]
    print(f"Prioritized models: {', '.join(prioritized_models)}")
    return prioritized_models

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

# Helper function to check the state of a model
def check_model_state(model):
    """Run gemini /stats and return the percentage of usage if found. Return 0 if failed."""
    print(f"Checking state for {model}...")
    try:
        # We pipe an empty string into it so it doesn't hang in interactive mode if /stats fails
        result = subprocess.run(
            ["gemini", "-m", model, "-p", "/stats", "-y"],
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 130:
            raise KeyboardInterrupt
            
        output = result.stdout + "\n" + result.stderr
        
        # Look for percentage like "20%" or "9%"
        match = re.search(r'(\d+)%', output)
        if match:
            percent = int(match.group(1))
            print(f"[{model}] State: {percent}%")
            return percent
        else:
            # Check if it hit a quota error explicitly
            if "exhausted your capacity" in output or "QuotaError" in output or "429" in output:
                print(f"[{model}] Quota exhausted.")
                return 0
            
            print(f"[{model}] Could not parse percentage from output. Assuming 100% to attempt.")
            # If we don't know the state, we can try to use it anyway
            return 100
    except subprocess.TimeoutExpired:
        print(f"[{model}] Timeout checking state. Assuming 100% to attempt.")
        return 100
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
    except Exception as e:
        print(f"[{model}] Error checking state: {e}")
        return 0

# Helper function to clean the translation output
def clean_translation(text):
    """Clean the LLM output to contain only the translated text."""
    # Remove ANSI escape sequences (e.g. CLI colored output)
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)

    # Some models return the text wrapped in triple backticks
    # E.g. ```markdown\n ... \n```
    # Find the longest block of text inside triple backticks if it exists
    code_blocks = re.findall(r'```(?:markdown)?\n?(.*?)\n?```', text, re.DOTALL)
    if code_blocks:
        # Use the largest block if there are multiple
        code_blocks.sort(key=len, reverse=True)
        text = code_blocks[0]
        
    # Optional: We could remove other conversational boilerplate if we notice the model outputting it
    # Currently, our system prompt strictly says "Task: Replace the existing text entirely... Do not provide summaries"
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
        # Sanitize for use as a filename: keep alphanumeric, spaces, hyphens (no underscores in name)
        name = re.sub(r'[^\w\s-]', '', line)  # remove special chars
        name = re.sub(r'_', ' ', name)          # convert any underscores to spaces
        name = re.sub(r'\s+', ' ', name).strip() # normalise whitespace
        # Strip leading "Chapter NNN" prefix so we don't duplicate it in the filename
        # e.g. "Chapter 481 Statistics and People" -> "Statistics and People"
        name = re.sub(r'^chapter\s+\d+[\s:\-\.]*', '', name, flags=re.IGNORECASE).strip()
        if name:
            return name[:60]  # cap to avoid overly long filenames
    return "Untitled"

# Helper function to run the Gemini CLI
def run_gemini_cli(model, prompt):
    """Run the Gemini CLI with the specified model and prompt.
    Returns the cleaned stdout text, or None if failed.
    """
    try:
        # Run subprocess and wait for completion, capturing output
        result = subprocess.run(
            ["gemini", "-m", model, "-p", prompt, "-y"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300 # Give it up to 5 mins for a chapter translation
        )
        
        if result.returncode == 130:
            raise KeyboardInterrupt
            
        if result.returncode == 0:
            return clean_translation(result.stdout)
        else:
            print(f"Model {model} failed (exit {result.returncode}).")
            if result.stderr:
                print(f"STDERR: {result.stderr.strip()}")
            return None
    except subprocess.TimeoutExpired:
        print(f"Model {model} timed out during translation.")
        return None
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
    except Exception as e:
        print(f"Exception running model {model}: {e}")
        return None

# Helper to build the output filename following the Convention: Chapter_{number}_{name}.md
def build_output_filename(pdf_path, chapter_name):
    """Build output filename as Chapter_{number}_{name}.md.
    Parses the chapter number from the input PDF filename.
    Expects filenames like: Chapter_0214.pdf, Chapter_14.pdf, chapter_214_foo.pdf, or 214.pdf
    """
    stem = os.path.splitext(os.path.basename(pdf_path))[0]  # e.g. "Chapter_0214"
    # Try to find a numeric block in the filename
    num_match = re.search(r'(\d+)', stem)
    if num_match:
        chapter_num = int(num_match.group(1))
        num_str = f"{chapter_num:03d}"  # zero-pad to at least 3 digits
    else:
        num_str = "000"
    return f"Chapter_{num_str}_{chapter_name}.md"

# Helper function to translate a single file
def translate_file(pdf_path, output_dir, system_prompt, models, start_model_idx):
    """Translate a single PDF and return the index of the model that succeeded."""
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    # Extract text first so we can derive the chapter name for the output filename
    text = extract_text(pdf_path)
    if not text:
        print(f"Skipping {pdf_path}, no text extracted.")
        return start_model_idx

    chapter_name = extract_chapter_name_from_text(text)
    output_filename = build_output_filename(pdf_path, chapter_name)
    output_md = os.path.join(output_dir, output_filename)
    
    needs_translation = not os.path.exists(output_md)

    if not needs_translation:
        print(f"Skipping {pdf_path}, outputs already exist.")
        return start_model_idx # Return the same index so we don't advance for skipped files

    print(f"\n--- Processing: {pdf_path} ---")
    print(f"Chapter name detected: '{chapter_name}'")

    full_prompt = system_prompt + "\n\n=== Original English Text ===\n\n" + text
    
    current_idx = start_model_idx
    success_translation = False

    while current_idx < len(models):
        model = models[current_idx]
        
        if needs_translation:
            print(f"Run translation for {base_name} with model: {model}...")
            translated_text = run_gemini_cli(model, full_prompt)
            
            if translated_text:
                # Write only the clean bangla translation
                with open(output_md, "w", encoding="utf-8") as f:
                    f.write(translated_text)
                    
                print(f"Success with {model}! Translated: {output_md} ({os.path.getsize(output_md)} bytes)")
                success_translation = True
            else:
                print(f"Translation failed with {model}. Falling back to next model...")
                current_idx += 1
                continue
        else:
            success_translation = True

        # If we reached here, we finished translation successfully.
        break
    
    if not success_translation and needs_translation:
        print(f"All models failed for {pdf_path}.")
        return start_model_idx # if all failed, we can return what we started with or just the last index
    
    return current_idx

def main():
    parser = argparse.ArgumentParser(description="Translate PDF chapter(s) to Bengali using Gemini CLI.")
    parser.add_argument("pdf_input", help="Path to a PDF file or a directory containing PDFs")
    parser.add_argument("output_folder", help="Directory to save the translated markdown files")
    
    args = parser.parse_args()
    
    pdf_input = args.pdf_input
    output_dir = args.output_folder

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
        pdf_files = [os.path.join(pdf_input, f) for f in os.listdir(pdf_input) if f.lower().endswith(".pdf")]
        pdf_files.sort() # Process in order
        print(f"Found {len(pdf_files)} PDF files in directory: {pdf_input}")
    else:
        pdf_files = [pdf_input]

    if not pdf_files:
        print("No PDF files found to process.")
        return

    models = get_available_models()
    
    # ---------------------------------------------------------
    # Check Model Usage Stats Before Starting
    # ---------------------------------------------------------
    print("\nGetting model usage status before starting...")
    model_states = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_model = {executor.submit(check_model_state, m): m for m in models}
        for future in concurrent.futures.as_completed(future_to_model):
            m = future_to_model[future]
            try:
                state = future.result()
                model_states[m] = state
            except Exception:
                model_states[m] = 0

    print("\n--- Model Usage Status ---")
    valid_models = []
    for m in models:
        state = model_states.get(m, 0)
        print(f"{m:<25} State: {state}%")
        if state >= 10:
            valid_models.append(m)
    print("--------------------------\n")

    if not valid_models:
        print("Error: No models available with >= 10% usage state. Aborting.")
        sys.exit(1)
        
    # Re-assign valid models to be used in the loop
    models = valid_models
    
    # State variable remembered across chapters (resets on script start)
    current_model_idx = 0

    # Process files one by one
    try:
        for i, pdf_path in enumerate(pdf_files):
            print(f"\nProgress: {i+1}/{len(pdf_files)}")
            # If the remaining models run out, we should probably reset or halt,
            # but the fallback logic prevents out-of-bounds in translate_file.
            if current_model_idx >= len(models):
                print("All available models have been exhausted. Stopping.")
                break
                
            current_model_idx = translate_file(pdf_path, output_dir, system_prompt, models, current_model_idx)
            # Small sleep between files to be safe
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)

    print("\nAll tasks completed.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
