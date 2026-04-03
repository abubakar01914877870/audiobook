"""
enrich_characters.py — Wiki enrichment for high-appearance characters.

Fetches the Lord of the Mysteries fandom wiki page for each character,
extracts the infobox (structured fields), Appearance section, and Personality
section, then uses Claude CLI to cross-match wiki details against the existing
character record and write confirmed updates back to characters.json.

Usage:
  python enrich_characters.py                          # threshold=10 (default)
  python enrich_characters.py --threshold 5            # lower threshold
  python enrich_characters.py --name "Klein Moretti"   # single character
  python enrich_characters.py --dry-run                # preview only, no writes
  python enrich_characters.py --stats                  # print appearance leaderboard
"""

import argparse
import json
import os
import re
import sys
import time
import tempfile
import subprocess
from typing import Optional

import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHARACTERS_JSON_PATH = os.path.join(_SCRIPT_DIR, "characters.json")
WIKI_API = "https://lordofthemysteries.fandom.com/api.php"
DEFAULT_THRESHOLD = 10
REQUEST_DELAY = 1.5   # seconds between wiki requests — be polite


# ── characters.json helpers ────────────────────────────────────────────────────

def _load_characters(path: str = CHARACTERS_JSON_PATH) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[error] Could not load characters.json: {e}")
            sys.exit(1)
    return {"version": "1.0", "last_updated": "", "characters": {}}


def _save_characters(data: dict, path: str = CHARACTERS_JSON_PATH):
    from datetime import datetime
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Wikitext cleaning ─────────────────────────────────────────────────────────

def _clean_wikitext(text: str) -> str:
    """Strip wikitext markup to plain text."""
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^/]*/>', '', text)
    text = re.sub(r'<[^>]+>', '', text)  # strip HTML tags
    text = re.sub(r'\{\{[Cc]ite [Bb]ook\|[^}]*\}\}', '', text)  # {{Cite Book|...}}
    text = re.sub(r'\{\{c\|([^}]*)\}\}', r'(\1)', text)  # {{c|text}} -> (text)
    text = re.sub(r'\{\{[^}]*\}\}', '', text)  # remaining templates
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r'\1', text)  # [[link|text]] -> text
    text = re.sub(r"'{2,3}", '', text)  # bold/italic
    text = re.sub(r'==+[^=]+=+', '', text)  # section headers
    text = re.sub(r'\[\d+\]', '', text)  # citation numbers
    text = re.sub(r'\n\s*\*\s*', '\n- ', text)  # bullet points
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _name_to_wiki_slug(name: str) -> str:
    """Convert 'Klein Moretti' → 'Klein_Moretti' for fandom wiki URLs."""
    return name.strip().replace(" ", "_")


# ── Wiki fetching (MediaWiki API) ─────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _wiki_get(params: dict) -> Optional[dict]:
    """Make a MediaWiki API request. Returns parsed JSON or None."""
    try:
        resp = requests.get(WIKI_API, headers=HEADERS, timeout=15, params=params)
    except requests.RequestException as e:
        print(f"  [wiki] Request failed: {e}")
        return None
    if resp.status_code != 200:
        print(f"  [wiki] HTTP {resp.status_code}")
        return None
    data = resp.json()
    if "error" in data:
        return None
    return data


def _fetch_wiki_sections(slug: str) -> Optional[list]:
    """Get the section list for a wiki page."""
    data = _wiki_get({"action": "parse", "page": slug, "prop": "sections", "format": "json"})
    if not data:
        return None
    return data.get("parse", {}).get("sections", [])


def _fetch_wiki_section_text(slug: str, section_idx: str) -> str:
    """Fetch and clean the wikitext of a specific section."""
    data = _wiki_get({
        "action": "parse", "page": slug, "prop": "wikitext",
        "section": section_idx, "format": "json",
    })
    if not data:
        return ""
    raw = data.get("parse", {}).get("wikitext", {}).get("*", "")
    return _clean_wikitext(raw)


def _parse_infobox(wikitext: str) -> dict:
    """Extract key-value fields from the {{Char temp ...}} infobox template."""
    # Find the Char temp template
    match = re.search(r'\{\{Char temp(.*)', wikitext, re.DOTALL)
    if not match:
        return {}

    block = match.group(1)
    # Balance braces to find template end
    depth = 1
    end = 0
    for i, ch in enumerate(block):
        if ch == '{' and i + 1 < len(block) and block[i + 1] == '{':
            depth += 1
        elif ch == '}' and i + 1 < len(block) and block[i + 1] == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    block = block[:end]

    fields = {}
    # Split on top-level |field = value patterns
    for m in re.finditer(r'\|(\w[\w\s()]*?)\s*=\s*(.*?)(?=\n\||\Z)', block, re.DOTALL):
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if val:
            # Clean the value
            val = _clean_wikitext(val)
            if val:
                fields[key] = val
    return fields


def _fetch_full_wiki_data(name: str) -> Optional[dict]:
    """Fetch infobox + Appearance + Personality from the wiki for a character.

    Returns a dict with keys: 'infobox', 'appearance', 'personality', or None if page not found.
    """
    slug = _name_to_wiki_slug(name)

    # Get sections list
    sections = _fetch_wiki_sections(slug)
    if sections is None:
        print(f"  [wiki] No page found for '{name}'")
        return None

    # Fetch intro/infobox (section 0)
    intro_data = _wiki_get({
        "action": "parse", "page": slug, "prop": "wikitext",
        "section": "0", "format": "json",
    })
    infobox = {}
    if intro_data:
        raw_intro = intro_data.get("parse", {}).get("wikitext", {}).get("*", "")
        infobox = _parse_infobox(raw_intro)

    # Find and fetch Appearance & Personality sections
    appearance_text = ""
    personality_text = ""
    for s in sections:
        line = s.get("line", "").strip().lower()
        if line == "appearance":
            appearance_text = _fetch_wiki_section_text(slug, s["index"])
        elif line == "personality":
            personality_text = _fetch_wiki_section_text(slug, s["index"])

    if not infobox and not appearance_text:
        print(f"  [wiki] No useful data found for '{name}'")
        return None

    result = {"infobox": infobox}
    if appearance_text:
        result["appearance"] = appearance_text
    if personality_text:
        result["personality"] = personality_text

    return result


# ── Wiki image download ───────────────────────────────────────────────────────

def _download_wiki_image(name: str) -> Optional[str]:
    """Download the official character image from the LotM fandom wiki.

    Tries multiple filename patterns. Returns path to downloaded temp file, or None.
    """
    slug = _name_to_wiki_slug(name)

    # Try these filename patterns in order
    patterns = [
        f"{slug}_Official.jpg",
        f"{slug}_Official_Crop.png",
        f"{slug}_Official.png",
        f"{slug}.jpg",
        f"{slug}.png",
    ]

    for filename in patterns:
        data = _wiki_get({
            "action": "query",
            "titles": f"File:{filename}",
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
        })
        if not data:
            continue

        pages = data.get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if page_id == "-1":
                continue  # file not found
            image_info = page.get("imageinfo", [])
            if not image_info:
                continue
            image_url = image_info[0].get("url", "")
            if not image_url:
                continue

            # Download the image
            try:
                resp = requests.get(image_url, headers=HEADERS, timeout=30)
                if resp.status_code != 200:
                    print(f"  [wiki] Image download failed: HTTP {resp.status_code}")
                    continue

                ext = os.path.splitext(filename)[1] or ".jpg"
                tmp_path = os.path.join(tempfile.gettempdir(), f"enrich_{slug}{ext}")
                with open(tmp_path, "wb") as f:
                    f.write(resp.content)

                size_kb = len(resp.content) / 1024
                print(f"  [wiki] Downloaded image: {filename} ({size_kb:.0f} KB)")
                return tmp_path

            except requests.RequestException as e:
                print(f"  [wiki] Image download error: {e}")
                continue

    print(f"  [wiki] No official image found for '{name}'")
    return None


# ── Claude CLI cross-match ─────────────────────────────────────────────────────

def _build_crossmatch_prompt(name: str, current: dict, wiki_data: Optional[dict],
                              image_path: Optional[str] = None) -> str:
    current_summary = json.dumps({
        k: v for k, v in current.items()
        if k in (
            "gender", "age_range", "skin_tone", "hair_color", "hair_style",
            "eye_color", "build", "height", "face_description", "visual_anchor",
            "unique_identifier", "era_clothing_lock", "color_palette",
            "distinguishing_features", "accessories", "confidence",
            "faction", "role", "codename", "full_name",
        )
    }, ensure_ascii=False, indent=2)

    # Build wiki info block
    wiki_sections = []

    infobox = wiki_data.get("infobox", {})
    if infobox:
        wiki_sections.append("INFOBOX FIELDS:")
        for k, v in infobox.items():
            wiki_sections.append(f"  {k}: {v}")

    appearance = wiki_data.get("appearance", "")
    if appearance:
        wiki_sections.append(f"\nAPPEARANCE SECTION:\n{appearance}")

    personality = wiki_data.get("personality", "")
    if personality:
        wiki_sections.append(f"\nPERSONALITY SECTION:\n{personality}")

    wiki_block = "\n".join(wiki_sections) if wiki_sections else "(no wiki text data)"

    # Image instruction
    image_instruction = ""
    if image_path:
        image_instruction = f"""
OFFICIAL CHARACTER IMAGE: {image_path}
Analyze this image carefully. Extract every visual detail you can see: hair color, hair style,
eye color, skin tone, build, clothing, accessories, face shape, distinguishing features, etc.

PRIORITY ORDER (highest to lowest):
1. CHARACTER IMAGE — what you SEE in the image is the most authoritative source
2. WIKI TEXT DATA — confirmed text descriptions from the fandom wiki
3. CURRENT DATABASE — existing entries (lowest priority, may contain guesses)

If the image shows something different from the wiki text or current DB, the IMAGE wins.
"""
    else:
        image_instruction = """
PRIORITY ORDER (highest to lowest):
1. WIKI TEXT DATA — confirmed text descriptions from the fandom wiki
2. CURRENT DATABASE — existing entries (lowest priority, may contain guesses)
"""

    return f"""You are updating a visual character database for the novel "Lord of the Mysteries".

CHARACTER: {name}
{image_instruction}
CURRENT DATABASE ENTRY:
{current_summary}

WIKI DATA (from lordofthemysteries.fandom.com):
{wiki_block}

TASK:
Cross-match ALL sources (image + wiki text + current DB) and produce updates.
Return ONLY valid JSON — no markdown fences, no explanation, nothing before or after the JSON.

RULES:
1. Include fields in "updated_fields" if a higher-priority source provides information that is
   missing or different from the current entry.
2. If a source confirms an existing value, include it in "confirmed_fields" (list of field names).
3. Map wiki infobox fields to our DB fields:
   - sex → gender
   - hair_color → hair_color
   - eye_color → eye_color
   - skin_color → skin_tone
   - height → height
   - aliases → codename (use the Tarot Club alias if present, e.g. "The Fool", "Justice", "The Hanged Man")
   - affiliation(s) → faction (primary current faction)
   - occupation(s) → role (primary current role/pathway)
   - age → age_range
   - vital_status, species, bloodline → distinguishing_features (append if noteworthy)
4. "visual_anchor" — rewrite based on the best available visual information (image > wiki > current).
   Keep Victorian/Edwardian era framing.
5. "era_clothing_lock" — update based on what clothing is visible in the image or described in wiki.
6. "face_description" — describe what you see in the image if available.
7. "source_notes" — brief note on what each source contributed.

VALID DB FIELDS you can update:
gender, age_range, skin_tone, hair_color, hair_style, eye_color, build, height,
face_description, visual_anchor, unique_identifier, era_clothing_lock, color_palette,
distinguishing_features, accessories, faction, role, codename, full_name, confidence

Return ONLY this JSON:
{{
  "updated_fields": {{
    "field_name": "new confirmed value"
  }},
  "confirmed_fields": ["field1", "field2"],
  "source_notes": "brief note"
}}"""


def _call_claude_cli(prompt: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
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


def _parse_json_response(raw: str) -> Optional[dict]:
    if not raw:
        return None
    ansi = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi.sub('', raw).strip()
    fence = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find('{')
    if start == -1:
        return None
    depth, end = 0, -1
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
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"  [parse] JSON error: {e}")
        return None


# ── Enrichment logic ───────────────────────────────────────────────────────────

def _enrich_one(name: str, char: dict, dry_run: bool) -> dict:
    """Fetch wiki + cross-match for a single character. Returns change summary."""
    print(f"\n{'─'*55}")
    count = char.get("appearance_count", "?")
    print(f"  {name}  (appearances: {count})")

    # Fetch wiki text data
    wiki_data = _fetch_full_wiki_data(name)

    # Download official image
    image_path = _download_wiki_image(name)

    if not wiki_data and not image_path:
        return {"status": "no_wiki"}

    parts = []
    if wiki_data:
        infobox = wiki_data.get("infobox", {})
        appearance = wiki_data.get("appearance", "")
        personality = wiki_data.get("personality", "")
        if infobox:
            parts.append(f"infobox ({len(infobox)} fields)")
        if appearance:
            parts.append(f"appearance ({len(appearance)} chars)")
        if personality:
            parts.append(f"personality ({len(personality)} chars)")
    if image_path:
        parts.append("official image")
    print(f"  [wiki] Found: {', '.join(parts)}")

    prompt = _build_crossmatch_prompt(name, char, wiki_data or {}, image_path)
    raw = _call_claude_cli(prompt)

    # Clean up temp image
    if image_path and os.path.exists(image_path):
        os.remove(image_path)

    result = _parse_json_response(raw)
    if not result:
        print("  [claude] Could not parse response.")
        return {"status": "parse_error"}

    updated = result.get("updated_fields", {})
    confirmed = result.get("confirmed_fields", [])
    notes = result.get("source_notes", "")

    if not updated and not confirmed:
        print("  No changes — wiki added nothing new.")
        return {"status": "no_change"}

    if dry_run:
        if updated:
            print("  [dry-run] Would update:")
            for field, value in updated.items():
                old = char.get(field, "")
                print(f"    {field}: '{old}' → '{value}'")
        if confirmed:
            print(f"  [dry-run] Would confirm: {', '.join(confirmed)}")
        return {"status": "dry_run", "updated": updated, "confirmed": confirmed}

    # Apply updates
    changed = []
    for field, value in updated.items():
        old = char.get(field, "")
        if value and value != old:
            char[field] = value
            changed.append(f"{field}: '{old}' → '{value}'")

    for field in confirmed:
        if field in char and char.get("confidence") != "confirmed":
            char["confidence"] = "confirmed"

    if notes:
        char["wiki_source_notes"] = notes
    char["wiki_enriched"] = True

    if changed:
        print(f"  Updated: {', '.join(changed)}")
    if confirmed:
        print(f"  Confirmed fields: {', '.join(confirmed)}")

    return {"status": "updated", "changed": changed}


# ── Stats printer ──────────────────────────────────────────────────────────────

def _print_stats(characters: dict):
    rows = []
    for name, c in characters.items():
        rows.append((
            c.get("appearance_count", 0),
            name,
            c.get("first_chapter", "?"),
            c.get("last_seen_chapter", "?"),
            c.get("role", ""),
            c.get("wiki_enriched", False),
        ))
    rows.sort(key=lambda r: -r[0])

    print(f"\n{'─'*70}")
    print(f"  {'#':>3}  {'Character':<28} {'Ch':>5}  {'Role':<16}  {'Wiki'}")
    print(f"{'─'*70}")
    for count, name, first, last, role, enriched in rows:
        wiki_tag = "✓" if enriched else ""
        print(f"  {count:>3}  {name:<28} {str(first)+'-'+str(last):>5}  {role:<16}  {wiki_tag}")
    print(f"{'─'*70}")
    print(f"  Total: {len(rows)} characters\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Enrich high-appearance characters from the LotM fandom wiki.\n"
            "  Default: python enrich_characters.py\n"
            "  Stats:   python enrich_characters.py --stats\n"
            "  Single:  python enrich_characters.py --name 'Klein Moretti'\n"
            "  Preview: python enrich_characters.py --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--threshold", type=int, default=DEFAULT_THRESHOLD,
        help=f"Minimum appearance_count to qualify (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--name", type=str, default=None,
        help="Enrich a specific character by name (ignores threshold)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without writing to characters.json",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print appearance leaderboard and exit",
    )
    parser.add_argument(
        "--characters", default=CHARACTERS_JSON_PATH,
        help=f"Path to characters.json (default: {CHARACTERS_JSON_PATH})",
    )
    args = parser.parse_args()

    data = _load_characters(args.characters)
    characters = data.get("characters", {})

    if args.stats:
        _print_stats(characters)
        return

    # Select candidates
    if args.name:
        if args.name not in characters:
            print(f"[error] '{args.name}' not found in characters.json")
            sys.exit(1)
        candidates = [(args.name, characters[args.name])]
        print(f"Enriching: {args.name}")
    else:
        candidates = [
            (name, c) for name, c in characters.items()
            if c.get("appearance_count", 0) >= args.threshold
        ]
        candidates.sort(key=lambda x: -x[1].get("appearance_count", 0))
        print(f"Found {len(candidates)} character(s) with appearance_count >= {args.threshold}")

    if not candidates:
        print("Nothing to enrich.")
        return

    summary = {"updated": 0, "no_change": 0, "no_wiki": 0, "errors": 0}

    for i, (name, char) in enumerate(candidates):
        result = _enrich_one(name, char, dry_run=args.dry_run)
        status = result.get("status", "error")

        if status == "updated":
            summary["updated"] += 1
            if not args.dry_run:
                data["characters"][name] = char
                _save_characters(data, args.characters)   # save after each character (safe progress)
        elif status == "no_change":
            summary["no_change"] += 1
        elif status == "no_wiki":
            summary["no_wiki"] += 1
        elif status in ("parse_error", "error"):
            summary["errors"] += 1

        # Polite delay between wiki requests (skip on last)
        if i < len(candidates) - 1:
            time.sleep(REQUEST_DELAY)

    print(f"\n{'='*55}")
    print(f"Enrichment complete.")
    print(f"  Updated:    {summary['updated']}")
    print(f"  No change:  {summary['no_change']}")
    print(f"  No wiki:    {summary['no_wiki']}")
    print(f"  Errors:     {summary['errors']}")
    if args.dry_run:
        print("  (dry-run — no changes written)")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
