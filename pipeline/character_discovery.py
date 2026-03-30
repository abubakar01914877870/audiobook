"""
character_discovery.py — Character discovery and consistency tracking.

Two outputs per chapter:
  1. characters.json (global master) — grows over time, every character ever seen
  2. Chapter_NNN_Title_character.json (chapter-level) — copy of only the characters
     in this chapter, stored alongside the translated MD in ch_N/. Used as both
     the skip marker and the source for translation/image prompt injection.

Model fallback order (for discovery):
  gemini-3.1-pro-preview → gemini-3-flash-preview → gemini-2.5-pro →
  gemini-2.5-flash → gemini-2.5-flash-lite → claude CLI → sys.exit(1)

NOTE: Failed Gemini models are added to the shared failed_models set, which is
also used by the translation and metadata steps in master_script.
"""

import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
CHARACTERS_JSON_PATH = os.path.join(_PROJECT_ROOT, "characters.json")

DISCOVERY_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


# ── CLI helpers ────────────────────────────────────────────────────────────────

def _call_gemini_cli(model: str, prompt: str) -> Optional[str]:
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
            return result.stdout
        stderr = result.stderr.strip()
        if "quota" in stderr.lower() or "429" in stderr or "exhausted" in stderr.lower():
            print(f"  [{model}] Quota exhausted.")
        else:
            print(f"  [{model}] Error (exit {result.returncode}): {stderr[:120]}")
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


def _call_claude_cli(prompt: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
        if result.returncode == 130:
            raise KeyboardInterrupt
        if result.returncode == 0:
            return result.stdout
        print(f"  [claude] Error (exit {result.returncode}): {result.stderr.strip()[:120]}")
        return None
    except subprocess.TimeoutExpired:
        print("  [claude] Timed out.")
        return None
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    except FileNotFoundError:
        print("  [claude] CLI not found in PATH.")
        return None
    except Exception as e:
        print(f"  [claude] Unexpected error: {e}")
        return None


# ── JSON parsing ───────────────────────────────────────────────────────────────

def _clean_json_output(text: str) -> str:
    """Strip ANSI codes and extract raw JSON from model output."""
    ansi = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi.sub('', text).strip()
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return text


def _parse_discovery_response(raw: str) -> Optional[dict]:
    """Parse and validate the AI discovery response as JSON."""
    if not raw:
        return None
    text = _clean_json_output(raw)
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    end = -1
    for i, c in enumerate(text[start:], start):
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    try:
        data = json.loads(text[start:end + 1])
        if "characters_in_chapter" not in data:
            print("  [discovery] Response missing 'characters_in_chapter' key.")
            return None
        return data
    except json.JSONDecodeError as e:
        print(f"  [discovery] JSON parse error: {e}")
        return None


# ── Chapter JSON helpers ───────────────────────────────────────────────────────

def _find_chapter_json(output_dir: str) -> Optional[str]:
    """Find the Chapter_NNN_*_character.json file in output_dir.
    Returns the file path if found, None otherwise.
    """
    files = glob.glob(os.path.join(output_dir, "*_character.json"))
    return files[0] if files else None


def _load_chapter_json(output_dir: str) -> Optional[dict]:
    """Load the chapter character JSON from output_dir. Returns None if not found."""
    path = _find_chapter_json(output_dir)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _write_chapter_json(
    output_dir: str,
    chapter_filename_stem: str,
    chapter_num: int,
    model_used: str,
    characters_in_chapter: list,
    master_characters: dict,
):
    """Write Chapter_NNN_Title_character.json with full character details copied from master."""
    chapter_chars = {}
    for name in characters_in_chapter:
        if name in master_characters:
            chapter_chars[name] = dict(master_characters[name])  # full copy

    data = {
        "chapter": chapter_num,
        "chapter_filename": chapter_filename_stem,
        "discovered_at": datetime.now().isoformat(),
        "model_used": model_used,
        "characters": chapter_chars,
    }
    out_path = os.path.join(output_dir, f"{chapter_filename_stem}_character.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Chapter character file: {os.path.basename(out_path)}")
    return out_path


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_discovery_prompt(text: str, existing_characters: dict) -> str:
    existing_names = list(existing_characters.get("characters", {}).keys())
    if existing_names:
        existing_list = "\n".join(f"  - {n}" for n in existing_names)
    else:
        existing_list = "  (none yet — this is the first chapter)"

    return f"""You are building a visual character database for the novel "Lord of the Mysteries" to ensure consistent AI image generation.

ALREADY TRACKED CHARACTERS (do NOT add these as new — only update if you find better confirmed info):
{existing_list}

TASK: Analyze the chapter text below and return ONLY valid JSON (no markdown fences, no explanation, no text before or after the JSON).

RULES:
1. List ALL named characters who appear or are mentioned in "characters_in_chapter"
2. For NEW characters (not in the tracked list): fill every mandatory field. If the text does not explicitly state a value, make a reasonable guess based on context (Victorian-era dark fantasy setting)
3. For EXISTING characters: only include them in "character_updates" if you found NEW CONFIRMED information from the text. Skip entirely if no new info.
4. Optional fields: fill only if found in text, otherwise use empty string ""
5. Bengali names: use standard phonetic Bengali transliteration

MANDATORY FIELDS for new characters (always fill — guess if needed):
  name_english, name_bengali, gender, age_range, skin_tone, hair_color, hair_style, eye_color, build, height, primary_clothing,
  face_description, visual_anchor, color_palette

OPTIONAL FIELDS (leave "" if unknown):
  face_shape, facial_hair, distinguishing_features, accessories, faction, role

confidence: "confirmed" if found explicitly in text, "guessed" if inferred/assumed

Return ONLY this JSON (nothing else before or after):
{{
  "characters_in_chapter": ["Name1", "Name2"],
  "new_characters": [
    {{
      "name_english": "...",
      "name_bengali": "...",
      "gender": "male or female or other",
      "age_range": "e.g. early 20s or mid-30s or teenager",
      "skin_tone": "e.g. fair or pale or medium or olive or dark",
      "hair_color": "...",
      "hair_style": "e.g. short or long wavy or curly short",
      "eye_color": "...",
      "build": "e.g. lean or athletic or average or stocky or slender",
      "height": "tall or average or short",
      "primary_clothing": "detailed description e.g. Victorian gentleman suit, dark charcoal, white cravat",
      "face_description": "1-2 sentences: face shape, jaw, nose, brow arch, eye shape e.g. sharp angular jaw, slightly gaunt face, deep-set dark eyes under heavy brows",
      "visual_anchor": "ONE sentence — most distinctive face+hair+outfit combo — will be copied verbatim into every image prompt e.g. early 20s male, pale ivory skin, short black hair, deep-set dark brown eyes under heavy brows, sharp angular jaw, always wearing halved black top hat and black Victorian suit",
      "color_palette": "3-4 dominant colors tied to this character e.g. deep black, pale ivory, topaz yellow, charcoal grey",
      "face_shape": "",
      "facial_hair": "",
      "distinguishing_features": "",
      "accessories": "",
      "faction": "",
      "role": "protagonist or antagonist or supporting or minor",
      "confidence": "confirmed or guessed",
      "source_notes": "brief note on where info came from or what was guessed"
    }}
  ],
  "character_updates": [
    {{
      "name_english": "ExistingCharacterName",
      "updated_fields": {{
        "field_name": "new confirmed value"
      }},
      "confidence": "confirmed",
      "source_notes": "brief note"
    }}
  ]
}}

=== CHAPTER TEXT ===

{text}"""


# ── Deduplication helpers ──────────────────────────────────────────────────────

def _find_canonical_name(name: str, characters: dict) -> Optional[str]:
    """Return the canonical name from characters dict if name matches an existing entry.

    Handles:
    - Case differences: "klein moretti" → "Klein Moretti"
    - First-name-only: "Klein" → "Klein Moretti"
    - Full-name when only first stored: "Klein Moretti" → "Klein"

    Returns None if no match found (genuinely new character).
    """
    name_stripped = name.strip()
    # 1. Exact match — fast path
    if name_stripped in characters:
        return name_stripped
    name_lower = name_stripped.lower()
    name_words = set(name_lower.split())
    for canonical in characters:
        canon_lower = canonical.lower()
        # 2. Case-insensitive exact match
        if name_lower == canon_lower:
            return canonical
        # 3. Word-set subset: one name's words are fully contained in the other's
        #    "Klein" ⊆ {"Klein","Moretti"} → same person
        #    {"Jack's","Father"} ∩ {"Jack"} = ∅ → different people (safe)
        canon_words = set(canon_lower.split())
        if name_words and canon_words:
            if name_words.issubset(canon_words) or canon_words.issubset(name_words):
                # Guard: skip if the overlap involves only very short tokens (Mr, Dr, …)
                longer = name_words if len(name_words) >= len(canon_words) else canon_words
                if all(len(w) >= 3 for w in longer):
                    return canonical
    return None


# ── Master DB helpers ──────────────────────────────────────────────────────────

def load_characters(json_path: str) -> dict:
    """Load characters.json. Returns empty scaffold if missing or corrupt."""
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            print(f"  [discovery] Warning: could not read {json_path}, starting fresh.")
    return {"version": "1.0", "last_updated": "", "characters": {}}


def _save_characters(data: dict, json_path: str):
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _merge_discovery(char_data: dict, discovery: dict, chapter_num: int) -> dict:
    """Merge AI discovery results into the master characters database."""
    characters = char_data.get("characters", {})

    # Update last_seen_chapter for all characters appearing in this chapter
    for name in discovery.get("characters_in_chapter", []):
        canonical = _find_canonical_name(name, characters)
        if canonical:
            characters[canonical]["last_seen_chapter"] = max(
                characters[canonical].get("last_seen_chapter", chapter_num),
                chapter_num,
            )

    # Add new characters
    for obj in discovery.get("new_characters", []):
        name = obj.get("name_english", "").strip()
        if not name:
            continue
        canonical = _find_canonical_name(name, characters)
        if canonical:
            if canonical != name:
                print(f"  ~ '{name}' matched existing '{canonical}' — merging as update (no duplicate).")
            else:
                print(f"  ~ '{name}' already tracked — applying as update.")
            for field, value in obj.items():
                if field in ("name_english", "name_bengali", "first_chapter") or not value:
                    continue
                old = characters[canonical].get(field, "")
                if value != old:
                    characters[canonical][field] = value
                    print(f"    {field}: '{old}' → '{value}'")
            characters[canonical]["last_seen_chapter"] = max(
                characters[canonical].get("last_seen_chapter", chapter_num), chapter_num
            )
        else:
            obj["first_chapter"] = chapter_num
            obj["last_seen_chapter"] = chapter_num
            characters[name] = obj
            print(f"  + New character: {name} ({obj.get('name_bengali', '')})"
                  f" [{obj.get('confidence', 'guessed')}]")

    # Apply targeted updates to existing characters
    for upd in discovery.get("character_updates", []):
        name = upd.get("name_english", "").strip()
        canonical = _find_canonical_name(name, characters)
        if not canonical:
            print(f"  ! Update for unknown character '{name}' — ignored.")
            continue
        name = canonical  # use canonical key for all operations below
        updated_fields = upd.get("updated_fields", {})
        changed = []
        for field, value in updated_fields.items():
            if field == "first_chapter" or not value:
                continue
            old = characters[name].get(field, "")
            if value != old:
                characters[name][field] = value
                changed.append(f"{field}: '{old}' → '{value}'")
        if changed:
            print(f"  ~ Updated {name}: {', '.join(changed)}")
        if upd.get("confidence") == "confirmed":
            characters[name]["confidence"] = "confirmed"
        characters[name]["last_seen_chapter"] = max(
            characters[name].get("last_seen_chapter", chapter_num), chapter_num
        )
        if upd.get("source_notes"):
            characters[name]["source_notes"] = upd["source_notes"]

    char_data["characters"] = characters
    return char_data


# ── Public API ─────────────────────────────────────────────────────────────────

def get_characters_in_chapter(output_dir: str) -> list:
    """Return character names found in this chapter (from chapter character JSON).
    Returns [] if no chapter character JSON exists.
    """
    data = _load_chapter_json(output_dir)
    if not data:
        return []
    return list(data.get("characters", {}).keys())


def build_character_reference_block(output_dir: str) -> str:
    """Build a CHARACTER VISUAL DNA block for injection into image prompts.
    Reads from the chapter-level character JSON in output_dir.
    Returns empty string if no chapter character file exists.
    """
    data = _load_chapter_json(output_dir)
    if not data:
        return ""
    characters = data.get("characters", {})
    if not characters:
        return ""

    lines = [
        "━━━ CHARACTER VISUAL DNA ━━━",
        "RULE: When a character appears in a prompt, copy their Visual Anchor phrase WORD-FOR-WORD.",
        "Do NOT paraphrase, shorten, or vary it. Identical phrasing = consistent face across all images.",
        "",
    ]
    found = 0
    for name, c in characters.items():
        eng = c.get("name_english", name)
        ben = c.get("name_bengali", "")
        header = f"▸ {eng} ({ben})" if ben else f"▸ {eng}"
        lines.append(header)

        visual_anchor = c.get("visual_anchor", "")
        if visual_anchor:
            lines.append(f'  Visual Anchor (copy verbatim): "{visual_anchor}"')
        else:
            # Fallback: assemble anchor from basic fields
            parts = []
            for field in ("age_range", "gender", "skin_tone", "hair_color", "hair_style", "eye_color", "build", "height"):
                v = c.get(field, "")
                if v:
                    parts.append(v)
            clothing = c.get("primary_clothing", "")
            anchor = ", ".join(parts)
            if clothing:
                anchor += f" — wearing {clothing}"
            lines.append(f'  Visual Anchor (copy verbatim): "{anchor}"')

        face_desc = c.get("face_description", "")
        if face_desc:
            lines.append(f"  Face: {face_desc}")

        palette = c.get("color_palette", "")
        if palette:
            lines.append(f"  Color Palette: {palette}")

        extras = ", ".join(
            x for x in [c.get("distinguishing_features", ""), c.get("accessories", "")]
            if x
        )
        if extras:
            lines.append(f"  Distinctive: {extras}")

        lines.append(f"  [{c.get('confidence', 'guessed')}]")
        lines.append("")
        found += 1

    return "\n".join(lines).rstrip() if found else ""


def build_translation_character_reference(output_dir: str) -> str:
    """Build a compact character name mapping for injection into translation prompts.
    Ensures consistent Bengali transliterations across the chapter.
    Returns empty string if no chapter character file exists.
    """
    data = _load_chapter_json(output_dir)
    if not data:
        return ""
    characters = data.get("characters", {})
    if not characters:
        return ""

    lines = [
        "CHARACTER NAME REFERENCE — use these exact Bengali transliterations consistently:",
        "",
    ]
    for name, c in characters.items():
        bengali = c.get("name_bengali", "")
        role = c.get("role", "")
        gender = c.get("gender", "")
        meta = ", ".join(x for x in [role, gender] if x)
        line = f"• {name} → {bengali}"
        if meta:
            line += f" ({meta})"
        lines.append(line)

    return "\n".join(lines)


def build_translation_character_reference_for_pdf(pdf_path: str, output_dir: str) -> str:
    """Build a character name reference block for a given PDF's chapter.

    Lookup order:
      1. output_dir/ch_N/*_character.json  (master_script.py structure)
      2. output_dir/*_character.json       (flat structure)
      3. Global characters.json            (all known characters as fallback)

    Returns empty string if no character data exists at all.
    """
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    m = re.search(r'(\d+)', stem)
    chapter_num = int(m.group(1)) if m else None

    # 1. Chapter subfolder
    if chapter_num is not None:
        ch_dir = os.path.join(output_dir, f"ch_{chapter_num}")
        ref = build_translation_character_reference(ch_dir)
        if ref:
            return ref

    # 2. Flat output dir
    ref = build_translation_character_reference(output_dir)
    if ref:
        return ref

    # 3. Global characters.json fallback
    data = load_characters(CHARACTERS_JSON_PATH)
    characters = data.get("characters", {})
    if not characters:
        return ""

    lines = [
        "CHARACTER NAME REFERENCE — use these exact Bengali transliterations consistently:",
        "",
    ]
    for name, c in characters.items():
        bengali = c.get("name_bengali", "")
        role = c.get("role", "")
        gender = c.get("gender", "")
        meta = ", ".join(x for x in [role, gender] if x)
        line = f"• {name} → {bengali}"
        if meta:
            line += f" ({meta})"
        lines.append(line)

    return "\n".join(lines)


def discover_characters_in_chapter(
    text: str,
    chapter_num: int,
    output_dir: str,
    characters_json_path: str,
    failed_models: set,
    chapter_filename_stem: str,
) -> tuple:
    """Run character discovery for one chapter.

    Returns (success: bool, characters_in_chapter: list, model_used: str).

    Skip check: if Chapter_NNN_*_character.json already exists in output_dir,
    returns cached data immediately with model_used = "skip".

    Hard stops (sys.exit(1)) if all models fail.

    Side effects:
    - Updates characters_json_path (master) with new/updated characters
    - Writes {chapter_filename_stem}_character.json to output_dir with full
      character details for characters appearing in this chapter
    - Adds failed Gemini models to failed_models set (shared with pipeline)
    """
    # Skip if chapter JSON already exists
    existing = _find_chapter_json(output_dir)
    if existing:
        print(f"  Skipping character discovery ({os.path.basename(existing)} exists).")
        data = _load_chapter_json(output_dir)
        chars = list(data.get("characters", {}).keys()) if data else []
        return True, chars, "skip"

    prompt = _build_discovery_prompt(text, load_characters(characters_json_path))
    response = None
    model_used = None

    # Try Gemini models first
    for model in DISCOVERY_MODELS:
        if model in failed_models:
            print(f"  Skipping {model} (failed earlier this run).")
            continue
        print(f"  Trying {model} for character discovery...")
        raw = _call_gemini_cli(model, prompt)
        if raw:
            parsed = _parse_discovery_response(raw)
            if parsed:
                response = parsed
                model_used = model
                break
            print(f"  [{model}] Response was not valid JSON — trying next model.")
        failed_models.add(model)

    # Fallback to Claude (slower — last resort)
    if response is None:
        print("  All Gemini models failed — trying Claude CLI (slower)...")
        raw = _call_claude_cli(prompt)
        if raw:
            parsed = _parse_discovery_response(raw)
            if parsed:
                response = parsed
                model_used = "claude"

    # Hard stop if everything failed
    if response is None:
        print("\n[ABORT] Character discovery failed — all models exhausted.")
        print("  Hard rule: pipeline cannot continue without character discovery.")
        print(f"  Chapter: {chapter_num}, Output dir: {output_dir}")
        sys.exit(1)

    # Merge into master characters.json
    char_data = load_characters(characters_json_path)
    char_data = _merge_discovery(char_data, response, chapter_num)
    _save_characters(char_data, characters_json_path)
    total = len(char_data["characters"])
    print(f"  Master characters.json updated ({total} total characters)")

    # Write chapter-level character JSON
    characters_in_chapter = response.get("characters_in_chapter", [])
    _write_chapter_json(
        output_dir,
        chapter_filename_stem,
        chapter_num,
        model_used,
        characters_in_chapter,
        char_data["characters"],
    )

    print(f"  {len(characters_in_chapter)} character(s) in chapter: {', '.join(characters_in_chapter)}")
    return True, characters_in_chapter, model_used


# ── Standalone CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import fitz  # PyMuPDF

    def _extract_text(pdf_path: str) -> Optional[str]:
        try:
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text() + "\n"
            text = re.sub(r'([a-zA-Z])-\n+([a-zA-Z])', r'\1\2', text)
            text = re.sub(r'[ \t]+', ' ', text)
            lines = text.splitlines()
            cleaned = [
                l.strip() for l in lines
                if not re.fullmatch(r'[-–—]?\s*\d+\s*[-–—]?', l.strip())
            ]
            result = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned))
            return result.strip()
        except Exception as e:
            print(f"  Error reading {pdf_path}: {e}")
            return None

    def _get_chapter_num(pdf_path: str) -> int:
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        m = re.search(r'(\d+)', stem)
        return int(m.group(1)) if m else 0

    def _extract_chapter_name(text: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if not line or re.match(r'^(chapter\s+)?\d+$', line, re.IGNORECASE):
                continue
            name = re.sub(r'[^\w\s-]', '', line)
            name = re.sub(r'[_\s]+', ' ', name).strip()
            name = re.sub(r'^chapter\s+\d+[\s:\-\.]*', '', name, flags=re.IGNORECASE).strip()
            if name:
                return name[:60]
        return "Untitled"

    def _build_chapter_stem(pdf_path: str, chapter_name: str) -> str:
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        m = re.search(r'(\d+)', stem)
        num_str = f"{int(m.group(1)):03d}" if m else "000"
        safe_name = re.sub(r'\s+', '_', chapter_name)
        return f"Chapter_{num_str}_{safe_name}"

    def _process_one(pdf_path: str, output_base: str, failed_models: set) -> bool:
        chapter_num = _get_chapter_num(pdf_path)
        output_dir = os.path.join(output_base, f"ch_{chapter_num}")
        os.makedirs(output_dir, exist_ok=True)

        text = _extract_text(pdf_path)
        if not text:
            return False

        chapter_name = _extract_chapter_name(text)
        chapter_filename_stem = _build_chapter_stem(pdf_path, chapter_name)
        print(f"  Chapter: {chapter_filename_stem}")

        discover_characters_in_chapter(
            text, chapter_num, output_dir, CHARACTERS_JSON_PATH, failed_models, chapter_filename_stem
        )
        return True

    parser = argparse.ArgumentParser(
        description=(
            "Run character discovery on PDF chapter(s) independently.\n"
            "  Single:  python character_discovery.py chapter_53.pdf ./output\n"
            "  Batch:   python character_discovery.py ./chapter_split/ ./output\n"
            "  From beginning (reset):  python character_discovery.py ./chapter_split/ ./output --reset"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Single PDF file OR folder of PDF files (searched recursively)")
    parser.add_argument("output_folder", help="Base output dir (ch_N subfolders created inside)")
    parser.add_argument(
        "--characters",
        default=CHARACTERS_JSON_PATH,
        help=f"Path to master characters.json (default: {CHARACTERS_JSON_PATH})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear characters.json and re-discover from scratch (existing chapter JSONs are also deleted)",
    )
    cli_args = parser.parse_args()

    # Allow overriding the characters path via --characters flag
    CHARACTERS_JSON_PATH = cli_args.characters

    if not os.path.exists(cli_args.input):
        print(f"Error: '{cli_args.input}' not found.")
        sys.exit(1)

    os.makedirs(cli_args.output_folder, exist_ok=True)

    # --reset: wipe master characters.json and all chapter character JSONs
    if cli_args.reset:
        if os.path.exists(CHARACTERS_JSON_PATH):
            os.remove(CHARACTERS_JSON_PATH)
            print(f"[reset] Deleted {CHARACTERS_JSON_PATH}")
        deleted_chapter_jsons = 0
        for root, _, files in os.walk(cli_args.output_folder):
            for fname in files:
                if fname.endswith("_character.json"):
                    os.remove(os.path.join(root, fname))
                    deleted_chapter_jsons += 1
        if deleted_chapter_jsons:
            print(f"[reset] Deleted {deleted_chapter_jsons} chapter character JSON(s) in '{cli_args.output_folder}'")
        print("[reset] Starting fresh.\n")

    _failed: set = set()

    if os.path.isfile(cli_args.input):
        if not cli_args.input.lower().endswith(".pdf"):
            print(f"Error: '{cli_args.input}' is not a PDF.")
            sys.exit(1)
        print(f"\n[1/1] {os.path.basename(cli_args.input)}")
        _process_one(cli_args.input, cli_args.output_folder, _failed)
        print("\nDone.")

    elif os.path.isdir(cli_args.input):
        # Recursively find all PDFs, sorted by chapter number
        all_pdfs = []
        for root, _, files in os.walk(cli_args.input):
            for fname in files:
                if fname.lower().endswith(".pdf"):
                    all_pdfs.append(os.path.join(root, fname))

        if not all_pdfs:
            print(f"Error: No PDF files found in '{cli_args.input}' (searched recursively).")
            sys.exit(1)

        # Sort by extracted chapter number, then by path for ties
        all_pdfs.sort(key=lambda p: (_get_chapter_num(p), p))
        print(f"Found {len(all_pdfs)} PDF(s) to process.")
        _failed_files = []
        for i, pdf_path in enumerate(all_pdfs, 1):
            print(f"\n{'='*55}")
            print(f"[{i}/{len(all_pdfs)}] {os.path.basename(pdf_path)}")
            print(f"{'='*55}")
            ok = _process_one(pdf_path, cli_args.output_folder, _failed)
            if not ok:
                _failed_files.append(pdf_path)

        print(f"\nBatch complete. {len(all_pdfs) - len(_failed_files)}/{len(all_pdfs)} succeeded.")
        if _failed_files:
            print("Failed:")
            for f in _failed_files:
                print(f"  {f}")
            sys.exit(1)

    else:
        print(f"Error: '{cli_args.input}' is neither a file nor a directory.")
        sys.exit(1)
