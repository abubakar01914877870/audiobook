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

def check_model_state(model):
    """Run gemini /stats and return the percentage of usage if found."""
    print(f"Checking state for {model}...")
    try:
        result = subprocess.run(
            ["gemini", "-m", model, "-p", "/stats", "-y"],
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 130:
            raise KeyboardInterrupt
            
        output = result.stdout + "\n" + result.stderr
        match = re.search(r'(\d+)%', output)
        if match:
            percent = int(match.group(1))
            print(f"[{model}] State: {percent}%")
            return percent
        else:
            if "exhausted your capacity" in output or "QuotaError" in output or "429" in output:
                print(f"[{model}] Quota exhausted.")
                return 0
            
            print(f"[{model}] Could not parse percentage from output. Assuming 100% to attempt.")
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

def clean_llm_output(text):
    """Clean the LLM output (removes ANSI and extracts from markdown blocks)."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)

    code_blocks = re.findall(r'```(?:markdown)?\n?(.*?)\n?```', text, re.DOTALL)
    if code_blocks:
        code_blocks.sort(key=len, reverse=True)
        text = code_blocks[0]
        
    return text.strip()

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

def run_gemini_cli(model, prompt):
    try:
        result = subprocess.run(
            ["gemini", "-m", model, "-p", prompt, "-y"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300
        )
        
        if result.returncode == 130:
            raise KeyboardInterrupt
            
        if result.returncode == 0:
            return clean_llm_output(result.stdout)
        else:
            print(f"Model {model} failed (exit {result.returncode}).")
            if result.stderr:
                print(f"STDERR: {result.stderr.strip()}")
            return None
    except subprocess.TimeoutExpired:
        print(f"Model {model} timed out.")
        return None
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
    except Exception as e:
        print(f"Exception running model {model}: {e}")
        return None

def build_output_filename(pdf_path, chapter_name):
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    num_match = re.search(r'(\d+)', stem)
    if num_match:
        chapter_num = int(num_match.group(1))
        num_str = f"{chapter_num:03d}"
    else:
        num_str = "000"
    return f"Chapter_{num_str}_{chapter_name}.md"

def get_valid_models():
    models = get_available_models()
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
        
    return valid_models

def main():
    parser = argparse.ArgumentParser(description="Translate a single PDF chapter and generate video metadata.")
    parser.add_argument("pdf_input", help="Path to a single PDF file")
    parser.add_argument("output_folder", help="Directory to save the translated markdown and metadata files")
    
    args = parser.parse_args()
    
    pdf_input = args.pdf_input
    output_dir = args.output_folder

    if not os.path.exists(pdf_input) or not os.path.isfile(pdf_input):
        print(f"Error: Input PDF '{pdf_input}' not found or is not a file.")
        sys.exit(1)

    if not os.path.exists(output_dir):
        print(f"Creating output folder: {output_dir}")
        os.makedirs(output_dir, exist_ok=True)
        
    valid_models = get_valid_models()
    current_model_idx = 0

    # ---------------------------------------------------------
    # PART 1: Translation
    # ---------------------------------------------------------
    text = extract_text(pdf_input)
    if not text:
        print(f"Error: No text extracted from {pdf_input}.")
        sys.exit(1)

    chapter_name = extract_chapter_name_from_text(text)
    output_filename = build_output_filename(pdf_input, chapter_name)
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

    if not os.path.exists(output_md):
        print(f"\n--- Processing Translation: {pdf_input} ---")
        print(f"Chapter name detected: '{chapter_name}'")
        
        success_translation = False
        while current_model_idx < len(valid_models):
            model = valid_models[current_model_idx]
            print(f"Run translation for {os.path.basename(pdf_input)} with model: {model}...")
            translated_text = run_gemini_cli(model, full_translate_prompt)
            
            if translated_text:
                with open(output_md, "w", encoding="utf-8") as f:
                    f.write(translated_text)
                print(f"Success with {model}! Translated: {output_md} ({os.path.getsize(output_md)} bytes)")
                success_translation = True
                break
            else:
                print(f"Translation failed with {model}. Falling back to next model...")
                current_model_idx += 1
                
        if not success_translation:
            print("All models failed for translation.")
            sys.exit(1)
    else:
        print(f"\nSkipping translation, output '{output_md}' already exists.")

    # ---------------------------------------------------------
    # PART 2: Video Metadata Generation
    # ---------------------------------------------------------
    stem = os.path.splitext(output_filename)[0]
    meta_md = os.path.join(output_dir, f"{stem}_meta.md")
    
    if os.path.exists(meta_md):
        print(f"Skipping metadata generation, '{meta_md}' already exists.")
        print("\nAll tasks completed.")
        return

    print(f"\n--- Processing Metadata: {output_md} ---")
    
    try:
        with open(output_md, 'r', encoding='utf-8') as f:
            md_text = f.read()
    except Exception as e:
        print(f"Error reading {output_md}: {e}")
        sys.exit(1)

    num_match = re.search(r'(\d+)', stem)
    chapter_num_str = num_match.group(1) if num_match else ""
    chapter_name_meta = stem.replace(f"Chapter_{chapter_num_str}_", "").replace("_", " ") if chapter_num_str else stem

    meta_system_prompt = f"""You are an expert AI assistant tasked with generating metadata for a YouTube narrated audiobook. 
The following text is a translated Bengali chapter of the novel "Lord of the Mysteries".

Based ONLY on this text, generate a single response containing two sections: Image Generation Prompt and YouTube Video Metadata.

Requirements for Image Generation Prompt:
- Describe the most important scene in this chapter.
- Language: English
- Style: Anime style, mobile resolution (9:16 aspect ratio, portrait orientation), highly detailed, vibrant colors, cinematic lighting.
- Content Guidelines: MUST strictly adhere to community guidelines. NO graphic violence, NO gore, NO sexual content, NO self-harm, and NO hateful imagery. Focus on safe, PG-13 friendly dramatic or scenic moments.
- Text on Image Requirement: Explicitly instruct the image generator to include the Bengali typography directly in the image (e.g., 'with prominent bright Bengali text "[Bengali translation of Chapter {chapter_num_str}: {chapter_name_meta}]"').

Requirements for YouTube Video Metadata:
- Video Title: Create an engaging title in Bengali. It MUST include the chapter number ({chapter_num_str}) and the chapter name.
- Video Description:
   - Write a short, engaging description of this chapter's events in Bengali.
   - Include original writer credit (Author: Cuttlefish That Loves Diving).
   - Include this exact text and link at the end: "Read the story in text here: https://kalponic.web.app/"

Output Format: Provide your response exactly in this Markdown structure:

### Image Generation Prompt
[Your English image prompt here]

### YouTube Metadata
**Title:** [Your Bengali title here]

**Description:**
[Your Bengali description here]
"""

    full_meta_prompt = meta_system_prompt + "\n\n=== Bengali Chapter Text ===\n\n" + md_text

    success_meta = False
    while current_model_idx < len(valid_models):
        model = valid_models[current_model_idx]
        print(f"Run metadata generation for {stem} with model: {model}...")
        
        response_text = run_gemini_cli(model, full_meta_prompt)
        
        if response_text:
            with open(meta_md, "w", encoding="utf-8") as f:
                f.write(response_text)
                
            print(f"Success with {model}! Saved metadata: {meta_md}")
            success_meta = True
            break
        else:
            print(f"Metadata generation failed with {model}. Falling back to next model...")
            current_model_idx += 1

    if not success_meta:
        print("All models failed for metadata generation.")
        sys.exit(1)

    print("\nAll tasks completed successfully.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting...")
        sys.exit(1)
