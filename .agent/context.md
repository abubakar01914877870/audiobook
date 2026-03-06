# Audiobook Project — Agent Context

This file provides persistent context for AI agents working on this project.
Read this before starting any new task.

## Project Goal

Translate PDF chapters of Chinese web novels (Lord of the Mysteries series) into literary Bengali.

## The 3 Core Scripts

| Script                      | Purpose                             | Input → Output                      |
| --------------------------- | ----------------------------------- | ----------------------------------- |
| `split_pdf.py`              | Split PDF by chapters or page range | `.pdf` → individual `.pdf` chapters |
| `run_gemini_translation.py` | Translate chapters to Bengali       | `.pdf` → `.md` (Bengali)            |
| `md_to_pdf.py`              | Render Bengali markdown as PDF      | `.md` → `.pdf`                      |

## Key Rules (from `.gemini`)

- **Translation CLI**: Always use the `gemini` CLI via subprocess. Do NOT use Python-based Gemini SDK for translation.
- **No local cleaning**: Do not write Python regex/strip logic to clean CLI output. Let the CLI output raw text directly to file.
- **Python version**: 3.9+ — use `Optional[str]` not `str | None`; use `importlib_metadata` shim for 3.9.
- **Package preference**: `google-genai` over `google-generativeai` for SDK usage.
- **File naming**: `Chapter_001_Chapter Name.md` / `Chapter_01_Chapter Name.pdf`

## Volumes

| Volume                                  | Folder            | Status                                            |
| --------------------------------------- | ----------------- | ------------------------------------------------- |
| Lord of the Mysteries Vol. 1 (Clown)    | `clown vol 1/`    | Chapters 1–140+ translated                        |
| Lord of the Mysteries Vol. 2 (Faceless) | `faceless vol 2/` | Split done (Ch. 214–482); translation in progress |

## Completed Tasks (History)

- **2026-03-05**: Cleaned up project root — removed 17 redundant files (test scripts, old translate scripts, stale outputs).
- **2026-03-05**: Rewrote `split_pdf.py` — generic PDF splitter moved to project root, supports auto-chapter and manual page-range modes. Old hardcoded `split_chapters.py` removed.
- **2026-03-05**: Updated `requirements.txt` — trimmed to 4 actual dependencies.

## Active Tasks

_None currently._

## Known Issues / Notes

- The Gemini CLI `/stats` command is used to check remaining quota before starting bulk translation.
- Model priority order: `gemini-3.1-pro-preview` → `gemini-3-flash-preview` → `gemini-2.5-pro` → `gemini-2.5-flash` → `gemini-2.5-flash-lite`
- `md_to_pdf.py` uses Playwright for rendering — ensure Playwright browsers are installed (`playwright install`).
