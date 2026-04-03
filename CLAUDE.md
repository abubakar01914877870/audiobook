# CLAUDE.md — Audiobook Translation Pipeline

## Project Overview

A Python pipeline for translating English fantasy novels (Lord of the Mysteries series) into colloquial Bengali for teen readers, then generating YouTube and TikTok videos with AI-generated visuals.

## Translation Workflow

**Three-step workflow:**
1. `split_pdf.py` — Split English novel PDF into chapter PDFs
2. `generate_translation.py` — Translate chapters to Bengali Markdown (Claude first, Gemini fallback)
3. `md_to_pdf.py` — Convert Markdown to styled Bengali PDF

## Video Pipeline Workflow

Run `master_script.py` to process one or more chapter PDFs end-to-end.

### Image Path (default — static Gemini images)

```
master_script.py chapter.pdf ./output
```

Steps:
0. **Character Discovery** — `character_discovery.py` — update character DB
1. **Translation** — `generate_translation.py` — Bengali Markdown
2. **Video Metadata** — `generate_video_meta.py` — AI decides scene count, generates image prompts
3. **Audio** — `generate_audio.py` — narration MP3/M4A
4. **Image Generation** — `generate_image.py` — Gemini web UI (Profile 9), PNG per prompt (count from meta)
5. **Render** — `render_images.py` — xfade slideshow; YouTube (1440×2560 av1/hevc) + TikTok (1080×1920 h264)
6. **YouTube Upload** — `upload_youtube.py`
7. **TikTok Upload** — `upload_tiktok.py`

### Video Path (Grok video clips)

```
master_script.py chapter.pdf ./output --path=video
```

Steps 0–3 same as above, then:
4. **Image Generation** — `generate_image.py` — PNG per prompt (count from meta)
5. **Video Generation** — `generate_video.py` — Grok web UI (Profile 10) + Chrome extension, 10s MP4 per scene image
6. **Render** — `render_videos.py` — sequential MP4 concat + ambient audio mix; YouTube + TikTok
7. **YouTube Upload**
8. **TikTok Upload**

## Key Files

| File | Purpose |
|------|---------|
| `master_script.py` | Orchestrate full pipeline; `--path=image` (default) or `--path=video` |
| `prepare_text.py` | AI-only sub-pipeline: character discovery + translation + video metadata |
| `split_pdf.py` | Split PDF by TOC bookmarks or page range |
| `generate_translation.py` | Translate via Claude (priority) then Gemini fallback |
| `md_to_pdf.py` | Markdown → Bengali PDF via Playwright/Chromium |
| `pipeline/generate_image.py` | Gemini image generation only (Profile 9) |
| `pipeline/generate_video.py` | Grok video generation only (Profile 10 + Chrome extension) |
| `pipeline/render_images.py` | Render video from static scene images (xfade slideshow) |
| `pipeline/render_videos.py` | Render video from Grok scene MP4 clips (sequential concat) |
| `get_model_stats.py` | Check Gemini model quota availability |
| `.gemini` | Translation style guidelines (system prompt context) |
| `.env` | `GOOGLE_API_KEY` — never commit |
| `chrome-plugins/grok-video-generator/` | Chrome extension driving the Grok video UI |

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

# AI text steps only (character discovery + translation + video metadata)
python prepare_text.py chapter_058.pdf ./clown_vol_1/output
python prepare_text.py ./chapter_split/ ./clown_vol_1/output --model=gemini   # batch, Gemini-first

# Full pipeline — image path (default)
python master_script.py chapter_058.pdf ./clown_vol_1/output
python master_script.py ./chapter_split/ ./clown_vol_1/output           # batch

# Full pipeline — video path
python master_script.py chapter_058.pdf ./clown_vol_1/output --path=video

# Individual pipeline steps
python pipeline/generate_image.py ./clown_vol_1/output/ch_58
python pipeline/generate_video.py ./clown_vol_1/output/ch_58
python pipeline/render_images.py ./clown_vol_1/output/ch_58
python pipeline/render_videos.py ./clown_vol_1/output/ch_58

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
- **TTS pronunciation (Google Docs voice):** Verb forms ending in bare `ল` (e.g. `তাকাল`, `বলল`, `গেল`) are mispronounced by Google Docs TTS — always use the `লো` form instead (e.g. `তাকালো`, `বললো`, `গেলো`). Apply this consistently to all past-tense verb endings.

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
2. `gemini-3-flash`
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
