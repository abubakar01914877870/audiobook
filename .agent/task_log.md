# Task Log

A running log of tasks completed in this project. Update this file whenever a significant task is done.
Agents should append a new entry here after completing any major change.

---

## 2026-03-05

### ✅ Project Cleanup

- Removed 17 redundant files from project root (all `test_*.py`, old translate scripts, stale outputs, `gemini_help.txt`, `test_font.ttf`, `__pycache__`).
- Trimmed `requirements.txt` to 4 actual dependencies.

### ✅ New Generic PDF Splitter

- Rewrote `split_pdf.py` as a generic, reusable tool at project root.
- **Modes:** Auto-split by TOC/bookmarks (with text-scan fallback) OR manual page range (`--start`/`--end`).
- **Output format:** `Chapter_01_Chapter Title.pdf`
- Removed the old hardcoded `faceless vol 2/split_chapters.py`.
- Removed `PyPDF2` from requirements (now uses `fitz`/pymupdf throughout).

### ✅ README & Agent Folder

- Rewrote `README.md` to document the current 3-script workflow with examples.
- Created `.agent/` folder with:
  - `context.md` — persistent project context, rules, known issues
  - `task_log.md` — this file
  - `workflows/split_pdf.md` — how to split PDFs
  - `workflows/translate.md` — how to run bulk translation
  - `workflows/md_to_pdf.md` — how to convert .md to PDF

---

_Add new entries above this line when tasks are completed._
