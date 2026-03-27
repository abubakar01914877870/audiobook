import os
import sys
import argparse
import time
import re
import subprocess
import concurrent.futures
from dotenv import load_dotenv

load_dotenv()

def get_available_models():
    prioritized_models = [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite"
    ]
    print(f"Prioritized models: {', '.join(prioritized_models)}")
    return prioritized_models

def check_model_state(model):
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

def clean_response(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)

    code_blocks = re.findall(r'```(?:markdown)?\n?(.*?)\n?```', text, re.DOTALL)
    if code_blocks:
        code_blocks.sort(key=len, reverse=True)
        text = code_blocks[0]
        
    return text.strip()

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
            return clean_response(result.stdout)
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

def process_file(md_path, models, start_model_idx):
    if md_path.endswith("_meta.md"):
        return start_model_idx

    base_name = os.path.basename(md_path)
    stem = os.path.splitext(base_name)[0]
    output_md = os.path.join(os.path.dirname(md_path), f"{stem}_meta.md")

    if os.path.exists(output_md):
        print(f"Skipping {md_path}, metadata already exists.")
        return start_model_idx

    print(f"\n--- Processing Metadata: {md_path} ---")

    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(f"Error reading {md_path}: {e}")
        return start_model_idx

    num_match = re.search(r'(\d+)', stem)
    chapter_num_str = num_match.group(1) if num_match else ""
    chapter_name = stem.replace(f"Chapter_{chapter_num_str}_", "").replace("_", " ") if chapter_num_str else stem

    system_prompt = f"""You are an expert AI assistant tasked with generating metadata for a YouTube narrated audiobook.
The following text is a translated Bengali chapter of the novel "Lord of the Mysteries".

Based ONLY on this text, generate a single response containing two sections: Image Generation Prompt and YouTube Video Metadata.

═══════════════════════════════════════════════════
SECTION 1 — IMAGE GENERATION PROMPT
═══════════════════════════════════════════════════

Identify the single most visually dramatic or emotionally powerful moment in the chapter.
Then write an English image generation prompt following this EXACT structure and order:

1. ART STYLE
   Dark fantasy manga illustration. Rich linework, painterly shading, Studio Trigger / Wit Studio aesthetic.
   9:16 portrait orientation for mobile screen.

2. CAMERA & COMPOSITION
   Specify the shot type: e.g. "dramatic low-angle portrait", "wide atmospheric establishing shot",
   "close-up with shallow depth of field", "over-the-shoulder two-shot", etc.
   Choose whichever best suits the scene's emotional weight.

3. SCENE
   Describe characters (appearance, expression, posture, clothing) and environment with specific detail.
   Victorian/Edwardian-era setting. Reference the novel's gothic mystery atmosphere.
   IMPORTANT: No graphic violence, no gore, no sexual content, no self-harm. PG-13 safe.

4. LIGHTING
   Be specific: e.g. "warm amber gas-lamp chiaroscuro casting hard shadows across the left side of the face",
   "cold moonlight rim-lighting with volumetric fog", "flickering candlelight with deep charcoal shadows".

5. COLOR PALETTE
   Name 3–4 specific dominant colors: e.g. "deep charcoal black, warm amber gold, cold slate blue, ivory".

6. MOOD
   One sentence: e.g. "Tense and mysterious, the silence before danger."

7. BENGALI TEXT (CRITICAL — state this clearly)
   The image MUST include this exact Bengali text rendered as glowing, stylized typography
   integrated naturally into the composition (not just overlaid):
   "অধ্যায় {chapter_num_str}: [Bengali translation of '{chapter_name}']"
   Placement: upper area of the image. Style: luminous golden or bright warm lettering.

Write the prompt as a single flowing paragraph in this order: style → camera → scene → lighting → palette → mood → text.
Do NOT use bullet points in the final prompt output. Do NOT add any explanation — only the prompt itself.

═══════════════════════════════════════════════════
SECTION 2 — YOUTUBE VIDEO METADATA
═══════════════════════════════════════════════════

- Video Title: Engaging Bengali title. MUST include chapter number ({chapter_num_str}) and chapter name.
- Video Description:
   - Short, engaging summary of this chapter's events in Bengali (3–5 sentences).
   - Credit: মূল লেখক: Cuttlefish That Loves Diving
   - Include exactly: "Read the story in text here: https://kalponic.web.app/"
   - Hashtags at the end: #BanglaStory #BanglaAudiobook #LordOfTheMysteries #BengaliTranslated #রহস্যের_প্রভু

Output Format — use EXACTLY this Markdown structure, nothing else:

### Image Generation Prompt
[Single paragraph prompt here]

### YouTube Metadata
**Title:** [Bengali title]

**Description:**
[Bengali description + credit + link + hashtags]
"""
    
    full_prompt = system_prompt + "\n\n=== Bengali Chapter Text ===\n\n" + text

    current_idx = start_model_idx
    success = False

    while current_idx < len(models):
        model = models[current_idx]
        print(f"Run metadata generation for {base_name} with model: {model}...")
        
        response_text = run_gemini_cli(model, full_prompt)
        
        if response_text:
            with open(output_md, "w", encoding="utf-8") as f:
                f.write(response_text)
                
            print(f"Success with {model}! Saved metadata: {output_md}")
            success = True
            break
        else:
            print(f"Metadata generation failed with {model}. Falling back to next model...")
            current_idx += 1

    if not success:
        print(f"All models failed for {md_path}.")
    
    return current_idx

def main():
    parser = argparse.ArgumentParser(description="Generate image prompt and YouTube metadata from translated MD.")
    parser.add_argument("input_path", help="Path to a translated .md file or a directory containing .md files")
    
    args = parser.parse_args()
    input_path = args.input_path

    if not os.path.exists(input_path):
        print(f"Error: Input '{input_path}' not found.")
        sys.exit(1)

    md_files = []
    if os.path.isdir(input_path):
        for root, dirs, files in os.walk(input_path):
            for file in files:
                if file.lower().endswith(".md") and not file.endswith("_meta.md"):
                    md_files.append(os.path.join(root, file))
        md_files.sort()
        print(f"Found {len(md_files)} Markdown files to process.")
    else:
        if input_path.lower().endswith(".md") and not input_path.endswith("_meta.md"):
            md_files = [input_path]
        else:
            print("Provided file is not a valid markdown or is already a meta file.")
            sys.exit(1)

    if not md_files:
        print("No valid .md files found to process.")
        return

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
        
    models = valid_models
    current_model_idx = 0

    try:
        for i, md_path in enumerate(md_files):
            print(f"\nProgress: {i+1}/{len(md_files)}")
            if current_model_idx >= len(models):
                print("All available models have been exhausted. Stopping.")
                break
                
            current_model_idx = process_file(md_path, models, current_model_idx)
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
