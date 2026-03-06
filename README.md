# Audiobook PDF Translator (Bengali)

A Python toolkit for splitting PDF books into chapters, translating them to Bengali using Gemini AI, and converting the translated Markdown back to styled PDFs.

## Project Structure

```
audiobook/
├── split_pdf.py               # Split any PDF by chapters or page range
├── run_gemini_translation.py  # Translate chapter PDFs → Bengali .md files
├── md_to_pdf.py               # Convert .md files → styled Bengali PDF
├── requirements.txt
├── .env                       # Gemini API key (not committed)
├── .gemini                    # AI assistant context & rules
├── clown vol 1/               # Lord of the Mysteries Vol. 1 — chapters & translations
└── faceless vol 2/            # Lord of the Mysteries Vol. 2 — source PDF & chapter splits
```

## Setup

1. **Create a virtual environment:**

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Set your API key** — create a `.env` file:

   ```
   GOOGLE_API_KEY=your_key_here
   ```

4. **Install Gemini CLI** (required for translation):
   ```bash
   npm install -g @google/gemini-cli
   ```

---

## Workflow

### Step 1 — Split a PDF into chapters

```bash
# Auto-detect chapters from the PDF's bookmarks/TOC:
python split_pdf.py "book.pdf" --output ./chapters

# Extract a specific page range (e.g. pages 10–20):
python split_pdf.py "book.pdf" --start 10 --end 20 --output ./output

# Extract a range with a custom output filename:
python split_pdf.py "book.pdf" --start 10 --end 20 --output ./output --name chapter_01
```

**Output file format:** `Chapter_01_Chapter Title.pdf`

---

### Step 2 — Translate chapter PDFs → Bengali Markdown

```bash
# Translate a single chapter PDF:
python run_gemini_translation.py "chapters/Chapter_01_Crimson.pdf" ./translated

# Translate all PDFs in a folder (processes in order, auto-skips already translated):
python run_gemini_translation.py "chapters/" ./translated
```

The script uses the Gemini CLI with automatic model fallback (tries `gemini-2.5-pro`, `gemini-2.5-flash`, etc. in priority order).

**Output file format:** `Chapter_001_Chapter Name.md`

---

### Step 3 — Convert Markdown → Bengali PDF

```bash
python md_to_pdf.py "translated/Chapter_001_Crimson.md"

# Specify output location:
python md_to_pdf.py "translated/Chapter_001_Crimson.md" --output "./pdf_output/Chapter_001.pdf"
```

---

## Translation Style

- **Language:** Literary Bengali (Cholitobhasha / colloquial)
- **Style reference:** Muhammed Zafar Iqbal's _Jolmanob_ — simple, fluid, teen-friendly
- **Pronouns:** Informal (`সে`, `তুমি`) — never formal (`আপনি`, `তিনি`)
- **Audience:** Teenagers; avoids archaic or heavy Sanskrit-based words
- **Atmosphere:** Mystery and thrill (Sci-Fi/Fantasy vibe)
