# CLAUDE.md — Audiobook Translation Pipeline

## Project Overview

A Python pipeline for translating English fantasy novels (Lord of the Mysteries series) into colloquial Bengali for teen readers.

**Three-step workflow:**
1. `split_pdf.py` — Split English novel PDF into chapter PDFs
2. `generate_translation.py` — Translate chapters to Bengali Markdown (Claude first, Gemini fallback)
3. `md_to_pdf.py` — Convert Markdown to styled Bengali PDF

## Key Files

| File | Purpose |
|------|---------|
| `split_pdf.py` | Split PDF by TOC bookmarks or page range |
| `generate_translation.py` | Translate via Claude (priority) then Gemini fallback |
| `md_to_pdf.py` | Markdown → Bengali PDF via Playwright/Chromium |
| `get_model_stats.py` | Check Gemini model quota availability |
| `.gemini` | Translation style guidelines (system prompt context) |
| `.env` | `GOOGLE_API_KEY` — never commit |

## CLI Usage

```bash
# Split PDF by chapters (auto-detect TOC)
python split_pdf.py "book.pdf" --output ./chapters

# Split specific page range
python split_pdf.py "book.pdf" --start 483 --end 490 --output ./chapters --name vol3_batch1

# Translate all chapters in a folder (Claude first, Gemini fallback)
python generate_translation.py "traveler_vol_3/chapter_split/483-490/" ./traveler_vol_3/output/483-490

# Convert translated markdown to PDF
python md_to_pdf.py "traveler_vol_3/output/483-490/Chapter_483_Title.md"

# Check Gemini model quota
python get_model_stats.py
```

## Translation Style Rules

These rules are critical — apply them in any AI translation prompt:

- **Style:** Muhammed Zafar Iqbal (simple, fluid, teen-friendly)
- **Language:** Cholitobhasha (colloquial Bengali) — no archaic/Sanskrit-heavy words
- **Pronouns:** Only informal — `সে` / `তুমি`. Never `আপনি` / `তিনি`
- **Technical words:** Format as `বাংলা_শব্দ (English_Word)` only when necessary
- **Character names:** Consistent transliteration e.g. `ক্লেইন (Klein)`
- **Chapter/Novel titles:** Keep in original English format
- **Dialogue:** Natural, conversational — how teens actually speak
- **Atmosphere:** Maintain mystery/sci-fi thriller vibe
- **Output:** Full immersive narrative — never summarize

## File Naming Conventions

- **Input PDFs:** `chapter_NNN_Title.pdf` (from split_pdf.py)
- **Output Markdown:** `Chapter_NNN_Title.md` (zero-padded)
- **Organized by volume/batch:** `traveler_vol_3/output/483-490/`

## Current Progress

| Volume | Folder | Status |
|--------|--------|--------|
| Vol. 1 — Clown | `clown vol 1/` | Complete (200+ chapters) |
| Vol. 2 — Faceless | `faceless vol 2/` | Complete (200+ chapters) |
| Vol. 3 — Traveler | `traveler_vol_3/` | In progress (483–530 split & translated) |

Source novels: `lotm-full-english-novel-translations/`

## Gemini Model Priority

Script tries these in order (falls back on quota exhaustion):
1. `gemini-3.1-pro-preview`
2. `gemini-3-flash-preview`
3. `gemini-2.5-pro`
4. `gemini-2.5-flash`
5. `gemini-2.5-flash-lite`

## Dependencies

```bash
pip install -r requirements.txt
# Also requires: Gemini CLI (npm install -g @google/gemini-cli)
# Also requires: Playwright (playwright install chromium)
```

## Important Notes

- `generate_translation.py` **auto-skips already-translated files** (checks if `.md` exists)
- Claude is tried first with **3 retries** and exponential backoff (10s, 20s, 30s); Gemini fallback chain runs if Claude fails
- Gemini has **5-minute hard timeout** per chapter with real-time quota error detection
- `md_to_pdf.py` uses **Playwright + Chromium** — not fpdf2 (despite requirements.txt listing fpdf2)
- Bengali fonts: `Hind Siliguri` (Google Fonts) with `SolaimanLipi` fallback
- Never commit `.env` — it contains the real `GOOGLE_API_KEY`
