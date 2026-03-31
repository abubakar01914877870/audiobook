import os
import sys
import argparse
import time
import re
import subprocess
import concurrent.futures
import fitz
from character_discovery import build_character_reference_block
from dotenv import load_dotenv

# ── Global style & world constants (injected into every prompt) ───────────────

STYLE_ANCHOR = (
    "Dark fantasy manga illustration, rich linework, painterly shading, "
    "Studio Trigger × Wit Studio aesthetic, 9:16 portrait orientation, "
    "Victorian/Edwardian era (1880s–1910s), gas-lamp lighting, no modern elements."
)

WORLD_VISUAL_RULES = """\
━━━ WORLD VISUAL RULES (HARD — applies to every single prompt) ━━━
ERA: Victorian/Edwardian (1880s–1910s) — strictly enforced, no exceptions.
LIGHTING: gas lamps, candles, oil lanterns, fireplaces ONLY — absolutely no electric light.
ARCHITECTURE: stone, brick, wrought iron, carved wood, heavy drapes, cobblestone streets, fog.
CLOTHING: period-accurate — frock coats, waistcoats, cravats, top hats, corsets, capes, petticoats,
  tailcoats, bonnets, leather boots, gloves, pocket watches, walking sticks.
TECHNOLOGY: no electricity, no motor vehicles, no cameras — steam-power at most.
HARD PROHIBITION: NO phones, NO cars, NO electric lights, NO synthetic fabrics,
  NO contemporary clothing shapes, NO modern hairstyles.
Every prompt MUST open with this exact phrase (copy verbatim):
  "{style_anchor}"
"""


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

load_dotenv()

GEMINI_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

def get_available_models():
    print(f"Prioritized models: {', '.join(GEMINI_MODELS)}")
    return list(GEMINI_MODELS)

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


def run_claude_cli(prompt, retries=3):
    """Run Claude CLI as a fallback when all Gemini models fail.

    Retries up to `retries` times with exponential backoff on transient failures.
    Returns cleaned response text, or None if all attempts fail.
    """
    for attempt in range(1, retries + 1):
        try:
            print(f"  Claude CLI attempt {attempt}/{retries}...")
            result = subprocess.run(
                ["claude", "-p", prompt, "--output-format", "text"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600  # 10 minutes — sufficient for metadata generation
            )

            if result.returncode == 130:
                raise KeyboardInterrupt

            if result.returncode == 0:
                return clean_response(result.stdout)
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
            print("  'claude' CLI not found — Claude fallback unavailable.")
            return None
        except Exception as e:
            print(f"  Exception running Claude CLI: {e}")
            if attempt < retries:
                wait = 10 * attempt
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)

    print("  Claude CLI: all retry attempts exhausted.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Scene distribution helpers
# ─────────────────────────────────────────────────────────────────────────────

MIN_IMAGE_PROMPTS = 6


def estimate_scene_count(text: str) -> int:
    """Suggest a scene count based on word count (~400 words per scene), clamped to [6, 20].

    This is a hint passed to the AI — the AI makes the final decision.
    """
    word_count = len(text.split())
    return max(6, min(20, round(word_count / 400)))


def split_into_sections(text: str, n: int) -> list:
    """Split text into n roughly equal chunks by character count.

    Tries to break at a newline boundary rather than mid-sentence.
    """
    if n <= 1:
        return [text.strip()]

    chunk_size = max(1, len(text) // n)
    sections = []
    start = 0

    for i in range(n):
        if i == n - 1:
            sections.append(text[start:].strip())
        else:
            end = start + chunk_size
            # Prefer breaking at a newline within a small lookahead window
            newline_pos = text.rfind('\n', start, end + 150)
            if newline_pos > start:
                end = newline_pos
            sections.append(text[start:end].strip())
            start = end + 1

    return [s for s in sections if s]


def get_page_window(scene_idx: int, n_scenes: int, n_sections: int) -> tuple:
    """Return (start_section, end_section) for scene k (all 1-based, inclusive).

    Formula: center = 1 + (k-1) * (n_sections-1) / (n_scenes-1)
    Window:  [center-1, center+1] clamped to [1, n_sections]

    Examples for n_scenes=n_sections=9:
      Scene 1 → sections 1-2
      Scene 2 → sections 1-3
      Scene 3 → sections 2-4
      ...
      Scene 8 → sections 7-9
      Scene 9 → sections 8-9
    """
    if n_scenes <= 1:
        return (1, n_sections)
    center = 1.0 + (scene_idx - 1) * (n_sections - 1) / (n_scenes - 1)
    start  = max(1, round(center) - 1)
    end    = min(n_sections, round(center) + 1)
    return (start, end)


def build_scene_assignments(n_scenes: int, n_sections: int) -> str:
    """Return assignments_str for all scene prompts.

    assignments_str lists which section(s) each scene must cover to ensure 
    the scenes are distributed chronologically across the entire chapter.
    """
    lines = []
    for k in range(1, n_scenes + 1):
        prompt_num = k + 1  # Prompt 01 = thumbnail, so scenes start at 02
        start, end = get_page_window(k, n_scenes, n_sections)
        if start == end:
            section_ref = f"SECTION {start}"
        else:
            section_ref = " or ".join(f"SECTION {s}" for s in range(start, end + 1))
        lines.append(
            f"  Image Prompt {prompt_num:02d} → Section: {section_ref}"
        )
    return "\n".join(lines)


def build_section_text(sections: list) -> str:
    """Return the chapter text with embedded section dividers."""
    n = len(sections)
    parts = []
    for i, chunk in enumerate(sections):
        parts.append(f"--- SECTION {i + 1}/{n} ---\n{chunk}")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Main processing
# ─────────────────────────────────────────────────────────────────────────────

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

def count_image_prompts(meta_path: str) -> int:
    """Count '### Image Prompt NN' sections in a meta file. Returns 0 on error."""
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


def count_video_prompts(meta_path: str) -> int:
    """Count '**Video Prompt:**' fields in a meta file. Returns 0 on error."""
    try:
        with open(meta_path, encoding="utf-8") as f:
            content = f.read()
        return len(re.findall(r'\*\*Video Prompt:\*\*', content))
    except Exception:
        return 0


def review_prompts_for_consistency(meta_content: str, char_block: str, models: list, current_idx: int,
                                   primary_model: str = "gemini") -> tuple:
    """2nd pass: review all image prompts together for character and environment consistency.

    Sends all prompts to AI with character VISUAL DNA cards and asks it to:
    - Make same-character descriptions identical word-for-word across all prompts
    - Align lighting/palette for same-location prompts

    Returns (updated_meta_content: str, model_idx: int).
    On failure returns (original meta_content unchanged, model_idx).
    YouTube Metadata section is always preserved.
    """
    # Split meta into prompts section + YouTube metadata
    yt_split = meta_content.find("### YouTube Metadata")
    if yt_split == -1:
        prompts_section = meta_content.rstrip()
        yt_section = ""
    else:
        prompts_section = meta_content[:yt_split].rstrip()
        yt_section = meta_content[yt_split:]

    prompt_count = len(re.findall(r'###\s*Image Prompt\s+\d+', prompts_section))
    if prompt_count == 0:
        print("  [Consistency Review] No prompts found — skipping.")
        return meta_content, current_idx

    char_section = f"\n{char_block}\n\n" if char_block else ""

    _world_rules_block = WORLD_VISUAL_RULES.format(style_anchor=STYLE_ANCHOR)

    review_prompt = f"""You are a visual consistency editor for an AI image generation pipeline.

Below are {prompt_count} image prompts for a single chapter of a dark fantasy manga-style audiobook.
These images all come from the same chapter — the same characters and locations appear across multiple prompts.
{_world_rules_block}
{char_section}
YOUR TASK — apply these 6 consistency rules:

1. STYLE PREFIX (HARD RULE)
   - Every **Prompt:** field MUST begin with this EXACT phrase, word-for-word:
     "{STYLE_ANCHOR}"
   - If any prompt is missing it or has a paraphrased version, replace the opening with the exact phrase above.

2. ERA ENFORCEMENT (HARD RULE)
   - Every prompt must be firmly set in the Victorian/Edwardian era (1880s–1910s).
   - Remove ANY modern element: no electric lights, no synthetic fabrics, no contemporary clothing, no modern technology.
   - If a scene feels timeless or ambiguous, add a specific Victorian detail (gas lamp, cobblestones, iron railings, horse-drawn carriage, period clothing).
   - Character clothing must match their Era Outfit from the DNA cards above — no exceptions.

3. CHARACTER CONSISTENCY
   - Find every character that appears in more than one prompt.
   - Their Visual Anchor phrase (face, hair, eyes, outfit) must be IDENTICAL word-for-word in every prompt they appear in.
   - Use the VISUAL DNA cards above as the authoritative reference. Copy the anchor verbatim.
   - Their UNIQUE IDENTIFIER must appear in every prompt they appear in.
   - If a character has no DNA card, make their description consistent across prompts yourself.

4. ENVIRONMENT CONSISTENCY
   - When multiple prompts are set in the same location (same room, same street), align their lighting description and dominant color palette so they feel like the same place.
   - All lighting must use only Victorian sources: gas lamps, candles, oil lanterns, fireplaces — never electric.

5. ART STYLE COHERENCE
   - Verify the style prefix is present and identical in all prompts (rule 1).
   - Ensure Gothic mystery atmosphere is maintained throughout.

6. EMOTION COHERENCE
   - Read the Emotion fields in order (Prompt 01 → last). This is the chapter's emotional arc.
   - If consecutive scenes are in the same location with no major story mood shift, their Emotion values must be the same or adjacent (e.g. Fear/Anxiety → Mystery/Sophistication is fine; Fear/Anxiety → Joy/Energy without a clear story beat is not).
   - Correct any Emotion value that breaks the arc without a story reason.

DO NOT change:
  - Image Titles
  - position_scores
  - Which scene is depicted (do not substitute a different moment)
  - The Bengali text in Prompt 01
  - The ### Image Prompt XX — heading lines
  - The NUMBER of prompts — you MUST return exactly {prompt_count} prompts, no more, no less

ONLY refine the **Prompt** and **Emotion** fields of each image for consistency.

Return ONLY the corrected image prompts in the EXACT same Markdown format.
Do NOT include the YouTube Metadata section. Do NOT add any explanation or preamble.

=== IMAGE PROMPTS TO REVIEW ===

{prompts_section}"""

    response = None
    new_idx = current_idx

    def _gemini_pass_consistency():
        nonlocal response, new_idx
        idx = new_idx
        while idx < len(models):
            model = models[idx]
            print(f"  [Consistency Review] Trying {model}...")
            r = run_gemini_cli(model, review_prompt)
            if r:
                print(f"  [Consistency Review] Success with {model}.")
                response = r
                new_idx = idx
                return
            print(f"  [Consistency Review] {model} failed. Trying next...")
            idx += 1
        new_idx = idx

    def _claude_pass_consistency():
        nonlocal response
        print("  [Consistency Review] Trying Claude CLI...")
        r = run_claude_cli(review_prompt)
        if r:
            print("  [Consistency Review] Success with Claude CLI.")
            response = r

    if primary_model == "claude":
        _claude_pass_consistency()
        if not response:
            print("  [Consistency Review] Claude failed — falling back to Gemini models...")
            _gemini_pass_consistency()
    else:
        _gemini_pass_consistency()
        if not response:
            print("  [Consistency Review] All Gemini models failed — trying Claude CLI...")
            _claude_pass_consistency()

    if not response:
        print("  [Consistency Review] WARNING: Review failed — keeping original prompts unchanged.")
        return meta_content, new_idx

    # Validate: reviewed response must contain exactly the same number of prompts
    reviewed_count = len(re.findall(r'###\s*Image Prompt\s+\d+', response))
    if reviewed_count != prompt_count:
        print(f"  [Consistency Review] WARNING: Got {reviewed_count}/{prompt_count} prompts back — keeping original.")
        return meta_content, new_idx

    # Reconstruct: reviewed prompts + original YouTube metadata
    if yt_section:
        updated = response.rstrip() + "\n\n" + yt_section
    else:
        updated = response.rstrip()

    print(f"  [Consistency Review] {reviewed_count} prompts reviewed and updated.")
    return updated, new_idx


def generate_video_prompts(meta_content: str, models: list, current_idx: int,
                           primary_model: str = "gemini") -> tuple:
    """Pass 3: generate a **Video Prompt:** for every image prompt.

    Sends all finalized image prompts (with Emotion values) to AI in one call.
    Returns (updated_meta_content, model_idx).
    On failure returns (original meta_content unchanged, model_idx).
    YouTube Metadata section is always preserved.
    """
    yt_split = meta_content.find("### YouTube Metadata")
    if yt_split == -1:
        prompts_section = meta_content.rstrip()
        yt_section = ""
    else:
        prompts_section = meta_content[:yt_split].rstrip()
        yt_section = meta_content[yt_split:]

    prompt_count = len(re.findall(r'###\s*Image Prompt\s+\d+', prompts_section))
    if prompt_count == 0:
        print("  [Video Prompts] No image prompts found — skipping.")
        return meta_content, current_idx

    existing_video = len(re.findall(r'\*\*Video Prompt:\*\*', prompts_section))
    if existing_video >= prompt_count:
        print(f"  [Video Prompts] All {prompt_count} video prompts already present — skipping.")
        return meta_content, current_idx

    video_gen_prompt = f"""You are a video animation director for an AI audiobook pipeline.

Below are {prompt_count} image prompts for a single chapter of a dark fantasy audiobook (Lord of the Mysteries).
Each image is set in the Victorian/Edwardian era (1880s–1910s) — gas lamps, candles, cobblestones, period clothing.
All motion and ambient audio must be consistent with this era — no electric sounds, no modern ambience.
Each image will be animated into a 10-second video using Super Grok image-to-video (model: grok-imagine-video).
The image is the FIRST FRAME — your prompt describes how it comes alive.

CRITICAL — SEAMLESS LOOPING REQUIREMENT:
Each 10-second clip will loop continuously for 1 minute or more behind the spoken narration.
The clip MUST loop seamlessly — it must end in the exact same visual state as it begins.
This means ALL motion must be CYCLICAL — it returns to its starting position by the end of the clip.

LOOPABLE CAMERA MOTIONS (use ONLY these):
  - locked-off static (no camera movement at all — safest choice for looping)
  - slow oscillating pan (camera drifts slightly left, then returns to center — or vice versa)
  - gentle breathing zoom (very subtle zoom in then back out to start — or out then back in)
  - slow parallax drift (background layers shift slightly then return to start position)
  DO NOT USE: push-in, pull-out, or any one-directional camera move — these cannot loop.

LOOPABLE PHYSICAL MOTIONS (all must be continuous and cyclical):
  - flame flickers continuously, candle pulses rhythmically
  - fog rolls in and out in slow waves
  - cloth sways gently back and forth
  - smoke curls upward continuously (smoke naturally loops well)
  - rain streaks fall continuously from top to bottom
  - leaves tremble continuously in a gentle breeze
  - water surface shimmers with repeating ripples
  - dust motes float slowly and continuously
  DO NOT USE: a character turning their head, a door opening, any one-shot action that has a clear end state different from the start.

AUDIOBOOK CONTEXT: These videos play silently behind spoken Bengali narration.
Keep all camera motion SUBTLE and SLOW — visuals support the listening experience, they must NOT compete with or distract from it.
No fast cuts, no sudden movements, no jarring transitions.

EMOTION → AUDIO MAPPING (use each image's Emotion field to choose ambient sounds and music):
  Joy/Energy             → bright ambient (birdsong, bustling street, cheerful crowd) + upbeat strings or light brass, lively tempo
  Sadness/Melancholy     → quiet ambient (soft rain, distant wind, silence) + slow solo piano or sparse strings, low register
  Anger/Tension          → tense ambient (crackling fire, distant rumble, metal scrape) + heavy low brass or driving percussion
  Nostalgia/Warmth       → warm ambient (fireplace crackle, soft breeze, gentle water) + acoustic guitar or warm strings, gentle tempo
  Fear/Anxiety           → unsettling ambient (dripping water, eerie hum, faint footsteps) + sparse dissonant strings, high tension
  Peace/Tranquility      → gentle ambient (soft breeze, leaves rustling, distant birds) + soft flute or harp, slow and airy
  Mystery/Sophistication → quiet ambient (low stone reverb, distant clock tick, flickering flame) + solo cello or viola, slow and deliberate

For EACH image prompt, write a **Video Prompt:** field as ONE flowing natural-language paragraph. Describe:
  1. Camera: one LOOPABLE movement from the approved list above (or locked-off static)
  2. Motion: what physically moves — use ONLY continuous cyclical motions from the approved list above
  3. Lighting: any subtle dynamic (candle pulse, ray shift, faint flicker) — or "lighting remains static" if none
  4. Audio: specific ambient environment sounds + background music genre/instruments/tempo/mood from the Emotion mapping above

End every Video Prompt paragraph with exactly: "Seamless loop. No speech. No voice. No dialogue."

STRICT RULES:
  - ALL motion must be cyclical and return to its starting state — the clip must loop invisibly
  - Keep motion gentle — this is an audiobook backdrop, not an action trailer
  - Match ambient sounds to the physical environment visible in the image
  - Music must match the image's Emotion field exactly using the mapping above
  - NO voice narration, NO speech, NO dialogue, NO singing in the audio description
  - Write ONLY the **Video Prompt:** field for each prompt — do NOT rewrite or repeat any other fields
  - You MUST generate exactly {prompt_count} Video Prompts — one per image prompt, no more, no less

Return the FULL prompts section with **Video Prompt:** appended after **Emotion:** in EVERY image prompt block.
Use EXACTLY this Markdown structure for the new field:

**Video Prompt:** [natural language paragraph ending with "Seamless loop. No speech. No voice. No dialogue."]

Do NOT include the YouTube Metadata section. Do NOT add any explanation or preamble.

=== IMAGE PROMPTS ===

{prompts_section}"""

    response = None
    new_idx = current_idx

    def _gemini_pass_vp():
        nonlocal response, new_idx
        idx = new_idx
        while idx < len(models):
            model = models[idx]
            print(f"  [Video Prompts] Trying {model}...")
            r = run_gemini_cli(model, video_gen_prompt)
            if r:
                print(f"  [Video Prompts] Success with {model}.")
                response = r
                new_idx = idx
                return
            print(f"  [Video Prompts] {model} failed. Trying next...")
            idx += 1
        new_idx = idx

    def _claude_pass_vp():
        nonlocal response
        print("  [Video Prompts] Trying Claude CLI...")
        r = run_claude_cli(video_gen_prompt)
        if r:
            print("  [Video Prompts] Success with Claude CLI.")
            response = r

    if primary_model == "claude":
        _claude_pass_vp()
        if not response:
            print("  [Video Prompts] Claude failed — falling back to Gemini models...")
            _gemini_pass_vp()
    else:
        _gemini_pass_vp()
        if not response:
            print("  [Video Prompts] All Gemini models failed — trying Claude CLI...")
            _claude_pass_vp()

    if not response:
        print("  [Video Prompts] WARNING: Generation failed — keeping original content unchanged.")
        return meta_content, new_idx

    reviewed_count = len(re.findall(r'###\s*Image Prompt\s+\d+', response))
    video_count = len(re.findall(r'\*\*Video Prompt:\*\*', response))
    if reviewed_count != prompt_count or video_count != prompt_count:
        print(f"  [Video Prompts] WARNING: Got {reviewed_count} prompts / {video_count} video prompts (expected {prompt_count} each) — keeping original.")
        return meta_content, new_idx

    updated = response.rstrip() + ("\n\n" + yt_section if yt_section else "")
    print(f"  [Video Prompts] {video_count} video prompts generated.")
    return updated, new_idx


def generate_video_consistency(meta_content: str, models: list, current_idx: int,
                               primary_model: str = "gemini") -> tuple:
    """Pass 4: lightweight consistency review for video prompts.

    Checks:
    1. Same location across clips → same ambient sounds word-for-word
    2. Music arc follows the chapter's emotional arc (no jarring jumps)
    3. Loopability — all camera and physical motions are cyclical; ending tag is correct

    Returns (updated_meta_content, model_idx).
    On failure returns (original meta_content unchanged, model_idx).
    YouTube Metadata section is always preserved.
    """
    yt_split = meta_content.find("### YouTube Metadata")
    if yt_split == -1:
        prompts_section = meta_content.rstrip()
        yt_section = ""
    else:
        prompts_section = meta_content[:yt_split].rstrip()
        yt_section = meta_content[yt_split:]

    video_count = len(re.findall(r'\*\*Video Prompt:\*\*', prompts_section))
    if video_count == 0:
        print("  [Video Consistency] No video prompts found — skipping.")
        return meta_content, current_idx

    review_prompt = f"""You are an audio-visual consistency editor for an AI audiobook pipeline.

Below are image prompts with Video Prompts for a single chapter of a dark fantasy audiobook set in the Victorian/Edwardian era (1880s–1910s).
These 10-second video clips loop continuously (1+ minutes each) behind spoken narration — they must feel like a coherent chapter, not disconnected clips.
ERA HARD RULE: All ambient sounds and motion must be period-accurate — horse hooves, gas-lamp flicker, cobblestone echo, fireplace crackle, wind through iron railings. No electric sounds, no modern ambience.

YOUR TASK — apply these 3 consistency rules to the **Video Prompt:** fields ONLY:

1. ERA AUDIO ENFORCEMENT (HARD RULE)
   - All ambient sounds must be Victorian/Edwardian — no electric hum, no modern traffic, no synthetic sounds.
   - Valid Victorian ambience: horse hooves on cobblestones, gas-lamp hiss, fireplace crackle, wind through iron railings, clock ticking, rain on stone, distant foghorn, crowd murmur in period clothing.
   - If any Video Prompt contains a non-Victorian sound, replace it with the nearest period-accurate equivalent.

2. LOCATION AUDIO CONSISTENCY
   - Identify clips that are set in the same physical location (same room, same street, same outdoor space).
   - Their ambient sound descriptions must be IDENTICAL word-for-word across all clips in that location.
   - Example: if three clips are in the same stone chamber, all three must have the exact same ambient description.

3. MUSIC ARC COHERENCE
   - Read the Emotion fields in order (Prompt 01 → last). This is the chapter's emotional arc.
   - The music described in each Video Prompt must follow this arc smoothly.
   - Fix any jarring music jump (e.g. "dark cello" immediately followed by "upbeat bright strings" with no emotional story reason).
   - Transitions between adjacent emotions should feel gradual unless a dramatic story beat justifies the shift.

4. LOOPABILITY CHECK
   - Each clip loops seamlessly for 1+ minutes. ALL motion must be cyclical (returns to its start state by end of clip).
   - Approved loopable camera moves: locked-off static, slow oscillating pan, gentle breathing zoom, slow parallax drift.
   - NON-LOOPABLE camera moves to fix: push-in, pull-out, or any one-directional move. Replace with the closest loopable equivalent.
   - Approved loopable physical motions: continuous flame flicker, fog rolling in waves, cloth swaying back and forth, smoke curling upward, rain streaking continuously, leaves trembling, water shimmering with repeating ripples, dust motes floating.
   - NON-LOOPABLE physical motions to fix: a character turning their head, a door opening, any action with a clear different end state. Replace with the nearest loopable equivalent (e.g. "a character turning their head" → "cloth on their shoulder sways gently").
   - Every Video Prompt must end with exactly: "Seamless loop. No speech. No voice. No dialogue."
     If it ends with the old "No speech. No voice. No dialogue." (without "Seamless loop."), add "Seamless loop." before it.

DO NOT change:
  - Image Titles, Prompts, position_scores, or Emotion fields
  - The ### Image Prompt XX — heading lines
  - The NUMBER of prompts — you MUST return exactly {video_count} prompts, no more, no less

ONLY refine the camera motion, physical motion, and ending tag within **Video Prompt:** fields, plus ambient sounds and music.

Return ONLY the corrected prompts section in the EXACT same Markdown format.
Do NOT include the YouTube Metadata section. Do NOT add any explanation or preamble.

=== IMAGE PROMPTS WITH VIDEO PROMPTS ===

{prompts_section}"""

    response = None
    new_idx = current_idx

    def _gemini_pass_vc():
        nonlocal response, new_idx
        idx = new_idx
        while idx < len(models):
            model = models[idx]
            print(f"  [Video Consistency] Trying {model}...")
            r = run_gemini_cli(model, review_prompt)
            if r:
                print(f"  [Video Consistency] Success with {model}.")
                response = r
                new_idx = idx
                return
            print(f"  [Video Consistency] {model} failed. Trying next...")
            idx += 1
        new_idx = idx

    def _claude_pass_vc():
        nonlocal response
        print("  [Video Consistency] Trying Claude CLI...")
        r = run_claude_cli(review_prompt)
        if r:
            print("  [Video Consistency] Success with Claude CLI.")
            response = r

    if primary_model == "claude":
        _claude_pass_vc()
        if not response:
            print("  [Video Consistency] Claude failed — falling back to Gemini models...")
            _gemini_pass_vc()
    else:
        _gemini_pass_vc()
        if not response:
            print("  [Video Consistency] All Gemini models failed — trying Claude CLI...")
            _claude_pass_vc()

    if not response:
        print("  [Video Consistency] WARNING: Review failed — keeping original video prompts unchanged.")
        return meta_content, new_idx

    prompt_count = len(re.findall(r'###\s*Image Prompt\s+\d+', prompts_section))
    reviewed_count = len(re.findall(r'###\s*Image Prompt\s+\d+', response))
    if reviewed_count != prompt_count:
        print(f"  [Video Consistency] WARNING: Got {reviewed_count}/{prompt_count} prompts back — keeping original.")
        return meta_content, new_idx

    updated = response.rstrip() + ("\n\n" + yt_section if yt_section else "")
    print(f"  [Video Consistency] Audio consistency review done.")
    return updated, new_idx


def process_file(pdf_path, output_dir, models, start_model_idx, primary_model: str = "gemini"):
    base_name = os.path.basename(pdf_path)
    stem = os.path.splitext(base_name)[0]
    num_match = re.search(r'(\d+)', stem)
    chapter_num = int(num_match.group(1)) if num_match else 0
    num_str = f"{chapter_num:03d}"

    if output_dir is None:
        abs_pdf = os.path.abspath(pdf_path)
        if "chapters_split" in abs_pdf:
            base_vol = abs_pdf.split("chapters_split")[0]
            output_dir = os.path.join(base_vol, "output", f"ch_{chapter_num}")
        else:
            output_dir = os.path.join(os.getcwd(), "output", f"ch_{chapter_num}")

    os.makedirs(output_dir, exist_ok=True)

    print(f"\n--- Processing Metadata: {pdf_path} ---")

    text = extract_text(pdf_path)
    if not text:
        print(f"Error reading {pdf_path} or no text found.")
        sys.exit(1)

    chapter_name = extract_chapter_name_from_text(text)
    output_filename = f"Chapter_{num_str}_{chapter_name}_meta.md"
    output_md = os.path.join(output_dir, output_filename)

    if os.path.exists(output_md):
        prompt_count = count_image_prompts(output_md)
        if prompt_count >= MIN_IMAGE_PROMPTS:
            existing_vp = count_video_prompts(output_md)
            if existing_vp >= prompt_count:
                print(f"Skipping {pdf_path}, metadata already complete ({prompt_count} image + {existing_vp} video prompts): {output_filename}")
                return start_model_idx
            # Image prompts done but video prompts missing — run passes 3-4 only
            print(f"Image prompts complete ({prompt_count}), video prompts: {existing_vp}/{prompt_count}. Running passes 3-4 only...")
            current_idx = start_model_idx
            with open(output_md, encoding="utf-8") as f:
                meta_content = f.read()
            video_content, current_idx = generate_video_prompts(meta_content, models, current_idx, primary_model=primary_model)
            with open(output_md, "w", encoding="utf-8") as f:
                f.write(video_content)
            vc = count_video_prompts(output_md)
            print(f"Video prompts done. {vc} video prompts in {os.path.basename(output_md)}")
            with open(output_md, encoding="utf-8") as f:
                meta_content = f.read()
            final_content, current_idx = generate_video_consistency(meta_content, models, current_idx, primary_model=primary_model)
            with open(output_md, "w", encoding="utf-8") as f:
                f.write(final_content)
            print(f"Video consistency review done. {os.path.basename(output_md)}")
            return current_idx
        print(f"Meta exists but only {prompt_count} prompt(s) (minimum {MIN_IMAGE_PROMPTS}). Regenerating...")
        os.remove(output_md)
    
    chapter_num_str = num_str

    # ── Character reference block (injected into prompts for visual consistency) ─
    import glob
    if not glob.glob(os.path.join(output_dir, "*_character.json")):
        print(f"Error: Missing *_character.json in {output_dir}. You MUST run Character Discovery before generating Video Metadata.")
        return start_model_idx

    _char_block = build_character_reference_block(output_dir)
    char_block_section = (
        f"\n{_char_block}\n\n"
        "CHARACTER RULE: For every scene prompt — when a character appears, copy their Visual Anchor phrase "
        "WORD-FOR-WORD into the Prompt field. Do not paraphrase or shorten it.\n"
    ) if _char_block else ""

    # ── Scene distribution ────────────────────────────────────────────────────
    suggested_scenes = estimate_scene_count(text)
    word_count = len(text.split())
    n_sections = max(6, suggested_scenes)
    sections   = split_into_sections(text, n_sections)
    section_text = build_section_text(sections)

    print(f"  Words: {word_count} → max scene extraction ({n_sections} sections, AI identifies every distinct moment)")

    output_format_scenes = (
        "### Image Prompt 02 — Scene\n"
        "**Image Title:** [short evocative title, 3–7 words]\n"
        "**Prompt:** [single paragraph prompt, no text overlay]\n"
        "**position_score:** [estimated 0-100 indicating exact chronological timing]\n"
        "**Emotion:** [one of: Joy/Energy | Sadness/Melancholy | Anger/Tension | Nostalgia/Warmth | Fear/Anxiety | Peace/Tranquility | Mystery/Sophistication]\n\n"
        "### Image Prompt 03 — Scene\n"
        "... (continue this exact structure for every scene you identify)"
    )

    _world_rules = WORLD_VISUAL_RULES.format(style_anchor=STYLE_ANCHOR)

    system_prompt = f"""You are an expert AI assistant tasked with generating metadata for a YouTube narrated audiobook.
The following English text is the original chapter of the novel "Lord of the Mysteries".

Based ONLY on this text, generate a response containing image generation prompts and YouTube video metadata.

{_world_rules}

═══════════════════════════════════════════════════
SECTION 1 — IMAGE GENERATION PROMPTS
═══════════════════════════════════════════════════

SCENE COUNT — MAXIMUM EXTRACTION:
  Your goal is to extract the MAXIMUM number of distinct scenes from this chapter.
  Read every paragraph. Every location change, action beat, dialogue exchange, emotional shift,
  or new dramatic moment is a separate scene — give it its own image prompt.
  Do NOT merge or group scenes together. Do NOT skip a scene to keep the count low.
  Hard limit: maximum 20 scenes (cap only if the chapter truly has more than 20 distinct moments).
  Minimum: {MIN_IMAGE_PROMPTS} scenes.
  This chapter is ~{word_count} words — aim to extract every visual moment possible.

COVERAGE RULE: Scenes MUST cover the entire chapter from beginning to end.
  The chapter text has been divided into {n_sections} sections below for your reference.
  Every section must have at least one scene prompt. Do NOT skip any section.

ART STYLE — every **Prompt:** field MUST begin with this EXACT phrase, copied verbatim:
  "{STYLE_ANCHOR}"
Do NOT paraphrase it. Do NOT move it. It is always the first sentence of every Prompt field.
After this opening phrase, continue with: camera → scene → lighting → palette → mood.
Gothic mystery atmosphere. IMPORTANT: No graphic violence, no gore, no sexual content, no self-harm. PG-13 safe.
{char_block_section}
Each prompt MUST have exactly 4 fields: Image Title, Prompt, position_score, Emotion.
  - Image Title: 3–7 word evocative label for this image.
  - Prompt: single flowing paragraph — style → camera → scene → lighting → palette → mood  (+ Bengali text for Prompt 01 only). No bullet points inside the Prompt field. Prompt text only.
  - position_score: integer 0–100 — accurately estimate where precisely this sentence happens in the overall chapter (0 = very first word, 100 = very last word). For example, if you pick an iconic scene from the middle of the chapter, output a score around 50. If the scene happens early on, output 18. This helps listener connect easily with the image and the story.
  - Emotion: choose EXACTLY ONE value from this list that best matches the mood of the depicted scene:
      Joy/Energy          → high saturation, warm yellows, bright highlights
      Sadness/Melancholy  → desaturated, heavy blues/cyans, low contrast
      Anger/Tension       → high contrast, crushed blacks, heavy reds/oranges
      Nostalgia/Warmth    → raised blacks (faded), creamy highlights, sepia or teal/orange
      Fear/Anxiety        → unnatural greens, muddy shadows, high grain
      Peace/Tranquility   → soft pastels, balanced greens and soft blues
      Mystery/Sophistication → deep purples, high teal-orange contrast, dark shadows
    This value will be used to apply a color-grading LUT to the final video — choose carefully.

━━━ PROMPT 01 — THUMBNAIL (required) ━━━

Choose the single most iconic or dramatic moment from the entire chapter as the thumbnail.
Camera: any shot type that best suits the scene's emotional weight.
Lighting: be specific (e.g. "warm amber gas-lamp chiaroscuro", "cold moonlight rim-lighting with volumetric fog").
Color palette: name 3–4 dominant colors.
Mood: one sentence.
position_score for thumbnail: estimate its exact score (0-100) based on where this iconic moment occurs in the story (we will duplicate this image for both the thumbnail and that specific timestamp).

BENGALI TEXT (thumbnail only — CRITICAL):
  The image MUST include this exact Bengali text as glowing stylized typography integrated into the composition:
  "অধ্যায় {chapter_num_str}: [Bengali translation of '{chapter_name}']"
  Placement: centered in the lower-middle third. Style: luminous golden lettering with dark semi-transparent background.

━━━ PROMPTS 02–NN — SCENE IMAGES (MAXIMUM — ONE PER DISTINCT MOMENT) ━━━

Extract EVERY distinct scene. Do not merge. Do not skip.
Each prompt MUST come from a different moment — cover the chapter from start to finish.
Estimate the position_score exactly based on where that specific scene happens in the story flow.
NO Bengali text in scene prompts — image only.
Same structure: style → camera → scene → lighting → palette → mood.

Output Format — use EXACTLY this Markdown structure, nothing else:

### Image Prompt 01 — Thumbnail
**Image Title:** [short evocative title, 3–7 words]
**Prompt:** [single paragraph prompt including Bengali text]
**position_score:** [estimated 0-100]
**Emotion:** [one of: Joy/Energy | Sadness/Melancholy | Anger/Tension | Nostalgia/Warmth | Fear/Anxiety | Peace/Tranquility | Mystery/Sophistication]

{output_format_scenes}

═══════════════════════════════════════════════════
SECTION 2 — YOUTUBE VIDEO METADATA
═══════════════════════════════════════════════════

- Video Title: Engaging Bengali title. MUST include chapter number ({chapter_num_str}) and chapter name. STRICT LIMIT: 100 characters maximum (count every Unicode character including Bengali). If the title would exceed 100 characters, shorten the descriptive part — never cut the chapter number or chapter name.
- Video Description:
   - Short, engaging summary of this chapter's events in Bengali (3–5 sentences).
   - Credit: মূল লেখক: Cuttlefish That Loves Diving
   - Include exactly: "Read the story in text here: https://kalponic.web.app/"
   - Hashtags at the end: #BanglaStory #BanglaAudiobook #LordOfTheMysteries #BengaliTranslated #রহস্যের_প্রভু

Output Format — use EXACTLY this Markdown structure, appended after all image prompts:

### YouTube Metadata
**Title:** [Bengali title]

**Description:**
[Bengali description + credit + link + hashtags]
"""

    full_prompt = (
        system_prompt
        + f"\n\n=== Original English Chapter Text (divided into {n_sections} sections) ===\n\n"
        + section_text
    )

    current_idx = start_model_idx
    response_text = None

    def _gemini_pass1():
        nonlocal response_text, current_idx
        idx = current_idx
        while idx < len(models):
            model = models[idx]
            print(f"[Gemini] Running metadata for {base_name} with model: {model}...")
            result = run_gemini_cli(model, full_prompt)
            if result:
                print(f"[Gemini] Success with {model}!")
                response_text = result
                current_idx = idx
                return
            print(f"[Gemini] Failed with {model}. Trying next...")
            idx += 1
        current_idx = idx

    def _claude_pass1():
        nonlocal response_text
        print(f"[Claude] Running metadata for {base_name} via Claude CLI...")
        result = run_claude_cli(full_prompt)
        if result:
            print("[Claude] Success!")
            response_text = result

    if primary_model == "claude":
        _claude_pass1()
        if not response_text:
            print(f"[Claude] Failed — falling back to Gemini models...")
            _gemini_pass1()
    else:
        _gemini_pass1()
        if not response_text:
            print(f"[Claude] All Gemini models failed for {base_name}. Trying Claude CLI fallback...")
            _claude_pass1()

    if response_text:
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(response_text)
        generated_count = count_image_prompts(output_md)
        print(f"Saved metadata: {output_md} ({generated_count} image prompts)")
        if generated_count < MIN_IMAGE_PROMPTS:
            print(f"Warning: only {generated_count} prompt(s) generated (minimum {MIN_IMAGE_PROMPTS}). Re-run to regenerate.")
            os.remove(output_md)
            sys.exit(2)
    else:
        print(f"All models (Gemini + Claude) failed for {pdf_path}.")
        sys.exit(2)

    # ── 2nd pass: consistency review ─────────────────────────────────────────
    print(f"\n--- Running Consistency Review: {base_name} ---")
    with open(output_md, encoding="utf-8") as f:
        meta_content = f.read()
    reviewed_content, current_idx = review_prompts_for_consistency(
        meta_content, _char_block, models, current_idx, primary_model=primary_model
    )
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(reviewed_content)
    final_count = count_image_prompts(output_md)
    print(f"Consistency review done. {final_count} prompts in {os.path.basename(output_md)}")

    # ── 3rd pass: video prompt generation ────────────────────────────────────
    print(f"\n--- Generating Video Prompts: {base_name} ---")
    with open(output_md, encoding="utf-8") as f:
        meta_content = f.read()
    video_content, current_idx = generate_video_prompts(meta_content, models, current_idx, primary_model=primary_model)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(video_content)
    vc = count_video_prompts(output_md)
    print(f"Video prompts done. {vc} video prompts in {os.path.basename(output_md)}")

    # ── 4th pass: video consistency review ───────────────────────────────────
    print(f"\n--- Running Video Consistency Review: {base_name} ---")
    with open(output_md, encoding="utf-8") as f:
        meta_content = f.read()
    final_content, current_idx = generate_video_consistency(meta_content, models, current_idx, primary_model=primary_model)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(final_content)
    print(f"Video consistency review done. {os.path.basename(output_md)}")

    return current_idx

def main():
    parser = argparse.ArgumentParser(description="Generate image prompt and YouTube metadata from original English PDF.")
    parser.add_argument("pdf_input", help="Path to a PDF file or a directory containing PDFs")
    parser.add_argument("output_folder", nargs="?", default=None, help="Directory to save the metadata files")
    parser.add_argument(
        "--skip-models", default="",
        help="Comma-separated list of Gemini models to skip (quota-exhausted this run)."
    )
    parser.add_argument(
        "--primary-model", choices=["claude", "gemini"], default="gemini",
        help="Primary AI model for text generation (default: gemini). gemini = Gemini first, Claude fallback. claude = Claude first, Gemini fallback.",
    )

    args = parser.parse_args()
    input_path = args.pdf_input
    output_dir = args.output_folder

    if not os.path.exists(input_path):
        print(f"Error: Input '{input_path}' not found.")
        sys.exit(1)
        
    if output_dir is not None:
        os.makedirs(output_dir, exist_ok=True)

    pdf_files = []
    if os.path.isdir(input_path):
        for root, dirs, files in os.walk(input_path):
            for file in files:
                if file.lower().endswith(".pdf"):
                    pdf_files.append(os.path.join(root, file))
        pdf_files.sort()
        print(f"Found {len(pdf_files)} PDF files to process.")
    else:
        if input_path.lower().endswith(".pdf"):
            pdf_files = [input_path]
        else:
            print("Provided file is not a valid PDF.")
            sys.exit(1)

    if not pdf_files:
        print("No valid .pdf files found to process.")
        return

    models = get_available_models()
    if args.skip_models:
        skip_set = {m.strip() for m in args.skip_models.split(",") if m.strip()}
        before = len(models)
        models = [m for m in models if m not in skip_set]
        skipped = before - len(models)
        if skipped:
            print(f"  Skipping {skipped} quota-exhausted Gemini model(s): {', '.join(skip_set)}")

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
        print("Warning: No Gemini models available (>= 10% quota). Will rely on Claude CLI fallback only.")

    models = valid_models
    current_model_idx = 0

    try:
        for i, pdf_path in enumerate(pdf_files):
            print(f"\nProgress: {i+1}/{len(pdf_files)}")
            current_model_idx = process_file(pdf_path, output_dir, models, current_model_idx, primary_model=args.primary_model)
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
